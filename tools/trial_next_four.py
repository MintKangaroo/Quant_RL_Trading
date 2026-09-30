"""시행 묶음 next-four — 마지막 모델 회차에서 기각된 시행의 좋은 조각을 살린 새 시행(① TB · ③ RE · ④ DF). ②는 다른 도구.

    .venv/bin/python tools/trial_next_four.py smoke                       # 합성 자료로 세 경로 배선 확인(수치 안 찍음, 아무 때나)
    .venv/bin/python tools/trial_next_four.py precheck [--index]          # 입력·HMM 커버리지·규칙 발동률(수익 안 봄)
    .venv/bin/python tools/trial_next_four.py tb --i-registered [--save]  # ① 지수 코어 + BE2 기울이기
    .venv/bin/python tools/trial_next_four.py re --i-registered [--save]  # ③ 국면 가중 앙상블
    .venv/bin/python tools/trial_next_four.py df --i-registered [--save]  # ④ 방어형 BF2

사전등록 `docs/protocols/next-four-2026-10.md`. 머리줄이 `> **초안` 이면 세 판정 명령 모두 거부한다(`--i-registered` 도 필요).

**새 학습이 없다.** 세 시행 모두 마지막 모델 회차가 이미 구운 판정 창 워크포워드 예측(시드 0~4)을 **읽기만** 한다:
BE1 `data/_diag/final-round/BE/seed{s}-judge` 조각 · BF1 `pred-BF1-seed{s}-{꼬리표}.pkl` · C0·C1 `pred-C{0,1}-seed{s}-{꼬리표}.pkl`
(꼬리표 `KR+US-20220701-20260630`). 판정 창·블록·퍼지·시드·비용·포트는 그 회차 그대로다(kit 한 곳).

- **① TB** — D1b 의 **기울이기 장부**(`trial_final_dfl.tilt_book`, λ = 0.5 · 상한 국장 30%·미장 10% · 10세션 재조정)에 BE2 점수를 넣는다.
  BE2 = 시드마다 `trial_final_transformer.rank_average(BE1_s, C1_s)` — 회차 판정이 BE2 를 만든 **바로 그 함수**다.
  대조 T0(C0 기울이기) · T1(C1 기울이기) · B0(지수 그대로, λ = 0). 기준은 D1b v2 ①~⑨.
- **③ RE** — 선정 점수 = w_BE1·pct(BE1) + w_C1·pct(C1) + w_BF2·pct(BF2), pct = (세션, 시장) 안 백분위.
  가중 = 그 세션까지 거른 HMM 국면 확률(p_위기·p_박스·p_급등) × **등록에 적은 표 하나**(`RE_TABLE`). 포트는 kit 규칙 포트.
  대조 C0 · C1 · **BE2(고정 반반)**. 기준 = 회차 ①~⑥ + ⑦ BE2 보다 연 +1%p.
- **④ DF** — pct(C1), 단 HMM 위기 확률 > 0.5 인 세션만 (pct(C1) + pct(BF2)) / 2. 대조 **C1′ = pct(C1)**(같은 변환 · 신호 없는
  같은 구성, 위기 세션이 하나도 없으면 DF 와 정확히 같다 — 테스트). 기준은 방어: MDD 1%p 얕게 · 연 ≥ C1′ − 0.5%p ·
  두 국면 ≥ C1′ − 1%p · 회전 ≤ C1′ × 1.2.

BF2 = `trial_final_lambdarank.rank_average(BF1_s, C1_s)` — BF 판정이 BF2 를 만든 바로 그 함수다.

**HMM 은 미래를 안 본다.** `tools/v2_regime_hmm.run` 그대로(달마다 그 달 첫 세션 **전**까지로 적합, 필터는 전방만) —
지수 종가는 판정 창 끝(2026-06-30)까지만 읽고, 확률은 행의 세션 **이하**의 가장 최근 값만 붙인다(`merge_asof` backward).
"앞 부분만 준 run 이 전체 run 의 앞 부분과 같다" 를 테스트가 단언한다. 상태 순서는 적합마다 평균 일수익 오름차순
(0 = 가장 나쁜 국면 = 이 문서의 '위기', 1 = '박스', 2 = 가장 좋은 = '급등') — 이름은 꼬리표일 뿐 달력 국면(박스 ~2024-12 · 급등 2025-01~)과 다르다.

관문(종료 코드): 등록 거부 1 · 입력 예측 없음 3 · 지수 구성 이력 미달 6(TB) · HMM 확률 커버리지 미달 8 · 처리·대조 채점 세션 불일치 9.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools import final_round_kit as kit  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.trial_ranker_ensemble import JUDGE_END, JUDGE_START  # noqa: E402

PROTOCOL = Path("docs/protocols/next-four-2026-10.md")
ENTITY = "next-four-2026-10"
FAMILY = "selection"
#: 이 묶음의 시행 — 다중검정을 한 줄에 적는다(②는 다른 도구가 같은 묶음 이름으로 적는다).
BUNDLE = ("①TB", "②(별도 도구)", "③RE", "④DF")
WINDOW = (JUDGE_START, JUDGE_END)
SEEDS = kit.SEEDS
MARKETS = ("KR", "US")
CACHE = kit.CACHE
WORK = CACHE / "next-four"
BE_DIR = CACHE / "BE"

# ── 등록 때 고정하는 값 (판정 창에서 고르지 않는다 — 규율 8) ─────────────────────────────

#: HMM 상태 수 — `v2_regime_hmm` 의 기본값 그대로(새로 고르지 않는다).
HMM_STATES = 3
#: 상태 번호 → 이 문서의 이름. 상태는 적합마다 평균 일수익 오름차순이다(0 이 가장 나쁘다).
STATE_NAMES = ("crisis", "box", "rally")
#: ③ RE 가중 표 — **설계 가설 하나**(대안 표를 두지 않는다). 행 = 국면, 열 = 모델, 행 합 1. 세션 가중 = Σ_s p_s · RE_TABLE[s].
#:  급등 → BE1(60세션 시계열 + 횡단면 주의: 추세가 이어지는 국면에 시간 축 정보가 가장 값진다는 가설)
#:  박스 → C1(횡단면 GBM·FA: 추세가 없을 때 재무·가치의 횡단면 차이가 남는다는 가설)
#:  위기 → BF2(10분위 버킷 LambdaRank + C1: 변동성이 클 때 맨 위 순위의 잡음을 덜 믿는 거친 순서가 버틴다는 가설)
RE_TABLE: dict[str, dict[str, float]] = {
    "crisis": {"BE1": 0.2, "C1": 0.3, "BF2": 0.5},
    "box": {"BE1": 0.2, "C1": 0.6, "BF2": 0.2},
    "rally": {"BE1": 0.6, "C1": 0.3, "BF2": 0.1},
}
RE_MODELS = ("BE1", "C1", "BF2")
#: ④ DF — 위기 확률 문턱(초과일 때만 발동)과 섞는 비율.
DF_THRESHOLD = 0.5
DF_BLEND = 0.5
#: ① TB — D1b 와 같은 기울이기 세기(λ). trial_final_dfl.LAMBDA 를 그대로 읽는다(아래 _dfl()).
IR_GATE = 0.30
#: ③ RE ⑦ — 국면 가중이 고정 반반(BE2)보다 나아야 하는 몫.
RE_GATE_BE2 = 0.01
#: ④ DF 기준(방어) — C1′ 대비.
DF_GATE_MDD, DF_GATE_ANN, DF_GATE_REGIME, DF_GATE_TURN = 0.01, -0.005, -0.01, 1.2
#: HMM 확률이 **그 날짜에 정확히** 있는 판정 세션의 몫이 이보다 적으면 멈춘다(지수 종가 결측).
HMM_MIN_COVERAGE = 0.95
#: 붙일 수 있는 가장 오래된 확률(달력일). 이보다 오래된 것밖에 없으면 그 행은 확률 없음이다.
HMM_MAX_STALE_DAYS = 7

REGISTER_EXIT, INPUT_EXIT, INDEX_EXIT, HMM_EXIT, SPAN_EXIT = 1, 3, 6, 8, 9
JUDGE_KEYS = ("ann", "box_ann", "rally_ann", "mdd", "turn", "ic")
KEYS = ["entity_id", "session", "market"]


def _dfl() -> ModuleType:
    """D1 도구(torch 를 부른다) — TB 경로에서만 필요하다. 기울이기 장부·지수 비중·B0 는 **그 파일의 함수**를 쓴다(베끼지 않는다)."""
    from tools import trial_final_dfl as dfl
    return dfl


# --------------------------------------------------------------------------- 점수 합성


def _dates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["session"] = pd.to_datetime(out["session"]).dt.date
    return out


def percentile(frame: pd.DataFrame) -> pd.Series:
    """(세션, 시장) 안 백분위 — `trial_final_transformer.rank_average` 가 BE2 를 만든 규칙(`rank(pct=True)`, 동점 평균)."""
    return frame.groupby(["session", "market"])["pred"].rank(pct=True)


def be2_scores(be1: pd.DataFrame, c1: pd.DataFrame) -> pd.DataFrame:
    """BE2 = 회차 판정의 합성 **그 함수** 그대로(시드 하나)."""
    from tools.trial_final_transformer import rank_average
    return rank_average(be1[[*KEYS, "pred"]], c1[[*KEYS, "pred"]])


def bf2_scores(bf1: pd.DataFrame, c1: pd.DataFrame) -> pd.DataFrame:
    """BF2 = BF 판정의 합성 **그 함수** 그대로(시드 하나). 쿼리 = (세션, 시장)."""
    from tools.trial_final_lambdarank import rank_average
    out = rank_average(bf1[[*KEYS, "pred"]], c1[[*KEYS, "pred"]], ["session", "market"])
    return out[[*KEYS, "pred"]]


def pct_frame(components: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """모델 이름 → 예측. (세션, 시장) 안 백분위로 바꿔 **inner** 로 붙인다 — 열 = 모델 이름."""
    out: pd.DataFrame | None = None
    for name, frame in components.items():
        part = frame[KEYS].copy()
        part[name] = percentile(frame).to_numpy()
        out = part if out is None else out.merge(part, on=KEYS, how="inner")
    assert out is not None
    return out


def weighted(pcts: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    """pred = Σ_모델 w_모델 · pct_모델. ``weights`` = KEYS 없이 (session, market) + 모델 열. 가중이 없는 행은 **버리지 않고 멈춘다**."""
    models = [c for c in weights.columns if c not in ("session", "market")]
    m = pcts.merge(weights, on=["session", "market"], how="left", suffixes=("", "_w"))
    wcols = [f"{c}_w" if f"{c}_w" in m.columns else c for c in models]
    if m[wcols].isna().any().any():
        bad = m.loc[m[wcols].isna().any(axis=1), ["session", "market"]].drop_duplicates().head(3).to_dict("records")
        raise ValueError(f"가중이 없는 세션이 있다(예: {bad}) — 조용히 빼지 않는다")
    pred = np.zeros(len(m))
    for model, wc in zip(models, wcols, strict=True):
        pred += m[wc].to_numpy(np.float64) * m[model].to_numpy(np.float64)
    return m[KEYS].assign(pred=pred)


def re_weights(probs: pd.DataFrame, table: dict[str, dict[str, float]] = RE_TABLE) -> pd.DataFrame:
    """③ RE 세션 가중 — (session, market, p0..p2) → (session, market, BE1, C1, BF2). 행 합 1(확률 합 1 이면)."""
    P = probs[[f"p{s}" for s in range(HMM_STATES)]].to_numpy(np.float64)
    W = np.array([[table[name][model] for model in RE_MODELS] for name in STATE_NAMES])
    out = probs[["session", "market"]].copy()
    out[list(RE_MODELS)] = P @ W
    return out.reset_index(drop=True)


def df_weights(probs: pd.DataFrame, threshold: float = DF_THRESHOLD, blend: float = DF_BLEND) -> pd.DataFrame:
    """④ DF 세션 가중 — 위기 확률(p0) > 문턱이면 C1 1−blend · BF2 blend, 아니면 C1 1."""
    fire = probs["p0"].to_numpy(np.float64) > threshold
    out = probs[["session", "market"]].copy()
    out["C1"] = np.where(fire, 1.0 - blend, 1.0)
    out["BF2"] = np.where(fire, blend, 0.0)
    return out.reset_index(drop=True)


def constant_weights(keys: pd.DataFrame, **w: float) -> pd.DataFrame:
    out = keys[["session", "market"]].drop_duplicates().reset_index(drop=True)
    for model, v in w.items():
        out[model] = float(v)
    return out


# --------------------------------------------------------------------------- HMM 국면 확률 (그 세션까지)


def regime_probs(closes: pd.Series, *, k: int = HMM_STATES) -> pd.DataFrame:
    """세션 × [p0..p{k-1}] — `v2_regime_hmm.run` 그대로(월 1회 확장창 적합 · 전방 필터). 인덱스는 date."""
    from tools import v2_regime_hmm as hmm
    out = hmm.run(closes.sort_index(), k)
    out.index = pd.to_datetime(pd.Index(out.index)).date
    return out[[f"p{s}" for s in range(k)]]


def attach_probs(keys: pd.DataFrame, probs: dict[str, pd.DataFrame], *,
                 max_stale_days: int = HMM_MAX_STALE_DAYS) -> pd.DataFrame:
    """(session, market) 마다 **그 세션 이하**의 가장 최근 확률(backward as-of). 미래 날짜의 확률은 절대 붙지 않는다.

    반환: session · market · p0..p2 · exact(그 날짜에 확률이 있었나). 오래된 것밖에 없으면 p 가 NaN 이다.
    """
    cols = [f"p{s}" for s in range(HMM_STATES)]
    parts = []
    for market, part in keys[["session", "market"]].drop_duplicates().groupby("market"):
        left = part.assign(ts=pd.to_datetime(part["session"])).sort_values("ts")
        table = probs.get(str(market))
        if table is None or table.empty:
            parts.append(left.assign(**{c: np.nan for c in cols}, exact=False).drop(columns="ts"))
            continue
        right = table[cols].copy()
        right["ts"] = pd.to_datetime(pd.Index(table.index))
        right = right.sort_values("ts")
        right["src"] = right["ts"]
        m = pd.merge_asof(left, right, on="ts", direction="backward")
        stale = (m["ts"] - m["src"]).dt.days > max_stale_days
        m.loc[stale | m["src"].isna(), cols] = np.nan
        m["exact"] = m["src"].notna() & (m["src"] == m["ts"])
        parts.append(m.drop(columns=["ts", "src"]))
    return pd.concat(parts, ignore_index=True)


def require_probs(attached: pd.DataFrame, *, min_coverage: float = HMM_MIN_COVERAGE) -> list[str]:
    """HMM 관문 — 시장마다 판정 세션의 min_coverage 이상이 그 날짜 확률을 갖고, 확률 없는 세션은 0 이어야 한다. 아니면 rc 8."""
    lines, ok = [], True
    for market, part in attached.groupby("market"):
        n = len(part)
        exact = int(part["exact"].sum())
        missing = int(part["p0"].isna().sum())
        lines.append(f"{market}: HMM 확률 그 날짜 {exact}/{n} ({exact / max(1, n):.1%}) · 7일 안 이월 {n - exact - missing} · 없음 {missing}")
        ok = ok and exact / max(1, n) >= min_coverage and missing == 0
    if not ok:
        print("HMM 확률 관문 — **미달**\n  " + "\n  ".join(lines), flush=True)
        raise SystemExit(HMM_EXIT)
    return lines


def index_closes(store: object, market: str, until: date) -> pd.Series:
    """지수 종가(`v2_regime_hmm.main` 과 같은 읽기) — **until 이하만**. 금고(2026-07-01~)는 읽지 않는다."""
    from tools import v2_regime_hmm as hmm
    if until >= kit.VAULT_START:
        raise ValueError(f"until {until} 이 금고 안이다 — 판정 창 끝까지만")
    end = datetime.combine(until, time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(until - date(2020, 1, 1)).days,  # type: ignore[attr-defined]
                    market=market, columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == hmm.INDEX[market]].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    closes = closes[(closes > 0) & (closes.index <= until)]
    return closes


def load_probs(store: object, until: date = WINDOW[1], *, cache: Path = WORK) -> dict[str, pd.DataFrame]:
    """시장별 HMM 확률 — 이 도구의 작업 캐시(`hmm-{시장}-{until}.pkl`). 없으면 `regime_probs` 로 굽는다(시장당 수 분~수십 분)."""
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for market in MARKETS:
        path = cache / f"hmm-{market}-{until:%Y%m%d}.pkl"
        if path.exists():
            out[market] = pd.read_pickle(path)  # invariant-allow: data-access — 이 도구가 쓴 작업 캐시
            continue
        began = datetime.now(UTC)  # invariant-allow: wallclock — 소요 시간 기록
        probs = regime_probs(index_closes(store, market, until))
        probs.to_pickle(path)  # invariant-allow: data-access — 작업 캐시
        print(f"  HMM {market}: {len(probs)}세션 ({min(probs.index)}~{max(probs.index)}) · "
              f"{(datetime.now(UTC) - began).total_seconds() / 60:.1f}분 → {path}", flush=True)  # invariant-allow: wallclock
        out[market] = probs
    return out


# --------------------------------------------------------------------------- 입력 (읽기만)


def light_panel(cache: Path = CACHE, window: tuple[date, date] = WINDOW) -> tuple[pd.DataFrame, list[date]]:
    """판정 패널 캐시에서 **키·라벨·재무 표지만** 읽는다 — (panel, 국장 세션 축). FA 76열은 이 시행들에 필요 없다.

    같은 조각을 `kit.load_full_panel` 이 읽으므로 행 집합·꼬리표(`kit.control_tag`)가 대조군과 같다.
    """
    tag = f"{'+'.join(MARKETS)}-{window[0]:%Y%m%d}-{window[1]:%Y%m%d}"
    frames = []
    for market in MARKETS:
        path = cache / f"panel-{market}-{tag}.parquet"  # invariant-allow: data-access — 작업 캐시(kit 과 같은 조각)
        if not path.exists():
            print(f"판정 패널 캐시가 없다: {path} — scripts/final_round_bake.sh 가 먼저다", flush=True)
            raise SystemExit(INPUT_EXIT)
        import pyarrow.parquet as pq  # invariant-allow: data-access — 작업 캐시의 열 이름만 본다
        have = set(pq.read_schema(path).names)  # invariant-allow: data-access — 작업 캐시 스키마
        cols = [c for c in ("entity_id", "session", "market", "y5", "fund_raw", "has_fund") if c in have]
        part = pd.read_parquet(path, columns=cols)  # invariant-allow: data-access — 창고가 아닌 작업 캐시(kit 과 같은 조각)
        part["session"] = kit._as_dates(part["session"])
        frames.append(part)
    panel = kit.share_objects(pd.concat(frames, ignore_index=True))
    sessions = sorted(panel.loc[panel["market"] == "KR", "session"].unique())
    return panel, sessions


def be1_path(seed: int, be_dir: Path = BE_DIR) -> Path:
    return be_dir / f"seed{seed}-judge.parquet"  # invariant-allow: data-access — 회차 BE 의 작업 파일


def bf1_path(seed: int, tag: str, cache: Path = CACHE) -> Path:
    return cache / f"pred-BF1-seed{seed}-{tag}.pkl"


def require_inputs(tag: str, seeds: Sequence[int], models: Sequence[str], *,
                   cache: Path = CACHE, be_dir: Path = BE_DIR) -> None:
    """처리 쪽 예측(BE1·BF1)이 있어야 한다 — 없으면 무엇이 없는지 말하고 rc 3. 이 도구는 **아무것도 학습하지 않는다**."""
    missing = []
    for s in seeds:
        if "BE1" in models and not be1_path(s, be_dir).exists():
            missing.append(str(be1_path(s, be_dir)))
        if "BF1" in models and not bf1_path(s, tag, cache).exists():
            missing.append(str(bf1_path(s, tag, cache)))
    if missing:
        print("처리 쪽 예측이 없다 — 판정을 시작하지 않는다(새로 학습하지 않는다):\n  " + "\n  ".join(missing[:10]), flush=True)
        raise SystemExit(INPUT_EXIT)


def read_be1(seed: int, be_dir: Path = BE_DIR) -> pd.DataFrame:
    frame = pd.read_parquet(be1_path(seed, be_dir))  # invariant-allow: data-access — 회차 BE 의 작업 파일
    return kit.share_objects(_dates(frame)[[*KEYS, "pred"]])


def read_bf1(seed: int, tag: str, cache: Path = CACHE) -> pd.DataFrame:
    frame = pd.read_pickle(bf1_path(seed, tag, cache))  # invariant-allow: data-access — 회차 BF 의 작업 캐시
    return kit.share_objects(_dates(frame)[[*KEYS, "pred"]])


def same_sessions(treated: pd.DataFrame, control: pd.DataFrame, *, label: str) -> None:
    """처리·대조가 **같은 (세션 × 시장)** 을 채점하는지. 다르면 rc 9 — 며칠 넓은 쪽이 이겼는지 진 건지 아무도 모른다(BF 규칙)."""
    from tools.trial_final_lambdarank import session_sets_match
    drift = session_sets_match(treated, control)
    if drift:
        print(f"{label}: 처리와 대조의 채점 세션이 다르다 — 판정을 멈춘다:\n  " + "\n  ".join(drift), flush=True)
        raise SystemExit(SPAN_EXIT)


# --------------------------------------------------------------------------- 판정


def _mean(table: dict[int, dict[str, float]], key: str) -> float:
    vals = [m[key] for m in table.values() if key in m and np.isfinite(m[key])]
    return float(np.mean(vals)) if vals else float("nan")


def require_keys(where: str, metrics: dict[str, float], keys: Sequence[str] = JUDGE_KEYS) -> dict[str, float]:
    bad = [k for k in keys if k not in metrics or not np.isfinite(metrics[k])]
    if bad:
        raise ValueError(f"{where}: 판정 키가 없거나 nan 이다 {bad} — 조용히 기각하지 않는다")
    return metrics


def _criteria(block: list[str], tag: str = "①~⑥") -> list[str]:
    """kit.judge 의 "판정:" 줄을 "①~⑥:" 으로 — 이 도구의 판정 줄은 시행마다 마지막 한 줄뿐이다."""
    return [line.replace("판정:", f"{tag}:", 1) if line.startswith("판정:") else line for line in block]


@dataclass
class Verdict:
    trial: str
    verdict: str
    lines: list[str] = field(default_factory=list)
    #: 군 → 시드 → 지표(판정에 들어간 그대로). 테스트·보고가 읽는다.
    tables: dict[str, dict[int, dict[str, float]]] = field(default_factory=dict)


def judge_tb(results: dict[int, dict[str, float]], t0: dict[int, dict[str, float]],
             t1: dict[int, dict[str, float]], b0: dict[str, float]) -> Verdict:
    """① TB — D1b v2 ①~⑨ 그대로: ①②③④⑤ 대 T0 · ⑥ 대 T1 +1%p · ⑦ 지수 대비 IR ≥ 0.30 · ⑧ 두 국면 지수 대비 초과 ≥ 0 · ⑨ 대 B0 ≥ 0."""
    for name, table in (("TB", results), ("T0", t0), ("T1", t1)):
        for s, m in table.items():
            require_keys(f"{name} 시드 {s}", m, (*JUDGE_KEYS, "ir", "box_excess", "rally_excess"))
    require_keys("B0", b0, ("ann",))
    block, base = kit.judge(results, t0, t1, label="TB BE2 기울이기(대 T0·T1): ")
    lines = [line.replace("⑥ 대 C1", "⑥ 대 T1", 1) for line in _criteria(block)]
    v2 = [_mean(results, "ir") >= IR_GATE,
          _mean(results, "box_excess") >= 0 and _mean(results, "rally_excess") >= 0,
          _mean(results, "ann") >= b0["ann"]]
    mk = rkit.mark
    lines.append(f"⑦⑧⑨: IR(지수) {_mean(results, 'ir'):+.2f} (≥{IR_GATE}) {mk(v2[0])} · 두 국면 지수 대비 "
                 f"{_mean(results, 'box_excess'):+.1%}/{_mean(results, 'rally_excess'):+.1%} {mk(v2[1])} · "
                 f"B0(λ=0) 대비 {_mean(results, 'ann') - b0['ann']:+.1%}p {mk(v2[2])}")
    lines.append(f"기록: 기울이기 장부 연수익 — TB {_mean(results, 'ann'):+.1%} · T0 {_mean(t0, 'ann'):+.1%} · "
                 f"T1 {_mean(t1, 'ann'):+.1%} · B0 {b0['ann']:+.1%} (B0 IR {b0.get('ir', float('nan')):+.2f}) · "
                 f"β TB {_mean(results, 'beta'):.2f} · T1 {_mean(t1, 'beta'):.2f}")
    if base.startswith("채택 —") and all(v2):
        verdict = "채택 후보 — BE2 기울이기(①~⑨)"
    elif base.startswith("채택") and all(v2):
        verdict = "①~⑤·⑦~⑨ 통과·⑥ 미통과 — T1(C1 기울이기)로 충분, TB 기각"
    else:
        verdict = "기각"
    return Verdict("TB", verdict, lines)


def judge_re(results: dict[int, dict[str, float]], c0: dict[int, dict[str, float]],
             c1: dict[int, dict[str, float]], be2: dict[int, dict[str, float]]) -> Verdict:
    """③ RE — 회차 ①~⑥(대 C0·C1) + ⑦ 시드 평균 연수익 ≥ BE2(고정 반반) + 1%p. 일곱 다 → 채택 후보."""
    for name, table in (("RE", results), ("C0", c0), ("C1", c1), ("BE2", be2)):
        for s, m in table.items():
            require_keys(f"{name} 시드 {s}", m)
    block, base = kit.judge(results, c0, c1, label="RE 국면 가중(대 C0·C1): ")
    lines = _criteria(block)
    seeds = sorted(set(results) & set(be2))
    wins = sum(results[s]["ann"] > be2[s]["ann"] for s in seeds)
    seventh = _mean(results, "ann") >= _mean(be2, "ann") + RE_GATE_BE2
    lines.append(f"⑦ 대 BE2(반반) {_mean(results, 'ann') - _mean(be2, 'ann'):+.1%}p (≥ +{RE_GATE_BE2:.0%}p) "
                 f"{rkit.mark(seventh)} · 시드 {wins}/{len(seeds)} 가 BE2 보다 높다(기록)")
    if base.startswith("채택 —") and seventh:
        verdict = "채택 후보 — 국면 가중이 반반보다 낫다(①~⑦)"
    elif base.startswith("채택 —"):
        verdict = "①~⑥ 통과·⑦ 미통과 — 국면 가중의 몫 없음(BE2 로 충분), RE 기각"
    else:
        verdict = "기각"
    return Verdict("RE", verdict, lines)


def judge_df(results: dict[int, dict[str, float]], c1p: dict[int, dict[str, float]]) -> Verdict:
    """④ DF — 방어 기준 넷(C1′ = pct(C1) 대비, 시드 평균): MDD 1%p 얕게 · 연 ≥ −0.5%p · 두 국면 ≥ −1%p · 회전 ≤ ×1.2."""
    for name, table in (("DF", results), ("C1′", c1p)):
        for s, m in table.items():
            require_keys(f"{name} 시드 {s}", m)
    d = {k: _mean(results, k) - _mean(c1p, k) for k in ("ann", "box_ann", "rally_ann", "mdd")}
    c = [d["mdd"] >= DF_GATE_MDD,
         d["ann"] >= DF_GATE_ANN,
         d["box_ann"] >= DF_GATE_REGIME and d["rally_ann"] >= DF_GATE_REGIME,
         _mean(results, "turn") <= _mean(c1p, "turn") * DF_GATE_TURN]
    mk = rkit.mark
    lines = [f"DF 방어형 BF2(대 C1′): ①MDD {_mean(results, 'mdd'):.1%} 대 {_mean(c1p, 'mdd'):.1%} "
             f"({d['mdd']:+.1%}p, ≥ +{DF_GATE_MDD:.0%}p) {mk(c[0])} · ②연 {d['ann']:+.1%}p (≥ {DF_GATE_ANN:+.1%}p) {mk(c[1])} · "
             f"③국면 {d['box_ann']:+.1%}p/{d['rally_ann']:+.1%}p (≥ {DF_GATE_REGIME:+.0%}p) {mk(c[2])} · "
             f"④회전 {_mean(results, 'turn'):.1f} 대 {_mean(c1p, 'turn'):.1f} (≤ ×{DF_GATE_TURN}) {mk(c[3])}"]
    verdict = "채택 후보 — 방어(①~④)" if all(c) else "기각"
    return Verdict("DF", verdict, lines)


def bundle_line(protocol_hash: str) -> str:
    return (f"묶음 {ENTITY}(family {FAMILY}, 해시 {protocol_hash}) — {' · '.join(BUNDLE)} 한 번에 등록·판정 "
            "(다중검정: 넷 중 하나라도 우연히 통과할 확률 1−(1−p)^4 — 채택은 후보, 확정은 두 번째 금고 10/1~11/13)")


def premise_line(store: object, trial: str) -> str:
    """①③ 의 전제 — BE2 금고(10/13) 판정. research_trials 의 vault_judge 행을 읽기만 한다."""
    if trial not in ("TB", "RE"):
        return "전제: 없음(BE2 와 무관 — C1·BF2 만 쓴다)"
    try:
        now = datetime.now(UTC)  # invariant-allow: wallclock — 지금까지 적힌 금고 판정 전부
        rows = store.get("research_trials", as_of=now, lookback=3650)  # type: ignore[attr-defined]
        rows = rows[(rows["entity_id"] == "final-model-round-2026-10:BE2") & (rows["source"].astype(str) == "vault_judge")]
    except Exception as exc:  # pragma: no cover - 창고 경로
        return f"전제: BE2 금고 판정을 못 읽었다({exc}) — 판정 뒤 손으로 확인"
    if rows.empty:
        return "전제: BE2 금고 판정 대기(10/13) — BE2 가 금고에서 기각되면 이 판정은 '전제 소멸' 이다"
    last = str(rows.sort_values("observed_at").iloc[-1]["detail"]).split(" | ")[0]
    return premise_from(last)


def premise_from(vault_verdict: str) -> str:
    """BE2 금고 판정 → ①③ 의 전제. **"채택" 으로 시작할 때만 유지**다 — "①~⑤ 통과·⑥ 미통과"(모델 주장 기각)도 BE2 가 떨어진 것이다."""
    if vault_verdict.startswith("채택"):
        return f"전제 유지: BE2 금고 판정 '{vault_verdict}'"
    return f"전제 소멸: BE2 금고 판정 '{vault_verdict}' — 이 시행의 채택 후보는 무효다(기록만 남긴다)"


# --------------------------------------------------------------------------- 경로 (실자료·합성 공통)


@dataclass
class Inputs:
    """판정 입력 한 벌 — 실자료(`real_inputs`)와 합성(`synthetic_inputs`)이 같은 모양으로 채운다."""

    panel: pd.DataFrame                   # 키 · y5 · (미장) fund_raw·has_fund
    sessions: list[date]                  # 국장 축(블록)
    books: dict[str, kit.MarketBook]
    y: pd.DataFrame
    controls: dict[str, dict[int, pd.DataFrame]]    # C0·C1
    be1: dict[int, pd.DataFrame]
    bf1: dict[int, pd.DataFrame]
    probs: dict[str, pd.DataFrame]         # 시장 → HMM 확률(날짜 인덱스)
    index_raw: dict[str, dict[date, pd.Series]] | None = None


def pooled(pred: pd.DataFrame, books: dict[str, kit.MarketBook], y: pd.DataFrame,
           control: pd.DataFrame | None = None) -> dict[str, float]:
    """kit 규칙 포트(시장별 → 합동). 시장별 주요 값도 남긴다(한 시장이 다른 시장을 가리는 것을 본다)."""
    by, out = kit.evaluate_all(pred, books, y=y, control=control)
    for market, m in by.items():
        for k in ("ann", "mdd", "turn", "ic"):
            if k in m:
                out[f"{market}_{k}"] = m[k]
    return out


def run_tb(inp: Inputs, seeds: Sequence[int]) -> Verdict:
    """① TB — BE2·C0·C1 을 **같은 기울이기 장부**(D1b)에. B0 는 같은 날들의 지수 그대로."""
    dfl = _dfl()
    if inp.index_raw is None:
        raise ValueError("TB 는 지수 구성(index_raw)이 필요하다")
    prep = dfl.prepare(inp.panel[["entity_id", "session", "market", "y5",
                                  *[c for c in ("fund_raw", "has_fund") if c in inp.panel.columns]]],
                       [], {}, inp.sessions, inp.books, inp.index_raw)
    keys = [k for first, last in prep.blocks for k in dfl.judged_keys(prep.data, prep.axis, first, last)]
    idx_lines = dfl.require_index_coverage(prep.data, keys)   # rc 6
    res: dict[str, dict[int, dict[str, float]]] = {"TB": {}, "T0": {}, "T1": {}}
    days_of: dict[str, list[date]] = {}
    for s in seeds:
        be2 = be2_scores(inp.be1[s], inp.controls["C1"][s])
        same_sessions(be2, inp.controls["C0"][s], label=f"TB 시드 {s}")
        for arm, pred in (("TB", be2), ("T0", inp.controls["C0"][s]), ("T1", inp.controls["C1"][s])):
            res[arm][s] = dfl.tilt_book(pred, prep.data, inp.books, inp.y, lam=dfl.LAMBDA)[0]
        if not days_of:
            days_of = {m: sorted(set(be2.loc[be2["market"] == m, "session"])) for m in prep.data}
        del be2
    b0 = dfl.base_book(prep.data, days_of)
    v = judge_tb(res["TB"], res["T0"], res["T1"], b0)
    v.lines = idx_lines + v.lines
    v.tables = {**res, "B0": {0: b0}}
    return v


def run_re(inp: Inputs, seeds: Sequence[int]) -> Verdict:
    """③ RE — 국면 가중 점수를 kit 규칙 포트로. 대조 C0·C1(원 예측, 회차와 같다)·BE2(반반)."""
    res: dict[str, dict[int, dict[str, float]]] = {"RE": {}, "C0": {}, "C1": {}, "BE2": {}}
    weight_lines: list[str] = []
    for s in seeds:
        c1 = inp.controls["C1"][s]
        pcts = pct_frame({"BE1": inp.be1[s], "C1": c1, "BF2": bf2_scores(inp.bf1[s], c1)})
        attached = attach_probs(pcts, inp.probs)
        if not weight_lines:
            weight_lines = require_probs(attached)   # rc 8
        w = re_weights(attached.dropna(subset=["p0"]))
        re = weighted(pcts, w)
        be2 = be2_scores(inp.be1[s], c1)
        for name, pred in (("RE", re), ("BE2", be2)):
            same_sessions(pred, inp.controls["C0"][s], label=f"{name} 시드 {s}")
        res["RE"][s] = pooled(re, inp.books, inp.y, control=be2)
        res["BE2"][s] = pooled(be2, inp.books, inp.y)
        res["C0"][s] = pooled(inp.controls["C0"][s], inp.books, inp.y)
        res["C1"][s] = pooled(c1, inp.books, inp.y)
        if s == seeds[0]:
            for market, part in w.groupby("market"):
                weight_lines.append(f"기록: {market} 세션 가중 평균 BE1 {part['BE1'].mean():.2f} · C1 {part['C1'].mean():.2f} · "
                                    f"BF2 {part['BF2'].mean():.2f} (BE1 최소~최대 {part['BE1'].min():.2f}~{part['BE1'].max():.2f})")
        del pcts, re, be2
    v = judge_re(res["RE"], res["C0"], res["C1"], res["BE2"])
    v.tables = res
    v.lines = weight_lines + v.lines + [
        f"기록: 상위 24 겹침(RE 대 BE2) {_mean(res['RE'], 'overlap'):.0%} · β RE {_mean(res['RE'], 'beta'):.2f} · "
        f"BE2 {_mean(res['BE2'], 'beta'):.2f} · C1 {_mean(res['C1'], 'beta'):.2f}"]
    return v


def run_df(inp: Inputs, seeds: Sequence[int]) -> Verdict:
    """④ DF — 위기 세션만 BF2 와 반반. 대조 C1′ = pct(C1)(같은 변환). 원 예측 C1 은 기록만."""
    res: dict[str, dict[int, dict[str, float]]] = {"DF": {}, "C1′": {}, "C1": {}}
    fire_lines: list[str] = []
    for s in seeds:
        c1 = inp.controls["C1"][s]
        pcts = pct_frame({"C1": c1, "BF2": bf2_scores(inp.bf1[s], c1)})
        attached = attach_probs(pcts, inp.probs)
        if not fire_lines:
            fire_lines = require_probs(attached)     # rc 8
            for market, part in attached.groupby("market"):
                fire = part["p0"] > DF_THRESHOLD
                fire_lines.append(f"기록: {market} 발동 세션 {int(fire.sum())}/{len(part)} ({fire.mean():.0%})")
        df = weighted(pcts, df_weights(attached.dropna(subset=["p0"])))
        c1p = weighted(pcts, constant_weights(pcts, C1=1.0))
        same_sessions(df, inp.controls["C0"][s], label=f"DF 시드 {s}")
        res["DF"][s] = pooled(df, inp.books, inp.y, control=c1p)
        res["C1′"][s] = pooled(c1p, inp.books, inp.y)
        res["C1"][s] = pooled(c1, inp.books, inp.y)
        del pcts, df, c1p
    v = judge_df(res["DF"], res["C1′"])
    v.tables = res
    v.lines = fire_lines + v.lines + [
        f"기록: 원 예측 C1 연 {_mean(res['C1'], 'ann'):+.1%} · MDD {_mean(res['C1'], 'mdd'):.1%} (C1′ 와의 차는 EMA 척도 차) · "
        f"상위 24 겹침(DF 대 C1′) {_mean(res['DF'], 'overlap'):.0%} · β DF {_mean(res['DF'], 'beta'):.2f} · C1′ {_mean(res['C1′'], 'beta'):.2f}"]
    return v


RUNNERS = {"tb": run_tb, "re": run_re, "df": run_df}
NEEDS = {"tb": ("BE1",), "re": ("BE1", "BF1"), "df": ("BF1",)}


# --------------------------------------------------------------------------- 실자료


def require_registered(args: argparse.Namespace, what: str, protocol: Path = PROTOCOL) -> str:
    """사전등록 전에는 결과를 보지 않는다 — 플래그·문서·초안 머리줄(`> **초안`). D1 과 같은 규칙."""
    if not getattr(args, "i_registered", False):
        raise SystemExit(f"{what} 은 사전등록 뒤에만 돈다 — {protocol} 승인 후 --i-registered 를 명시하라")
    if not protocol.exists():
        raise SystemExit(f"등록 문서가 없다: {protocol}")
    if any(line.startswith("> **초안") for line in protocol.read_text().splitlines()[:5]):
        raise SystemExit(f"{protocol} 이 아직 초안이다(머리줄) — 사용자 승인·해시 고정 뒤에 돈다")
    return hashlib.sha256(protocol.read_bytes()).hexdigest()[:16]


def real_inputs(store: object, cmd: str, seeds: Sequence[int]) -> Inputs:
    """판정 입력 — 관문 순서: 패널 캐시(rc 3) → 대조군(rc 3) → 처리 예측(rc 3) → 장부 → (TB) 지수 비중 · (RE·DF) HMM."""
    panel, sessions = light_panel()
    tag = kit.control_tag(panel)
    controls = kit.require_controls(panel, seeds=tuple(seeds))
    models = NEEDS[cmd]
    require_inputs(tag, seeds, models)
    books = kit.market_books(store, sessions, panel=panel)  # type: ignore[arg-type]
    y = panel[["entity_id", "session", "y5"]]
    be1 = {s: read_be1(s) for s in seeds} if "BE1" in models else {}
    bf1 = {s: read_bf1(s, tag) for s in seeds} if "BF1" in models else {}
    probs: dict[str, pd.DataFrame] = {}
    index_raw = None
    if cmd == "tb":
        index_raw = _dfl().index_weights(store, books, {m: sorted(set(panel.loc[panel["market"] == m, "session"]))
                                                        for m in books})
    else:
        probs = load_probs(store)
    print(f"입력: 패널 {len(panel):,}행 · 국장 세션 {len(sessions)} · 시드 {len(seeds)} · 모델 {','.join(models)} · "
          f"RSS {kit.rss_mb():.0f}MB", flush=True)
    return Inputs(panel, sessions, books, y, controls, be1, bf1, probs, index_raw)


def cmd_judge(args: argparse.Namespace) -> int:
    hashed = require_registered(args, f"판정({args.cmd})")
    from quant_rl_trading.store import Store

    seeds = tuple(SEEDS[: args.seeds])
    store = Store(root=Path(args.root))
    print(f"=== 시행 {args.cmd.upper()} — {PROTOCOL} (해시 {hashed}) · 시드 {len(seeds)} ===", flush=True)
    inp = real_inputs(store, args.cmd, seeds)
    v = RUNNERS[args.cmd](inp, seeds)
    lines = [*v.lines, premise_line(store, v.trial), bundle_line(hashed),
             f"기록: 최대 RSS {kit.rss_mb():.0f}MB", f"판정: {v.verdict}"]
    print("\n" + "\n".join(lines), flush=True)
    if args.save:
        rkit.record(store, entity=f"{ENTITY}:{v.trial}", source="trial_next_four", family=FAMILY, digest=hashed,
                    verdict=v.verdict, lines=[bundle_line(hashed), premise_line(store, v.trial), *v.lines[-4:]],
                    market="KR,US", run_tag=v.trial)
        print(f"research_trials 기록: {FAMILY}/{ENTITY}:{v.trial} · protocol {hashed}", flush=True)
    return 0


def cmd_precheck(args: argparse.Namespace) -> int:
    """등록 전 점검 — 입력 파일·행 집합·HMM 커버리지·규칙 발동률·RE 가중 분포. **수익·IC 를 보지 않는다.**"""
    from quant_rl_trading.store import Store

    store = Store(root=Path(args.root))
    panel, sessions = light_panel()
    tag = kit.control_tag(panel)
    for model in ("BE1", "BF1"):
        have = [s for s in SEEDS if (be1_path(s) if model == "BE1" else bf1_path(s, tag)).exists()]
        print(f"{model}: 시드 {have} 있음", flush=True)
    keys = panel[["session", "market"]].drop_duplicates()
    first = kit.block_span(sessions, *kit.blocks(sessions)[0])[0]
    keys = keys[keys["session"] >= first]
    attached = attach_probs(keys, load_probs(store))
    try:
        print("\n".join(require_probs(attached)), flush=True)
    except SystemExit:
        return HMM_EXIT
    for market, part in attached.groupby("market"):
        w = re_weights(part.dropna(subset=["p0"]))
        print(f"{market}: DF 발동(p0 > {DF_THRESHOLD}) {(part['p0'] > DF_THRESHOLD).mean():.0%} · RE 가중 평균 "
              f"BE1 {w['BE1'].mean():.2f} · C1 {w['C1'].mean():.2f} · BF2 {w['BF2'].mean():.2f}", flush=True)
    if args.index:
        dfl = _dfl()
        books = kit.market_books(store, sessions, panel=panel)
        index_raw = dfl.index_weights(store, books, {m: sorted(set(panel.loc[panel["market"] == m, "session"]))
                                                     for m in books})
        prep = dfl.prepare(panel, [], {}, sessions, books, index_raw)
        keys2 = [k for f, la in prep.blocks for k in dfl.judged_keys(prep.data, prep.axis, f, la)]
        try:
            print("\n".join(dfl.require_index_coverage(prep.data, keys2)), flush=True)
        except SystemExit:
            return INDEX_EXIT
    print(f"판정 첫 세션 {first} · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


# --------------------------------------------------------------------------- 합성


def synthetic_inputs(seed: int = 0, n_sessions: int = 360, n_entities: int = 80,
                     seeds: Sequence[int] = (0, 1)) -> Inputs:
    """합성 두 시장 — D1 의 합성 자료(`trial_final_dfl.synthetic`) 위에 BE1·BF1·C0·C1 예측과 HMM 확률을 만든다. 수치는 뜻이 없다.

    HMM 확률은 Dirichlet 잡음(계산이 무거운 EM 을 스모크에서 돌리지 않는다) — HMM 자체의 as-of 성질은 테스트가 따로 본다.
    """
    dfl = _dfl()
    syn = dfl.synthetic(seed, n_sessions=n_sessions, n_entities=n_entities)
    bl = kit.blocks(syn.sessions)
    rows = pd.concat([kit.block_rows(syn.panel, syn.sessions, f, la) for f, la in bl])
    base = rows[KEYS].reset_index(drop=True)
    f0, f2, f4 = (rows[c].to_numpy() for c in ("f0", "f2", "f4"))
    controls: dict[str, dict[int, pd.DataFrame]] = {"C0": {}, "C1": {}}
    be1, bf1 = {}, {}
    for s in seeds:
        rng = np.random.default_rng(100 + s)
        controls["C0"][s] = base.assign(pred=0.1 * (f0 + rng.normal(0, 1.0, len(rows))))
        controls["C1"][s] = base.assign(pred=0.1 * (f0 + 0.3 * f2 + rng.normal(0, 0.8, len(rows))))
        be1[s] = base.assign(pred=f0 + 0.5 * f4 + rng.normal(0, 1.0, len(rows)))
        bf1[s] = base.assign(pred=f0 + rng.normal(0, 1.5, len(rows)))
    rng = np.random.default_rng(seed + 7)
    probs = {}
    for market in MARKETS:
        days = sorted(set(syn.panel.loc[syn.panel["market"] == market, "session"]))
        p = rng.dirichlet([1.0, 1.0, 1.0], size=len(days))
        probs[market] = pd.DataFrame(p, index=days, columns=[f"p{k}" for k in range(HMM_STATES)])
    panel = syn.panel[[*KEYS, "y5", "fund_raw", "has_fund"]]
    return Inputs(panel, syn.sessions, syn.books, panel[["entity_id", "session", "y5"]], controls, be1, bf1,
                  probs, syn.index_raw)


def cmd_smoke(args: argparse.Namespace) -> int:
    """합성 자료로 세 경로를 끝까지 — 판정 줄 모양만 확인한다(수치는 찍지 않는다)."""
    seeds = (0, 1)
    inp = synthetic_inputs(seeds=seeds)
    for cmd in ("tb", "re", "df"):
        v = RUNNERS[cmd](inp, seeds)
        print(f"스모크 {cmd.upper()}: 판정 줄 {len(v.lines)}개 · 판정 '{v.verdict.split(' — ')[0]}' 모양 확인(수치는 뜻 없음)",
              flush=True)
    print(f"스모크 끝 · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("tb", "re", "df"):
        p = sub.add_parser(name)
        p.add_argument("--i-registered", action="store_true", help="등록 문서 승인·해시 고정 뒤에만")
        p.add_argument("--save", action="store_true", help="research_trials 에 1행(시행 예산 소진)")
        p.add_argument("--seeds", type=int, default=len(SEEDS))
        p.add_argument("--root", default="data")
    p = sub.add_parser("precheck")
    p.add_argument("--index", action="store_true", help="TB 지수 구성 커버리지까지(장부·시총을 읽는다 — 무겁다)")
    p.add_argument("--root", default="data")
    sub.add_parser("smoke")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in RUNNERS:
        return cmd_judge(args)
    if args.cmd == "precheck":
        return cmd_precheck(args)
    return cmd_smoke(args)


if __name__ == "__main__":
    raise SystemExit(main())
