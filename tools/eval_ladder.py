"""공통 평가 사다리 — 어떤 새 점수(또는 랭커 재료 후보)든 **같은 세 칸**으로 판정표를 낸다(2026-10-13, 사용자 "판단 기준·좋은 종목을 못 찾으면 말짱도루묵").

    # 0) 공통 자료(가격·명단·라벨·벤치·V6·시총·업종)를 한 번 굽는다 — 무거움, 연구 슬롯 잠금 안에서
    for p in market caps sectors; do flock -n data/_locks/research-slot-1.lock nice -n 10 .venv/bin/python tools/eval_ladder.py context --part $p; done

    # 1) 모델(랭커 대체) 판정 — 점수 파일 하나 대 대조(기본 C0) 파일
    #    --score·--control 에 이름만 주면 data/_diag/eval-ladder/scores/<이름> 점수 파일을 읽는다(경로도 된다)
    .venv/bin/python tools/eval_ladder.py run --name BE2 --score BE2 --control C0 --n-tests 3 --loss

    # 2) 재료(랭커 입력 후보) 판정 — 칸 ② 는 C0 6점수 GBM 에 재료를 더해 다시 배운다(워크포워드, 무거움)
    flock -n data/_locks/research-slot-2.lock nice -n 10 .venv/bin/python tools/eval_ladder.py run --mode feature \
        --name CNN --score <재료 점수 파일> --n-tests 1

**세 칸**(모두 거래가능 전 종목 · 결정 t → t+1 종가 진입 · 편도 0.41%):

| 칸 | 무엇 | O 조건(기본) |
|---|---|---|
| ① 원리 | 횡단면 h5 순위 IC(시드 평균 IC_t), 십분위 D10−D1(일 재조정·비용 없음), 국면별·연도별 부호 | IC>0 · NW t ≥ t* · D10−D1 NW t ≥ t* · 연도 부호 ≥ 75% |
| ② 한계기여 | 대조 대비 ΔIC 짝 차(같은 시드). 재료 모드는 C0+재료 재학습 대 C0 재학습 | ΔIC>0 · NW t ≥ t* · 시드 ≥ 4/5 |
| ③ 실제 포트 | 현행 구성(EMA5 · 상위 24 · 완충 3N · 10세션 · 동일가중) 비용 후 — 시드별 + 시드 평균 묶음 | 대 K200 > 0 · 대 유니버스 EW > 0 · 시드 ≥ 4/5(대 K200) · MDD ≥ K200 − 5%p · (대조 있으면) 회전 ≤ 대조 × 1.2 |

- **특수 구간 견고성**(사용자 10/13 밤): 모든 숫자를 **전체 / 특수 구간 뺀 것 / 특수 구간만** 으로 낸다. 특수 구간은 코드 상수
  `SPECIAL_SPANS`(코로나 2020-02-17~06-30 · 급등장 2025-01-01~2026-06-30 고정 달력) + `EXTREME_DAYS`(2026-03-04·07-28·07-31).
  ①a·①b·②a 는 뺀 쪽도 양수·NW t ≥ 2(`ex_t`), ③a·③b 는 뺀 쪽도 양수여야 O. 전체만 통과하면 **S(능력이지만 특수)** — 탈락으로 센다.
- 기본 t* = 3.0(사용자 결정 "긴 과거 엄격 기준"). `--t-star` 로 바꾼다. **다중검정 수**(`--n-tests`)는 결과 JSON 과
  `data/_diag/eval-ladder/ledger.jsonl` 에 남고 Bonferroni 보정 p 를 같이 찍는다(판정 문턱은 t* 그대로).
- **날짜 상한 2026-09-30(`DATE_CAP`) 은 코드에 못 박았다.** 가격은 그 날 장 마감 뒤 as_of 로만 읽고, 그 뒤 세션이 든 점수 파일은
  **거부**한다(rc 2) — 금고 second(10/1~) 봉인. 상한을 바꾸려면 이 파일을 고치고 그 사실을 커밋에 적는다.
- 진단 도구다. `research_trials` 에 적지 않는다(시행이면 등록 문서가 먼저다). 창고에 쓰지 않는다.
- 비중은 연구 kit 관례대로 **동일가중**이다 — 실전 risk_parity(팩터 공분산)는 연구 kit 에 없다(한계로 적는다).

점수 파일 형식: parquet 또는 pickle, 열 `entity_id · session · pred`(+ `seed`; 없으면 시드 하나). 국장만.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402

# --------------------------------------------------------------------------- 고정값

#: **금고 second 봉인선.** 이 날 장 마감 뒤의 가격·라벨은 어떤 경로로도 계산하지 않는다(사용자 확정 2026-10-13).
DATE_CAP = date(2026, 9, 30)
#: 가격 as_of — 9/30 16:00 UTC = 10/1 01:00 KST(10/1 장 전). 이 시각 뒤 관측은 읽지 않는다.
CAP_AS_OF = datetime.combine(DATE_CAP, time(16, 0), tzinfo=UTC)
#: 국장 점수 조각의 첫 날(확장 패널 FA2021) — 공통 자료의 시작.
CONTEXT_START = date(2021, 11, 10)
OUT = Path("data/_diag/eval-ladder")  # invariant-allow: data-access — 연구 캐시(창고 아님)
SCORES = OUT / "scores"
LEDGER = OUT / "ledger.jsonl"
CONTEXT_FILE = OUT / "context"
#: 연구 캐시 — 거래가능 명단(≤ 2026-06-30 연구 확장 패널, 7/1~9/30 금고 early 작업 캐시 = 실전 `tradable_entities`).
TRAD_LONG = Path("data/_diag-long/tradable-KR.pkl")  # invariant-allow: data-access — 연구 캐시
TRAD_EARLY = Path("data/_diag/vault-early/tradable-KR.pkl")  # invariant-allow: data-access — 금고 early 작업 캐시(이제 탐색 자료)
CAPS_CACHE = Path("data/_diag/p1a-prime/caps-rank.parquet")  # invariant-allow: data-access — 시총 캐시(market_stats 를 store.get 으로 만든 것)
#: 6점수 패널 — ≤ 2026-06-30 은 확장 패널, 7/1~ 은 금고 early 점수 캐시(signals 를 store.get 으로 굽은 것).
PANEL_LONG = Path("data/_diag/fa2021/panel/panel-KR-KR+US-20211110-20260630.parquet")  # invariant-allow: data-access — 연구 패널
EARLY_SCORES = Path("data/_diag/vault-early")  # invariant-allow: data-access — 금고 early 점수 캐시
SCORE_FEATS = ("chart", "event", "flow", "fundamental", "regime", "risk")
EARLY_NAME = {"flow": "flow_kr"}

INDEX = "KR:IDX:KOSPI200"
ANN = 252
ONE_WAY_COST = 0.0041
MAX_MOVE = 0.5
SPAN, N, MULT, EVERY = 5, 24, 3, 10
LAG = 4
SEEDS = (0, 1, 2, 3, 4)
#: 실현 구간 (시작, 끝] — `diag_bear_2022.realized_end` 기준. 앞 넷은 앞선 진단(hedged-sleeve·style-hedge)과 같다.
REGIMES: dict[str, tuple[date, date]] = {
    "하락": (date(2022, 1, 3), date(2022, 9, 30)),
    "반등": (date(2022, 9, 30), date(2023, 1, 31)),
    "박스": (date(2023, 1, 31), date(2024, 12, 31)),
    "급등": (date(2024, 12, 31), date(2026, 6, 30)),
    "7~9월": (date(2026, 6, 30), DATE_CAP),
}
KST = timezone(timedelta(hours=9))
#: **특수 구간**(사용자 10/13 밤 "코로나·작년 코스피 급등장은 특이한 케이스 — 그것도 능력이지만 특수") — 결과를 보기 전에
#: 달력으로 고정한다. 급등장은 **고정 달력 규칙**(2025-01-01~2026-06-30, 앞선 진단의 '급등' 구간과 같은 경계)을 쓴다 —
#: '60일 수익 상위 x%' 같은 자료 의존 규칙은 쓰지 않았다. 극단일 셋은 그날 수익이 실현되는 세션(일 수익)·그날을 라벨 창에
#: 품은 세션(IC)을 특수로 센다(10/3 확인: 실제로 있었던 ±10%대 날).
#: 코로나 구간은 공통 자료(2021-11~) 밖이라 지금 판정에는 0세션이다 — 과거 금고(2011~2021) 자료에 쓸 때 걸린다.
SPECIAL_SPANS: dict[str, tuple[date, date]] = {
    "코로나": (date(2020, 2, 17), date(2020, 6, 30)),
    "급등장": (date(2025, 1, 1), date(2026, 6, 30)),
}
EXTREME_DAYS = (date(2026, 3, 4), date(2026, 7, 28), date(2026, 7, 31))


@dataclass(frozen=True)
class Thresholds:
    t_star: float = 3.0           # 사용자 결정 "긴 과거 엄격 기준 t≥3"
    sign_share: float = 0.75      # 연도별 IC 부호 양수 비율
    seeds_need: int = 4           # 5 중
    port_t: float = 0.0           # ③ 의 NW t 문턱(기본은 부호만 — 포트 수익은 검정력이 낮다, §10)
    mdd_margin: float = 0.05      # ③ MDD ≥ K200 MDD − 여유
    turn_ratio: float = 1.2       # ③ 회전 ≤ 대조 × 비
    ex_t: float = 2.0             # 특수 구간을 뺀 쪽: 부호 같고 NW t ≥ ex_t(①②). ③ 은 부호만(port_t — 포트 검정력이 낮다)


class CapViolation(SystemExit):
    """금고 봉인선 위반 — rc 2."""

    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message
        print(f"거부 — {message}", file=sys.stderr, flush=True)


def guard_cap(sessions: Sequence[date] | pd.Index | pd.Series, what: str) -> None:
    """세션 하나라도 DATE_CAP 뒤면 거부한다(조용히 자르지 않는다 — 누가 봉인 창 점수를 만들었는지 드러나야 한다)."""
    vals = pd.to_datetime(pd.Series(list(sessions))).dt.date if len(sessions) else pd.Series([], dtype=object)
    late = [d for d in vals if d > DATE_CAP]
    if late:
        raise CapViolation(f"{what} 에 {DATE_CAP} 뒤 세션 {len(late)}개(첫 {min(late)}) — 금고 second 봉인")


# --------------------------------------------------------------------------- 공통 자료


@dataclass
class Context:
    """평가에 쓰는 시장 자료 — 모두 DATE_CAP 장 마감까지만."""

    close: pd.DataFrame                  # 세션 × 종목 수정 종가
    ret: pd.DataFrame                    # 결정 t 의 값 = 종가 t+1 → t+2 (|r| > 50% 결측)
    bench: pd.Series                     # K200 가격지수, 같은 정렬
    k200_close: pd.Series
    trad: dict[date, set[str]]
    uni: pd.Series                       # 거래가능 전 종목 동일가중(비용 없음, 매일)
    scale: pd.Series                     # V6 노출 배수(결정 세션 전날 종가까지)
    caps: pd.DataFrame | None = None     # session · entity_id · cap
    sectors: dict[str, str] = field(default_factory=dict)
    cost: float = ONE_WAY_COST          # 편도 비용(국장 0.41%, 미장은 config accounting.fee_us)
    market: str = "KR"

    @property
    def calendar(self) -> list[date]:
        return list(self.ret.index)

    def y5(self) -> pd.DataFrame:
        """h5 라벨 = 종가 t+1 → t+6. t+6 이 DATE_CAP 뒤면 결측(가격이 없다). 한 번 만들고 붙잡아 둔다."""
        cached = self.__dict__.get("_y5")
        if cached is None:
            r5 = self.close.shift(-6) / self.close.shift(-1) - 1.0
            r5 = r5.replace([np.inf, -np.inf], np.nan)
            long = r5.stack().rename("target").reset_index()
            long.columns = ["session", "entity_id", "target"]
            cached = long[["entity_id", "session", "target"]]
            self.__dict__["_y5"] = cached
        return cached

    def y5w(self) -> pd.DataFrame:
        """h5 라벨 넓은 표(세션 × 종목, float32)."""
        cached = self.__dict__.get("_y5w")
        if cached is None:
            r5 = (self.close.shift(-6) / self.close.shift(-1) - 1.0).replace([np.inf, -np.inf], np.nan)
            cached = r5.astype(np.float32)
            self.__dict__["_y5w"] = cached
        return cached

    def trad_mask(self) -> pd.DataFrame:
        """거래가능 넓은 표(세션 × 종목, bool) — close 의 축."""
        cached = self.__dict__.get("_tmask")
        if cached is None:
            cols = {e: i for i, e in enumerate(self.close.columns)}
            m = np.zeros(self.close.shape, dtype=bool)
            rows = {d: i for i, d in enumerate(self.close.index)}
            for d, names in self.trad.items():
                i = rows.get(d)
                if i is None:
                    continue
                idx = [cols[e] for e in names if e in cols]
                m[i, idx] = True
            cached = pd.DataFrame(m, index=self.close.index, columns=self.close.columns)
            self.__dict__["_tmask"] = cached
        return cached

    def ret_long(self) -> pd.DataFrame:
        """거래가능 명단 안 (session, entity_id, r) — 십분위용. 한 번 만들고 붙잡아 둔다."""
        cached = self.__dict__.get("_ret_long")
        if cached is None:
            r = self.ret.stack().rename("r").reset_index()
            r.columns = ["session", "entity_id", "r"]
            tr = pd.DataFrame([(d, e) for d, names in self.trad.items() for e in names], columns=["session", "entity_id"])
            cached = r.merge(tr, on=["session", "entity_id"], how="inner")
            self.__dict__["_ret_long"] = cached
        return cached

    def k5(self) -> pd.Series:
        return self.k200_close.shift(-6) / self.k200_close.shift(-1) - 1.0


def _trad_frame(path: Path) -> pd.DataFrame:
    f = pd.read_pickle(path)  # invariant-allow: data-access — 연구 캐시
    f["session"] = pd.to_datetime(f["session"]).dt.date
    return f[["session", "entity_id"]]


def build_context(store: Any, *, start: date = CONTEXT_START, end: date = DATE_CAP, with_caps: bool = True,
                  with_sectors: bool = True) -> Context:
    """가격·벤치·명단·V6·시총·업종. **as_of 는 CAP_AS_OF 를 넘지 않는다.**"""
    from quant_rl_trading.store.prices import read_prices
    from tools.diag_bear_2022 import exposure_scales

    end = min(end, DATE_CAP)
    as_of = min(CAP_AS_OF, datetime.combine(end, time(16, 0), tzinfo=UTC))
    span = (end - start).days + 40
    prices = read_prices(store, as_of=as_of, lookback=span, columns=["close"], adjusted=True, market="KR")
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    close = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    del prices
    close = close[(close.index >= start - timedelta(days=200)) & (close.index <= end)].astype("float64")
    guard_cap(close.index, "가격")
    ret = (close.shift(-2) / close.shift(-1) - 1.0)
    ret = ret.where(ret.abs() <= MAX_MOVE)
    idx = store.get("indices", as_of=as_of, lookback=span + 200, market="KR", entity=[INDEX],
                    columns=["entity_id", "valid_from", "close"])
    idx["day"] = pd.to_datetime(idx["valid_from"]).dt.date
    k = idx.groupby("day")["close"].last().astype(float).sort_index()
    k = k[k.index <= end].reindex(close.index).ffill()
    bench = k.shift(-2) / k.shift(-1) - 1.0
    trad_f = pd.concat([_trad_frame(TRAD_LONG), _trad_frame(TRAD_EARLY)], ignore_index=True)
    trad_f = trad_f[(trad_f["session"] >= start) & (trad_f["session"] <= end)]
    trad = {d: set(p["entity_id"]) for d, p in trad_f.groupby("session")}
    days = [d for d in ret.index if d >= start and d in trad]
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad[d])).mean()) for d in days})
    scale, _state = exposure_scales(store, days, as_of)
    caps = _caps(store, start, end, as_of) if with_caps else None
    sectors = _sectors(store, as_of, list(close.columns)) if with_sectors else {}
    return Context(close=close, ret=ret, bench=bench, k200_close=k, trad=trad, uni=uni,
                   scale=scale.reindex(days).fillna(1.0), caps=caps, sectors=sectors)


def _caps(store: Any, start: date, end: date, as_of: datetime) -> pd.DataFrame:
    """시총 — ≤ 2026-06-30 은 P1-a′ 캐시, 그 뒤는 `market_stats` 를 달마다(as_of 는 그 달 끝, 봉인선 안)."""
    base = pd.read_parquet(CAPS_CACHE)  # invariant-allow: data-access — 시총 캐시
    base["session"] = pd.to_datetime(base["session"]).dt.date
    last = max(base["session"])
    parts = [base[(base["session"] >= start) & (base["session"] <= end)]]
    month = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    while month <= end:
        hi = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        stamp = min(as_of, datetime.combine(hi, time(0), tzinfo=UTC))
        f = store.get("market_stats", as_of=stamp, lookback=(hi - month).days + 1, until=stamp, market="KR",
                      columns=["entity_id", "valid_from", "metric", "value"])
        f = f[f["metric"] == "market_cap"]
        f["session"] = pd.to_datetime(f["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
        f = f[(f["session"] >= month) & (f["session"] < hi) & (f["session"] <= end)]
        parts.append(f.groupby(["session", "entity_id"], as_index=False)["value"].last().rename(columns={"value": "cap"}))
        month = hi
    caps = pd.concat(parts, ignore_index=True)
    return caps[caps["cap"] > 0]


def _sectors(store: Any, as_of: datetime, entities: list[str]) -> dict[str, str]:
    """DART 표준산업분류 중분류(KSIC 앞 2자리). **2026-08 에 처음 적재된 값을 과거에도 고정 속성으로 쓴다** —
    정답(라벨) 잔차화에만 쓰고 피처로는 쓰지 않는다(진단 한계로 적는다)."""
    from quant_rl_trading.selector.candidates import sector_map

    try:
        raw = sector_map(store, as_of=as_of, entities=entities, market="KR", source="dart_company", lookback=None)
    except Exception as exc:  # noqa: BLE001 — 업종이 없으면 잔차 정답에서 업종만 빠진다(조용히 넘기지 않고 찍는다)
        print(f"업종 읽기 실패 — 업종 더미 없이 간다: {exc}", flush=True)
        return {}
    return {e: str(v).replace("KSIC:", "")[:2] for e, v in raw.items()}


_CTX_FIELDS = ("close", "bench", "k200_close", "uni", "scale", "caps", "sectors", "trad")
_CTX_META = "meta.pkl"


def _trad_frame_of(trad: dict[date, set[str]]) -> pd.DataFrame:
    """명단 dict → (session, entity_id 범주) 표. 튜플 수백만 개를 만들지 않는다."""
    days = list(trad)
    ents = [e for d in days for e in trad[d]]
    sess = np.repeat(np.array(days, dtype=object), [len(trad[d]) for d in days])
    return pd.DataFrame({"session": sess, "entity_id": pd.Categorical(ents)})


def save_context(ctx: Context, path: Path = CONTEXT_FILE) -> None:
    """필드마다 따로 쓴다 — 통째로 pickle 하면 직렬화 사본이 겹쳐 4GB 상한에 걸린다(2026-10-13 22:07 MemoryError).
    ret 은 close 에서 다시 만든다(같은 식). trad 는 (session, entity_id) 표로."""
    import gc

    path.mkdir(parents=True, exist_ok=True)
    for name in _CTX_FIELDS:
        obj = getattr(ctx, name)
        if name == "close":
            obj = obj.astype("float32")
        if name == "trad":
            obj = _trad_frame_of(obj)
        pd.to_pickle(obj, path / f"{name}.pkl")  # invariant-allow: data-access — 연구 캐시
        del obj
        gc.collect()
    pd.to_pickle({"cost": ctx.cost, "market": ctx.market}, path / _CTX_META)  # invariant-allow: data-access — 연구 캐시


def save_context_part(store: Any, part: str, path: Path = CONTEXT_FILE) -> None:
    """공통 자료를 조각으로 굽는다 — market(가격·벤치·명단·V6) · caps(시총) · sectors(업종). 프로세스마다 하나씩
    (2026-10-13 22:07·22:09 한 프로세스에서 다 하다 4GB 가상 메모리 상한에 MemoryError)."""
    import gc

    path.mkdir(parents=True, exist_ok=True)
    as_of = CAP_AS_OF
    if part == "market":
        ctx = build_context(store, with_caps=False, with_sectors=False)
        ctx.caps, ctx.sectors = None, {}
        for name in ("close", "bench", "k200_close", "uni", "scale", "trad"):
            obj = getattr(ctx, name)
            if name == "close":
                obj = obj.astype("float32")
            if name == "trad":
                obj = pd.DataFrame([(d, e) for d, names in obj.items() for e in names], columns=["session", "entity_id"])
            pd.to_pickle(obj, path / f"{name}.pkl")  # invariant-allow: data-access — 연구 캐시
            del obj
            gc.collect()
    elif part == "caps":
        pd.to_pickle(_caps(store, CONTEXT_START, DATE_CAP, as_of), path / "caps.pkl")  # invariant-allow: data-access — 연구 캐시
    else:
        close = pd.read_pickle(path / "close.pkl")  # invariant-allow: data-access — 연구 캐시
        pd.to_pickle(_sectors(store, as_of, list(close.columns)), path / "sectors.pkl")  # invariant-allow: data-access — 연구 캐시


def load_context(path: Path = CONTEXT_FILE) -> Context:
    parts = {n: pd.read_pickle(path / f"{n}.pkl") for n in _CTX_FIELDS}  # invariant-allow: data-access — 연구 캐시
    close = parts["close"].astype("float64")
    guard_cap(close.index, "공통 자료")
    ret = close.shift(-2) / close.shift(-1) - 1.0
    ret = ret.where(ret.abs() <= MAX_MOVE)
    trad = {d: set(p["entity_id"]) for d, p in parts["trad"].groupby("session")}
    meta = pd.read_pickle(path / _CTX_META) if (path / _CTX_META).exists() else {}  # invariant-allow: data-access — 연구 캐시
    return Context(close=close, ret=ret, bench=parts["bench"], k200_close=parts["k200_close"], trad=trad, uni=parts["uni"],
                   scale=parts["scale"], caps=parts["caps"], sectors=parts["sectors"],
                   cost=float(meta.get("cost", ONE_WAY_COST)), market=str(meta.get("market", "KR")))


# --------------------------------------------------------------------------- 점수 파일


SCORE_SUFFIX = ".parquet"  # invariant-allow: data-access — 점수 캐시 확장자(한 곳)


def score_path(name: str, folder: Path = SCORES) -> Path:
    """이름 → 점수 파일 경로(`folder/<이름>` + 확장자)."""
    return folder / f"{name}{SCORE_SUFFIX}"


def resolve(spec: str) -> Path:
    """`--score`·`--control` 값 — 경로면 그대로, 이름이면 `score_path(이름)`."""
    p = Path(spec)
    return p if (p.suffix or "/" in spec) else score_path(spec)


class LazyScores(Mapping[int, pd.DataFrame]):
    """점수 파일의 시드를 **하나씩** 읽는다(필요할 때만) — 시드 다섯 × 300만 행을 한꺼번에 들면 4GB 상한을 넘는다
    (2026-10-13 22:19 scores 단계 MemoryError). `lo`·`hi` 는 세션 창(양끝 포함)."""

    def __init__(self, path: Path, lo: date | None = None, hi: date | None = None) -> None:
        self.path, self.lo, self.hi = path, lo, hi
        seeds = pd.read_parquet(path, columns=["seed"])["seed"]  # invariant-allow: data-access — 점수 캐시
        self._seeds = sorted(int(x) for x in seeds.unique())

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._seeds)

    def __len__(self) -> int:
        return len(self._seeds)

    def __getitem__(self, seed: int) -> pd.DataFrame:
        if seed not in self._seeds:
            raise KeyError(seed)
        filters: list[tuple[str, str, Any]] = [("seed", "==", int(seed))]
        if self.lo is not None:
            filters.append(("session", ">=", self.lo))
        if self.hi is not None:
            filters.append(("session", "<=", self.hi))
        import pyarrow.compute as pc
        import pyarrow.parquet as pq  # invariant-allow: data-access — 점수 캐시

        t = pq.read_table(self.path, columns=["entity_id", "session", "pred"], filters=filters,  # invariant-allow: data-access — 점수 캐시
                          read_dictionary=["entity_id"])
        days = pc.cast(pc.cast(t.column("session"), "date32"), "int32").to_numpy()
        uniq, inv = np.unique(days, return_inverse=True)
        sess = pd.Categorical.from_codes(inv.astype(np.int32),
                                         categories=pd.Index([date.fromordinal(int(d) + 719163) for d in uniq]))
        ent = t.column("entity_id").unify_dictionaries().combine_chunks()
        f = pd.DataFrame({"entity_id": pd.Categorical.from_codes(ent.indices.to_numpy(zero_copy_only=False).astype(np.int32),
                                                                   categories=pd.Index(ent.dictionary.to_pylist())),
                          "session": sess, "pred": t.column("pred").to_numpy()})
        del t, ent
        guard_cap(list(sess.categories), str(self.path))
        return f

    def window(self, lo: date | None, hi: date | None) -> LazyScores:
        out = LazyScores.__new__(LazyScores)
        out.path, out._seeds = self.path, self._seeds
        out.lo = max(x for x in (self.lo, lo) if x is not None) if (self.lo or lo) else None
        out.hi = min(x for x in (self.hi, hi) if x is not None) if (self.hi or hi) else None
        return out


def read_scores(path: Path | str) -> Mapping[int, pd.DataFrame]:
    """점수 파일 → {seed: (entity_id, session, pred)}. parquet 는 시드를 하나씩 읽는 `LazyScores`. DATE_CAP 뒤 세션이 있으면 거부."""
    path = resolve(str(path))
    if path.suffix == SCORE_SUFFIX:
        sess = pd.read_parquet(path, columns=["session"])["session"]  # invariant-allow: data-access — 점수 캐시
        guard_cap(pd.Series(sess.unique()), str(path))
        del sess
        return LazyScores(path)
    frame = pd.read_pickle(path)  # invariant-allow: data-access — 점수 캐시
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    guard_cap(frame["session"].unique(), str(path))
    if "seed" not in frame.columns:
        frame["seed"] = 0
    return {int(s): p[["entity_id", "session", "pred"]].reset_index(drop=True) for s, p in frame.groupby("seed")}


def write_scores(preds: Mapping[int, pd.DataFrame] | Any, path: Path) -> None:
    """시드마다 한 조각씩 흘려 쓴다(전체를 이어 붙이지 않는다). `preds` 는 Mapping 또는 (seed, frame) 반복자."""
    import pyarrow as pa
    import pyarrow.parquet as pq  # invariant-allow: data-access — 점수 캐시 쓰기

    items = preds.items() if isinstance(preds, Mapping) else preds
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    schema = pa.schema([("entity_id", pa.string()), ("session", pa.date32()), ("pred", pa.float64()), ("seed", pa.int64())])
    writer = pq.ParquetWriter(tmp, schema)  # invariant-allow: data-access — 점수 캐시
    try:
        for s, p in items:
            guard_cap(p["session"].unique(), str(path))
            part = p[["entity_id", "session", "pred"]].assign(seed=int(s))
            part["entity_id"] = part["entity_id"].astype(str)
            part["session"] = pd.to_datetime(part["session"]).dt.date
            writer.write_table(pa.Table.from_pandas(part, schema=schema, preserve_index=False))  # invariant-allow: data-access — 점수 캐시
            del part
    finally:
        writer.close()
    tmp.replace(path)


def bundle(preds: Mapping[int, pd.DataFrame], ctx: Context | None = None) -> pd.DataFrame:
    """시드 평균 묶음 — 같은 (종목, 세션)의 점수 평균(AL 의 '5시드 평균 점수'). 시드를 하나씩 더한다.
    ctx 를 주면 넓은 표로(메모리 작다)."""
    if ctx is not None:
        tot: pd.DataFrame | None = None
        cnt: pd.DataFrame | None = None
        for p in preds.values():
            w = as_wide(p, ctx)
            one = w.notna().astype(np.float32)
            tot = w.fillna(0.0) if tot is None else tot.add(w.fillna(0.0), fill_value=0.0)
            cnt = one if cnt is None else cnt.add(one, fill_value=0.0)
            del p, w, one
        return (tot / cnt.where(cnt > 0)).astype(np.float32)
    total: pd.Series | None = None
    count: pd.Series | None = None
    for p in preds.values():
        v = p.set_index(["entity_id", "session"])["pred"].astype(float)
        one = pd.Series(1.0, index=v.index)
        total = v if total is None else total.add(v, fill_value=0.0)
        count = one if count is None else count.add(one, fill_value=0.0)
        del p, v, one
    out = (total / count).rename("pred").reset_index()
    return out[["entity_id", "session", "pred"]]


def restrict(preds: Mapping[int, pd.DataFrame], lo: date | None, hi: date | None) -> Mapping[int, pd.DataFrame]:
    if isinstance(preds, LazyScores):
        return preds.window(lo, hi)
    out = {}
    for s, p in preds.items():
        m = pd.Series(True, index=p.index)
        if lo is not None:
            m &= p["session"] >= lo
        if hi is not None:
            m &= p["session"] <= hi
        out[s] = p[m].reset_index(drop=True)
    return out


# --------------------------------------------------------------------------- 통계


def nw_t(x: pd.Series | np.ndarray, lag: int = LAG) -> float:
    s = pd.Series(np.asarray(x, dtype=float)).dropna()
    return float(ic_module.newey_west_t(s, lag=lag)) if len(s) > lag + 2 else float("nan")


def mdd(r: pd.Series) -> float:
    nav = (1.0 + r.fillna(0.0)).cumprod()
    return float((nav / nav.cummax() - 1.0).min()) if len(nav) else float("nan")


def regime_days(ctx: Context, days: Sequence[date]) -> dict[str, list[date]]:
    from tools.diag_bear_2022 import in_window, realized_end

    spans = realized_end(list(days), ctx.calendar)
    out = {k: in_window(pd.Index(list(days)), spans, v) for k, v in REGIMES.items()}
    out["전체"] = [d for d in days if d in spans]
    return out


def special_sessions(index: Sequence[date], calendar: Sequence[date], *, label_days: int) -> set[date]:
    """특수 구간에 드는 결정 세션. 일 수익 계열(label_days=2)은 실현 종가 t+1·t+2 중 하나가, IC(label_days=6)는
    라벨 창 t+1~t+6 중 하나가 특수 구간·극단일에 닿으면 특수다(결정 세션 자체가 구간 안이어도 특수)."""
    cal = list(calendar)
    pos = {d: i for i, d in enumerate(cal)}
    ext = set(EXTREME_DAYS)
    out = set()
    for d in index:
        i = pos.get(d)
        span = (cal[i + 1:i + 1 + label_days] if i is not None else []) or [d]
        if any(lo <= x <= hi for x in (d, *span) for lo, hi in SPECIAL_SPANS.values()) or any(x in ext for x in span):
            out.add(d)
    return out


def _part(x: pd.Series, scale: float) -> dict[str, Any]:
    x = x.dropna()
    return {"ann": float(x.mean() * scale) if len(x) else float("nan"), "t": nw_t(x), "n": int(len(x))}


def describe(x: pd.Series, ctx: Context, *, scale: float = ANN) -> dict[str, Any]:
    """일 계열 하나 → 연율(평균 × scale)·NW t·국면별 연율 + **전체 / 특수 구간 뺀 것 / 특수 구간만**.
    IC 처럼 단위가 없는 계열은 scale=1(라벨 창 6세션 기준으로 특수를 센다)."""
    x = x.dropna()
    rd = regime_days(ctx, list(x.index)) if scale != 1 else _ic_regimes(x.index)
    out: dict[str, Any] = _part(x, scale)
    out["regimes"] = {k: (float(x.reindex(v).mean() * scale) if len(v) else float("nan")) for k, v in rd.items() if k != "전체"}
    sp = special_sessions(list(x.index), ctx.calendar, label_days=6 if scale == 1 else 2)
    out["ex_special"] = _part(x.reindex([d for d in x.index if d not in sp]), scale)
    out["special_only"] = _part(x.reindex(sorted(sp)), scale)
    return out


def robust(d: dict[str, Any], *, min_t: float) -> tuple[bool, str]:
    """특수 구간을 빼도 방향(양수)·유의성(NW t ≥ min_t)이 유지되나. 반환 (유지, 설명)."""
    ex, sp = d["ex_special"], d["special_only"]
    ok = bool(np.isfinite(ex["t"]) and ex["ann"] > 0 and ex["t"] >= min_t)
    return ok, f"특수 뺀 쪽 {ex['ann']:+.4g}(t {ex['t']:+.2f}, n {ex['n']}) · 특수만 {sp['ann']:+.4g}(t {sp['t']:+.2f}, n {sp['n']})"


def judge(full_ok: bool, d: dict[str, Any], *, min_t: float, text: str) -> tuple[bool | str, str]:
    """기본 판정 = 전체 통과 **그리고** 특수 구간을 뺀 쪽도 양수·t ≥ min_t. 전체만 통과하고 뺀 쪽이 무너지면 'S'(능력이지만 특수)."""
    ok, why = robust(d, min_t=min_t)
    if d["ex_special"]["n"] == 0 or d["special_only"]["n"] == 0:
        return full_ok, f"{text} · {why} (특수 구간과 겹치지 않는 창 — 견고성 판정 없음)"
    if full_ok and ok:
        return True, f"{text} · {why}"
    if full_ok and d["special_only"]["ann"] > 0:
        return "S", f"{text} · {why} → 능력이지만 특수"
    return False, f"{text} · {why}"


def _ic_regimes(index: Sequence[date]) -> dict[str, list[date]]:
    """IC 는 결정 세션 기준으로 국면을 나눈다(라벨 t+1→t+6 이 구간 끝을 조금 넘을 수 있다 — 단위 없는 비교라 허용)."""
    out = {k: [d for d in index if lo <= d < hi] for k, (lo, hi) in REGIMES.items()}
    out["전체"] = list(index)
    return out


def yearly_sign_share(x: pd.Series) -> tuple[float, dict[int, float]]:
    by = x.dropna().groupby([d.year for d in x.dropna().index]).mean()
    return (float((by > 0).mean()) if len(by) else float("nan")), {int(k): float(v) for k, v in by.items()}


# --------------------------------------------------------------------------- 칸 ① 원리


def is_wide(p: pd.DataFrame) -> bool:
    return "pred" not in p.columns


def as_wide(p: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    """긴 점수(entity_id·session·pred) → 넓은 표(세션 × close 의 종목, float32). 이미 넓으면 그대로.
    가격이 없는 종목은 빠진다(라벨·수익을 잴 수 없다)."""
    if is_wide(p):
        return p
    sc = p["session"].astype("category")
    ec = p["entity_id"].astype("category")
    mat = np.full((len(sc.cat.categories), len(ec.cat.categories)), np.nan, dtype=np.float32)
    mat[sc.cat.codes.to_numpy(), ec.cat.codes.to_numpy()] = p["pred"].to_numpy(np.float32)
    days = [d if isinstance(d, date) else pd.Timestamp(d).date() for d in sc.cat.categories]
    w = pd.DataFrame(mat, index=pd.Index(days), columns=pd.Index([str(e) for e in ec.cat.categories]))
    guard_cap(w.index, "점수")
    return w.sort_index().reindex(columns=ctx.close.columns)


def _row_rank(a: np.ndarray) -> np.ndarray:
    return pd.DataFrame(a).rank(axis=1, pct=True).to_numpy()


def wide_ic(w: pd.DataFrame, ctx: Context) -> pd.Series:
    """세션별 Spearman — 거래가능 ∩ 점수 ∩ 라벨 이 있는 종목에서 순위의 피어슨(= power_principle.session_stats 의 ic)."""
    idx = w.index.intersection(ctx.close.index)
    P = w.loc[idx].to_numpy(np.float64)
    Y = ctx.y5w().loc[idx].to_numpy(np.float64)
    M = ctx.trad_mask().loc[idx].to_numpy() & ~np.isnan(P) & ~np.isnan(Y)
    P[~M] = np.nan
    Y[~M] = np.nan
    rp, ry = _row_rank(P), _row_rank(Y)
    rp = rp - np.nanmean(rp, axis=1, keepdims=True)
    ry = ry - np.nanmean(ry, axis=1, keepdims=True)
    num = np.nansum(rp * ry, axis=1)
    den = np.sqrt(np.nansum(rp * rp, axis=1) * np.nansum(ry * ry, axis=1))
    ic = np.where((M.sum(axis=1) >= 10) & (den > 0), num / np.where(den > 0, den, 1), np.nan)
    return pd.Series(ic, index=idx).dropna()


def ic_frame(preds: Mapping[int, pd.DataFrame], ctx: Context) -> pd.DataFrame:
    """세션 × 시드 IC(Spearman, 거래가능 전 종목)."""
    return pd.DataFrame({s: wide_ic(as_wide(p, ctx), ctx) for s, p in preds.items()}).sort_index()


def decile_frame(pred: pd.DataFrame, ctx: Context, *, q: float = 0.10) -> pd.DataFrame:
    """일 재조정 십분위(동일가중, 비용 없음) — 결정 t 의 원 점수(평활 없음)로 t+1→t+2 수익. 열 d10·d1."""
    w = as_wide(pred, ctx)
    idx = w.index.intersection(ctx.ret.index)
    P = w.loc[idx].to_numpy(np.float64)
    R = ctx.ret.loc[idx, w.columns].to_numpy(np.float64)
    M = ctx.trad_mask().loc[idx, w.columns].to_numpy() & ~np.isnan(P) & ~np.isnan(R)
    P[~M] = np.nan
    rp = _row_rank(P)
    with np.errstate(invalid="ignore"):
        top = np.where(rp > 1 - q, R, np.nan)
        bot = np.where(rp <= q, R, np.nan)
        d10 = np.nanmean(top, axis=1)
        d1 = np.nanmean(bot, axis=1)
    return pd.DataFrame({"d10": d10, "d1": d1}, index=idx).dropna(how="all")


# --------------------------------------------------------------------------- 칸 ③ 포트


def book(pred: pd.DataFrame, ctx: Context, *, n: int = N, mult: int = MULT, every: int = EVERY,
         theta: float | None = None, smooth: bool = True) -> tuple[pd.Series, pd.Series]:
    """현행 구성 롱 다리(EMA5 → 상위 n · 완충 mult·n · every 세션 재조정 · 동일가중 · 드리프트 · 편도 0.41%).
    `diag_style_hedge_cost.long_leg` 그대로(그 진단 검산 3 이 `trial_ranker_kit.portfolio` 와 일치를 확인했다).
    반환 (비용 후 일수익, 일 회전)."""
    from tools.diag_style_hedge_cost import long_leg

    wide = as_wide(pred, ctx).dropna(axis=1, how="all")
    if smooth:
        wide = wide.ewm(span=SPAN).mean()
    daily, turn, _ = long_leg(wide, ctx.ret, ctx.trad, n, every=every, mult=mult, theta=theta)
    turn = turn.reindex(daily.index).fillna(0.0)
    if ctx.cost != ONE_WAY_COST:                       # long_leg 는 국장 0.41% 를 박아 둔다 — 시장 비용으로 바꿔 끼운다
        daily = daily + turn * (ONE_WAY_COST - ctx.cost)
    return daily, turn


def v6(net: pd.Series, ctx: Context) -> pd.Series:
    from tools.diag_bear_2022 import apply_exposure

    out, _ = apply_exposure(net, ctx.scale.reindex(net.index).fillna(1.0))
    return out


def port_summary(net: pd.Series, turn: pd.Series, ctx: Context) -> dict[str, Any]:
    days = list(net.index)
    b = ctx.bench.reindex(days).fillna(0.0)
    u = ctx.uni.reindex(days)
    gross = net + turn * ctx.cost
    ex_k, ex_u = net - b, net - u
    with_v6 = v6(net, ctx)
    return {
        "ann": float(net.mean() * ANN), "gross_ann": float(gross.mean() * ANN),
        "vs_k200": describe(ex_k, ctx), "vs_uni": describe(ex_u, ctx), "gross_vs_uni": describe(gross - u, ctx),
        "v6_vs_k200": describe(with_v6 - b, ctx),
        "mdd": mdd(net), "k200_mdd": mdd(b), "v6_mdd": mdd(with_v6),
        "turn": float(turn.sum() * ANN / max(1, len(days))), "cost": float((turn * ctx.cost).sum() * ANN / max(1, len(days))),
    }


# --------------------------------------------------------------------------- 손실표


STAGES = (
    ("ic", "① 횡단면 IC(h5)"),
    ("d10_d1", "② 십분위 D10−D1(일 재조정·비용 전)"),
    ("d10_u", "② D10 − 유니버스 EW"),
    ("d1_u", "② D1 − 유니버스 EW"),
    ("top_raw", "③ 상위 24 매일·평활 없음·비용 전 − U(꼬리 집중)"),
    ("top_gross", "④ 상위 24 현행 규칙 비용 전 − U(평활·완충·10세션)"),
    ("top_net", "⑤ 상위 24 현행 규칙 비용 후 − U(선택 알파)"),
    ("top_k200", "⑥ 상위 24 비용 후 − K200(스타일 포함)"),
    ("v6_k200", "⑦ + V6 노출 − K200"),
)


def stage_series(pred: pd.DataFrame, ctx: Context, y5: pd.DataFrame) -> dict[str, pd.Series]:
    """점수 하나(시드 하나 또는 묶음)의 단계별 일 계열. IC 는 단위 없음, 나머지는 일 초과수익."""
    pred = as_wide(pred, ctx)
    ic = wide_ic(pred, ctx)
    dec = decile_frame(pred, ctx)
    u = ctx.uni
    raw, raw_turn = book(pred, ctx, n=N, mult=1, every=1, smooth=False)
    net, turn = book(pred, ctx)
    days = list(net.index)
    b = ctx.bench.reindex(days).fillna(0.0)
    return {
        "ic": ic,
        "d10_d1": dec["d10"] - dec["d1"],
        "d10_u": dec["d10"] - u.reindex(dec.index),
        "d1_u": dec["d1"] - u.reindex(dec.index),
        "top_raw": (raw + raw_turn * ctx.cost) - u.reindex(raw.index),
        "top_gross": (net + turn * ctx.cost) - u.reindex(days),
        "top_net": net - u.reindex(days),
        "top_k200": net - b,
        "v6_k200": v6(net, ctx) - b,
        "_net": net, "_turn": turn,
    }


def loss_table(preds: Mapping[int, pd.DataFrame], ctx: Context, *, y5: pd.DataFrame | None = None) -> dict[str, Any]:
    """단계별 연율·NW t·국면·시드 범위 + 시드 평균 묶음. 모든 단계를 **같은 세션 창**(점수가 있는 창)에서 잰다."""
    per_seed = {s: stage_series(p, ctx, None) for s, p in preds.items()}
    bun = stage_series(bundle(preds, ctx), ctx, None) if len(preds) > 1 else None
    rows = {}
    for key, label in STAGES:
        scale = 1 if key == "ic" else ANN
        seeds = {s: describe(v[key], ctx, scale=scale) for s, v in per_seed.items()}
        mean_series = pd.DataFrame({s: v[key] for s, v in per_seed.items()}).mean(axis=1)
        rows[key] = {"label": label, "seed_mean": describe(mean_series, ctx, scale=scale),
                     "seed_range": [min(x["ann"] for x in seeds.values()), max(x["ann"] for x in seeds.values())],
                     "bundle": describe(bun[key], ctx, scale=scale) if bun is not None else None}
    ports = {s: port_summary(v["_net"], v["_turn"], ctx) for s, v in per_seed.items()}
    return {"rows": rows, "ports": ports,
            "bundle_port": port_summary(bun["_net"], bun["_turn"], ctx) if bun is not None else None,
            "window": [str(min(v["_net"].index.min() for v in per_seed.values())),
                       str(max(v["_net"].index.max() for v in per_seed.values()))]}


# --------------------------------------------------------------------------- 칸 ② 재료 모드 — 재학습


def score_panel(ctx: Context | None = None) -> tuple[pd.DataFrame, list[date]]:
    """국장 6점수 패널(rank-gauss, 세션 안) — ≤ 2026-06-30 확장 패널 + 7/1~ 금고 early 점수 캐시. 반환 (패널, 국장 세션 축)."""
    from tools.trial_pooled_rank import rank_gauss

    long = pd.read_parquet(PANEL_LONG, columns=["entity_id", "session", "market", *SCORE_FEATS])  # invariant-allow: data-access — 연구 패널
    long = long[long["market"] == "KR"]
    long["session"] = pd.to_datetime(long["session"]).dt.date
    parts = []
    for feat in SCORE_FEATS:
        f = pd.read_pickle(EARLY_SCORES / f"scores-{EARLY_NAME.get(feat, feat)}-KR.pkl")  # invariant-allow: data-access — 금고 early 점수 캐시
        f["session"] = pd.to_datetime(f["session"]).dt.date
        parts.append(f.set_index(["entity_id", "session"])["score"].rename(feat))
    early = pd.concat(parts, axis=1).reset_index().assign(market="KR")
    early = early[early["session"] > max(long["session"])]
    panel = pd.concat([long, early], ignore_index=True)
    panel = panel[panel["session"] <= DATE_CAP]
    guard_cap(panel["session"].unique(), "점수 패널")
    panel = rank_gauss(panel, list(SCORE_FEATS))
    return panel.reset_index(drop=True), sorted(panel["session"].unique())


def fit(X: np.ndarray, y: np.ndarray | None, *, seed: int, dataset: Any = None):  # type: ignore[no-untyped-def]
    """시행 L 하이퍼파라미터(`trial_ranker_kit.fit`) 그대로 — 스레드만 환경(OMP_NUM_THREADS, 기본 2)에서."""
    import lightgbm as lgb

    params = dict(objective="regression", num_leaves=7, min_data_in_leaf=2000, learning_rate=0.03, bagging_fraction=0.8,
                  bagging_freq=1, feature_fraction=1.0, lambda_l2=1.0, verbose=-1, seed=seed,
                  num_threads=int(os.environ.get("OMP_NUM_THREADS", "2")))
    ds = dataset if dataset is not None else lgb.Dataset(X, y, free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=300)


def walk_kr(panel: pd.DataFrame, sessions: list[date], feats: Sequence[str], target: str,
            seeds: Sequence[int] = SEEDS, *, label: str = "", progress: Callable[[str], None] | None = None) -> dict[int, pd.DataFrame]:
    """국장 워크포워드 — C0 규격(20세션 블록 · 첫 블록 155 · 학습 끝 = 블록 시작 − 퍼지 5 − 엠바고 5 − 1).
    `final_round_kit.walk_gbm` 과 같은 블록·간격, 시장만 국장 하나다."""
    import lightgbm as lgb

    from tools import final_round_kit as fkit

    out: dict[int, list[pd.DataFrame]] = {int(s): [] for s in seeds}
    bl = fkit.blocks(sessions)
    for k, (first, last) in enumerate(bl):
        end = fkit.train_end(sessions, first)
        train = panel[(panel["session"] <= end) & panel[target].notna()]
        hi = sessions[last + 1] if last + 1 < len(sessions) else None
        test = panel[(panel["session"] >= sessions[first]) & ((panel["session"] < hi) if hi else True)]
        if train.empty or test.empty:
            continue
        ds = lgb.Dataset(train[list(feats)].to_numpy(np.float32), train[target].to_numpy(np.float32), free_raw_data=False)
        Xt = test[list(feats)].to_numpy(np.float32)
        base = test[["entity_id", "session"]].reset_index(drop=True)
        for s in seeds:
            out[int(s)].append(base.assign(pred=fit(None, None, seed=int(s), dataset=ds).predict(Xt)))
        msg = f"  {label} 블록 {k + 1}/{len(bl)} {sessions[first]}~{sessions[last]} · 학습 {len(train):,}행 ~{end}"
        (progress or print)(msg)
        del ds, Xt, train, test
    return {s: pd.concat(v, ignore_index=True) for s, v in out.items() if v}


#: 재료 파일의 추가 열 — `x_*` 는 연속 재료(세션 안 rank-gauss, 결측 0), `m_*` 는 결측 표지(0/1, 원값 그대로 — 파일에 없는
#: (종목, 세션)은 1 = 모름). `pred` 는 칸 ① 이 보는 대표 재료(부호는 사전 가설 방향 — 클수록 좋다)이고 칸 ② 에도 들어간다.
#: (2026-10-14 새 정보 N1~N7: "결측은 0 이 아니라 표지 열" — 열 하나짜리 재료 모드로는 표지를 못 넣었다.)
EXTRA_PREFIXES = ("x_", "m_")
BASE_CACHE = OUT / "feature-base"  # invariant-allow: data-access — C0 재학습 예측 캐시(재료와 무관하게 같다)


def read_feature_frame(path: Path | str) -> pd.DataFrame:
    """재료 파일 → (entity_id, session, pred, x_*, m_*) 한 표. 시드 열이 있으면 시드 0(재료는 시드가 없다). 봉인선 검사."""
    path = resolve(str(path))
    frame = pd.read_parquet(path) if path.suffix == SCORE_SUFFIX else pd.read_pickle(path)  # invariant-allow: data-access — 재료 캐시
    if "seed" in frame.columns:
        frame = frame[frame["seed"] == frame["seed"].min()].drop(columns=["seed"])
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    guard_cap(frame["session"].unique(), str(path))
    keep = ["entity_id", "session", "pred", *[c for c in frame.columns if c.startswith(EXTRA_PREFIXES)]]
    return frame[keep].reset_index(drop=True)


def _base_key(sessions: Sequence[date], n_rows: int, seeds: Sequence[int]) -> str:
    import hashlib

    blob = f"{sessions[0]}|{sessions[-1]}|{len(sessions)}|{n_rows}|{tuple(int(s) for s in seeds)}|{SCORE_FEATS}|v1"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _base_walk(panel: pd.DataFrame, sessions: list[date], seeds: Sequence[int], *, cache: bool) -> dict[int, pd.DataFrame]:
    """C0 재학습(6점수만) — 재료와 무관하게 같으므로 한 번 굽고 캐시한다(재료마다 절반의 계산을 아낀다)."""
    path = BASE_CACHE / f"base-{_base_key(sessions, len(panel), seeds)}{SCORE_SUFFIX}"
    if cache and path.exists():
        print(f"  C0 재학습 캐시 {path}", flush=True)
        return {int(s): p.reset_index(drop=True) for s, p in
                pd.read_parquet(path).assign(session=lambda f: pd.to_datetime(f["session"]).dt.date).groupby("seed")[  # invariant-allow: data-access — 캐시
                    ["entity_id", "session", "pred"]]}
    base = walk_kr(panel, sessions, SCORE_FEATS, "y", seeds, label="C0 재학습")
    if cache:
        write_scores(base, path)
    return base


def feature_marginal(feature: pd.DataFrame, ctx: Context, seeds: Sequence[int] = SEEDS, *,
                     cache_base: bool = True) -> dict[int, dict[str, pd.DataFrame]]:
    """재료 모드 칸 ② — C0 6점수 대 6점수 + 재료(rank-gauss)를 같은 블록·시드로 다시 배운다. 반환 {seed: {"base", "plus"}}.
    `feature` 에 `x_*`·`m_*` 열이 있으면 같이 넣는다(`EXTRA_PREFIXES`)."""
    from tools.trial_pooled_rank import rank_gauss

    panel, sessions = score_panel(ctx)
    y5 = ctx.y5()
    panel = panel.merge(y5.rename(columns={"target": "r5"}), on=["entity_id", "session"], how="left")
    panel["y"] = panel["r5"]
    panel = rank_gauss(panel, ["y"])
    panel["y"] = panel["y"].where(panel["r5"].notna())          # rank_gauss 는 결측을 0 으로 채운다 — 정답은 결측 그대로
    extra = [c for c in feature.columns if c.startswith(EXTRA_PREFIXES)]
    feat = feature.rename(columns={"pred": "cand"})[["entity_id", "session", "cand", *extra]]
    n_rows = len(panel)
    base = _base_walk(panel, sessions, seeds, cache=cache_base)
    panel = panel.merge(feat, on=["entity_id", "session"], how="left")
    assert len(panel) == n_rows, "재료 파일에 (종목, 세션) 중복이 있다"
    xs = ["cand", *[c for c in extra if c.startswith("x_")]]
    panel = rank_gauss(panel, xs)
    ms = [c for c in extra if c.startswith("m_")]
    for c in ms:
        panel[c] = panel[c].fillna(1.0).astype(np.float32)
    plus = walk_kr(panel, sessions, [*SCORE_FEATS, *xs, *ms], "y", seeds, label="C0+재료")
    return {s: {"base": base[s], "plus": plus[s]} for s in base}


# --------------------------------------------------------------------------- 사다리


def mark(ok: bool | str | None) -> str:
    if isinstance(ok, str):
        return ok
    return "?" if ok is None else ("O" if ok else "X")


def _p_two(t: float) -> float:
    return float(2 * (1 - norm.cdf(abs(t)))) if np.isfinite(t) else float("nan")


class _Subset(Mapping[int, pd.DataFrame]):
    """Mapping 의 일부 키만 — 값은 꺼낼 때 읽는다(LazyScores 를 통째로 들지 않는다)."""

    def __init__(self, base: Mapping[int, pd.DataFrame], keys: Sequence[int]) -> None:
        self.base, self.keys_ = base, list(keys)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.keys_)

    def __len__(self) -> int:
        return len(self.keys_)

    def __getitem__(self, k: int) -> pd.DataFrame:
        return self.base[k]


def ladder(preds: Mapping[int, pd.DataFrame], ctx: Context, *, control: Mapping[int, pd.DataFrame] | None = None,
           thresholds: Thresholds = Thresholds(), n_tests: int = 1, plus_base: Mapping[int, Mapping[str, pd.DataFrame]] | None = None,
           y5: pd.DataFrame | None = None) -> dict[str, Any]:
    """세 칸 판정표. control 이 있으면 칸 ② 는 짝 ΔIC(점수 − 대조), plus_base 가 있으면 재료 모드(재학습 짝).

    재료 모드에서는 칸 ① 이 재료 자체, 칸 ② 가 재학습 짝 ΔIC, 칸 ③ 이 **재학습한 랭커(C0+재료)의 포트 대 C0 재학습 포트**다.
    """
    if plus_base is not None:
        port_preds = {s: v["plus"] for s, v in plus_base.items()}
        port_control: Mapping[int, pd.DataFrame] | None = {s: v["base"] for s, v in plus_base.items()}
    else:
        port_preds, port_control = preds, control
    th = thresholds
    preds = {s: as_wide(p, ctx) for s, p in preds.items()}          # 시드마다 한 번 읽어 넓은 표로(작다)
    if control is not None:
        control = {s: as_wide(p, ctx) for s, p in control.items()}
    if plus_base is None:
        port_preds, port_control = preds, control
    res: dict[str, Any] = {"thresholds": th.__dict__, "n_tests": int(n_tests), "checks": {}}
    ics = ic_frame(preds, ctx)
    ic_mean = ics.mean(axis=1).dropna()
    dec = pd.DataFrame({s: (lambda d: d["d10"] - d["d1"])(decile_frame(p, ctx)) for s, p in preds.items()}).mean(axis=1)
    share, years = yearly_sign_share(ic_mean)
    ic_d, dec_d = describe(ic_mean, ctx, scale=1), describe(dec, ctx)
    c = res["checks"]
    c["①a IC>0·t≥t*"] = judge(ic_d["ann"] > 0 and ic_d["t"] >= th.t_star, ic_d, min_t=th.ex_t,
                              text=f"IC {ic_d['ann']:+.4f} · t {ic_d['t']:+.2f}")
    c["①b D10−D1 t≥t*"] = judge(dec_d["ann"] > 0 and dec_d["t"] >= th.t_star, dec_d, min_t=th.ex_t,
                                text=f"연 {dec_d['ann']:+.1%} · t {dec_d['t']:+.2f}")
    c["①c 연도 부호"] = (share >= th.sign_share, f"양수 {share:.0%} · " + " ".join(f"{y}:{v:+.3f}" for y, v in years.items()))
    res["principle"] = {"ic": ic_d, "d10_d1": dec_d, "years": years,
                        "ic_seed": {int(s): float(ics[s].mean()) for s in ics.columns}}
    # ② 한계기여
    if plus_base is not None:
        dic = pd.DataFrame({s: ic_frame({0: v["plus"]}, ctx)[0] - ic_frame({0: v["base"]}, ctx)[0]
                            for s, v in plus_base.items()})
        kind = "재학습(C0+재료 − C0)"
    elif control is not None:
        common = sorted(set(preds) & set(control))
        cic = ic_frame(_Subset(control, common), ctx)
        dic = pd.DataFrame({s: ics[s] - cic[s] for s in common})
        kind = "짝 ΔIC(점수 − 대조)"
    else:
        dic = None
        kind = "대조 없음"
    if dic is not None:
        dm = dic.mean(axis=1).dropna()
        dd = describe(dm, ctx, scale=1)
        pos = int((dic.mean(axis=0) > 0).sum())
        c["②a ΔIC>0·t≥t*"] = judge(dd["ann"] > 0 and dd["t"] >= th.t_star, dd, min_t=th.ex_t,
                                   text=f"ΔIC {dd['ann']:+.4f} · t {dd['t']:+.2f}")
        c["②b 시드 일관"] = (pos >= min(th.seeds_need, dic.shape[1]), f"{pos}/{dic.shape[1]}")
        res["marginal"] = {"kind": kind, "dic": dd, "seed_pos": pos, "dic_seed": {int(s): float(dic[s].mean()) for s in dic.columns}}
    else:
        c["②a ΔIC>0·t≥t*"] = (None, kind)
        c["②b 시드 일관"] = (None, kind)
        res["marginal"] = {"kind": kind}
    # ③ 포트
    per_seed = {s: book(p, ctx) for s, p in port_preds.items()}
    ports = {s: port_summary(net, turn, ctx) for s, (net, turn) in per_seed.items()}
    bnet, bturn = book(bundle(port_preds, ctx), ctx) if len(port_preds) > 1 else per_seed[next(iter(per_seed))]
    bp = port_summary(bnet, bturn, ctx)
    pos_k = sum(1 for v in ports.values() if v["vs_k200"]["ann"] > 0)
    c["③a 대 K200>0"] = judge(bp["vs_k200"]["ann"] > 0 and bp["vs_k200"]["t"] >= th.port_t, bp["vs_k200"], min_t=th.port_t,
                              text=f"묶음 연 {bp['vs_k200']['ann']:+.1%} · t {bp['vs_k200']['t']:+.2f}")
    c["③b 대 U EW>0"] = judge(bp["vs_uni"]["ann"] > 0 and bp["vs_uni"]["t"] >= th.port_t, bp["vs_uni"], min_t=th.port_t,
                              text=f"묶음 연 {bp['vs_uni']['ann']:+.1%} · t {bp['vs_uni']['t']:+.2f}")
    c["③c 시드(대 K200)"] = (pos_k >= min(th.seeds_need, len(ports)), f"{pos_k}/{len(ports)} · 범위 "
                           f"{min(v['vs_k200']['ann'] for v in ports.values()):+.1%}~{max(v['vs_k200']['ann'] for v in ports.values()):+.1%}")
    c["③d MDD"] = (bp["mdd"] >= bp["k200_mdd"] - th.mdd_margin, f"{bp['mdd']:+.1%} 대 K200 {bp['k200_mdd']:+.1%}")
    res["port"] = {"bundle": bp, "seeds": ports}
    if port_control is not None:
        cn, ct = book(bundle(port_control, ctx), ctx) if len(port_control) > 1 else book(next(iter(port_control.values())), ctx)
        cp = port_summary(cn, ct, ctx)
        idx = bnet.index.intersection(cn.index)
        diff = describe(bnet.reindex(idx) - cn.reindex(idx), ctx)
        c["③e 회전"] = (bp["turn"] <= cp["turn"] * th.turn_ratio, f"{bp['turn']:.1f} 대 대조 {cp['turn']:.1f}")
        res["port"]["control_bundle"] = cp
        res["port"]["vs_control"] = diff
    else:
        c["③e 회전"] = (None, f"{bp['turn']:.1f}(대조 없음)")
    # 첫 탈락 칸
    first = None
    for rung in ("①", "②", "③"):
        if any(v[0] is False or v[0] == "S" for k, v in c.items() if k.startswith(rung)):
            first = rung
            break
    res["first_fail"] = first
    res["special_flags"] = [k for k, v in c.items() if v[0] == "S"]
    res["bonferroni"] = {"ic_p": _p_two(ic_d["t"]), "ic_p_adj": min(1.0, _p_two(ic_d["t"]) * n_tests),
                         "dic_p_adj": (min(1.0, _p_two(res["marginal"]["dic"]["t"]) * n_tests)
                                       if "dic" in res["marginal"] else None)}
    return res


def render(name: str, res: dict[str, Any]) -> str:
    lines = [f"### 사다리 — {name}", "", "| 칸 | 결과 | 숫자 |", "|---|:---:|---|"]
    for k, (ok, text) in res["checks"].items():
        lines.append(f"| {k} | {mark(ok)} | {text} |")
    ff = res["first_fail"]
    lines.append("")
    lines.append("O 통과 · X 탈락 · S 전체로는 통과하나 특수 구간(코로나·2025~26 급등장·극단일 셋)을 빼면 무너짐"
                 "(능력이지만 특수 — 탈락으로 센다) · ? 판정 불가")
    lines.append(f"**첫 탈락 칸: {ff or '없음(세 칸 통과)'}** · 다중검정 {res['n_tests']}회 · IC p(보정) {res['bonferroni']['ic_p_adj']:.2g}")
    return "\n".join(lines)


def render_loss(name: str, lt: dict[str, Any]) -> str:
    regs = list(REGIMES)
    head = "| 단계 | 전체(시드 평균) | NW t | 특수 뺀 쪽 (t) | 특수만 (t) | " + " | ".join(regs) + " | 시드 범위 | 묶음 |"
    lines = [f"### 손실표 — {name} ({lt['window'][0]} ~ {lt['window'][1]})", "", head, "|" + "---|" * (7 + len(regs))]
    for key, row in lt["rows"].items():
        sm = row["seed_mean"]
        fmt = (lambda v: f"{v:+.4f}") if key == "ic" else (lambda v: f"{v:+.1%}")
        reg = " | ".join(fmt(sm["regimes"].get(r, float("nan"))) for r in regs)
        bun = fmt(row["bundle"]["ann"]) if row["bundle"] else "—"
        ex, so = sm["ex_special"], sm["special_only"]
        lines.append(f"| {row['label']} | {fmt(sm['ann'])} | {sm['t']:+.2f} | {fmt(ex['ann'])} ({ex['t']:+.2f}) | "
                     f"{fmt(so['ann'])} ({so['t']:+.2f}) | {reg} | {fmt(row['seed_range'][0])}~{fmt(row['seed_range'][1])} | {bun} |")
    bp = lt["bundle_port"] or next(iter(lt["ports"].values()))
    lines.append("")
    lines.append(f"포트(묶음): 비용 후 연 {bp['ann']:+.1%} · 비용 전 {bp['gross_ann']:+.1%} · 회전 {bp['turn']:.1f}/년 · 비용 {bp['cost']:.1%}/년 · "
                 f"MDD {bp['mdd']:+.1%}(K200 {bp['k200_mdd']:+.1%}, +V6 {bp['v6_mdd']:+.1%})")
    return "\n".join(lines)


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def record(name: str, res: dict[str, Any], *, ledger: Path = LEDGER, out_dir: Path = OUT) -> Path:
    """결과 JSON + 원장 한 줄(다중검정 수 추적). 창고에는 쓰지 않는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"result-{name}.json"
    path.write_text(json.dumps(_jsonable(res), ensure_ascii=False, indent=1))
    stamp = datetime.now(KST).isoformat(timespec="seconds")  # invariant-allow: wallclock — 원장 기록 시각
    with ledger.open("a", encoding="utf-8") as fh:  # invariant-allow: data-access — 연구 원장(창고 아님)
        fh.write(json.dumps({"at": stamp, "name": name, "n_tests": res.get("n_tests"), "first_fail": res.get("first_fail")},
                            ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- 실행 창 관문


def heavy_allowed(now: datetime | None = None) -> tuple[bool, str]:
    """무거운 계산 금지 창 — 22:20~00:45(매일), 평일 08:20~15:50. 공유 머신 규칙(2026-10-13)."""
    k = (now or datetime.now(KST)).astimezone(KST)  # invariant-allow: wallclock — 실행 창 관문
    t = k.time()
    if t >= time(22, 20) or t < time(0, 45):
        return False, "22:20~00:45 금지 창"
    if k.weekday() < 5 and time(8, 20) <= t < time(15, 50):
        return False, "평일 08:20~15:50 금지 창"
    return True, "허용"


def _mem_available_gb() -> float:
    with open("/proc/meminfo") as fh:  # invariant-allow: data-access — /proc, 창고 아님
        for line in fh:
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024 / 1024
    return float("nan")


def require_heavy(force: bool = False) -> None:
    ok, why = heavy_allowed()
    if not ok and not force:
        raise SystemExit(f"무거운 계산 거부 — {why}")
    gb = _mem_available_gb()
    if gb < 5.0 and not force:
        raise SystemExit(f"무거운 계산 거부 — 가용 메모리 {gb:.1f}GB < 5GB")


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    cx = sub.add_parser("context", help="공통 자료 굽기(무거움)")
    cx.add_argument("--root", default="data")
    cx.add_argument("--force", action="store_true")
    cx.add_argument("--part", choices=("market", "caps", "sectors"), required=True,
                    help="조각마다 따로 돈다(프로세스를 나눠 4GB 상한 안에 둔다)")
    rn = sub.add_parser("run", help="사다리 판정표")
    rn.add_argument("--name", required=True)
    rn.add_argument("--score", required=True)
    rn.add_argument("--control", default="C0")
    rn.add_argument("--no-control", action="store_true")
    rn.add_argument("--mode", choices=("model", "feature"), default="model")
    rn.add_argument("--t-star", type=float, default=Thresholds.t_star)
    rn.add_argument("--n-tests", type=int, default=1, help="이 질문에서 지금까지(이번 포함) 본 변형 수")
    rn.add_argument("--start", type=date.fromisoformat, default=None)
    rn.add_argument("--end", type=date.fromisoformat, default=None)
    rn.add_argument("--loss", action="store_true", help="손실표도 낸다")
    rn.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "context":
        require_heavy(args.force)
        from quant_rl_trading.store import Store

        save_context_part(Store(root=Path(args.root)), args.part)
        print(f"공통 자료 {CONTEXT_FILE} · 조각 {args.part} 저장", flush=True)
        return 0
    if args.end is not None and args.end > DATE_CAP:
        raise CapViolation(f"--end {args.end} > {DATE_CAP}")
    ctx = load_context()
    preds = restrict(read_scores(args.score), args.start, args.end)
    control = None if args.no_control else restrict(read_scores(args.control), args.start, args.end)
    plus_base = None
    if args.mode == "feature":
        require_heavy(args.force)
        feat = read_feature_frame(args.score)
        feat = feat[[(args.start is None or d >= args.start) and (args.end is None or d <= args.end) for d in feat["session"]]]
        plus_base = feature_marginal(feat, ctx)
        control = None
        preds = {0: feat[["entity_id", "session", "pred"]]}
    res = ladder(preds, ctx, control=control, thresholds=Thresholds(t_star=args.t_star), n_tests=args.n_tests,
                 plus_base=plus_base)
    print(render(args.name, res), flush=True)
    if args.loss:
        lt = loss_table(preds, ctx)
        res["loss"] = lt
        print("\n" + render_loss(args.name, lt), flush=True)
    print(f"\n결과 {record(args.name, res)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
