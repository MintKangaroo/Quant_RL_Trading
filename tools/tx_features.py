#!/usr/bin/env python
"""시행 TX — 문서 벡터 → 랭커 피처 3개(tx_tone20 · tx_worst60 · tx_change). 등록 초안: docs/protocols/tx-filing-text-2026-10.md.

    .venv/bin/python tools/tx_features.py --stage fit       # 유형 중심 + 리지 머리를 2023-01-31 까지로 얼린다(등록 고정 뒤에만)
    .venv/bin/python tools/tx_features.py --stage bake      # 얼린 머리로 문서 점수 → 세션 피처 월 조각
    .venv/bin/python tools/tx_features.py --stage coverage  # 굽힌 조각이 세션·종목을 얼마나 덮나(수익·IC 안 봄)

**누수 차단 — 코드로 강제한다.**
- 유형 중심·표준화·리지 계수는 `visible ≤ CUTOFF(2023-01-31)` 인 문서로만 적합한다(`fit_frozen`). CUTOFF 보다 늦은 컷오프를
  넘기면 `CutoffViolation`. 컷오프 뒤 문서의 벡터·라벨을 바꿔도 얼린 값은 한 자리도 안 바뀐다(테스트가 고정).
- 적합 구간 문서의 점수는 **시간순 5겹 OOF**(퍼지 5세션) — 제 라벨로 적합한 점수를 GBM 에 주지 않는다.
- `--stage fit` 은 등록 문서 머리줄이 아직 `> **초안` 이면 거절한다(rc 4) — 등록 전에 라벨을 보지 않는다.

관측 규칙: 문서(접수일 d)는 **d 다음 날 08:00 KST** 에 관측된다 → 처음 보이는 세션 = d 보다 **뒤**의 첫 거래일.
세션 s 의 피처는 그 규칙으로 s 까지 보인 문서만 쓴다. 정기보고서(tx_change)도 같다.

출력: `data/_diag/tx/features/` 의 `TX-KR-<연월>` 월 조각(entity_id · session · 피처 3) — 회차 kit 의 묶음 월 조각과 같은 모양.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.tx_embed import KST, MODEL, REVISION, VECTOR_ROOT, is_periodic  # noqa: E402

#: 판정 창 첫 채점 세션(2023-02-15)의 학습 끝점 = `final_round_kit.train_end(sessions, blocks[0][0])` (BE3 와 같다).
CUTOFF = date(2023, 1, 31)
TOP_TITLES = 30
ALPHAS = (10.0, 100.0, 1_000.0, 10_000.0)
FOLDS = 5
PURGE = 5
TONE_WINDOW = 20
WORST_WINDOW = 60
CHANGE_MAX_DAYS = 400
FEATURES = ("tx_tone20", "tx_worst60", "tx_change")
PROTOCOL = Path("docs/protocols/tx-filing-text-2026-10.md")
TARGETS = Path("data/_diag-long/targets-KR-h5.pkl")
OUT = Path("data/_diag/tx/features")
MODEL_DIR = Path("data/models/tx")
DOC_SCORES = Path("data/_diag/tx/doc-scores.parquet")  # invariant-allow: data-access — 연구 캐시(창고 아님)
CALENDAR_ENTITY = "KR:005930"     # 모든 세션에 거래된 종목 — 그 시세 날짜가 국장 달력이다
_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")
_KIND = re.compile(r"^(분기|반기|사업)보고서")


class CutoffViolation(RuntimeError):
    """컷오프 뒤 자료가 얼리는 적합에 들어가려 했다."""


# -- 세션 -----------------------------------------------------------------------------


def visible_index(filed: Iterable[date], sessions: np.ndarray) -> np.ndarray:
    """접수일 → 처음 보이는 세션의 위치(접수일보다 **뒤** 첫 세션). 달력 끝을 넘으면 len(sessions)."""
    return np.searchsorted(sessions, np.asarray(list(filed), dtype="datetime64[D]"), side="right")


def calendar(store: Store, now: datetime, *, start: date = date(2020, 7, 1)) -> np.ndarray:
    frame = read_prices(store, as_of=now, entity=CALENDAR_ENTITY, lookback=(now.date() - start).days + 2,
                        columns=["entity_id", "valid_from"])
    days = pd.to_datetime(frame["valid_from"]).dt.tz_convert(KST).dt.date
    return np.array(sorted(set(days)), dtype="datetime64[D]")


def title_key(title: Any) -> str:
    text = _PREFIX.sub("", str(title or ""))
    return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", text))


# -- 문서 벡터 ------------------------------------------------------------------------


def load_vectors(root: Path) -> tuple[pd.DataFrame, np.ndarray]:
    metas, mats = [], []
    for part in sorted(glob.glob(str(root / "*" / "part-*.npz"))):  # invariant-allow: data-access — 연구 캐시
        with np.load(part, allow_pickle=False) as data:  # invariant-allow: data-access — 연구 캐시
            metas.append(pd.DataFrame({k: data[k] for k in ("doc_id", "entity_id", "valid_from", "doc_type", "title")}))
            mats.append(np.asarray(data["vec"], dtype=np.float32))
    if not metas:
        return pd.DataFrame(columns=["doc_id", "entity_id", "valid_from", "doc_type", "title"]), np.zeros((0, 0), np.float32)
    meta = pd.concat(metas, ignore_index=True)
    X = np.vstack(mats)
    keep = ~meta["doc_id"].duplicated(keep="last")
    meta, X = meta[keep].reset_index(drop=True), X[keep.to_numpy()]
    meta["valid_from"] = pd.to_datetime(meta["valid_from"]).dt.date
    return meta, X


def attach_sessions(meta: pd.DataFrame, sessions: np.ndarray) -> pd.DataFrame:
    out = meta.copy()
    out["sidx"] = visible_index(out["valid_from"], sessions)
    out = out[out["sidx"] < len(sessions)].copy()
    out["visible"] = pd.to_datetime(sessions[out["sidx"].to_numpy()]).date
    return out


# -- 얼린 머리 ------------------------------------------------------------------------


@dataclass
class FrozenTx:
    cutoff: date
    keys: list[str]                 # 유형 키(doc_type|제목 상위 30종, 나머지는 doc_type)
    centroids: np.ndarray           # keys × d
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    alpha: float
    alpha_ic: dict[str, float]
    n_centroid: int
    n_ridge: int
    last_fit_visible: date

    def key_of(self, meta: pd.DataFrame) -> pd.Series:
        known = set(self.keys)
        full = meta["doc_type"].astype(str) + "|" + meta["title"].map(title_key)
        return full.where(full.isin(known), meta["doc_type"].astype(str))

    def residual(self, meta: pd.DataFrame, X: np.ndarray) -> np.ndarray:
        index = {k: i for i, k in enumerate(self.keys)}
        rows = self.key_of(meta).map(lambda k: index.get(k, -1)).to_numpy()
        base = np.where(rows[:, None] >= 0, self.centroids[np.maximum(rows, 0)], 0.0)
        return X.astype(np.float64) - base

    def score(self, meta: pd.DataFrame, X: np.ndarray) -> np.ndarray:
        Z = (self.residual(meta, X) - self.mean) / self.scale
        return np.asarray(Z @ self.coef + self.intercept)

    def digest(self) -> str:
        h = hashlib.sha256()
        for arr in (self.centroids, self.mean, self.scale, self.coef):
            h.update(np.ascontiguousarray(arr, dtype=np.float64).tobytes())
        h.update(f"{self.cutoff}|{self.alpha}|{self.intercept}|{','.join(self.keys)}".encode())
        return h.hexdigest()[:16]

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"frozen-{self.digest()}.npz"
        np.savez_compressed(path, cutoff=str(self.cutoff), keys=np.array(self.keys), centroids=self.centroids,
                            mean=self.mean, scale=self.scale, coef=self.coef, intercept=self.intercept, alpha=self.alpha,
                            alpha_ic=json.dumps(self.alpha_ic), n_centroid=self.n_centroid, n_ridge=self.n_ridge,
                            last_fit_visible=str(self.last_fit_visible), model=f"{MODEL}@{REVISION}")
        return path

    @classmethod
    def load(cls, path: Path) -> FrozenTx:
        with np.load(path, allow_pickle=False) as d:  # invariant-allow: data-access — 모델 산출물
            frozen = cls(cutoff=date.fromisoformat(str(d["cutoff"])), keys=[str(k) for k in d["keys"]],
                         centroids=d["centroids"], mean=d["mean"], scale=d["scale"], coef=d["coef"],
                         intercept=float(d["intercept"]), alpha=float(d["alpha"]), alpha_ic=json.loads(str(d["alpha_ic"])),
                         n_centroid=int(d["n_centroid"]), n_ridge=int(d["n_ridge"]),
                         last_fit_visible=date.fromisoformat(str(d["last_fit_visible"])))
        check_cutoff(frozen.cutoff)
        if frozen.last_fit_visible > frozen.cutoff:
            raise CutoffViolation(f"{path.name}: 적합에 쓴 마지막 문서 {frozen.last_fit_visible} > 컷오프 {frozen.cutoff}")
        return frozen


def check_cutoff(cutoff: date) -> None:
    if cutoff > CUTOFF:
        raise CutoffViolation(f"컷오프 {cutoff} 는 등록 컷오프 {CUTOFF} 보다 늦다 — 판정 창 라벨을 본다")


def ridge(Z: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    mu = float(y.mean())
    gram = Z.T @ Z + alpha * np.eye(Z.shape[1])
    return np.linalg.solve(gram, Z.T @ (y - mu)), mu


def rank_ic(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def time_folds(sidx: np.ndarray, folds: int = FOLDS, purge: int = PURGE) -> list[tuple[np.ndarray, np.ndarray]]:
    """세션 순으로 연속 5겹. 각 겹의 학습 행은 시험 겹 앞뒤 `purge` 세션을 뺀다."""
    unique = np.unique(sidx)
    out = []
    for block in np.array_split(unique, folds):
        if len(block) == 0:
            continue
        lo, hi = block.min(), block.max()
        test = (sidx >= lo) & (sidx <= hi)
        train = (sidx < lo - purge) | (sidx > hi + purge)
        out.append((train, test))
    return out


def fit_frozen(meta: pd.DataFrame, X: np.ndarray, y: pd.Series, *, cutoff: date = CUTOFF,
               alphas: tuple[float, ...] = ALPHAS) -> tuple[FrozenTx, pd.Series]:
    """(얼린 머리, 적합 구간 문서의 OOF 점수). `meta` 에는 `visible`·`sidx` 가 붙어 있어야 한다.

    컷오프 뒤 문서는 **행 선택 단계에서** 빠진다 — 그 뒤의 어떤 계산도 그 행을 보지 않는다.
    """
    check_cutoff(cutoff)
    in_window = (pd.to_datetime(meta["visible"]).dt.date <= cutoff).to_numpy()
    fit_meta, fit_X = meta[in_window].reset_index(drop=True), X[in_window]
    fit_y = pd.Series(np.asarray(y)[in_window])
    if fit_meta.empty:
        raise ValueError("컷오프 이전 문서가 없다")
    last = max(pd.to_datetime(fit_meta["visible"]).dt.date)
    if last > cutoff:
        raise CutoffViolation(f"적합 행의 마지막 관측 {last} > {cutoff}")

    full = fit_meta["doc_type"].astype(str) + "|" + fit_meta["title"].map(title_key)
    top = list(full.value_counts().head(TOP_TITLES).index)
    key = full.where(full.isin(set(top)), fit_meta["doc_type"].astype(str))
    keys = sorted(set(key))
    centroids = np.vstack([fit_X[(key == k).to_numpy()].astype(np.float64).mean(axis=0) for k in keys])
    probe = FrozenTx(cutoff, keys, centroids, np.zeros(X.shape[1]), np.ones(X.shape[1]), np.zeros(X.shape[1]), 0.0,
                     0.0, {}, int(len(fit_meta)), 0, last)
    R = probe.residual(fit_meta, fit_X)

    has = fit_y.notna().to_numpy()
    R, yv, sidx = R[has], fit_y[has].to_numpy(np.float64), fit_meta["sidx"].to_numpy()[has]
    mean, scale = R.mean(axis=0), R.std(axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    Z = (R - mean) / scale

    folds = time_folds(sidx)
    alpha_ic: dict[str, float] = {}
    for alpha in alphas:
        ics = []
        for train, test in folds:
            if train.sum() < 10 or test.sum() < 3:
                continue
            coef, mu = ridge(Z[train], yv[train], alpha)
            ics.append(rank_ic(Z[test] @ coef + mu, yv[test]))
        alpha_ic[str(alpha)] = float(np.nanmean(ics)) if ics else float("nan")
    best = max(alphas, key=lambda a: -np.inf if np.isnan(alpha_ic[str(a)]) else alpha_ic[str(a)])

    oof = np.full(len(yv), np.nan)
    for train, test in folds:
        if train.sum() < 10:
            continue
        coef, mu = ridge(Z[train], yv[train], best)
        oof[test] = Z[test] @ coef + mu
    coef, mu = ridge(Z, yv, best)
    frozen = FrozenTx(cutoff, keys, centroids, mean, scale, coef, mu, best, alpha_ic, int(len(fit_meta)), int(has.sum()), last)
    oof_series = pd.Series(oof, index=fit_meta["doc_id"].to_numpy()[has]).dropna()
    return frozen, oof_series


def doc_scores(meta: pd.DataFrame, X: np.ndarray, frozen: FrozenTx, oof: pd.Series) -> pd.Series:
    """문서 점수. 적합 라벨이 있던 문서는 OOF, 나머지(컷오프 뒤·라벨 없음)는 얼린 머리."""
    scores = pd.Series(frozen.score(meta, X), index=meta["doc_id"].to_numpy())
    common = scores.index.isin(oof.index)
    scores[common] = oof.reindex(scores.index[common]).to_numpy()
    return scores


def labels(meta: pd.DataFrame, targets: pd.DataFrame, sessions: np.ndarray) -> pd.Series:
    """문서가 처음 보이는 세션의 y5 rank-gauss(세션별, 국장 전체 종목). 없으면 NaN."""
    from scipy.stats import norm  # type: ignore[import-untyped]

    t = targets.dropna(subset=["target"]).copy()
    t["session"] = pd.to_datetime(t["session"]).dt.date
    r = t.groupby("session")["target"].rank()
    n = t.groupby("session")["target"].transform("count")
    t["y"] = norm.ppf((r - 0.5) / n)
    lookup = t.set_index(["entity_id", "session"])["y"]
    keys = pd.MultiIndex.from_arrays([meta["entity_id"].to_numpy(), pd.to_datetime(meta["visible"]).dt.date.to_numpy()])
    return pd.Series(lookup.reindex(keys).to_numpy(), index=meta.index)


# -- 세션 피처 ------------------------------------------------------------------------


def aggregate(meta: pd.DataFrame, scores: pd.Series, sessions: np.ndarray) -> pd.DataFrame:
    """종목 × 세션: 최근 20세션 점수 평균 · 최근 60세션 점수 최솟값. 문서가 보인 세션부터 센다."""
    frame = meta[["doc_id", "entity_id", "sidx"]].copy()
    frame["score"] = scores.reindex(frame["doc_id"].to_numpy()).to_numpy()
    frame = frame.dropna(subset=["score"])
    if frame.empty:
        return pd.DataFrame(columns=["entity_id", "session", "tx_tone20", "tx_worst60"])
    entities = np.array(sorted(frame["entity_id"].unique()))
    e = np.searchsorted(entities, frame["entity_id"].to_numpy())
    S = len(sessions)
    total = np.zeros((len(entities), S + 1))
    count = np.zeros((len(entities), S + 1))
    worst = np.full((len(entities), S), np.inf)
    np.add.at(total, (e, frame["sidx"].to_numpy()), frame["score"].to_numpy())
    np.add.at(count, (e, frame["sidx"].to_numpy()), 1.0)
    np.minimum.at(worst, (e, frame["sidx"].to_numpy()), frame["score"].to_numpy())
    total, count = total[:, :S], count[:, :S]

    def rolling_sum(a: np.ndarray, w: int) -> np.ndarray:
        c = np.cumsum(a, axis=1)
        out = c.copy()
        out[:, w:] = c[:, w:] - c[:, :-w]
        return out

    s20, c20 = rolling_sum(total, TONE_WINDOW), rolling_sum(count, TONE_WINDOW)
    tone = np.where(c20 > 0, s20 / np.maximum(c20, 1), np.nan)
    w60 = worst.copy()
    for shift in range(1, WORST_WINDOW):
        w60[:, shift:] = np.minimum(w60[:, shift:], worst[:, :-shift])
    w60 = np.where(np.isfinite(w60), w60, np.nan)
    rows, cols = np.nonzero(~np.isnan(w60))
    return pd.DataFrame({"entity_id": entities[rows], "session": pd.to_datetime(sessions[cols]).date,
                         "tx_tone20": tone[rows, cols].astype(np.float32), "tx_worst60": w60[rows, cols].astype(np.float32)})


# -- Lazy Prices (정기보고서 문서 유사도) ----------------------------------------------


def sections(text: str) -> str:
    """'II. 사업의 내용'~'III. 재무에 관한 사항' + 'IV. 이사의 경영진단 및 분석의견'~'V.' — 목차를 피해 가장 긴 구간. 없으면 전문."""
    lines = text.splitlines()
    compact = [re.sub(r"\s+", "", x) for x in lines]

    def longest(start: str, stop: str) -> str:
        best = ""
        for i, c in enumerate(compact):
            if c.startswith(start):
                for j in range(i + 1, len(compact)):
                    if compact[j].startswith(stop):
                        span = "\n".join(lines[i + 1:j])
                        if len(span) > len(best):
                            best = span
                        break
        return best

    body = longest("II.사업의내용", "III.재무에관한사항") + "\n" + longest("IV.이사의경영진단", "V.")
    return body if body.strip() else text


class Hasher:
    """해시 단어 빈도 — 적합할 것이 없다(IDF 없음). 숫자는 지운다(보고서마다 바뀌는 숫자가 유사도를 흐린다)."""

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer  # type: ignore[import-untyped]

        self.vec = HashingVectorizer(n_features=2**20, alternate_sign=False, norm=None)

    def __call__(self, text: str) -> Any:
        m = self.vec.transform([re.sub(r"\d+", " ", text)])
        m.data = 1.0 + np.log(m.data)
        n = np.sqrt(m.multiply(m).sum())
        return m / n if n > 0 else m


def report_period(title: Any) -> tuple[str, int, int] | None:
    text = re.sub(r"\s+", "", _PREFIX.sub("", str(title or "")))
    kind, period = _KIND.match(text), _PERIOD.search(text)
    if not kind or not period:
        return None
    return kind.group(1), int(period.group(1)), int(period.group(2))


def lazy_prices(periodic: pd.DataFrame, sessions: np.ndarray, read_text: Callable[[Path], str],
                log: Callable[[str], None] = print) -> pd.DataFrame:
    """종목마다 시간순: 같은 종류·같은 달의 전년 보고서(그때까지 나온 마지막 판) 대비 1 − cos. 다음 정기보고서가 보일
    때까지 유지, 접수 400일이 지나면 결측."""
    hasher = Hasher()
    events = []
    periodic = periodic.sort_values(["entity_id", "valid_from", "doc_id"])
    for n_ent, (entity, group) in enumerate(periodic.groupby("entity_id", sort=False)):
        last: dict[tuple[str, int, int], Any] = {}
        for record in group.to_dict(orient="records"):
            period = report_period(record["title"])
            if period is None:
                continue
            try:
                vec = hasher(sections(read_text(Path(str(record["raw_path"])))))
            except (OSError, EOFError, ValueError):
                continue
            kind, year, month = period
            prior = last.get((kind, year - 1, month))
            value = float("nan") if prior is None else float(1.0 - vec.multiply(prior).sum())
            last[(kind, year, month)] = vec
            events.append((entity, record["valid_from"], value))
        if n_ent % 200 == 0:
            log(f"  Lazy Prices 종목 {n_ent:,}")
    if not events:
        return pd.DataFrame(columns=["entity_id", "session", "tx_change"])
    ev = pd.DataFrame(events, columns=["entity_id", "filed", "tx_change"])
    ev["sidx"] = visible_index(ev["filed"], sessions)
    ev = ev[ev["sidx"] < len(sessions)]
    out = []
    session_dates = pd.to_datetime(sessions).date
    for entity, g in ev.groupby("entity_id"):
        g = g.sort_values("sidx").drop_duplicates("sidx", keep="last")
        starts = g["sidx"].to_numpy()
        ends = np.append(starts[1:], len(sessions))
        for s0, s1, value, filed in zip(starts, ends, g["tx_change"].to_numpy(), g["filed"], strict=True):
            if np.isnan(value):
                continue
            for k in range(s0, s1):
                if (session_dates[k] - filed).days > CHANGE_MAX_DAYS:
                    break
                out.append((entity, session_dates[k], value))
    return pd.DataFrame(out, columns=["entity_id", "session", "tx_change"]).astype({"tx_change": np.float32})


def periodic_frame(store: Store, now: datetime, *, start: date = date(2020, 8, 1)) -> pd.DataFrame:
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=(now.date() - start).days + 2, market="KR",
                      columns=["entity_id", "valid_from", "revision", "source", "doc_id", "title", "raw_path"])
    frame = frame.sort_values("revision").groupby(["entity_id", "valid_from", "doc_id"], as_index=False).tail(1)
    frame = frame[(frame["source"] == docs.SOURCE) & frame["title"].map(is_periodic)]
    path = frame["raw_path"].fillna("").astype(str)
    frame = frame[(path.str.len() > 1) & (path != "None")].copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"]).dt.tz_convert(KST).dt.date
    return frame


# -- 쓰기 -----------------------------------------------------------------------------


def write_months(features: pd.DataFrame, out: Path, *, start: date, end: date) -> int:
    out.mkdir(parents=True, exist_ok=True)
    f = features[(features["session"] >= start) & (features["session"] <= end)].copy()
    for c in FEATURES:
        if c not in f.columns:
            f[c] = np.nan
        f[c] = f[c].astype(np.float32)
    f["month"] = pd.to_datetime(f["session"]).dt.strftime("%Y%m")
    n = 0
    for month, g in f.groupby("month"):
        g[["entity_id", "session", *FEATURES]].to_parquet(out / f"TX-KR-{month}.parquet", index=False)  # invariant-allow: data-access — 연구 캐시
        n += 1
    return n


def registration_fixed(path: Path | None = None) -> bool:
    path = path or PROTOCOL
    first = path.read_text(encoding="utf-8").splitlines()[0] if path.exists() else ""
    return bool(first) and not first.startswith("> **초안")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--stage", choices=("fit", "bake", "coverage"), required=True)
    parser.add_argument("--start", default="2021-11-01", help="피처를 쓸 첫 세션")
    parser.add_argument("--end", default="", help="피처를 쓸 마지막 세션(기본: 달력 끝)")
    parser.add_argument("--frozen", default="", help="bake 에 쓸 얼린 머리(기본: data/models/tx 의 유일한 파일)")
    args = parser.parse_args(argv)

    if args.stage == "fit" and not registration_fixed():
        print(f"등록이 아직 초안이다({PROTOCOL}) — 라벨을 보는 적합은 등록 고정 뒤에만 한다", file=sys.stderr)
        return 4
    root = Path(args.root)
    store = Store(root=root)
    now = LiveClock().now()
    sessions = calendar(store, now)
    vectors = root / VECTOR_ROOT.relative_to("data")

    if args.stage == "fit":
        meta, X = load_vectors(vectors)
        meta = attach_sessions(meta, sessions)
        X = X[meta.index.to_numpy()]
        meta = meta.reset_index(drop=True)
        targets = pd.read_pickle(root / TARGETS.relative_to("data"))  # invariant-allow: data-access — 작업 캐시
        y = labels(meta, targets, sessions)
        frozen, oof = fit_frozen(meta, X, y)
        path = frozen.save(root / MODEL_DIR.relative_to("data"))
        scores = doc_scores(meta, X, frozen, oof)
        out = root / DOC_SCORES.relative_to("data")
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"doc_id": scores.index, "score": scores.to_numpy(),
                      "oof": scores.index.isin(oof.index)}).to_parquet(out, index=False)
        print(f"얼림 {path.name} · 유형 키 {len(frozen.keys)} · 적합 문서 {frozen.n_centroid:,}(라벨 {frozen.n_ridge:,}) · "
              f"마지막 관측 {frozen.last_fit_visible} · 알파 {frozen.alpha:g} · 알파별 OOF IC {frozen.alpha_ic} · 점수 {len(scores):,}")
        return 0

    if args.stage == "bake":
        model_dir = root / MODEL_DIR.relative_to("data")
        files = [Path(args.frozen)] if args.frozen else sorted(model_dir.glob("frozen-*.npz"))
        if len(files) != 1:
            print(f"얼린 머리가 {len(files)}개다 — --frozen 으로 하나를 고른다", file=sys.stderr)
            return 3
        FrozenTx.load(files[0])  # 컷오프 검사
        meta, _ = load_vectors(vectors)
        meta = attach_sessions(meta, sessions).reset_index(drop=True)
        scored = pd.read_parquet(root / DOC_SCORES.relative_to("data"))  # invariant-allow: data-access — 연구 캐시
        tone = aggregate(meta, scored.set_index("doc_id")["score"], sessions)
        change = lazy_prices(periodic_frame(store, now), sessions, docs.read_text, log=lambda s: print(s, flush=True))
        features = tone.merge(change, on=["entity_id", "session"], how="outer")
        end = date.fromisoformat(args.end) if args.end else pd.to_datetime(sessions[-1]).date()
        n = write_months(features, root / OUT.relative_to("data"), start=date.fromisoformat(args.start), end=end)
        print(f"굽기 · 행 {len(features):,} · 월 조각 {n} · tone {tone['tx_tone20'].notna().sum():,} · "
              f"worst {tone['tx_worst60'].notna().sum():,} · change {change['tx_change'].notna().sum():,}")
        return 0

    # coverage — 피처가 붙는 비율만(수익·IC 안 봄)
    parts = sorted(glob.glob(str(root / OUT.relative_to("data") / "TX-KR-*.parquet")))  # invariant-allow: data-access — 연구 캐시
    if not parts:
        print("굽힌 조각이 없다", file=sys.stderr)
        return 1
    f = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)  # invariant-allow: data-access — 연구 캐시
    f["year"] = pd.to_datetime(f["session"]).dt.year
    print(f.groupby("year").agg(rows=("entity_id", "size"), entities=("entity_id", "nunique"),
                                tone=("tx_tone20", lambda s: s.notna().mean()), worst=("tx_worst60", lambda s: s.notna().mean()),
                                change=("tx_change", lambda s: s.notna().mean())).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
