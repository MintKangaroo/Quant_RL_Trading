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

WEIGHTS = "analyst_weights"
SIGNALS = "signals"
VERDICTS = "verdicts"

#: 아직 구현되지 않았거나 데이터가 없어 관찰 모드인 Analyst.
#: 명단에서 빼지 않는다 — 빠지면 "왜 없지" 를 아무도 묻지 않게 된다.
PLANNED = {
    "chart": "가격 추세",
    "flow_kr": "투자자별 수급 (LS t1717)",
    "flow_us": "미장 수급 — 데이터 없음",
    "fundamental": "DART 재무",
    "news": "공시·뉴스 필터 (Verdict)",
    "sns": "펌핑 탐지 (Verdict)",
    "regime": "지수·변동성",
    "event": "달력",
    "risk": "상관·변동성·유동성",
    "volume": "거래량 급증 (chart 에서 분리)",
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


def roster(store: Store, *, as_of: datetime, lookback: int) -> list[dict[str, Any]]:
    """Analyst 전원의 상태. 측정 안 된 것도 명단에 남긴다."""
    measured = latest_weights(store, as_of=as_of, lookback=lookback)
    by_name: dict[str, dict[str, Any]] = {}
    if not measured.empty:
        for row in measured.to_dict(orient="records"):
            by_name[str(row["entity_id"])] = row

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
                    "ic": None,
                    "passed": None,
                    "sample_days": None,
                    "version": None,
                    "market": None,
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


def ic_history(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """Analyst 별 IC 측정 이력.

    한 점만 있으면 추이가 아니다. 그래도 그리는 이유는 **감쇠를 보기 위해**서다
    — 재측정이 쌓이면 IC 가 내려가는 것이 여기서 먼저 보인다 (알파 소멸).
    """
    frame = store.get(WEIGHTS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"series": [], "points": 0}

    series: list[dict[str, Any]] = []
    for name, group in frame.sort_values("valid_from").groupby("entity_id"):
        series.append(
            {
                "analyst": str(name),
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


def signal_activity(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """Signal 기록 현황. '점수를 내고 있는가' 를 본다.

    가중치 0(관찰 모드)이어도 **Signal 은 계속 기록돼야 한다.** 기록이 멈추면
    나중에 그 Analyst 를 켤 근거를 만들 수 없다.
    """
    frame = store.get(SIGNALS, as_of=as_of, lookback=lookback)
    if frame.empty:
        return {"analysts": [], "total": 0}

    rows: list[dict[str, Any]] = []
    for (name, version), group in frame.groupby(["analyst", "analyst_version"]):
        latency = group["latency_ms"].astype(float)
        rows.append(
            {
                "analyst": str(name),
                "version": str(version),
                "signals": len(group),
                "entities": int(group["entity_id"].nunique()),
                "sessions": int(group["valid_from"].dt.date.nunique()),
                "last_as_of": group["valid_from"].max().isoformat(),
                "latency_p50_ms": float(latency.quantile(0.5)),
                "latency_p90_ms": float(latency.quantile(0.9)),
                "mean_confidence": float(group["confidence"].astype(float).mean()),
            }
        )
    return {"analysts": sorted(rows, key=lambda item: str(item["analyst"])), "total": len(frame)}


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
            "scorecard": scorecard.evaluate_blocks(store, as_of=as_of, lookback=lookback),
        }

    blocked = frame[frame["decision"] == "block"]
    active = blocked[blocked["expires_at"] > as_of]

    return {
        "blocks": len(blocked),
        "active": len(active),
        # 차단이 실제로 손실을 피하게 해 줬는지. IC 를 못 쓰는 이 둘의
        # 유일한 검증 수단이다.
        "scorecard": scorecard.evaluate_blocks(store, as_of=as_of, lookback=lookback),
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
    people = roster(store, as_of=as_of, lookback=lookback)
    activity = signal_activity(store, as_of=as_of, lookback=lookback)
    verdicts = verdict_scorecard(store, as_of=as_of, lookback=lookback)

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
    "signal_activity",
    "summary",
    "verdict_scorecard",
]
