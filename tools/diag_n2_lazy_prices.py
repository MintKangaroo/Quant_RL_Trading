"""진단 — 새 정보원 후보 N2 "정기보고서 문구 변화(Lazy Prices)" 의 피처 + 탐색 구간 IC(docs/diag/n2-lazy-prices-2026-10-11.md).

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
배경: docs/diag/source-scan-2026-10-10.md(N2) · docs/diag/n2-n3-feasibility-2026-10-11.md(수집 비용·섹션 추출).
문헌: Cohen·Malloy·Nguyen 2020(JF) "Lazy Prices" — 직전 동종 보고서 대비 문구를 많이 바꾼 회사가 이후 부진(패자).

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 nice -n 10 \
        .venv/bin/python tools/diag_n2_lazy_prices.py            # 피처 캐시가 있으면 재사용
    ... tools/diag_n2_lazy_prices.py --rebuild                  # 피처를 다시 계산(추가 수집이 늘었을 때)
    ... tools/diag_n2_lazy_prices.py --features-only            # 피처·커버리지만(수익 안 봄)

**재료 둘** — 같은 섹션 절단기(`sections`)를 쓴다:
1. 창고 `documents` 의 정기보고서 원문(`raw_path`, TX 백필·정규 수집기가 받은 평문 gzip, 2021-08-17~).
2. 추가 수집분(`tools/collect_n2_periodic.py`, 2016-01~2021-08-16) — **창고 밖** `data/_diag/n2_periodic/`
   (섹션 gzip JSON + 색인 parquet). documents 시작일을 당기면 등록된 시행(TX 등)의 연구 캐시가 바뀌어서 따로 둔다.

**피처** — 같은 회사의 직전 동종 보고서(사업↔직전 사업, 반기↔전년 반기, 분기↔전년 동분기 — 제목의 (YYYY.MM) 에서 연도만
하나 뺀 것)와의 유사도. 원본만 쓴다([기재정정]·[첨부정정] 등 정정류 제외, 같은 회사·종류·기간에 원본이 둘이면 먼저 낸 것).
섹션: 전체(아래 셋을 이은 것) · biz(II. 사업의 내용) · risk(그 밖에 투자자 보호를 위하여 필요한 사항) · mdna(이사의 경영진단 —
사업보고서끼리만, 분기·반기는 서식상 "기재하지 않음"). 척도: 단어빈도 코사인 · 자카드(한글/영문 2자 이상 토큰).
값이 **클수록 덜 바뀜** — 문헌 부호는 + (유사도 높음 = 승자, 변화 큼 = 패자).

**시각** — 관측 = 접수일 18:00 KST(목록 백필 규약 `backfill.dart_publication_hour_kst` 와 같다). 피처는 접수일 **다음 세션**
결정일부터 쓴다(하루 늦춤). 라벨은 결정일 t 에서 c(t+1+h)/c(t+1)−1 — 진입이 하루 더 늦다. 원문을 **받은** 시각(정정본 observed_at,
2026-09~10)은 쓰지 않는다 — 문구는 접수 때 확정이고, 이것은 연구용 되감기다(시행이면 수집 지연을 따로 규정해야 한다).
신호는 연 1~4회만 바뀌므로 다음 보고서(어느 종류든)가 올 때까지 값을 유지한다(전체·biz·risk 최대 130세션, mdna 는 다음
사업보고서까지 최대 260세션). 다음 보고서에 짝이 없으면 그날부터 결측. 유지 기간(staleness)을 같이 잰다.

**금고 규칙** — 수익·라벨은 END(2026-06-30) 까지만. 시세는 ``as_of=END``·``until=END`` 로 읽고, 라벨 마지막 세션이 END 를
넘으면 멈춘다(``guard``). data/_past_vault 는 열지 않는다. 문서 색인은 원문 경로를 찾으려고 지금 시각으로 읽지만 접수일이
END 이후인 보고서는 버린다.

**측정** — 5·20·60세션 지평 IC(NW t, lag=max(4,h)) · 부분 IC(rev5·mom60·lowvol·liq·로그시총·베타, 피처와 라벨 **둘 다**
잔차화 — tools/diag_n1_issuance.partial_ic) · 국면(박스 ~2024-12 / 급등 2025-01~) · 십분위(동일가중 대비) · 유지 기간별 IC ·
랭커 잔차 IC(5세션) · 공시 직후 60세션 이벤트 창(분기별 오분위, 변화 최대 Q1 대 최소 Q5).

`&cr;` — DART 원문 고유 개체(줄바꿈)를 `dart_documents.to_text` 가 못 풀어 저장 평문에 문자열로 남는다(2026-10-11 확인).
수집기는 고치지 않았다 — 고치면 그날부터 받는 원문과 이미 받은 원문의 평문이 달라져 TX 임베딩 입력이 갈린다. 읽을 때 `clean` 으로 푼다.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from collections import Counter
from datetime import UTC, date, datetime
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
END_DAY = date(2026, 6, 30)
PANEL_START = date(2020, 9, 1)
REGIMES = (("박스", date(2021, 1, 1), date(2024, 12, 31)), ("급등", date(2025, 1, 1), date(2026, 6, 30)))
HORIZONS = (5, 20, 60)
#: 우주 — 20일 평균 거래대금 10억 이상 · 이력 120세션(베타 창). N1 은 252세션이지만 시세가 2021-08 부터라 첫해가 잘린다.
MIN_VALUE = 1e9
MIN_HISTORY = 120
HOLD = {"all": 130, "biz": 130, "risk": 130, "mdna": 260}
SECTIONS = ("all", "biz", "risk", "mdna")
MEASURES = ("cos", "jac")
#: 섹션으로 치는 최소 글자 수. 이보다 짧으면 "없음"(목차 줄·"기재하지 않음" 한 줄).
MIN_CHARS = 300
MAX_CHARS = 400_000

EXTRA_ROOT = Path("data/_diag/n2_periodic")
FEATURES = EXTRA_ROOT / "features.parquet"  # invariant-allow: data-access — 이 진단의 피처 캐시(창고 밖)
OUT_CSV = Path("logs/diag-n2-lazy-prices-20261011.csv")

# --------------------------------------------------------------------------- 섹션 절단(수집기와 공유)

_CR = re.compile(r"&cr;", re.I)
#: 상위 제목 줄 — "II. 사업의 내용". 재무제표 안의 "I. 유동자산" 같은 줄도 걸리지만 이름 패턴이 걸러 준다.
_HEAD = re.compile(r"^[ \t]*([IVX]{1,5})[ \t]*\.[ \t]*(\S[^\n]{0,80})$", re.M)
#: 섹션을 끝내는 줄 — 확인서(【 전문가의 확인 】)·첨부 감사보고서 머리.
_STOP = re.compile(r"^[ \t]*(【|(독립된[ \t]*)?감사인의[ \t]*감사보고서|외부감사[ \t]*실시내용)", re.M)
PATTERNS = {"biz": r"^사업의내용", "mdna": r"^(이사의)?경영진단", "risk": r"^(그밖에투자자|투자자보호|기타필요한사항|기타투자자)"}
_ROMAN = {"I": 1, "V": 5, "X": 10}
_TOK = re.compile(r"[가-힣A-Za-z]{2,}")


def clean(text: str) -> str:
    """`&cr;` → 줄바꿈. 제목 줄이 `&cr;` 뒤에 붙어 있으면 줄 정규식이 못 잡는다."""
    return _CR.sub("\n", text)


def roman(s: str) -> int:
    total = 0
    for a, b in zip(s, s[1:] + " ", strict=True):
        v = _ROMAN.get(a, 0)
        total += -v if _ROMAN.get(b, 0) > v else v
    return total


def sections(text: str) -> dict[str, str]:
    """평문(`dart_documents.extract` 형식)에서 biz·risk·mdna 를 자른다. 없으면 빈 문자열.

    시작 = 이름 패턴에 맞는 상위 제목 줄. 끝 = 그 뒤 첫 상위 제목 중 로마 숫자가 **더 큰** 것, 또는 확인서·감사보고서 머리
    (재무제표 안의 "I. 유동자산" 은 숫자가 작아 끝으로 안 친다). 목차처럼 같은 제목이 여러 번이면 **가장 긴** 구간.
    """
    text = clean(text)
    heads = [(m.start(), roman(m.group(1)), re.sub(r"\s+", "", m.group(2))) for m in _HEAD.finditer(text)]
    stops = [m.start() for m in _STOP.finditer(text)]
    out: dict[str, str] = {}
    for name, pat in PATTERNS.items():
        best = ""
        for i, (pos, num, title) in enumerate(heads):
            if not re.match(pat, title):
                continue
            end = len(text)
            for p2, n2, _ in heads[i + 1:]:
                if n2 > num:
                    end = p2
                    break
            nxt = [s for s in stops if s > pos]
            if nxt:
                end = min(end, nxt[0])
            seg = text[pos:min(end, pos + MAX_CHARS)]
            if len(seg) > len(best):
                best = seg
        out[name] = best if len(best) >= MIN_CHARS else ""
    return out


def similarity(a: str, b: str) -> tuple[float, float]:
    """(단어빈도 코사인, 자카드). 둘 중 하나가 비면 NaN."""
    ta, tb = _TOK.findall(a), _TOK.findall(b)
    if not ta or not tb:
        return float("nan"), float("nan")
    ca, cb = Counter(ta), Counter(tb)
    num = sum(ca[w] * cb[w] for w in ca.keys() & cb.keys())
    den = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    sa, sb = set(ca), set(cb)
    return (num / den if den else float("nan")), len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------------- 보고서 색인

_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
_TITLE = re.compile(r"^(분기|반기|사업)보고서\((\d{4})\.(\d{2})\)")
KINDS = {"사업": "A", "반기": "H", "분기": "Q"}


def parse_title(title: str) -> tuple[str, int, int, bool] | None:
    """제목 → (종류 A/H/Q, 연, 월, 정정류 여부). 정기보고서가 아니면 None."""
    amended = bool(_PREFIX.match(str(title or "")))
    m = _TITLE.match(re.sub(r"\s+", "", _PREFIX.sub("", str(title or ""))))
    if not m:
        return None
    return KINDS[m.group(1)], int(m.group(2)), int(m.group(3)), amended


def index_main() -> pd.DataFrame:
    """창고 documents 의 정기보고서 원본(원문 받은 것). 원문 경로를 찾으려고 지금 시각으로 읽는다(접수일 > END 는 버림)."""
    from quant_rl_trading.replay.clock import LiveClock
    from quant_rl_trading.store import Store

    store = Store(root=Path("data"))
    now = LiveClock().now()
    d = store.get("documents", as_of=now, lookback=(now.date() - date(2021, 1, 1)).days, market="KR",
                  columns=["entity_id", "valid_from", "doc_id", "title", "raw_path", "doc_type"])
    d = d[(d["doc_type"] == "earnings") & d["entity_id"].astype(str).str.match(r"^KR:\d{6}$")]
    return _shape(d.rename(columns={"doc_id": "rcept_no", "raw_path": "path"}), src="docs")


def index_extra(root: Path = EXTRA_ROOT) -> pd.DataFrame:
    parts = sorted((root / "index").glob("*.parquet"))  # invariant-allow: data-access — 진단 수집분 색인(창고 밖)
    if not parts:
        return pd.DataFrame()
    x = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)  # invariant-allow: data-access — 진단 수집분(창고 밖)
    x = x[x["status"] == "ok"].drop_duplicates("rcept_no", keep="last")
    x = x.rename(columns={"report_nm": "title"})
    x["entity_id"] = "KR:" + x["stock_code"].astype(str)
    x["valid_from"] = pd.to_datetime(x["rcept_dt"]).dt.tz_localize("Asia/Seoul")
    return _shape(x, src="extra")


def _shape(d: pd.DataFrame, *, src: str) -> pd.DataFrame:
    parsed = d["title"].map(parse_title)
    d = d[parsed.notna()].copy()
    parsed = parsed[parsed.notna()]
    d["kind"] = [p[0] for p in parsed]
    d["fy"] = [p[1] for p in parsed]
    d["fm"] = [p[2] for p in parsed]
    d["amended"] = [p[3] for p in parsed]
    d["day"] = pd.to_datetime(d["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    d["path"] = d["path"].fillna("").astype(str)
    d = d[~d["amended"] & (d["path"].str.len() > 1) & (d["day"] <= END_DAY)]
    d["src"] = src
    return d[["entity_id", "rcept_no", "day", "kind", "fy", "fm", "path", "src"]]


def build_index() -> pd.DataFrame:
    a, b = index_main(), index_extra()
    x = pd.concat([a, b], ignore_index=True) if len(b) else a
    x["rcept_no"] = x["rcept_no"].astype(str)
    x = x.drop_duplicates("rcept_no", keep="first")  # 겹치는 접수번호(2021-08-16 근처)는 창고본 우선
    # 같은 회사·종류·기간에 원본이 둘 이상이면 먼저 낸 것
    return x.sort_values(["entity_id", "kind", "fy", "fm", "day"]).drop_duplicates(["entity_id", "kind", "fy", "fm"], keep="first")


# --------------------------------------------------------------------------- 피처(회사 단위 병렬)


def _read(path: str, src: str) -> dict[str, str]:
    if src == "extra":
        with gzip.open(path, "rt", encoding="utf-8") as h:  # invariant-allow: data-access — 진단 수집분(창고 밖 원문 조각)
            sec = json.load(h)
    else:
        from quant_rl_trading.collectors.dart_documents import read_text
        sec = sections(read_text(Path(path)))
    sec = {k: clean(sec.get(k, "")) for k in ("biz", "risk", "mdna")}
    sec["all"] = "\n".join(sec[k] for k in ("biz", "risk", "mdna"))
    return sec


def _entity_pairs(rows: list[dict]) -> list[dict]:
    """한 회사의 보고서들 → 짝 피처. 각 원문은 한 번만 읽는다."""
    cache: dict[str, dict[str, str]] = {}
    by_key = {(r["kind"], r["fy"], r["fm"]): r for r in rows}
    out = []
    for r in rows:
        rec = {"entity_id": r["entity_id"], "rcept_no": r["rcept_no"], "day": r["day"], "kind": r["kind"],
               "fy": r["fy"], "fm": r["fm"], "src": r["src"]}
        try:
            cur = cache.setdefault(r["rcept_no"], _read(r["path"], r["src"]))
        except (OSError, ValueError, EOFError) as error:
            rec["error"] = f"{type(error).__name__}"[:80]
            out.append(rec)
            continue
        for k in ("biz", "risk", "mdna"):
            rec[f"len_{k}"] = len(cur[k])
        prev = by_key.get((r["kind"], r["fy"] - 1, r["fm"]))
        if prev is not None:
            try:
                old = cache.setdefault(prev["rcept_no"], _read(prev["path"], prev["src"]))
            except (OSError, ValueError, EOFError):
                old = None
            if old is not None:
                rec["prev_rcept_no"] = prev["rcept_no"]
                for s in SECTIONS:
                    if s == "mdna" and r["kind"] != "A":
                        continue
                    if s != "all" and (not cur[s] or not old[s]):
                        continue
                    c, j = similarity(cur[s], old[s])
                    rec[f"cos_{s}"], rec[f"jac_{s}"] = c, j
        out.append(rec)
        # 메모리 — 회사 안에서 이 보고서가 다음 해의 '직전' 으로 다시 쓰이므로 1년 전 것만 지운다
        if prev is not None:
            cache.pop(prev["rcept_no"], None)
    return out


def compute_features(index: pd.DataFrame, workers: int = 2) -> pd.DataFrame:
    groups = [g.sort_values("day").to_dict(orient="records") for _, g in index.groupby("entity_id")]
    rows: list[dict] = []
    with Pool(workers, maxtasksperchild=200) as pool:
        for k, part in enumerate(pool.imap_unordered(_entity_pairs, groups, chunksize=4)):
            rows.extend(part)
            if (k + 1) % 300 == 0:
                print(f"  피처 {k + 1}/{len(groups)} 회사", flush=True)
    return pd.DataFrame(rows)


def coverage(feat: pd.DataFrame) -> pd.DataFrame:
    """종류·접수연도별 — 섹션 추출 성공률(길이 ≥ MIN_CHARS)과 짝 피처 커버리지."""
    f = feat.copy()
    f["year"] = pd.to_datetime(f["day"]).dt.year
    g = f.groupby(["kind", "year"])
    out = pd.DataFrame({
        "보고서": g.size(),
        "biz추출": g["len_biz"].apply(lambda s: (s > 0).mean()),
        "risk추출": g["len_risk"].apply(lambda s: (s > 0).mean()),
        "mdna추출": g["len_mdna"].apply(lambda s: (s > 0).mean()),
        "짝있음": g["prev_rcept_no"].apply(lambda s: s.notna().mean()) if "prev_rcept_no" in f else 0.0,
        "cos_all": g["cos_all"].apply(lambda s: s.notna().mean()),
        "cos_risk": g["cos_risk"].apply(lambda s: s.notna().mean()),
        "cos_mdna": g["cos_mdna"].apply(lambda s: s.notna().mean()),
    })
    return out


# --------------------------------------------------------------------------- 패널·측정


def guard(days: list[date]) -> None:
    if days and max(days) > END_DAY:
        raise SystemExit(f"금고 침범: {max(days)} > {END_DAY} — 멈춘다")


def panel(feat: pd.DataFrame, col: str, sessions: np.ndarray, cols: pd.Index, hold: int, *, annual_only: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """보고서 접수일 **다음 세션**에 값을 놓고 다음 보고서까지 유지. (값, 유지 세션 수)."""
    f = feat if not annual_only else feat[feat["kind"] == "A"]
    f = f[f["entity_id"].isin(cols)].copy()
    pos = np.searchsorted(sessions, f["day"].to_numpy(), side="right")  # 접수일 다음 세션(관측 18:00 → 하루 늦춤)
    f = f[pos < len(sessions)]
    f["s"] = sessions[pos[pos < len(sessions)]]
    f = f.sort_values(["entity_id", "s", "day"]).drop_duplicates(["entity_id", "s"], keep="last")
    SENT = -999.0  # 짝 없는 새 보고서 = 그날부터 결측(옛 값을 끌고 가지 않는다)
    v = f.pivot_table(index="s", columns="entity_id", values=col, aggfunc="last", dropna=False)
    v = v.reindex(index=sessions, columns=cols)
    mark = f.assign(one=1.0).pivot_table(index="s", columns="entity_id", values="one", aggfunc="last").reindex(index=sessions, columns=cols)
    v = v.where(mark.isna() | v.notna(), SENT)
    v = v.ffill(limit=hold)
    v = v.where(v != SENT)
    last = pd.DataFrame(np.where(mark.notna(), np.arange(len(sessions))[:, None], np.nan), index=sessions, columns=cols).ffill()
    stale = pd.DataFrame(np.repeat(np.arange(len(sessions))[:, None], len(cols), axis=1), index=sessions, columns=cols) - last
    return v, stale.where(v.notna())


def nw(x: pd.Series, h: int) -> float:
    from quant_rl_trading.analysts import ic as ic_module
    return float(ic_module.newey_west_t(x.dropna(), lag=max(4, h)))


def event_windows(F: dict[str, pd.DataFrame], feat: pd.DataFrame, c: pd.DataFrame, uni: pd.DataFrame,
                  sessions: np.ndarray, names: list[str], start: date) -> pd.DataFrame:
    """공시 다음 세션 t0 에서 c(t0+61)/c(t0+1)−1 − 같은 창 우주 동일가중. 접수 분기별 오분위(Q1 = 유사도 최저 = 변화 최대)."""
    h = 60
    r = c.shift(-(1 + h)) / c.shift(-1) - 1.0
    r = r.where(uni)
    ex = r.sub(r.mean(axis=1), axis=0)
    pos = np.searchsorted(sessions, feat["day"].to_numpy(), side="right")
    e = feat[pos < len(sessions)].copy()
    e["s"] = sessions[pos[pos < len(sessions)]]
    e = e[(e["s"] >= start) & e["entity_id"].isin(c.columns)]
    stack = ex.stack()
    stack.index.names = ["s", "entity_id"]
    e = e.merge(stack.rename("ex").reset_index(), on=["s", "entity_id"], how="inner")
    e["q"] = pd.PeriodIndex(pd.to_datetime(e["s"]), freq="Q").astype(str)
    rows = []
    for name in names:
        x = e.dropna(subset=[name, "ex"]).copy()
        x["bin"] = x.groupby("q")[name].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if len(s) >= 25 else np.nan)
        x = x.dropna(subset=["bin"])
        byq = x.groupby(["q", "bin"])["ex"].mean().unstack()
        spread = (byq[0] - byq[4]).dropna()
        row = {"name": name, "events": len(x), "quarters": len(spread)}
        for b in range(5):
            row[f"Q{b + 1}"] = float(x.loc[x["bin"] == b, "ex"].mean())
        row["Q1-Q5"] = float(spread.mean())
        row["t(분기)"] = float(spread.mean() / (spread.std(ddof=1) / math.sqrt(len(spread)))) if len(spread) > 2 else float("nan")
        rows.append(row)
    return pd.DataFrame(rows).set_index("name")


def measure(feat: pd.DataFrame, *, ranker: bool) -> None:
    from quant_rl_trading.store import Store
    from tools import diag_n1_issuance as N1
    from tools import diag_ta_features as T

    store = Store(root=Path("data"))
    d = T.load(store, END, PANEL_START)
    c, v = d["close"], d["value"]
    guard(list(c.index))
    idx, cols = c.index, c.columns
    sessions = np.array(list(idx))
    uni = (T.rmean(v, 20) >= MIN_VALUE) & (c.notna().rolling(MIN_HISTORY, min_periods=1).sum() >= MIN_HISTORY)

    # 통제(N1 build 와 같은 정의, 하루 늦춤 없음 = 결정일 종가까지)
    s = N1.load_shares(store, idx, cols)
    r = c / c.shift(1) - 1.0
    mcap = d["close_raw"] * s.ffill(limit=5)
    mkt = r.clip(-0.5, 1.0).mean(axis=1)
    ctl = {"_rev5": c / c.shift(5) - 1.0, "_mom60": c / c.shift(60) - 1.0, "_lowvol": -r.ewm(span=60, min_periods=60).std(),
           "_liq": np.log(T.rmean(v, 20)), "_logcap": np.log(mcap.where(mcap > 0)),
           "_beta": r.rolling(120, min_periods=100).cov(mkt).div(mkt.rolling(120, min_periods=100).var(), axis=0)}
    ctl = {k: x.replace([np.inf, -np.inf], np.nan) for k, x in ctl.items()}

    names = [f"{m}_{sct}" for sct in SECTIONS for m in MEASURES]
    raw, stale = {}, {}
    for n in names:
        sct = n.split("_", 1)[1]
        raw[n], stale[n] = panel(feat, n, sessions, cols, HOLD[sct], annual_only=(sct == "mdna"))

    first = min(x.notna().any(axis=1).idxmax() for x in raw.values())
    cover = (raw["cos_all"].where(uni).notna().sum(axis=1) / uni.sum(axis=1)).fillna(0)
    start = next((dd for dd in idx if dd >= first and cover.loc[dd] >= 0.5), first)
    print(f"\n피처 첫 세션 {first} · 우주 커버리지 50% 첫 결정일 {start}")

    rank = N1.ranker_panel(idx, cols) if ranker else None
    rows, decs, stab = [], {}, []
    for h in HORIZONS:
        label = c.shift(-(1 + h)) / c.shift(-1) - 1.0
        if h == 5:
            label = label.where(label.abs() <= 0.5)
        days = [x for x in idx if x >= start]
        label = label.where(uni).loc[days]
        label = label[label.notna().sum(axis=1) >= 100]
        days = list(label.index)
        last_label = sessions[list(sessions).index(days[-1]) + 1 + h]
        guard([last_label])
        print(f"h={h}: 결정일 {days[0]}~{days[-1]} {len(days)}일 · 라벨 마지막 세션 {last_label} · 평균 우주 {label.notna().sum(axis=1).mean():.0f}")
        C = [x.where(uni).loc[days] for x in ctl.values()]
        for n in names:
            x = raw[n].where(uni).loc[days]
            ic = T.rowwise_ic(x, label)
            row = {"h": h, "name": n, "일수": len(ic), "커버": float((x.notna().sum(axis=1) / label.notna().sum(axis=1)).mean()),
                   "유지중앙": float(stale[n].where(uni).loc[days].stack().median()),
                   "IC": float(ic.mean()), "t": nw(ic, h)}
            for rn, a, b in REGIMES:
                part = ic[(ic.index >= a) & (ic.index <= b)]
                row[f"IC_{rn}"], row[f"t_{rn}"] = float(part.mean()), nw(part, h)
            pic = N1.partial_ic(x, label, C)
            row["편IC"], row["편t"] = float(pic.mean()), nw(pic, h)
            for rn, a, b in REGIMES:
                part = pic[(pic.index >= a) & (pic.index <= b)]
                row[f"편IC_{rn}"], row[f"편t_{rn}"] = float(part.mean()), nw(part, h)
            if rank is not None and h == 5:
                ric = N1.partial_ic(x, label, [rank.where(uni).loc[days]])
                row["랭커잔차IC"], row["랭커잔차t"] = float(ric.mean()), nw(ric, h)
            dec = N1.deciles(x, label)
            decs[(h, n)] = dec
            row["D10-D1"] = dec[9] - dec[0]
            rows.append(row)
            if h == 5:
                st = stale[n].where(uni).loc[days]
                for lo, hi in ((0, 20), (20, 60), (60, 400)):
                    sub = T.rowwise_ic(x.where((st >= lo) & (st < hi)), label)
                    stab.append({"name": n, "유지": f"{lo}~{hi}", "일수": len(sub), "IC": float(sub.mean()) if len(sub) else np.nan,
                                 "t": nw(sub, h) if len(sub) > 10 else np.nan})
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    for h in HORIZONS:
        print(f"\n=== IC h={h} ===")
        print(out[out["h"] == h].drop(columns="h").set_index("name").round(4).to_string())
    print("\n=== 십분위별 초과(동일가중 대비, %) — D1 = 유사도 최저(변화 최대), D10 = 최고 ===")
    dd = pd.DataFrame({f"h{h}:{n}": v for (h, n), v in decs.items()}, index=[f"D{k}" for k in range(1, 11)]).T * 100
    print(dd.round(3).to_string())
    print("\n=== 유지 기간별 IC (h=5) ===")
    sb = pd.DataFrame(stab)
    print(sb.round(4).to_string(index=False))
    print("\n=== 공시 직후 60세션 이벤트 창 — 접수 분기별 오분위, 동일가중 대비 초과(%) ===")
    ev_start = start
    ev = event_windows(raw, feat[feat["day"] >= date(2021, 1, 1)], c, uni, sessions, names, ev_start)
    print((ev[[f"Q{k}" for k in range(1, 6)] + ["Q1-Q5"]] * 100).join(ev[["events", "quarters", "t(분기)"]]).round(3).to_string())
    # 피처끼리 · 스타일과의 겹침(일별 순위상관 평균, h=5 결정일)
    days = [x for x in idx if x >= start and x <= END_DAY]
    sty = {}
    for n in ("cos_all", "cos_risk", "cos_mdna", "jac_all"):
        x = raw[n].where(uni).loc[days]
        sty[n] = {k: float(T.rowwise_ic(x, y.where(uni).loc[days]).mean()) for k, y in {**ctl, "cos_all": raw["cos_all"], "jac_all": raw["jac_all"]}.items()}
    print("\n=== 스타일 겹침(일별 횡단면 순위상관 평균) ===")
    print(pd.DataFrame(sty).T.round(3).to_string())
    OUT_CSV.parent.mkdir(exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    dd.to_csv(OUT_CSV.with_name("diag-n2-lazy-prices-deciles-20261011.csv"))
    sb.to_csv(OUT_CSV.with_name("diag-n2-lazy-prices-stale-20261011.csv"), index=False)
    ev.to_csv(OUT_CSV.with_name("diag-n2-lazy-prices-events-20261011.csv"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="피처 캐시를 다시 계산")
    ap.add_argument("--features-only", action="store_true", help="피처·커버리지만(수익 안 봄)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-ranker", action="store_true")
    ap.add_argument("--limit-entities", type=int, default=0, help="시험용 — 회사 수 제한")
    args = ap.parse_args(argv)

    index = build_index()
    if args.limit_entities:
        keep = sorted(index["entity_id"].unique())[: args.limit_entities]
        index = index[index["entity_id"].isin(keep)]
    print(f"보고서 색인 {len(index):,}건(원본) · 회사 {index['entity_id'].nunique():,} · 원천 {index['src'].value_counts().to_dict()} · "
          f"접수 {index['day'].min()}~{index['day'].max()}")
    if FEATURES.exists() and not args.rebuild and not args.limit_entities:
        feat = pd.read_parquet(FEATURES)  # invariant-allow: data-access — 이 진단의 피처 캐시
        if set(feat["rcept_no"]) != set(index["rcept_no"]):
            print("  색인이 캐시와 다르다 — --rebuild 로 다시 계산하라(지금은 캐시를 쓴다)")
    else:
        feat = compute_features(index, workers=args.workers)
        if not args.limit_entities:
            FEATURES.parent.mkdir(parents=True, exist_ok=True)
            feat.to_parquet(FEATURES, index=False)  # invariant-allow: data-access — 이 진단의 피처 캐시
    feat["day"] = pd.to_datetime(feat["day"]).dt.date
    for col in [f"{m}_{s}" for s in SECTIONS for m in MEASURES] + ["prev_rcept_no", "len_biz", "len_risk", "len_mdna"]:
        if col not in feat:
            feat[col] = np.nan
    print("\n=== 커버리지(종류·접수연도) ===")
    print(coverage(feat).round(3).to_string())
    print("\n=== 피처 분포(짝 있는 보고서) ===")
    print(feat[[f"{m}_{s}" for s in SECTIONS for m in MEASURES]].describe().round(3).to_string())
    if args.features_only:
        return 0
    measure(feat, ranker=not args.no_ranker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
