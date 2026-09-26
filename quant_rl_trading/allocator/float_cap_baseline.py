"""유동시총 가중 + 종목 상한 — Z2 트랙의 비중(docs/design/portfolio-construction.md "Z2 트랙").

시행 Z 의 Z2(K200 구성종목 · 유동시총 가중 · 상한 10%)를 실전 파이프라인에 옮긴 것. 후보는 selector 가 이미 골랐다(랭커 상위 24,
완충) — 여기서는 **얼마씩 드나**만 정한다: 시가총액 × 유동비율에 비례, 한 종목 상한을 넘친 몫은 나머지에 비례해 다시 나눈다.
합은 ``1 − cash_buffer`` 다(다른 룰 베이스라인과 같다). 노출 배수·사이징은 호출부가 그대로 건다.

**점수는 안 쓴다.** 점수는 누구를 드나(선정)에만 쓰였다 — 시행 Z 의 Z2 가 그랬다. 점수 부호로 후보를 빼지도 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.store.errors import ConfigNotFound

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

logger = logging.getLogger(__name__)

MARKET_STATS = "market_stats"
FLOAT_RATIO = "float_ratio"
#: 시총 스냅샷을 찾는 창(달력일). 시총은 매일 들어오므로 짧게.
CAP_LOOKBACK_DAYS = 20
#: 유동비율은 드물게 바뀐다.
FLOAT_LOOKBACK_DAYS = 400
#: 시총을 아는 후보가 이보다 적으면 동일가중으로 물러선다(경로를 driver 로 남긴다).
MIN_CAPPED = 5
#: 시총 커버리지 하한을 못 읽을 때의 값. 정상 경로는 config `allocator.float_cap_min_coverage`(불변식 10).
DEFAULT_MIN_COVERAGE = 0.8


def capped_with_residual(weights: pd.Series, limit: float) -> tuple[pd.Series, float]:
    """(상한을 씌운 비중, 못 나눈 몫). 넘친 몫은 나머지에 비례해 다시 나눈다 — 수렴할 때까지.

    **종목 수 × 상한 < 1 이면 전부 상한에 붙어 합이 1 에 못 미친다.** 예전에는 그 몫을 조용히 버려
    합이 예산보다 작아졌다(2026-09-26 점검). 버린 사실을 두 번째 값으로 내보내 호출부가 드러내게 한다.
    """
    w = weights / weights.sum()
    for _ in range(50):
        over = w > limit + 1e-12
        if not over.any():
            break
        excess = float((w[over] - limit).sum())
        w[over] = limit
        free = ~over & (w < limit)
        if not free.any():
            break
        w[free] += excess * w[free] / float(w[free].sum())
    return w, max(0.0, 1.0 - float(w.sum()))


def capped(weights: pd.Series, limit: float) -> pd.Series:
    """``capped_with_residual`` 의 비중만. 못 나눈 몫을 볼 필요가 없는 곳에서 쓴다."""
    return capped_with_residual(weights, limit)[0]


def _min_coverage(store: Store, *, as_of: datetime) -> float:
    try:
        # 이름은 리터럴로 적는다 — tests/allocator/test_cache_config_scope.py 가 소스를 훑어 RL 캐시 지문을 강제한다.
        return float(store.config("allocator.float_cap_min_coverage", as_of=as_of))
    except (ConfigNotFound, LookupError, TypeError, ValueError):
        logger.warning("allocator.float_cap_min_coverage 가 창고 config 에 없다 — 기본 %.2f 로 본다"
                       " (seed_config_defaults 필요)", DEFAULT_MIN_COVERAGE)
        return DEFAULT_MIN_COVERAGE


def _one_per_company(cap: pd.Series) -> pd.Series:
    """같은 회사의 여러 클래스(같은 시총 · 티커 접두 관계)에서 하나만 남긴다."""
    drop: set[str] = set()
    for _, group in cap.groupby(cap.values):
        names = list(group.index)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ta, tb = str(a).partition(":")[2], str(b).partition(":")[2]
                if ta and tb and (tb.startswith(ta) or ta.startswith(tb)):
                    drop.add(b if len(tb) >= len(ta) else a)
    return cap.drop(index=sorted(drop))


def allocate_float_cap(
    store: Store, *, as_of: datetime, market: str, candidates: Sequence[str], limit: float, cash_buffer: float,
) -> tuple[dict[str, float], str]:
    """(목표 비중, 경로).

    경로는 ``float_cap`` 또는 ``float_cap:equal_fallback:coverage=0.33`` 처럼 **왜 그 길로 갔는지**까지 적는다.
    시총을 모르는 후보를 반환에서 빼면 목표 0 = 전량 매도가 되므로, **후보는 언제나 전원 반환한다**(2026-09-26 점검).
    """
    names = list(dict.fromkeys(candidates))
    if not names:
        return {}, "float_cap"
    min_coverage = _min_coverage(store, as_of=as_of)
    caps = store.get(MARKET_STATS, as_of=as_of, lookback=CAP_LOOKBACK_DAYS, until=as_of, market=market,
                     entity=names, columns=["entity_id", "metric", "value", "valid_from"])
    caps = caps[caps["metric"] == "market_cap"] if not caps.empty else caps
    cap = (caps.sort_values("valid_from").groupby("entity_id")["value"].last().astype(float)
           if not caps.empty else pd.Series(dtype=float))
    cap = cap[cap > 0]
    # **같은 회사의 두 클래스는 하나로 센다.** 미장 시총은 회사 합계 주식수로 만들어 GOOG·GOOGL 이 **똑같은** 회사 시총을
    # 받는다(us_shares "못 하는 것"). 둘 다 두면 알파벳이 두 번 들어가 11.7%(SPY 약 7%)가 됐다(2026-09-25 G1 트랙 시험).
    # 시총이 정확히 같고 **티커가 한쪽의 앞부분인** 종목(GOOG ⊂ GOOGL)만 한 회사로 보고 이름순 첫 하나를 남긴다.
    known = cap.sort_index()
    cap = _one_per_company(known)
    # 한 회사로 접혀 빠진 이름은 **되살리지 않는다** — 아래 중앙값 보충과 섞이면 알파벳이 또 두 번 들어간다.
    folded = set(known.index) - set(cap.index)
    coverage = len(known) / len(names)
    budget = 1.0 - cash_buffer
    # **커버리지가 얕으면 동일가중.** 시총을 아는 몇 종목에만 예산을 다 실으면 나머지는 목표 0 이 되어
    # 이유 없는 전량 매도가 난다(Z2 에서 KRX 시총 보충이 실패한 날의 모양). 절대 하한(MIN_CAPPED)과
    # 비율 하한(config) 둘 다 본다 — 후보가 6 이면 5종목도 비율로는 충분하고, 후보가 450 이면 5 는 턱없다.
    if len(cap) < MIN_CAPPED or coverage < min_coverage:
        return ({e: budget / len(names) for e in names},
                f"float_cap:equal_fallback:coverage={coverage:.2f}")
    # 커버리지가 충분하면 **시총을 모르는 후보에는 아는 종목의 중앙값 시총**을 준다 — 유동비율 결측과 같은 규칙.
    # 빼면 그 종목만 조용히 팔리고, 0 으로 두면 같은 결과다.
    missing = [n for n in names if n not in cap.index and n not in folded]
    if missing:
        cap = pd.concat([cap, pd.Series({n: float(cap.median()) for n in missing})])
    ratio = store.get(FLOAT_RATIO, as_of=as_of, lookback=FLOAT_LOOKBACK_DAYS, entity=list(cap.index),
                      columns=["entity_id", "float_ratio", "observed_at"])
    fr = (ratio.sort_values("observed_at").groupby("entity_id")["float_ratio"].last().astype(float)
          if not ratio.empty else pd.Series(dtype=float))
    # 유동비율을 모르는 종목은 **아는 종목의 중앙값**으로 — 시행 Z 와 같은 규칙. 0 으로 두면 그 종목을 안 사는 것이 되고,
    # 1 로 두면 대주주 지분이 큰 종목을 과대 가중한다.
    fr = fr.reindex(cap.index).fillna(float(fr.median()) if not fr.empty else 1.0).clip(lower=0.01, upper=1.0)
    shares, residual = capped_with_residual(cap * fr, limit)
    w = shares * budget
    path = "float_cap"
    if missing:
        path += f":median_cap={len(missing)}"
    # 종목 수 × 상한 < 1 이면 예산을 다 못 쓴다. 노출이 낮은 이유가 여기 있다는 것을 driver 로 드러낸다.
    if residual > 1e-9:
        path += f":residual={residual:.4f}"
    return {str(k): float(v) for k, v in w.items()}, path
