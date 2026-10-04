"""노출 지표(regime)의 평가 — IC 가 아니라 **노출 기여**로 잰다(modelops-ranker.md ①, 2026-10-04 사용자 승인).

regime 의 쓸모는 시장 수준 상태(국면 → V6 노출 배수)다. 횡단면 IC 로 채점하면 매주 "미통과" 가 찍혀 국면 판정까지 실패처럼
읽힌다. 여기서는 최근 N세션(`modelops.exposure_eval_sessions`) 동안 **실전 노출 배수**를 지수 일수익에 씌운 '노출 적용 지수'와
'지수 100%' 를 견준다. 표시만 한다 — 임계치도 판정도 없다.

- 배수: 장부(`events` 의 exposure 단계 기록)가 우선이다. 기록이 없는 세션은 **실전 함수 그대로** 다시 계산한다
  (`RegimeAnalyst.state` → `exposure.decide`, 그 시점 설정·확인 기간·데드밴드). 몇 세션이 기록이고 몇 세션이 재계산인지 같이 돌려준다.
- 정렬: 장부의 세션 d 기록은 **d−1 종가까지** 보고(08:40 세션) d 아침에 집행된다 — d 종가 → d+1 종가 수익을 번다. 연구 kit·하락장 검진
  (`tools/diag_bear_2022.py`)의 'd−1 결정 → d→d+1 수익' 과 같은 정렬이다. 재계산도 같은 정보 시점(d−1 세션 공표 시각)으로 한다.
- 비용: 배수 변화만큼 지수를 사고판다고 보고 `accounting.fee_kr`(양방향) + 줄일 때 `accounting.transaction_tax_kr` 를 뺀다(설정에서 읽는다).
- 지수: `benchmark.kr_index`(실전 노출이 보는 지수와 같은 설정). KODEX200 은 창고에 2026-08 부터라 창 대부분을 못 덮는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.collectors.publication import publication_policy
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store

SESSIONS_KEY = "modelops.exposure_eval_sessions"
EVENTS = "events"
STAGE = "exposure"


def recorded_scales(ledger: Store, *, as_of: datetime, lookback: int) -> dict[Any, float]:
    """장부의 노출 결정 기록 — 세션 날짜 → 배수. 같은 날 여러 번이면 마지막 기록."""
    frame = ledger.get(EVENTS, as_of=as_of, lookback=lookback, columns=["valid_from", "observed_at", "stage", "payload"])
    if frame.empty:
        return {}
    frame = frame[frame["stage"] == STAGE].sort_values(["valid_from", "observed_at"])
    out: dict[Any, float] = {}
    for row in frame.to_dict(orient="records"):
        payload = row["payload"]
        try:
            data = json.loads(payload) if isinstance(payload, str) else dict(payload)
            out[pd.Timestamp(row["valid_from"]).date()] = float(data["scale"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


class _IndexView:
    """지수만 미리 읽어 두고 창고와 같은 필터(`observed_at <= as_of` · `valid_from` 창 · 정정본 최신)로 답하는 얇은 창고.

    세션마다 실전 함수(`RegimeAnalyst.state`·`exposure.decide`)가 400일 지수를 다시 읽으면 120세션에 30초 가까이 걸린다
    (화면 요청 하나로는 못 쓴다). 함수는 그대로 두고 읽는 곳만 바꾼다 — `config` 는 진짜 창고로 넘긴다(불변식 10).
    """

    def __init__(self, store: Store, frame: pd.DataFrame) -> None:
        self._store = store
        self._frame = frame

    def config(self, key: str, *, as_of: datetime) -> Any:
        return self._store.config(key, as_of=as_of)

    def get(self, table: str, *, as_of: datetime, entity: Any = None, lookback: Any = None,
            columns: Any = None, **_kwargs: Any) -> pd.DataFrame:
        if table != "indices":
            return self._store.get(table, as_of=as_of, entity=entity, lookback=lookback, columns=columns, **_kwargs)
        out = self._frame[self._frame["observed_at"] <= as_of]
        if entity is not None:
            wanted = {entity} if isinstance(entity, str) else set(entity)
            out = out[out["entity_id"].isin(wanted)]
        if lookback is not None:
            floor = datetime.combine(as_of.astimezone(UTC).date() - timedelta(days=int(lookback)), time.min, tzinfo=UTC)
            out = out[out["valid_from"] >= floor]
        out = out.sort_values(["valid_from", "revision"]).groupby(["entity_id", "valid_from"], as_index=False).tail(1)
        return out if columns is None else out[[c for c in dict.fromkeys(["entity_id", "valid_from", *columns]) if c in out.columns]]


def recompute_scales(store: Store, days: list[Any], *, as_of: datetime, start_held: float | None) -> dict[Any, float]:
    """기록이 없는 세션의 배수 — `session/daily.run` 의 노출 단계와 같은 함수·같은 설정 시점."""
    from quant_rl_trading.analysts.regime import LOOKBACK_DAYS, MARKET_PROXIES, RegimeAnalyst
    from quant_rl_trading.selector import exposure

    if not days:
        return {}
    policy = publication_policy(store, Market.KR, clock=ReplayClock(as_of))
    entities = sorted({*MARKET_PROXIES, str(store.config("benchmark.kr_index", as_of=as_of))})
    reach = max(LOOKBACK_DAYS, exposure.INDEX_LOOKBACK_DAYS) + (as_of.date() - days[0]).days + 10
    frame = store.get("indices", as_of=as_of, entity=entities, market="KR", lookback=reach,
                      columns=["entity_id", "valid_from", "observed_at", "close", "revision"])
    view = _IndexView(store, frame)
    out: dict[Any, float] = {}
    held = start_held
    calendar = trading_days(Market.KR, days[0] - timedelta(days=14), days[-1])
    previous = {d: calendar[i - 1] for i, d in enumerate(calendar) if i > 0}
    for day in days:
        # 모의계좌 08:40 세션과 같은 시점들 — 설정은 그 세션(d)의 것을, 지수는 **d−1 종가까지** 본다.
        moment, seen = policy.for_session(day), policy.for_session(previous[day])
        params = exposure.ExposureParams.from_store(store, as_of=moment)
        index_id = str(store.config("benchmark.kr_index", as_of=moment))
        regime = RegimeAnalyst(view, ReplayClock(seen))  # type: ignore[arg-type]
        state = regime.state(seen)
        # 확인 창은 실전과 같은 함수 — 관측된 종가 세션 축(`RegimeAnalyst.recent_states`, 2026-10-04 결함 수정).
        # 장부 기록이 있는 세션(모의계좌, 2026-08-26~)은 기록이 우선이라 고치기 전 실제 결정이 그대로 쓰인다.
        confirm = int(params.regime_confirm_sessions)
        decision = exposure.decide(
            view,  # type: ignore[arg-type]
            as_of=seen, index_id=index_id, regime_state=state, params=params,
            recent_regime_states=list(regime.recent_states(seen, confirm - 1)) if confirm > 1 else [],
            held=held if params.deadband > 0.0 else None,
        )
        out[day] = float(decision.scale)
        held = out[day]
    return out


def _mdd(r: pd.Series) -> float:
    nav = (1.0 + r).cumprod()
    return float((nav / nav.cummax() - 1.0).min()) if len(nav) else float("nan")


def _down_beta(r: pd.Series, b: pd.Series) -> float:
    mask = b < 0
    x, y = b[mask], r[mask]
    return float(np.cov(y, x)[0, 1] / x.var()) if len(x) > 2 and x.var() > 0 else float("nan")


def exposure_effect(store: Store, *, as_of: datetime, ledger: Store | None = None,
                    sessions: int | None = None) -> dict[str, Any]:
    """최근 N세션 '노출 적용 지수' 대 '지수 100%'. ``ledger`` 는 노출 기록을 읽을 장부(기본 ``store``)."""
    ledger = ledger or store
    n = int(sessions if sessions is not None else store.config(SESSIONS_KEY, as_of=as_of))
    index_id = str(store.config("benchmark.kr_index", as_of=as_of))
    fee = float(store.config("accounting.fee_kr", as_of=as_of))
    tax = float(store.config("accounting.transaction_tax_kr", as_of=as_of))
    span = int(n * 7 / 5) + 40
    frame = store.get("indices", as_of=as_of, entity=[index_id], market="KR", lookback=span,
                      columns=["entity_id", "valid_from", "close", "revision"])
    if frame.empty:
        return {"sessions": 0, "index": index_id, "reason": f"{index_id} 종가가 없다"}
    closes = (frame.sort_values(["valid_from", "revision"]).assign(day=lambda f: f["valid_from"].dt.date)
              .groupby("day")["close"].last().astype(float))
    calendar = [d for d in trading_days(Market.KR, closes.index[0], closes.index[-1]) if d in closes.index]
    # 세션 d 는 d+1 종가까지 알려져 있어야 채점된다(d 종가 → d+1 종가).
    decisions = calendar[1:-1][-n:]
    if not decisions:
        return {"sessions": 0, "index": index_id, "reason": "채점할 세션이 없다"}
    pos = {d: i for i, d in enumerate(calendar)}
    bench = pd.Series({d: closes[calendar[pos[d] + 1]] / closes[d] - 1.0 for d in decisions})

    recorded = recorded_scales(ledger, as_of=as_of, lookback=span)
    missing = [d for d in decisions if d not in recorded]
    recomputed = recompute_scales(store, missing, as_of=as_of, start_held=None) if missing else {}
    scale = pd.Series({d: recorded.get(d, recomputed.get(d, 1.0)) for d in decisions})

    prev = scale.shift(1).fillna(scale.iloc[0])
    delta = scale - prev
    cost = delta.abs() * fee + (-delta).clip(lower=0.0) * tax
    applied = scale * bench - cost
    cum_a, cum_b = float((1 + applied).prod() - 1), float((1 + bench).prod() - 1)
    return {
        "sessions": len(decisions), "index": index_id,
        "start": decisions[0].isoformat(), "end": decisions[-1].isoformat(),
        "recorded": len(decisions) - len(missing), "recomputed": len(missing),
        "avg_scale": float(scale.mean()), "below_one_share": float((scale < 1.0).mean()),
        "switches": int((delta.abs() > 1e-12).sum()),
        "cum_applied": cum_a, "cum_index": cum_b, "cum_diff": cum_a - cum_b,
        "mdd_applied": _mdd(applied), "mdd_index": _mdd(bench),
        "down_beta": _down_beta(applied, bench), "cost_total": float(cost.sum()),
    }


def effect_line(result: dict[str, Any]) -> str:
    """주간 IC 로그·화면이 같이 쓰는 한 줄."""
    if not result.get("sessions"):
        return f"regime [대상 아님 — 노출 지표] 노출 기여: 계산 못 함 ({result.get('reason', '자료 없음')})"
    r = result
    return (
        f"regime [대상 아님 — 노출 지표] 최근 {r['sessions']}세션({r['start']}~{r['end']}) 노출 적용 {r['index'].split(':')[-1]} "
        f"{r['cum_applied']:+.2%} 대 100% {r['cum_index']:+.2%} (차 {r['cum_diff']:+.2%}p) · "
        f"MDD {r['mdd_applied']:+.2%} 대 {r['mdd_index']:+.2%} · 하방β {r['down_beta']:.2f} · "
        f"평균 배수 {r['avg_scale']:.0%} · 기록 {r['recorded']}/재계산 {r['recomputed']}"
    )


__all__ = ["SESSIONS_KEY", "effect_line", "exposure_effect", "recompute_scales", "recorded_scales"]
