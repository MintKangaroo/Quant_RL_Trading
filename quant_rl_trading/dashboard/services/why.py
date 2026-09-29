"""'이 종목은 왜 샀나 / 왜 안 샀나' — 트레이딩 탭 AI 결정 패널의 다섯 단계.

    ① 점수   ② 걸러짐   ③ 선정 규칙   ④ 비중   ⑤ 오늘 주문

## 화면이 결정을 다시 내리지 않는다 (불변식 5)

선정 규칙을 화면이 따로 짜면 세션이 실제로 한 일과 어긋난다. 그래서 두 곳에서만 읽는다.

- **세션 기록** — `events` 표의 그 세션(run_id ``session-{시장}-{날짜}``) 행들. ``select`` 는 실제로 뽑힌 후보,
  ``allocate`` 는 노출 전 목표 비중과 재조정 주기, ``exposure`` 는 노출 배수, ``observe`` 는 그날 자본.
- **같은 선정 함수** — ``selector.pipeline.screen``. 세션이 부른 그 함수를 **그 세션 as_of·그 세션 자본**으로 다시
  부른다. 창고는 append-only 이고 ``store.get(as_of=...)`` 는 그 시각까지 관측된 것만 주므로 같은 입력 → 같은 결과다.
  상관 감점·섹터 상한·상위 N(4~6단계)은 부르지 않는다 — 비싸고 보유 목록이 필요하다. 그 결과는 ``select`` 기록이 말한다.

값이 없으면 ``None`` 이고 화면은 "모름" 이라 적는다. 0 이나 짐작으로 채우지 않는다.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime
from threading import Lock
from typing import Any

import pandas as pd

from quant_rl_trading.selector import cadence as cadence_module
from quant_rl_trading.selector import constraints as constraints_module
from quant_rl_trading.selector import exposure as exposure_module
from quant_rl_trading.selector.candidates import market_config
from quant_rl_trading.store import ConfigNotFound, Store

EVENTS = "events"
KST = "Asia/Seoul"
#: 마지막 세션 기록을 찾는 창(달력일) — 연휴를 넘긴다. 노출 배수의 직전 값 창과 같은 값이다.
SESSION_LOOKBACK_DAYS = exposure_module.HELD_LOOKBACK_DAYS

#: ``screen`` 결과 캐시 — (창고, 시장, 세션, 자본) 마다 한 벌. 같은 세션을 다시 부르면 같은 답이다(위 독스트링).
#: 한 번에 ~1.5초라 종목을 누를 때마다 부르면 화면이 굼뜨다. 들고 있는 것은 종목별 숫자 몇 개뿐이다.
_SCREEN_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_SCREEN_CACHE_SIZE = 4
_SCREEN_LOCK = Lock()
#: 위험 점수 분포 막대의 칸 수 — **표시용**(판정에 안 쓴다). 좁은 패널(~380px)에서 칸당 ~12px.
RISK_HIST_BINS = 24


# -- 세션 기록 -------------------------------------------------------------------


def session_record(store: Store, *, as_of: datetime, market: str) -> dict[str, Any] | None:
    """as_of 에 보이는 마지막 세션의 기록. ``{"run_id", "as_of", "stages": {stage: {"actor", "payload"}}}``."""
    frame = store.get(
        EVENTS, as_of=as_of, lookback=SESSION_LOOKBACK_DAYS,
        columns=["entity_id", "valid_from", "observed_at", "seq", "stage", "actor", "payload"],
    )
    if frame.empty:
        return None
    prefix = f"session-{market}-"
    rows = frame[frame["entity_id"].astype(str).str.startswith(prefix)]
    if rows.empty:
        return None
    latest_run = rows.sort_values(["valid_from", "seq"])["entity_id"].iloc[-1]
    run = rows[rows["entity_id"] == latest_run].sort_values(["seq", "observed_at"])
    stages: dict[str, dict[str, Any]] = {}
    for row in run.to_dict(orient="records"):
        try:
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        stages[str(row["stage"])] = {"actor": str(row["actor"]), "payload": payload}
    moment = pd.Timestamp(run["valid_from"].iloc[0])
    return {"run_id": str(latest_run), "as_of": moment.to_pydatetime(), "stages": stages}


def _screen_summary(store: Store, *, as_of: datetime, market: str, equity: float, run_id: str) -> dict[str, Any]:
    """``pipeline.screen`` 을 부르고 화면에 필요한 숫자만 남긴다(캐시)."""
    key = (str(getattr(store, "root", id(store))), market, run_id, pd.Timestamp(as_of).isoformat(), round(equity, 2))
    with _SCREEN_LOCK:
        hit = _SCREEN_CACHE.get(key)
        if hit is not None:
            _SCREEN_CACHE.move_to_end(key)
            return hit
    from quant_rl_trading.selector import pipeline as selector_pipeline

    screen = selector_pipeline.screen(store, as_of=as_of, market=market, equity=equity)
    scored = screen.scored.sort_values(ascending=False, kind="mergesort")
    passed = screen.scores.sort_values(ascending=False, kind="mergesort")
    summary = {
        "fault": screen.fault,
        "n_candidates": screen.params.n_candidates,
        "exit_rank": screen.params.exit_rank,
        "counts": dict(screen.trace.counts),
        "risk_threshold": screen.trace.measures.get(constraints_module.RISK_FLOOR_THRESHOLD),
        "dropped": dict(screen.trace.dropped),
        "scored": {str(e): float(v) for e, v in scored.items()},
        "scored_rank": {str(e): i + 1 for i, e in enumerate(scored.index)},
        "passed_rank": {str(e): i + 1 for i, e in enumerate(passed.index)},
        "risk": {str(e): float(v) for e, v in screen.risk.items()},
    }
    with _SCREEN_LOCK:
        _SCREEN_CACHE[key] = summary
        while len(_SCREEN_CACHE) > _SCREEN_CACHE_SIZE:
            _SCREEN_CACHE.popitem(last=False)
    return summary


def _config(store: Store, name: str, *, as_of: datetime, market: str | None = None) -> Any:
    try:
        return market_config(store, name, as_of=as_of, market=market) if market else store.config(name, as_of=as_of)
    except ConfigNotFound:
        return None


# -- 다섯 단계 -------------------------------------------------------------------


def explain(
    store: Store,
    *,
    as_of: datetime,
    market: str,
    entity_id: str,
    held_quantity: float,
    ranker_score: float | None,
    all_scores: dict[str, float],
    order_rows: list[dict[str, Any]],
    realized: dict[str, float | None],
) -> dict[str, Any]:
    """한 종목의 다섯 단계. 인자로 받는 것은 호출부(트레이딩 서비스)가 이미 읽은 값이다.

    - ``held_quantity`` 는 as_of 장부의 보유 수량, ``ranker_score`` 는 기여 분해의 랭커 점수
    - ``all_scores`` 는 워치리스트가 쓰는 전 종목 합성 점수(최신 신호) — "전체 N종목 중 몇 위" 의 분모
    - ``order_rows`` 는 주문표 행(오늘 움직인 조각), ``realized`` 는 realized_weights 의 목표·실현
    """
    record = session_record(store, as_of=as_of, market=market)
    session_as_of = record["as_of"] if record else None
    stages = record["stages"] if record else {}
    equity = (stages.get("observe") or {}).get("payload", {}).get("nav")

    summary: dict[str, Any] | None = None
    if session_as_of is not None and equity:
        try:
            summary = _screen_summary(
                store, as_of=session_as_of, market=market, equity=float(equity), run_id=record["run_id"]
            )
        except Exception:  # 설명 칸 하나가 트레이딩 탭 전체를 죽이면 안 된다(없으면 "모름")
            summary = None

    selected_list = [str(e) for e in (stages.get("select") or {}).get("payload", {}).get("candidates", [])]
    held = held_quantity > 0 or any(
        row.get("side") == "sell" for row in order_rows if row.get("entity_id") == entity_id
    )
    return {
        "session": None if record is None else {
            "run_id": record["run_id"],
            "as_of": pd.Timestamp(session_as_of).tz_convert(KST).isoformat()
            if pd.Timestamp(session_as_of).tzinfo else pd.Timestamp(session_as_of).isoformat(),
        },
        "score": _score_step(store, as_of=as_of, market=market, entity_id=entity_id,
                             ranker_score=ranker_score, all_scores=all_scores, summary=summary),
        "filters": _filter_step(store, as_of=as_of, entity_id=entity_id, summary=summary),
        "rule": _rule_step(store, market=market, entity_id=entity_id, summary=summary, stages=stages,
                           session_as_of=session_as_of, selected_list=selected_list, held=held,
                           recorded=record is not None),
        "weight": _weight_step(entity_id=entity_id, stages=stages, realized=realized),
        "orders": _order_step(entity_id=entity_id, order_rows=order_rows),
        "rl": _rl_step(stages),
    }


def _score_step(
    store: Store, *, as_of: datetime, market: str, entity_id: str, ranker_score: float | None,
    all_scores: dict[str, float], summary: dict[str, Any] | None,
) -> dict[str, Any]:
    prefix = f"{market}:"
    ordered = sorted(((e, v) for e, v in all_scores.items() if str(e).startswith(prefix)), key=lambda kv: -kv[1])
    rank_all = next((i + 1 for i, (e, _) in enumerate(ordered) if e == entity_id), None)
    span = _config(store, "ranker.smoothing_span", as_of=as_of, market=market)
    scored = (summary or {}).get("scored", {})
    return {
        "ranker_score": ranker_score,
        "composite": all_scores.get(entity_id),
        "rank_all": rank_all,
        "n_all": len(ordered) or None,
        # 거래 가능(유니버스·부실 필터 통과) 종목 안 순위 — 세션 as_of 의 합성 점수.
        "rank_tradable": (summary or {}).get("scored_rank", {}).get(entity_id),
        "n_tradable": len(scored) or None,
        "smoothing_span": int(span) if span is not None else None,
    }


def _filter_step(store: Store, *, as_of: datetime, entity_id: str, summary: dict[str, Any] | None) -> dict[str, Any]:
    percentile = _config(store, "selector.risk_floor_percentile", as_of=as_of)
    if summary is None:
        return {"known": False, "risk_percentile": percentile}
    dropped = summary["dropped"].get(entity_id)
    risk_cut = dropped is not None and dropped.startswith("위험 하위")
    in_scored = entity_id in summary["scored"]
    risk = summary["risk"].get(entity_id)
    if risk_cut:
        risk_status = "cut"
    elif not in_scored:
        risk_status = "not_reached"       # 앞 관문(유니버스·부실)에서 이미 빠졌다
    elif risk is None:
        risk_status = "no_score"          # 위험 점수 없음 — 자르지 않는다(constraints.apply_risk_floor)
    else:
        risk_status = "pass"
    gate = None if (dropped is None or risk_cut) else dropped
    return {
        "known": True,
        "gate_reason": gate,              # 유니버스·부실·News·SNS 등 위험 밖 관문 사유(없으면 통과)
        "risk_status": risk_status,
        "risk_score": risk,
        "risk_threshold": summary["risk_threshold"],
        "risk_percentile": percentile,
        "counts": summary["counts"],
        # 화면의 '위험 필터 자' — 세션이 본 위험 점수(거래 가능 종목)의 분포. 표시용 숫자뿐, 판정은 위 임계가 한다.
        "risk_hist": _histogram(summary["risk"].values()),
    }


def _histogram(values: Any, bins: int = RISK_HIST_BINS) -> dict[str, Any] | None:
    """``{"lo", "hi", "counts"}`` — 값이 둘 미만이거나 폭이 0 이면 ``None``(화면은 분포를 숨긴다)."""
    data = [float(v) for v in values if v is not None and pd.notna(v)]
    if len(data) < 2:
        return None
    lo, hi = min(data), max(data)
    if hi <= lo:
        return None
    counts = [0] * bins
    for v in data:
        counts[min(bins - 1, int((v - lo) / (hi - lo) * bins))] += 1
    return {"lo": lo, "hi": hi, "counts": counts}


def _rule_step(
    store: Store, *, market: str, entity_id: str, summary: dict[str, Any] | None, stages: dict[str, Any],
    session_as_of: datetime | None, selected_list: list[str], held: bool, recorded: bool,
) -> dict[str, Any]:
    allocate = stages.get("allocate") or {}
    holding_day = allocate.get("actor") == "hold:cadence"
    next_day = None
    every = anchor = None
    if session_as_of is not None:
        moment = pd.Timestamp(session_as_of)
        moment = (moment.tz_convert(KST) if moment.tzinfo else moment).to_pydatetime()
        every, anchor = cadence_module.settings(store, as_of=moment, market=market)
        next_day = cadence_module.next_rebalance(store, as_of=moment, market=market)
    rank = (summary or {}).get("passed_rank", {}).get(entity_id)
    n = (summary or {}).get("n_candidates")
    m = (summary or {}).get("exit_rank")
    selected = entity_id in selected_list if "select" in stages else None
    if selected is None or (summary is None and not selected):
        verdict = "unknown"
    elif selected:
        verdict = "buffer_keep" if (held and rank is not None and n is not None and rank > n) else (
            "keep" if held else "buy")
    elif rank is None:
        verdict = "filtered"
    elif held and m and rank > m:
        verdict = "sell_out_of_buffer"
    elif n is not None and rank <= n:
        # 상위 N 순위인데 명단 밖 — 완충으로 남은 보유 종목이 자리를 지켰거나 상관 감점·섹터 상한에 밀렸다.
        # 4~6단계는 다시 돌리지 않으므로 어느 쪽인지 단정하지 않는다(완충 자리 수만 센다).
        verdict = "pushed_out"
    elif held:
        verdict = "sell"
    else:
        verdict = "not_top"
    passed_rank = (summary or {}).get("passed_rank", {})
    buffer_slots = (
        sum(1 for e in selected_list if n is not None and passed_rank.get(e, 0) > n) if summary is not None else None
    )
    return {
        "recorded": recorded,
        "verdict": verdict,
        # 이번 명단 중 상위 N 밖인데 완충으로 남은 보유 종목 수 — 그만큼 새 종목 자리가 줄었다.
        "buffer_slots": buffer_slots,
        "selected": selected,
        "held": held,
        "rank": rank,
        "n_passed": len((summary or {}).get("passed_rank", {})) or None,
        "n_candidates": n,
        "exit_rank": m,
        "n_selected": len(selected_list) if "select" in stages else None,
        "rebalance_every": every,
        "rebalance_anchor": anchor.isoformat() if anchor else None,
        "holding_day": holding_day if allocate else None,
        "cadence": (allocate.get("payload") or {}).get("cadence"),
        "next_rebalance": next_day.isoformat() if next_day else None,
    }


def _weight_step(*, entity_id: str, stages: dict[str, Any], realized: dict[str, float | None]) -> dict[str, Any]:
    allocate = stages.get("allocate") or {}
    exposure = stages.get("exposure") or {}
    base = (allocate.get("payload") or {}).get("weights", {}).get(entity_id) if allocate else None
    scale = (exposure.get("payload") or {}).get("scale") if exposure else None
    scaled = None
    if base is not None and scale is not None:
        # 곱셈도 세션이 쓴 그 함수로 — 배수 ≥ 1 이면 그대로 둔다(exposure.apply).
        decision = exposure_module.ExposureDecision(scale=float(scale), driver=str(exposure.get("actor")))
        scaled = exposure_module.apply({entity_id: float(base)}, decision)[entity_id]
    return {
        "allocator": allocate.get("actor"),                  # 예: risk_parity:volatile · hold:cadence
        "allocated": base,                                   # 노출 전 목표 비중(명단에 없으면 None)
        "exposure_scale": scale,
        "exposure_driver": exposure.get("actor"),
        "exposure_notes": list((exposure.get("payload") or {}).get("notes", [])),
        "target": scaled,
        "recorded_target": realized.get("target_weight"),    # realized_weights 에 적힌 목표(주문 뒤 기록)
        "realized": realized.get("realized_weight"),
    }


def _order_step(*, entity_id: str, order_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in order_rows if row.get("entity_id") == entity_id]
    if not rows:
        return {"count": 0}
    sides = sorted({str(row.get("side")) for row in rows})
    filled = sum(1 for row in rows if row.get("status") == "filled")
    partial = sum(1 for row in rows if row.get("status") == "partial")
    quantity = sum(float(row.get("quantity") or 0.0) for row in rows)
    got = sum(float(row.get("fill_quantity") or 0.0) for row in rows)
    statuses: dict[str, int] = {}
    for row in rows:
        statuses[str(row.get("status"))] = statuses.get(str(row.get("status")), 0) + 1
    return {
        "count": len(rows),
        "side": sides[0] if len(sides) == 1 else "mixed",
        "quantity": quantity,
        "filled_slices": filled,
        "partial_slices": partial,
        "filled_quantity": got,
        "statuses": statuses,
    }


def _rl_step(stages: dict[str, Any]) -> dict[str, Any]:
    actor = (stages.get("allocate") or {}).get("actor")
    return {"active": actor == "rl" if actor else False, "allocator": actor}
