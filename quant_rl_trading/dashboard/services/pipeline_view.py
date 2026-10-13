"""'매매 과정' 탭 — 흐름도 + AI 속 들여다보기 (dashboard.md §7-2, 사용자 요청 2026-10-11).

## 화면이 매매를 다시 하지 않는다 (불변식 5)

숫자의 출처는 셋뿐이다 — '왜 샀나' 패널(`services/why`)과 같다.

- **세션 기록** — 장부 창고 `events` 의 그 세션 행(`why.session_record`).
- **세션이 쓴 선정 함수** — `selector.pipeline.screen` 을 그 세션 as_of·자본으로 다시 부른 결과(`why._screen_summary`,
  같은 캐시). 단계 수·탈락 사유는 `SelectionTrace` 원문이다.
- **설정** — 단계 켜짐/꺼짐·값은 그 세션 as_of 의 `store.config`. 켜짐 목록을 여기 적지 않는다 — 그래서 10/13 전환
  (`tools/apply_switch.py`)이 설정 정정본으로 들어오면 손대지 않아도 그 세션부터 단계가 켜지고, 되감으면 옛 방식이 나온다.

AI 속(입력 기여)은 그 세션이 쓴 모델과 **실전 입력 조립 코드 그대로**(`RankerAnalyst.input_features` · `fa_features`)로
`pred_contrib=True` 를 부른다. 값이 없으면 ``None`` — 화면은 "모름".
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
import yaml

from quant_rl_trading.dashboard.services import model_story, why
from quant_rl_trading.selector import constraints as constraints_module
from quant_rl_trading.selector.candidates import market_config
from quant_rl_trading.store import ConfigNotFound, Store
from quant_rl_trading.store import names as names_module

KST = "Asia/Seoul"
SIGNALS = "signals"
REPO_ROOT = Path(__file__).resolve().parents[3]

#: 학습 모델인 Analyst — 흐름도의 'AI' 색. **여기 한 곳**이다. 새 학습 모델이 매매 경로에 들어오면 한 줄 더한다.
AI_ANALYSTS: dict[str, str] = {
    "ranker": "랭커 — LightGBM(순위 목적)",
    "be2": "BE2 — 세트 트랜스포머 + GBM 순위 평균",
    "tsfm": "TTM — 시계열 파운데이션 모델(제로샷)",
    "bf1": "BF1 — LambdaRank",
}
#: LLM 을 부르는 Analyst — 'LLM' 색(매수 금지 확인·해설뿐, 보상에 안 들어간다 — 불변식 8).
LLM_ANALYSTS = frozenset({"llm_pick", "news_screen", "macro_brief"})

#: 흐름 단계 → 설명 문구(`model_story.PIPELINE_STAGES` 의 key). 문구를 여기 다시 적지 않는다.
STORY_OF: dict[str, str] = {
    "collect": "collect", "analysts": "analysts", "model": "ranker",
    "universe": "screen", "healthy": "screen", "scored": "screen",
    "risk_floor": "floors", "extra_floor": "floors", "ceiling": "floors", "combo_floor": "floors",
    "verdicts": "verdicts", "cadence": "rebalance", "select": "select",
    "weights": "weights", "exposure": "exposure", "execute": "execute", "fill": "paper",
    "commentary": "commentary",
}
FLOW_KEYS: tuple[str, ...] = tuple(STORY_OF)

#: 단계 → 그 단계의 설정 키(옆 패널). 값은 매 요청 config 에서 — 키 이름만 여기 둔다.
STAGE_SETTINGS: dict[str, list[tuple[str, str]]] = {
    "risk_floor": [("selector.risk_floor_percentile", "하위 잘라내는 비율"),
                   ("selector.floor_analyst", "재는 Analyst")],
    "extra_floor": [("selector.extra_floor_analyst", "Analyst(비면 꺼짐)"),
                    ("selector.extra_floor_percentile", "하위 비율")],
    "ceiling": [("selector.ceiling_analyst", "Analyst(비면 꺼짐)"),
                ("selector.ceiling_percentile", "상위에서 빼는 비율")],
    "combo_floor": [("selector.combo_floor_analyst", "Analyst(비면 꺼짐)"),
                    ("selector.combo_floor_percentile", "결합 하위 비율")],
    "model": [("selector.weights_override", "점수 가중치 덮어쓰기(비면 측정값)"),
              ("selector.weights_override_us", "미장 점수 가중치 덮어쓰기(비면 측정값 — BE2 는 국장 전용)"),
              ("ranker.smoothing_span", "평활(최근 N세션 EMA)")],
    "cadence": [("selector.rebalance_every", "몇 세션마다 교체"),
                ("selector.rebalance_anchor", "주기를 세는 기준일")],
    "select": [("selector.n_candidates", "살 종목 수"), ("selector.exit_rank", "완충 — 이 순위 안이면 유지"),
               ("selector.swap_min_z", "교체 문턱(z, 0 = 끔)"), ("risk.max_positions", "최대 보유 종목 수")],
}

#: 유니버스 필터의 '다른 시장' 사유(`selector.filters`) — 국장 흐름에 미장 행이 들어온 수로 세지 않는다.
OTHER_MARKET = "시장 불일치"
#: 단계별 걸러진 종목 예시 수 — 표시용.
EXAMPLES = 6
#: 세션 고르기 목록 길이·창(달력일).
SESSION_LIST = 30
SESSION_LIST_DAYS = 60
#: TTM·가격선 창(달력일) — 표시용.
TTM_DAYS = 120
PRICE_DAYS = 180
#: BE2 기여 막대 — 76열 중 큰 것만, 나머지는 한 줄로 합친다.
BE2_TOP = 12


# --------------------------------------------------------------------------- 공통


def _cfg(store: Store, key: str, *, as_of: datetime, market: str | None = None) -> Any:
    try:
        return market_config(store, key, as_of=as_of, market=market) if market else store.config(key, as_of=as_of)
    except ConfigNotFound:
        return None


def _iso_kst(moment: Any) -> str | None:
    if moment is None:
        return None
    stamp = pd.Timestamp(moment)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
    return stamp.tz_convert(KST).isoformat()


def _kst_date(moment: Any) -> date:
    stamp = pd.Timestamp(moment)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp
    return stamp.tz_convert(KST).date()


def _kind_of(analyst: str | None) -> str:
    if not analyst:
        return "rule"
    if analyst in AI_ANALYSTS:
        return "ai"
    if analyst in LLM_ANALYSTS:
        return "llm"
    return "rule"


def _display(value: Any) -> str:
    return model_story._display(value)


def _settings(store: Store, keys: list[tuple[str, str]], *, as_of: datetime, market: str) -> list[dict[str, Any]]:
    out = []
    for key, label in keys:
        value = _cfg(store, key, as_of=as_of, market=market)
        found = value is not None or _exists(store, key, as_of=as_of)
        if isinstance(value, (pd.Timestamp, datetime, date)):
            value = str(value)
        out.append({"key": key, "label": label, "found": found, "value": value,
                    "display": _display(value) if found else "설정 없음"})
    return out


def _exists(store: Store, key: str, *, as_of: datetime) -> bool:
    try:
        store.config(key, as_of=as_of)
        return True
    except ConfigNotFound:
        return False


def _story(key: str) -> dict[str, Any]:
    return next((s for s in model_story.PIPELINE_STAGES if s["key"] == STORY_OF[key]), {})


# --------------------------------------------------------------------------- 세션 목록


def sessions(store: Store, *, as_of: datetime, market: str) -> list[dict[str, Any]]:
    """as_of 에 보이는 최근 세션들 — 고르기 목록. 링크 시각은 **다음 세션 직전**(그 세션의 주문·체결이 보이는 마지막 순간)."""
    frame = store.get(why.EVENTS, as_of=as_of, lookback=SESSION_LIST_DAYS, columns=["entity_id", "valid_from"])
    if frame.empty:
        return []
    frame = frame[frame["entity_id"].astype(str).str.startswith(f"session-{market}-")]
    if frame.empty:
        return []
    runs = frame.groupby("entity_id")["valid_from"].min().sort_values()
    out = []
    items = list(runs.items())
    for index, (run, moment) in enumerate(items):
        nxt = items[index + 1][1] if index + 1 < len(items) else None
        out.append({"run_id": str(run), "date": "-".join(str(run).split("-")[2:5]),
                    "as_of": _iso_kst(moment),
                    "link_as_of": _iso_kst(pd.Timestamp(nxt) - timedelta(minutes=1)) if nxt is not None
                    else _iso_kst(as_of)})
    return out[-SESSION_LIST:][::-1]


# --------------------------------------------------------------------------- 흐름도


_CENSUS_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_CENSUS_LOCK = Lock()
_CENSUS_SIZE = 6


def _signal_census(store: Store, *, as_of: datetime, market: str, run_id: str) -> dict[str, Any]:
    """세션 as_of 에 보이던 Analyst 별 최신 신호 종목 수. 표시용 숫자뿐(캐시)."""
    key = (str(getattr(store, "root", id(store))), market, run_id, pd.Timestamp(as_of).isoformat())
    with _CENSUS_LOCK:
        hit = _CENSUS_CACHE.get(key)
        if hit is not None:
            return hit
    frame = store.get(SIGNALS, as_of=as_of, lookback=3, market=market,
                      columns=["entity_id", "valid_from", "analyst"])
    out: dict[str, Any] = {"analysts": {}, "entities": 0}
    if not frame.empty:
        frame = frame[frame["entity_id"].astype(str).str.startswith(f"{market}:")]
        latest = frame.groupby("analyst")["valid_from"].transform("max")
        fresh = frame[frame["valid_from"] == latest]
        for name, part in fresh.groupby("analyst"):
            out["analysts"][str(name)] = {"n": int(part["entity_id"].nunique()),
                                          "at": _iso_kst(part["valid_from"].iloc[0])}
        out["entities"] = int(fresh["entity_id"].nunique())
    with _CENSUS_LOCK:
        _CENSUS_CACHE[key] = out
        while len(_CENSUS_CACHE) > _CENSUS_SIZE:
            _CENSUS_CACHE.popitem(last=False)
    return out


def _drop_stage(reason: str) -> str:
    """`SelectionTrace.dropped` 사유 원문 → 흐름 단계. 사유 문구는 각 단계 코드가 쓴 그대로다."""
    if reason.startswith("부실 공시"):
        return "healthy"
    if reason.startswith("위험 하위"):
        return "risk_floor"
    if " 전체 하위 " in reason:
        return "extra_floor"
    if " 전체 상위 " in reason:
        return "ceiling"
    if reason.startswith("합성+"):
        return "combo_floor"
    if reason.startswith("News·SNS"):
        return "verdicts"
    return "universe"


def _reason_head(reason: str) -> str:
    """사유에서 종목마다 다른 숫자 괄호를 뗀다 — 사유별 개수를 세려고."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", reason).strip()


def _drops(summary: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """단계별 탈락 — 사유별 개수와 예시(점수가 높았는데 빠진 순)."""
    if not summary:
        return {}
    scored = summary.get("scored", {})
    grouped: dict[str, list[tuple[str, str]]] = {}
    for entity, reason in summary.get("dropped", {}).items():
        if str(reason).startswith(OTHER_MARKET):
            continue   # 다른 시장 종목 — 이 시장 흐름의 '들어온 수' 가 아니다(따로 센다)
        grouped.setdefault(_drop_stage(str(reason)), []).append((str(entity), str(reason)))
    out = {}
    for stage, rows in grouped.items():
        reasons: dict[str, int] = {}
        for _, reason in rows:
            head = _reason_head(reason)
            reasons[head] = reasons.get(head, 0) + 1
        rows.sort(key=lambda r: (-(scored.get(r[0]) if scored.get(r[0]) is not None else -9.0), r[0]))
        out[stage] = {"count": len(rows),
                      "reasons": [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])][:8],
                      "examples": [{"entity_id": e, "reason": r, "score": scored.get(e)} for e, r in rows[:EXAMPLES]]}
    return out


def _model_info(alpha: list[str], *, models_root: Path | None, as_of: datetime) -> dict[str, Any]:
    """그 세션 점수 모델 — 이름·버전·학습 종료일. 모델 파일 폴더의 사이드카에서 읽는다(실전과 같은 선택 규칙)."""
    info: dict[str, Any] = {"alpha": alpha, "name": None, "kind": "rule", "model": None}
    if "be2" in alpha:
        info.update(name=AI_ANALYSTS["be2"], kind="ai", model=_be2_meta(models_root, as_of))
    elif "ranker" in alpha:
        info.update(name=AI_ANALYSTS["ranker"], kind="ai",
                    model=model_story._ranker_model(models_root, as_of))
    elif alpha:
        ai = [a for a in alpha if a in AI_ANALYSTS]
        info.update(name=" + ".join(AI_ANALYSTS.get(a, a) for a in alpha) if ai else "가중 합성(규칙)",
                    kind="ai" if ai else "rule")
    return info


def _be2_meta(models_root: Path | None, as_of: datetime) -> dict[str, Any] | None:
    if models_root is None:
        return None
    from quant_rl_trading.analysts import be2 as be2_module

    model = _be2_model(models_root, as_of)
    if model is None:
        return None
    return {"file": model.sidecar.name, "version": model.version, "trained_through": model.trained_through.isoformat(),
            "usable_from": model.usable_from.isoformat(),
            "n_features": len(model.features), "seeds": list(model.seeds), "protocol_hash": model.meta.get("protocol_hash"),
            "parts": "세트 트랜스포머(60세션 창) + GBM(그날 FA 76열), 시드마다 세션 안 백분위 평균",
            "contract": be2_module.VERSION}


_BE2_CACHE: dict[tuple[str, str], Any] = {}


def _be2_model(models_root: Path, as_of: datetime) -> Any:
    """`usable_be2_model` 결과 캐시 — 파일 지문을 매번 재지 않는다(같은 날짜 → 같은 답)."""
    from quant_rl_trading.analysts import be2 as be2_module

    day = _kst_date(as_of)
    key = (str(models_root), day.isoformat())
    if key not in _BE2_CACHE:
        _BE2_CACHE[key] = be2_module.usable_be2_model(
            models_root, as_of=datetime.combine(day, datetime.min.time()))
        if len(_BE2_CACHE) > 8:
            _BE2_CACHE.pop(next(iter(_BE2_CACHE)))
    return _BE2_CACHE[key]


def _orders_of(store: Store, *, as_of: datetime, market: str, session_day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    session_id = f"{market}-{session_day.isoformat()}"
    orders = store.get("orders", as_of=as_of, lookback=20)
    if not orders.empty and "session_id" in orders.columns:
        orders = orders[orders["session_id"].astype(str) == session_id]
    trades = store.get("trades", as_of=as_of, lookback=20)
    return orders, trades


def flow(store: Store, *, as_of: datetime, market: str, models_root: Path | None = None) -> dict[str, Any]:
    """흐름도 한 벌 — 단계마다 들어온 수·나간 수·켜짐·종류(AI·규칙·LLM)·설정·탈락 예시."""
    record = why.session_record(store, as_of=as_of, market=market)
    stages_rec = record["stages"] if record else {}
    session_as_of = record["as_of"] if record else None
    cfg_at = session_as_of or as_of
    equity = (stages_rec.get("observe") or {}).get("payload", {}).get("nav")

    summary: dict[str, Any] | None = None
    summary_error = None
    if record is not None and equity:
        try:
            summary = why._screen_summary(
                store, as_of=session_as_of, market=market, equity=float(equity), run_id=record["run_id"])
        except Exception as error:
            summary_error = f"{type(error).__name__}: {error}"[:200]
    counts = (summary or {}).get("counts", {})
    drops = _drops(summary)

    select_payload = (stages_rec.get("select") or {}).get("payload", {})
    session_weights = {str(k): float(v) for k, v in (select_payload.get("weights") or {}).items()}
    alpha = sorted(k for k, v in session_weights.items() if v)
    census = _signal_census(store, as_of=session_as_of, market=market, run_id=record["run_id"]) if record else None
    model = _model_info(alpha, models_root=models_root, as_of=cfg_at)

    floor = constraints_module.ConstraintParams.from_store(store, as_of=cfg_at) if _exists(
        store, "selector.risk_floor_percentile", as_of=cfg_at) else None
    allocate = stages_rec.get("allocate") or {}
    exposure = stages_rec.get("exposure") or {}
    execute = stages_rec.get("execute") or {}
    candidates = [str(e) for e in select_payload.get("candidates", [])]

    def count(name: str) -> int | None:
        value = counts.get(name)
        return int(value) if value is not None else None

    def drop_count(stage: str) -> int:
        return int(drops.get(stage, {}).get("count", 0))

    nodes: list[dict[str, Any]] = []

    def node(key: str, title: str, *, kind: str, active: bool = True, n_in: int | None = None,
             n_out: int | None = None, note: str | None = None, extra: dict[str, Any] | None = None) -> None:
        story = _story(key)
        nodes.append({
            "key": key, "title": title, "kind": kind, "active": active, "n_in": n_in, "n_out": n_out, "note": note,
            "plain": story.get("plain"), "detail": story.get("detail"), "story_key": story.get("key"),
            "story_title": story.get("title"),
            "settings": _settings(store, STAGE_SETTINGS.get(key, []), as_of=cfg_at, market=market),
            "drops": drops.get(key), **(extra or {}),
        })

    # 0~1. 재료 · Analyst
    analysts = []
    for name, info in sorted(((census or {}).get("analysts") or {}).items()):
        weight = session_weights.get(name)
        if name in constraints_module.CONSTRAINT_ANALYSTS:
            role = "제약 — 하한·상한 재료(합성 밖)"
        elif weight:
            role = "알파 — 점수 가중치"
        elif "ranker" in alpha and name in _ranker_inputs():
            role = "랭커 입력"
        else:
            role = "관찰 — 가중치 0"
        analysts.append({"name": name, "n": info["n"], "at": info["at"], "weight": weight, "role": role,
                         "kind": _kind_of(name), "label": AI_ANALYSTS.get(name)})
    node("collect", "재료 수집", kind="rule", n_out=(census or {}).get("entities") or None,
         note="수집 상세는 데이터 품질 탭")
    node("analysts", "Analyst 점수", kind="rule", n_out=len(analysts) or None,
         note="종목 수 = 세션 시각에 보이던 각 Analyst 최신 신호", extra={"analysts": analysts})
    model_n = None
    if census and alpha:
        model_n = max((census["analysts"].get(a, {}).get("n", 0) for a in alpha), default=None) or None
    node("model", model["name"] or "점수 모델", kind=model["kind"], active=bool(alpha), n_out=model_n,
         note=None if alpha else "세션 기록에 점수 가중치가 없다 — 모름", extra={"model": model})

    # 2. 유니버스 · 부실 · 점수
    universe_out = count("universe")
    other = sum(1 for r in (summary or {}).get("dropped", {}).values() if str(r).startswith(OTHER_MARKET))
    node("universe", "유니버스 · 거래 가능", kind="rule",
         n_in=(universe_out + drop_count("universe")) if universe_out is not None else None, n_out=universe_out,
         note=f"다른 시장 종목 {other:,}개는 세지 않았다" if other else None)
    node("healthy", "부실 제외", kind="rule", n_in=universe_out, n_out=count("healthy"))
    node("scored", "점수 있음", kind=model["kind"], n_in=count("healthy"), n_out=count("scored"),
         note="합성 점수가 붙은 종목(점수 결측은 여기서 빠진다)")

    # 3. 하한·상한 — 켜짐은 그 세션 config 가 말한다
    risk_kind = _kind_of(floor.floor_analyst) if floor else "rule"
    node("risk_floor", f"하한 — {floor.floor_analyst if floor else 'risk'}", kind=risk_kind,
         active=bool(floor and floor.risk_floor_percentile > 0), n_in=count("scored"), n_out=count("risk_floor"),
         extra={"threshold": (summary or {}).get("risk_threshold")})
    previous = count("risk_floor")
    for key, analyst, pct, label in (
        ("extra_floor", floor.extra_floor_analyst if floor else "", floor.extra_floor_percentile if floor else 0.0,
         "두 번째 하한"),
        ("ceiling", floor.ceiling_analyst if floor else "", floor.ceiling_percentile if floor else 0.0, "상한"),
        ("combo_floor", floor.combo_floor_analyst if floor else "", floor.combo_floor_percentile if floor else 0.0,
         "결합 하한"),
    ):
        on = bool(analyst) and pct > 0
        out_n = count(key)
        node(key, f"{label} — {analyst}" if on else f"{label} — 꺼짐", kind=_kind_of(analyst) if on else "rule",
             active=on, n_in=previous if on else None, n_out=out_n if on else None,
             note=None if on else "설정이 꺼져 있다(Analyst 비었거나 비율 0)",
             extra={"analyst": analyst or None, "percentile": pct if on else None})
        if on and out_n is not None:
            previous = out_n
    node("verdicts", "뉴스 매수 금지", kind="llm", n_in=previous, n_out=count("after_verdicts"),
         note="키워드 규칙 + LLM 확인(막은 것을 풀어 주기만)")

    # 4. 주기 · 선정
    cadence = (allocate.get("payload") or {}).get("cadence")
    holding = allocate.get("actor") == "hold:cadence" if allocate else None
    node("cadence", "재조정 주기", kind="rule", active=True,
         note=cadence or ("세션 기록 없음 — 모름" if record is None else None),
         extra={"holding_day": holding})
    node("select", "평활 · 완충 · 상위 N", kind="rule", n_in=count("after_verdicts"),
         n_out=len(candidates) if "select" in stages_rec else None,
         note="상관 감점·섹터 상한·상위 N 결과는 세션 기록(select)에서 읽는다")

    # 5. 비중 · 노출 · 집행 · 체결
    weights = (allocate.get("payload") or {}).get("weights") or {}
    actor = allocate.get("actor")
    node("weights", "비중 — " + ("RL 정책" if actor == "rl" else "리스크 패리티" if actor else "모름"),
         kind="ai" if actor == "rl" else "rule", active=bool(allocate), n_in=len(candidates) if candidates else None,
         n_out=len(weights) or None,
         extra={"allocator": actor, "weight_sum": float(sum(float(v) for v in weights.values())) if weights else None})
    exp_payload = exposure.get("payload") or {}
    exp_actor = str(exposure.get("actor") or "")
    source = _cfg(store, "exposure.source", as_of=cfg_at)
    learned = exp_actor.startswith(("learned:", "rl:")) or (source not in (None, "", "rule"))
    node("exposure", "노출 — " + (f"학습({source})" if learned else "국면 규칙(V6)"),
         kind="ai" if learned else "rule", active=bool(exposure),
         note=None if exposure else "세션 기록 없음 — 모름",
         extra={"scale": exp_payload.get("scale"), "driver": exp_actor or None, "notes": list(exp_payload.get("notes", []))})
    ex_payload = execute.get("payload") or {}
    slices = ex_payload.get("orders") or []
    plan = _cfg(store, "execution.plan_source", as_of=cfg_at)
    node("execute", "집행 — 조각 주문", kind="ai" if plan not in (None, "", "rule") else "rule", active=bool(execute),
         n_in=len(weights) or None, n_out=len(slices) if execute else None,
         note=" · ".join(str(n) for n in ex_payload.get("notes", [])) or None,
         extra={"buy": sum(1 for o in slices if o.get("side") == "buy"),
                "sell": sum(1 for o in slices if o.get("side") == "sell"),
                "names": len({o.get("entity_id") for o in slices}), "blocked_by": ex_payload.get("blocked_by") or None,
                "plan_source": plan})
    fill = None
    if record is not None:
        from quant_rl_trading.dashboard.services import trading as trading_module

        orders, trades = _orders_of(store, as_of=as_of, market=market, session_day=_kst_date(session_as_of))
        fill = trading_module.fill_rate_counts(orders, trades, market=market) if not orders.empty else None
    node("fill", "체결", kind="rule", active=bool(fill and fill.get("sent_count")),
         n_in=(fill or {}).get("sent_count") or None, n_out=(fill or {}).get("filled_count") if fill else None,
         note=None if fill else "이 세션 주문이 아직 장부에 없다(as_of 까지)", extra={"fill": fill})
    node("commentary", "LLM 해설 — 매매 밖", kind="llm", active=True, note="해설만 — 매매에 안 들어감(불변식 8)")

    # 선정 명단 — AI 속 들여다보기의 종목 목록
    names = names_module.of(store, as_of=as_of, entities=candidates) if candidates else {}
    passed_rank = (summary or {}).get("passed_rank", {})
    selected = [{"entity_id": e, "name": names.get(e), "rank": passed_rank.get(e),
                 "weight": weights.get(e)} for e in candidates]
    for stage in drops.values():
        ex_names = names_module.of(store, as_of=as_of, entities=[x["entity_id"] for x in stage["examples"]])
        for x in stage["examples"]:
            x["name"] = ex_names.get(x["entity_id"])

    return {
        "market": market,
        "session": None if record is None else {"run_id": record["run_id"], "as_of": _iso_kst(session_as_of),
                                                "nav": equity},
        "config_as_of": _iso_kst(cfg_at),
        "screen_error": summary_error,
        "notes": [],
        "nodes": nodes,
        "steps": _steps(nodes),
        "selected": selected,
        "sessions": sessions(store, as_of=as_of, market=market),
    }


def _steps(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """큰 단계 일곱(`model_story.PIPELINE_STEPS`) — AI 여부는 그 세션 **켜진** 노드의 kind 에서 정한다.

    '점수 있음'(scored)은 점수 모델 결과를 세기만 하므로 AI 판정에서 뺀다. 숫자는 켜진 첫 노드의 들어온 수(없으면 나간 수)
    → 켜진 마지막 노드의 나간 수.
    """
    by_key = {n["key"]: n for n in nodes}
    out = []
    for step in model_story.PIPELINE_STEPS:
        members = [by_key[k] for k in step["nodes"] if k in by_key]
        live = [n for n in members if n.get("active")]
        kinds = {n["kind"] for n in live if n["key"] != "scored"}
        ai = "ai" if "ai" in kinds else ("llm" if "llm" in kinds else "rule")
        # 종목 수를 세는 노드만 — Analyst 칸은 Analyst 개수, 집행·체결은 주문 건수라 따로 적는다.
        counted = [n for n in live if n["key"] not in ("analysts", "execute", "fill", "commentary")]
        first, last = (counted[0], counted[-1]) if counted else (None, None)
        n_in = None if first is None else (first.get("n_in") if first.get("n_in") is not None else first.get("n_out"))
        n_out = None if last is None else last.get("n_out")
        text = None
        if n_out is not None:
            text = f"{n_out:,}종목" if n_in is None or n_in == n_out else f"{n_in:,} → {n_out:,}종목"
        if step["key"] == "order":
            sent, filled = by_key.get("execute", {}).get("n_out"), by_key.get("fill", {}).get("n_out")
            if sent is not None:
                text = f"주문 {sent:,}건" + (f" → 체결 {filled:,}건" if filled is not None else "")
        out.append({"no": step["no"], "key": step["key"], "title": step["title"], "does": step["does"],
                    "why_ai": step["why_ai"], "ai": ai, "nodes": [n["key"] for n in members],
                    "live_nodes": [n["key"] for n in live], "n_in": n_in, "n_out": n_out, "count_text": text})
    return out


def _ranker_inputs() -> set[str]:
    from quant_rl_trading.analysts.ranker import BASE_ANALYSTS

    return set(BASE_ANALYSTS)


# --------------------------------------------------------------------------- 후보 칩


def _doc_hash(path: str) -> dict[str, Any]:
    """등록 문서 해시 — sha256 앞 16자. 파일 머리(앞 3줄)에 '고정' 이 있고 '초안' 이 없을 때만 고정으로 본다."""
    file = REPO_ROOT / path
    if not file.is_file():
        return {"exists": False, "fixed": False, "state": "missing", "hash": None}
    raw = file.read_bytes()
    head = "\n".join(raw.decode("utf-8", errors="replace").splitlines()[:3])
    draft = "초안" in head and "고정 20" not in head
    state = "draft" if draft else "fixed" if "고정 20" in head else "unmarked"
    return {"exists": True, "fixed": state == "fixed", "state": state,
            "hash": None if draft else hashlib.sha256(raw).hexdigest()[:16]}


def _switch_values() -> dict[str, dict[str, str]]:
    """`tools/apply_switch.COMPONENTS` — 전환 구성 요소가 바꾸는 키와 값(한 곳). 못 읽으면 빈 dict."""
    try:
        from tools.apply_switch import COMPONENTS
    except Exception:
        return {}
    return COMPONENTS


def _same(config_value: Any, literal: str) -> bool:
    try:
        want = yaml.safe_load(literal)
    except yaml.YAMLError:
        want = literal
    if isinstance(want, dict) and isinstance(config_value, dict):
        return {str(k): float(v) for k, v in want.items()} == {str(k): float(v) for k, v in config_value.items()}
    if isinstance(want, (int, float)) and isinstance(config_value, (int, float)):
        return abs(float(want) - float(config_value)) < 1e-9
    return str(want) == str(config_value)


def _ledger(research: Store, *, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
    frame = research.get("research_trials", as_of=as_of, lookback=model_story.LEDGER_LOOKBACK_DAYS,
                         columns=["entity_id", "valid_from", "observed_at", "detail", "protocol_hash"])
    out: dict[str, list[dict[str, Any]]] = {}
    if frame.empty:
        return out
    for row in frame.sort_values("observed_at").to_dict(orient="records"):
        out.setdefault(str(row["entity_id"]), []).append(
            {"at": _iso_kst(row["observed_at"]), "detail": str(row.get("detail") or ""),
             "protocol_hash": row.get("protocol_hash")})
    return out


def candidates(research: Store, config: Store, *, as_of: datetime, market: str, root: Path | None) -> dict[str, Any]:
    """후보 칩 — `model_story.STAGE_CANDIDATES` 한 표. 결과는 대장·카탈로그·순차 검정을 **옮기기만** 한다."""
    day = _kst_date(as_of)
    try:
        ledger = _ledger(research, as_of=as_of)
    except Exception:
        ledger = {}
    catalog = {t["id"]: t for t in model_story.load_catalog().trials}
    switch = _switch_values()
    seq = _pairs(root, config, as_of) if root is not None else {}
    out = []
    for cand in model_story.STAGE_CANDIDATES:
        rows = [dict(r, entity_id=e) for e in cand["ledger"] for r in ledger.get(e, [])]
        rows.sort(key=lambda r: r["at"] or "")
        cat = catalog.get(cand["catalog"]) if cand.get("catalog") else None
        if cat is not None and (cat.get("date") is None or cat["date"] > day):
            cat = None
        pending = [j for j in cand["judge"] if j.get("date") and date.fromisoformat(j["date"]) > day]
        passed = [j for j in cand["judge"] if j.get("date") and date.fromisoformat(j["date"]) <= day]
        applied = None
        if cand.get("switch") and switch.get(cand["switch"]):
            values = switch[cand["switch"]]
            applied = all(_same(_cfg(config, k, as_of=as_of), v) for k, v in values.items())
        lead = None
        if rows:
            first = rows[-1]["detail"].strip()
            lead = next((w for w in ("채택", "기각", "보류") if first.startswith(w)), None)
        if applied:
            status = "실전 적용"
        elif rows:
            status = (lead or "판정 기록 있음") + (f" · 다음 판정 {pending[0]['date']}" if pending else "")
        elif pending:
            status = "판정 전 — 결과 없음"
        elif passed:
            status = "판정 기록 없음 — 대장에 아직 안 적힘"
        else:
            status = "등록 — 판정일 미정"
        doc = _doc_hash(cand["doc"])
        out.append({
            "id": cand["id"], "stage": cand["stage"], "title": cand["title"], "change": cand["change"],
            "doc": cand["doc"], "doc_fixed": doc["fixed"], "doc_state": doc["state"], "doc_hash": doc["hash"], "doc_exists": doc["exists"],
            "judge": cand["judge"], "status": status, "applied": applied,
            "switch": cand.get("switch"),
            "switch_values": switch.get(cand["switch"]) if cand.get("switch") else None,
            "results": [{"entity_id": r["entity_id"], "at": r["at"], "detail": r["detail"][:1500],
                         "protocol_hash": r["protocol_hash"]} for r in rows],
            "catalog": None if cat is None else {"id": cat["id"], "date": str(cat["date"]), "result": cat.get("result"),
                                                 "why": cat.get("why")},
            "sequential": seq.get(cand["pair"]) if cand.get("pair") else None,
            "pair": cand.get("pair"),
        })
    return {"candidates": out, "market": market}


def _pairs(root: Path, config: Store, as_of: datetime) -> dict[str, dict[str, Any]]:
    """shadow 장부 짝 순차 검정 — `alpha_ir._sequential` 그대로(짝 정의도 그곳)."""
    from quant_rl_trading.accounting import relative
    from quant_rl_trading.dashboard.services import alpha_ir

    ledger_of = {b["key"]: b["ledger"] for b in alpha_ir.BOOKS}
    wanted = {k for t, c, _ in alpha_ir.PAIRS for k in (t, c)}
    indexes: dict[str, Any] = {}
    for key in wanted:
        path = root / ledger_of.get(key, f"_{key}_shadow")
        if not (path / "curated" / "nav_daily").is_dir():
            continue
        try:
            indexes[key] = relative.book_index(Store(root=path), as_of=as_of, lookback=None)
        except Exception:
            indexes[key] = None
    result = alpha_ir._sequential(indexes, config, as_of)
    return {p["label"]: dict(p, alpha=result.get("alpha"), t_star=result.get("t_star"))
            for p in result.get("pairs", [])}


# --------------------------------------------------------------------------- AI 속


_INSIDE_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_INSIDE_LOCK = Lock()
_INSIDE_SIZE = 3


def _ranker_batch(store: Store, *, as_of: datetime, market: str, models_root: Path | None, run_id: str) -> dict[str, Any]:
    """세션 하나의 랭커 입력 표 + 원점수 — 실전 `RankerAnalyst` 의 입력 조립과 모델 선택 그대로(캐시)."""
    key = ("ranker", str(getattr(store, "root", id(store))), market, run_id, str(models_root))
    with _INSIDE_LOCK:
        hit = _INSIDE_CACHE.get(key)
        if hit is not None:
            _INSIDE_CACHE.move_to_end(key)
            return hit
    from quant_rl_trading.analysts.ranker import RankerAnalyst
    from quant_rl_trading.collectors.market_hours import Market

    analyst = RankerAnalyst(store, None, market=Market(market), models_root=models_root)  # type: ignore[arg-type]
    features = analyst.features(as_of)       # 모델 선택(_resolve_model) + 입력 조립(input_features) — 실전 그대로
    model = analyst._model
    out: dict[str, Any] = {"model": None, "features": None, "raw": None, "booster": None}
    if model is not None and not features.empty:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(model.path))
        matrix = features.loc[:, list(model.features)].to_numpy(np.float32)
        raw = pd.Series(np.asarray(booster.predict(matrix), dtype=float), index=features.index)
        out = {"model": model, "features": features.loc[:, list(model.features)].astype(np.float32),
               "raw": raw, "booster": booster}
    with _INSIDE_LOCK:
        _INSIDE_CACHE[key] = out
        while len(_INSIDE_CACHE) > _INSIDE_SIZE:
            _INSIDE_CACHE.popitem(last=False)
    return out


def _be2_batch(store: Store, *, as_of: datetime, market: str, models_root: Path | None, run_id: str) -> dict[str, Any]:
    """BE2 의 GBM 절반 — 그 세션 `fa_features` 행 + 얼린 C1 GBM 시드들(캐시). 트랜스포머 절반은 분해하지 않는다."""
    key = ("be2", str(getattr(store, "root", id(store))), market, run_id, str(models_root))
    with _INSIDE_LOCK:
        hit = _INSIDE_CACHE.get(key)
        if hit is not None:
            _INSIDE_CACHE.move_to_end(key)
            return hit
    from quant_rl_trading.analysts import fa_features

    out: dict[str, Any] = {"model": None, "flat": None, "boosters": []}
    model = _be2_model(models_root, as_of) if models_root is not None and market == "KR" else None
    if model is not None:
        session = fa_features.session_of(market, as_of)
        frame = fa_features.read_window(store, as_of=as_of, market=market, lookback_days=7)
        today = frame[frame["session"] == session] if not frame.empty else frame
        if not today.empty:
            import lightgbm as lgb

            flat = today.set_index("entity_id").loc[:, list(model.features)].astype(np.float32)
            boosters = [lgb.Booster(model_file=str(model.gbm[s])) for s in model.seeds]
            out = {"model": model, "flat": flat, "boosters": boosters}
    with _INSIDE_LOCK:
        _INSIDE_CACHE[key] = out
        while len(_INSIDE_CACHE) > _INSIDE_SIZE:
            _INSIDE_CACHE.popitem(last=False)
    return out


def _stored_score(store: Store, *, as_of: datetime, market: str, entity_id: str, analyst: str) -> dict[str, Any] | None:
    frame = store.get(SIGNALS, as_of=as_of, entity=[entity_id], lookback=3, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "analyst", "score"])
    if frame.empty:
        return None
    frame = frame[frame["analyst"].astype(str) == analyst]
    if frame.empty:
        return None
    last = frame.sort_values(["valid_from", "observed_at"]).iloc[-1]
    return {"score": float(last["score"]), "at": _iso_kst(last["valid_from"])}


def contributions(store: Store, *, as_of: datetime, market: str, entity_id: str, alpha: list[str],
                  models_root: Path | None, run_id: str) -> dict[str, Any]:
    """그 세션 점수 모델의 입력별 기여(`pred_contrib=True`). 모델·입력이 없으면 이유와 함께 None."""
    if "be2" in alpha:
        batch = _be2_batch(store, as_of=as_of, market=market, models_root=models_root, run_id=run_id)
        stored = _stored_score(store, as_of=as_of, market=market, entity_id=entity_id, analyst="be2")
        if batch["model"] is None:
            return {"model": "be2", "available": False, "reason": "얼린 BE2 모델이나 그 세션 FA 행이 없다", "stored": stored}
        flat = batch["flat"]
        if entity_id not in flat.index:
            return {"model": "be2", "available": False, "reason": "이 종목은 그 세션 FA 행이 없다 — BE2 점수 대상 밖",
                    "stored": stored}
        row = flat.loc[[entity_id]].to_numpy(np.float32)
        contrib = np.mean([b.predict(row, pred_contrib=True)[0] for b in batch["boosters"]], axis=0)
        names = list(batch["model"].features)
        pairs = sorted(zip(names, contrib[:-1], row[0], strict=True), key=lambda t: -abs(t[1]))
        top = [{"feature": n, "contrib": float(c), "value": float(v)} for n, c, v in pairs[:BE2_TOP]]
        rest = float(sum(c for _, c, _ in pairs[BE2_TOP:]))
        preds = pd.Series(np.mean([b.predict(flat.to_numpy(np.float32)) for b in batch["boosters"]], axis=0),
                          index=flat.index)
        rank = int((preds > preds[entity_id]).sum()) + 1
        return {"model": "be2", "available": True, "part": "GBM 절반(시드 평균)",
                "part_note": "BE2 는 트랜스포머와 GBM 의 세션 안 백분위 평균이다. 트랜스포머 절반은 입력 기여를 분해하지 않는다.",
                "bias": float(contrib[-1]), "raw": float(preds[entity_id]), "rank": rank, "n": len(preds),
                "bars": top, "rest": rest if len(pairs) > BE2_TOP else None, "n_features": len(names), "stored": stored}
    if "ranker" in alpha:
        batch = _ranker_batch(store, as_of=as_of, market=market, models_root=models_root, run_id=run_id)
        stored = _stored_score(store, as_of=as_of, market=market, entity_id=entity_id, analyst="ranker")
        if batch["model"] is None:
            return {"model": "ranker", "available": False,
                    "reason": "그 세션에 쓸 수 있는 랭커 모델이나 입력 신호가 없다", "stored": stored}
        features = batch["features"]
        if entity_id not in features.index:
            return {"model": "ranker", "available": False,
                    "reason": "이 종목은 그 세션 랭커 입력에 없다(거래 가능 명단 밖이거나 신호 없음)", "stored": stored}
        row = features.loc[[entity_id]].to_numpy(np.float32)
        contrib = batch["booster"].predict(row, pred_contrib=True)[0]
        raw = batch["raw"]
        bars = [{"feature": f, "contrib": float(c), "value": float(v)}
                for f, c, v in zip(batch["model"].features, contrib[:-1], row[0], strict=True)]
        return {"model": "ranker", "available": True, "part": "LightGBM 전체",
                "file": batch["model"].path.name, "bias": float(contrib[-1]), "raw": float(raw[entity_id]),
                "rank": int((raw > raw[entity_id]).sum()) + 1, "n": len(raw),
                "bars": sorted(bars, key=lambda b: -abs(b["contrib"])), "rest": None,
                "value_note": "입력 값은 그 세션 안 순위 정규분위(rank-gauss) — 0 이 중앙",
                "stored": stored}
    return {"model": None, "available": False, "reason": "그 세션 점수가 학습 모델이 아니다(가중 합성)", "stored": None}


def ttm(store: Store, *, as_of: datetime, market: str, entity_id: str) -> dict[str, Any]:
    """TTM(`tsfm`) 저장 점수와 실제 가격선. r̂ 원값·예측 경로는 창고에 없다 — 있는 것만."""
    frame = store.get(SIGNALS, as_of=as_of, entity=[entity_id], lookback=TTM_DAYS, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "analyst", "score", "analyst_version"])
    points = []
    version = None
    if not frame.empty:
        frame = frame[frame["analyst"].astype(str) == constraints_module.TSFM_ANALYST]
        frame = frame.sort_values("observed_at").groupby("valid_from", as_index=False).tail(1).sort_values("valid_from")
        points = [{"at": _iso_kst(r["valid_from"]), "score": float(r["score"])} for r in frame.to_dict(orient="records")]
        version = str(frame["analyst_version"].iloc[-1]) if not frame.empty else None
    from quant_rl_trading.store.prices import read_prices

    prices = read_prices(store, as_of=as_of, entity=[entity_id], lookback=PRICE_DAYS, market=market,
                         columns=["close", "valid_from"], adjusted=True)
    line = []
    if not prices.empty:
        prices = prices.sort_values("valid_from")
        line = [{"at": _iso_kst(t), "close": float(c)} for t, c in zip(prices["valid_from"], prices["close"], strict=True)]
    return {"version": version, "points": points, "prices": line,
            "stored_note": "창고에 저장된 것은 그날 계산 대상 안 백분위 점수(−1 ~ +1)뿐이다. 예측 수익률 원값(r̂)·예측 경로는 저장되지 않는다."}


def inside(store: Store, *, as_of: datetime, market: str, entity_id: str | None,
           models_root: Path | None = None) -> dict[str, Any]:
    """AI 속 들여다보기 — 한 종목의 점수 모델 기여 · TTM · '왜 샀나' 단계 요약."""
    record = why.session_record(store, as_of=as_of, market=market)
    stages = record["stages"] if record else {}
    select = (stages.get("select") or {}).get("payload", {})
    candidates_list = [str(e) for e in select.get("candidates", [])]
    entity = entity_id or (candidates_list[0] if candidates_list else None)
    if record is None or entity is None:
        return {"session": None, "entity_id": entity, "name": None, "contrib": None, "ttm": None, "why": None,
                "reason": "as_of 에 보이는 세션 기록이 없다 — 모름" if record is None else "선정 명단이 비었다"}
    session_as_of = record["as_of"]
    alpha = sorted(k for k, v in (select.get("weights") or {}).items() if v)
    try:
        contrib = contributions(store, as_of=session_as_of, market=market, entity_id=entity, alpha=alpha,
                                models_root=models_root, run_id=record["run_id"])
    except Exception as error:
        contrib = {"available": False, "reason": f"기여 계산 실패 — {type(error).__name__}", "stored": None}
    equity = (stages.get("observe") or {}).get("payload", {}).get("nav")
    summary = None
    if equity:
        try:
            summary = why._screen_summary(store, as_of=session_as_of, market=market, equity=float(equity),
                                          run_id=record["run_id"])
        except Exception:
            summary = None
    held = entity in ((stages.get("allocate") or {}).get("payload", {}).get("weights") or {})
    filters = why._filter_step(store, as_of=as_of, entity_id=entity, summary=summary)
    filters.pop("risk_hist", None)
    rule = why._rule_step(store, market=market, entity_id=entity, summary=summary, stages=stages,
                          session_as_of=session_as_of, selected_list=candidates_list, held=held,
                          recorded=True)
    weight = why._weight_step(entity_id=entity, stages=stages, realized={})
    names = names_module.of(store, as_of=as_of, entities=[entity])
    return {
        "session": {"run_id": record["run_id"], "as_of": _iso_kst(session_as_of)},
        "entity_id": entity, "name": names.get(entity), "alpha": alpha,
        "contrib": contrib,
        "ttm": ttm(store, as_of=as_of, market=market, entity_id=entity),
        "why": {"filters": filters, "rule": rule, "weight": weight},
    }


__all__ = ["AI_ANALYSTS", "FLOW_KEYS", "STORY_OF", "candidates", "contributions", "flow", "inside", "sessions", "ttm"]
