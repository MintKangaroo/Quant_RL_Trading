"""Agent Health 집계 — Analyst 가 살아 있는지, 그리고 쓸모가 있는지.

Data Quality 가 "데이터가 썩고 있나" 를 본다면 이 화면은 **"에이전트가 썩고
있나"** 를 본다. 둘은 다른 질문이다. 데이터가 완벽해도 Analyst 의 IC 는
시간이 지나면 감쇠한다(알파 소멸). 그걸 못 보면 죽은 신호에 계속 가중치를
주게 된다.

여기서 가장 중요한 숫자는 IC 가 아니라 **가중치**다. IC 0.03 을 넘지 못한
Analyst 는 가중치 0(관찰 모드)으로 동작한다 — 점수는 계속 기록하되 매매에는
쓰지 않는다. 그 상태가 화면에 상시 보여야, 검증되지 않은 것이 조용히 켜져
있는 일이 생기지 않는다.

**없는 것은 없다고 말한다.** 아직 측정하지 않은 Analyst, 아직 비어 있는
Verdict 성적표는 0 으로 채우지 않고 비어 있음을 그대로 돌려준다. 0 과
'모름' 은 다른 사실이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.analysts import scorecard
from quant_rl_trading.store import Store
from quant_rl_trading.store.memo import derived
from quant_rl_trading.selector import weights as weights_module

WEIGHTS = "analyst_weights"
SIGNALS = "signals"
VERDICTS = "verdicts"

#: 아직 구현되지 않았거나 데이터가 없어 관찰 모드인 Analyst.
#: 명단에서 빼지 않는다 — 빠지면 "왜 없지" 를 아무도 묻지 않게 된다.
PLANNED = {
    "chart": "가격 추세",
    "flow_kr": "투자자별 수급 (LS t1717)",
    "flow_us": "미장 수급 — FINRA 일별 공매도 거래량 (short_flow)",
    "fundamental": "DART 재무",
    "news": "공시·뉴스 필터 (Verdict)",
    "sns": "펌핑 탐지 (Verdict)",
    "regime": "지수·변동성",
    "event": "달력",
    "risk": "상관·변동성·유동성",
    "volume": "거래량 급증 (chart 에서 분리)",
    "ranker": "기초 점수 6개의 순위 목적 GBM 결합 (시행 L)",
}


def latest_weights(store: Store, *, as_of: datetime, lookback: int) -> pd.DataFrame:
    """Analyst 별 가장 최근 측정 결과.

    같은 Analyst 를 여러 번 측정하면 행이 쌓인다 (append-only). 현재 상태는
    그중 가장 늦은 ``valid_from`` 이다.
    """
    frame = store.get(WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return frame
    return frame.sort_values("valid_from").groupby(["entity_id", "market"]).tail(1)


def roster(
    store: Store, *, as_of: datetime, lookback: int, market: str | None = None,
) -> list[dict[str, Any]]:
    """Analyst 전원의 상태. 측정 안 된 것도 명단에 남긴다."""
    if market == "ALL":
        return [
            item for region in ("KR", "US")
            for item in roster(store, as_of=as_of, lookback=lookback, market=region)
        ]
    measured = latest_weights(store, as_of=as_of, lookback=lookback)
    if market is not None and not measured.empty:
        measured = measured.loc[measured["market"] == market]
    by_name: dict[str, dict[str, Any]] = {}
    if not measured.empty:
        for row in measured.to_dict(orient="records"):
            by_name[str(row["entity_id"])] = row
    # **적용 가중치** — 갱신을 K 세션에 걸쳐 섞은 값(selector.md §6). 측정값과 다를 수 있고,
    # 매매가 보는 건 이쪽이다. 시장이 없으면 섞을 달력이 없어 측정값 그대로 둔다.
    applied: dict[str, float] = {}
    if market in ("KR", "US"):
        applied = weights_module.measured_weights(store, as_of=as_of, market=market, lookback=lookback)

    out: list[dict[str, Any]] = []
    for name, note in sorted(PLANNED.items()):
        row = by_name.get(name)
        if row is None:
            out.append(
                {
                    "analyst": name,
                    "note": note,
                    "measured": False,
                    # 측정 전에는 가중치가 0 이다. "아직 모름" 이 아니라
                    # "아직 자격 없음" 이 맞다 — 검증 전에는 매매에 쓰지 않는다.
                    "weight": 0.0,
                    "applied": 0.0,
                    "ic": None,
                    "passed": None,
                    "sample_days": None,
                    "version": None,
                    "market": market,
                    "measured_at": None,
                }
            )
            continue
        out.append(
            {
                "analyst": name,
                "note": note,
                "measured": True,
                "weight": float(row["weight"]),
                "applied": float(applied.get(name, 0.0)) if applied else float(row["weight"]),
                "ic": float(row["ic"]),
                "ic_threshold": float(row["ic_threshold"]),
                "passed": bool(row["passed"]),
                "sample_days": int(row["sample_days"]),
                "version": str(row["analyst_version"]),
                "market": str(row["market"]),
                "measured_at": row["valid_from"].isoformat(),
            }
        )
    return out


def ic_history(
    store: Store, *, as_of: datetime, lookback: int, market: str | None = None,
) -> dict[str, Any]:
    """Analyst 별 IC 측정 이력.

    한 점만 있으면 추이가 아니다. 그래도 그리는 이유는 **감쇠를 보기 위해**서다
    — 재측정이 쌓이면 IC 가 내려가는 것이 여기서 먼저 보인다 (알파 소멸).
    """
    frame = store.get(WEIGHTS, as_of=as_of, lookback=lookback)
    if market not in (None, "ALL") and not frame.empty:
        frame = frame.loc[frame["market"] == market]
    if frame.empty:
        return {"series": [], "points": 0}

    series: list[dict[str, Any]] = []
    label_market = market == "ALL" or (market is None and frame["market"].nunique() > 1)
    for (name, region), group in frame.sort_values("valid_from").groupby(["entity_id", "market"]):
        series.append(
            {
                "analyst": f"{name} · {region}" if label_market else str(name),
                "market": str(region),
                "points": [
                    {
                        "at": row["valid_from"].isoformat(),
                        "ic": float(row["ic"]),
                        "threshold": float(row["ic_threshold"]),
                        "passed": bool(row["passed"]),
                    }
                    for row in group.to_dict(orient="records")
                ],
            }
        )
    return {"series": series, "points": len(frame)}


#: 신호 현황 창(거래일 아닌 달력일). 90일 창은 통계가 아니라 부하였다.
SIGNAL_ACTIVITY_DAYS = 14


#: 신호 현황이 읽는 열. 요약 카드와 신호 패널이 **같은 인자**로 읽어야 한다 — 두 API 가
#: 동시에 오면 프로세스 캐시(``SharedMemo``)가 한 번만 읽고 나눠 준다. 열을 따로 좁히면
#: 질의가 둘이 되어 71만 행 정렬이 두 번 동시에 돈다(실측 각 1초 → 각 4초).
_SIGNAL_COLUMNS = [
    "entity_id", "valid_from", "analyst", "analyst_version", "latency_ms", "confidence",
]


def _signal_frame(store: Store, *, as_of: datetime, lookback: int) -> tuple[pd.DataFrame, int]:
    window = min(int(lookback), SIGNAL_ACTIVITY_DAYS)
    return store.get(SIGNALS, as_of=as_of, lookback=window, columns=_SIGNAL_COLUMNS), window


def signal_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """Signal 기록 현황. '점수를 내고 있는가' 를 본다.

    가중치 0(관찰 모드)이어도 **Signal 은 계속 기록돼야 한다.** 기록이 멈추면
    나중에 그 Analyst 를 켤 근거를 만들 수 없다.

    요약 카드도 이것을 부른다(건수·경고). 결과는 프로세스 캐시가 기억한다
    (``store.memo.derived``) — 탭이 요약 → 신호 패널을 차례로 부를 때 71만 행을 두 번
    읽지 않는다. 창(``window``)이 같으면 같은 답이라 키는 창으로 잡는다.
    """
    window = min(int(lookback), SIGNAL_ACTIVITY_DAYS)
    return derived(
        store,
        ("agent_health.signal_activity", as_of, window),
        lambda: _signal_activity(store, as_of=as_of, lookback=lookback),
    )


def _signal_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    # 현황은 최근 2주면 답한다 — 90일 전 컬럼(evidence_json 등)을 3백만 행 읽던 것이
    # Agent Health 탭 6초의 정체였다(2026-08-30). 창과 컬럼을 좁힌다.
    frame, window = _signal_frame(store, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"analysts": [], "total": 0, "window_days": window}

    # 그룹을 프레임째 잘라 돌지 않는다 — 70만 행을 그룹 수만큼 take 하고 그룹마다
    # ``dt.date`` 로 파이썬 date 객체를 만들던 것이 조회 뒤 0.9초였다(2026-09-29).
    # 정수 집계는 groupby 가 한 번에 내고, 실수 통계(분위·평균)는 **그룹 안 원래 순서
    # 그대로의 같은 값**에 같은 함수를 부른다 — 합산 순서가 바뀌면 끝자리가 달라진다.
    grouped = frame.groupby(["analyst", "analyst_version"])
    counts = grouped.size()
    # 문자열 키를 한 번만 부호화한다. ``ngroup`` 번호는 ``counts`` 의 순서와 같다.
    group_no = grouped.ngroup().to_numpy()
    entities = frame["entity_id"].groupby(group_no).nunique()
    # ``dt.date`` 와 같은 날 구분이다 — 둘 다 컬럼의 시간대에서 자정으로 자른다.
    sessions = frame["valid_from"].dt.normalize().groupby(group_no).nunique()
    last_seen = frame["valid_from"].groupby(group_no).max()
    latency_all = frame["latency_ms"].astype(float)
    confidence_all = frame["confidence"].astype(float)

    positions_of = grouped.indices
    rows: list[dict[str, Any]] = []
    # ``counts`` 의 순서(groupby 정렬 순)로 돈다 — ``indices`` 사전의 순서는 약속이 없고,
    # 같은 Analyst 의 두 버전이 화면에 놓이는 순서가 그것에 달려 있다.
    for number, key in enumerate(counts.index):
        name, version = key
        positions = positions_of[key]
        latency = latency_all.iloc[positions]
        rows.append(
            {
                "analyst": str(name),
                "version": str(version),
                "signals": int(counts[key]),
                "entities": int(entities.loc[number]),
                "sessions": int(sessions.loc[number]),
                "last_as_of": last_seen.loc[number].isoformat(),
                "latency_p50_ms": float(latency.quantile(0.5)),
                "latency_p90_ms": float(latency.quantile(0.9)),
                "mean_confidence": float(confidence_all.iloc[positions].mean()),
            }
        )
    return {"analysts": sorted(rows, key=lambda item: str(item["analyst"])), "total": len(frame), "window_days": window}


def scorecard_of(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """거부 성적표. 에이전트 상태·AI 리뷰 두 탭이 같은 인자로 부른다 — 프로세스 캐시가
    한 번 낸 것을 나눠 준다(``store.memo.derived``). 답은 ``evaluate_blocks`` 그대로다."""
    return derived(
        store,
        ("scorecard.evaluate_blocks", as_of, lookback),
        lambda: scorecard.evaluate_blocks(store, as_of=as_of, lookback=lookback),
    )


def verdict_scorecard(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """News·SNS 거부 성적표.

    차단한 종목의 **이후 수익률**을 추적한다. 필터가 실제로 손실을 피하게
    해 줬는지 사후에 따지지 않으면, 이 필터는 검증되지 않은 채로 매수만
    막는 장치가 된다.

    거부는 IC 로 검증할 수 없다(점수가 아니라 판정이므로). 그래서 이 성적표가
    유일한 검증 수단이다.
    """
    frame = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {
            "blocks": 0, "by_category": [], "by_analyst": [], "active": 0,
            "scorecard": scorecard_of(store, as_of=as_of, lookback=lookback),
        }

    blocked = frame[frame["decision"] == "block"]
    active = blocked[blocked["expires_at"] > as_of]

    return {
        "blocks": len(blocked),
        "active": len(active),
        # 차단이 실제로 손실을 피하게 해 줬는지. IC 를 못 쓰는 이 둘의
        # 유일한 검증 수단이다.
        "scorecard": scorecard_of(store, as_of=as_of, lookback=lookback),
        "by_category": [
            {"category": str(name), "count": int(count)}
            for name, count in blocked["category"].value_counts().items()
        ],
        "by_analyst": [
            {"analyst": str(name), "count": int(count)}
            for name, count in blocked["analyst"].value_counts().items()
        ],
    }


def summary(
    store: Store, *, as_of: datetime, lookback: int, thresholds: dict[str, Any]
) -> dict[str, Any]:
    # 요약은 거부 **건수만** 쓴다. 패널용 ``verdict_scorecard`` 를 부르면 쓰지도 않는 사후
    # 성적표(시세 수십만 행 + 구간 수익률)까지 내서 이 API 가 5~9초였다(2026-09-29 실측).
    # 신호 현황은 패널과 같은 함수다 — 기억된 결과를 신호 패널이 그대로 받는다.
    people = roster(store, as_of=as_of, lookback=lookback)
    activity = signal_activity(store, as_of=as_of, lookback=lookback)
    verdict_frame = store.get(VERDICTS, as_of=as_of, lookback=lookback)
    verdicts = {
        "blocks": 0 if verdict_frame.empty
        else int((verdict_frame["decision"] == "block").sum()),
    }

    passed = [item for item in people if item["passed"]]
    measured = [item for item in people if item["measured"]]

    return {
        "total": len(people),
        "measured": len(measured),
        "passed": len(passed),
        "observing": len(people) - len(passed),
        "active_weight": sum(float(item["weight"]) for item in people),
        "signals": activity["total"],
        "blocks": verdicts["blocks"],
        "warnings": _warnings(people, activity, thresholds),
    }


def _warnings(
    people: list[dict[str, Any]],
    activity: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[str]:
    found: list[str] = []
    passed = [item for item in people if item["passed"]]

    # M2 완료 기준: 최소 2개 Analyst 가 IC 0.03 통과.
    if len(passed) < 2:
        found.append(
            f"IC 통과 Analyst {len(passed)}명 — M2 완료 기준은 2명이다"
        )
    if not activity["analysts"]:
        found.append("기록된 Signal 이 없다. 관찰 모드여도 점수는 남겨야 한다")

    unmeasured = [item["analyst"] for item in people if not item["measured"]]
    if unmeasured:
        found.append(f"미측정 {len(unmeasured)}명: {', '.join(unmeasured)}")
    return found


__all__ = [
    "PLANNED",
    "ic_history",
    "latest_weights",
    "roster",
    "scorecard_of",
    "signal_activity",
    "summary",
    "verdict_scorecard",
]
