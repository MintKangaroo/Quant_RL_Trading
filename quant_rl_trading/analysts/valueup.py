"""밸류업·주주환원 공시 묶음(BA 5) — 시행 BA(docs/protocols/kr-valueup-2026-09.md)의 피처 정의.

**연구 도구와 실전(BE2 shadow)이 같은 함수를 쓴다**(불변식 5). 이 함수는 원래 `tools/trial_kr_valueup.py` 에
있었고, 마지막 모델 회차의 FA 패널(`tools/final_round_kit._ba_block`)이 그것을 불렀다. BE2 를 매일 돌리려면
패키지(`analysts/fa_features.py`)가 같은 규칙을 불러야 하는데 패키지는 `tools` 를 부르지 않는다 — 그래서
**본문을 그대로 옮기고** 도구는 여기서 다시 내보낸다(`from quant_rl_trading.analysts.valueup import ...`).
규칙은 한 줄도 바꾸지 않았다.

창고 `documents`(DART) 를 한 번 읽고 세션마다 **관측 시각 < 그 세션 개장(09:00 KST = 00:00 UTC)** 인 공시만 센다.
"""
from __future__ import annotations

from datetime import UTC, datetime, time

import numpy as np
import pandas as pd

WINDOW_DAYS = 365
#: (피처, 제목 규칙) — 자회사 공시·정정·첨부추가는 뺀다(같은 결정을 두 번 세지 않는다).
KINDS = {
    "vu_plan": r"^기업가치제고계획\(자율공시\)",
    "vu_cancel": r"^주식소각결정$",
    "vu_buyback": r"^주요사항보고서\(자기주식취득(?:신탁계약체결)?결정\)$",
    "vu_dividend": r"^현금ㆍ현물배당결정$",
}
NEW = [*KINDS, "vu_plan_age"]


def features(store, sessions: list, entities: set[str], *, as_of: datetime | None = None) -> pd.DataFrame:  # type: ignore[no-untyped-def,type-arg]
    """(entity_id, session, vu_plan·vu_cancel·vu_buyback·vu_dividend·vu_plan_age).

    ``as_of`` 는 창고를 읽는 시점이다. 주지 않으면 연구 도구의 원래 규칙(마지막 세션 23:00 UTC)이다.
    실전 경로는 세션 공표 시각을 준다 — 어느 쪽이든 **관측 시각 < 그 세션 개장** 인 공시만 세므로 값은 같다
    (공시가 개장 뒤 ~ 23:00 UTC 사이에 관측되더라도 그 세션에는 안 들어간다).
    """
    end = as_of or datetime.combine(sessions[-1], time(23), tzinfo=UTC)
    docs = store.get("documents", as_of=end, lookback=(sessions[-1] - sessions[0]).days + WINDOW_DAYS + 30,
                     columns=["entity_id", "valid_from", "observed_at", "title"])
    docs = docs[docs["entity_id"].isin(entities)]
    title = docs["title"].astype(str).str.strip()
    opens = np.array([np.datetime64(datetime.combine(s, time(0), tzinfo=UTC).replace(tzinfo=None), "ns") for s in sessions])  # 09:00 KST
    per_kind = []
    for kind, pattern in KINDS.items():
        hit = docs[title.str.match(pattern)]
        # 같은 종목·같은 날 여러 건(정정 원본 등)은 한 번 — 결정 한 번이 한 사건이다.
        hit = hit.assign(day=pd.to_datetime(hit["valid_from"]).dt.date).sort_values("observed_at").drop_duplicates(["entity_id", "day"])
        rows = []
        for entity, g in hit.groupby("entity_id"):
            seen = np.sort(pd.to_datetime(g["observed_at"]).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]"))
            upto = np.searchsorted(seen, opens, side="left")                               # 개장 전 관측만
            since = np.searchsorted(seen, opens - np.timedelta64(WINDOW_DAYS, "D"), side="left")
            f = pd.DataFrame({"entity_id": entity, "session": sessions, kind: (upto - since).astype(np.float32)})
            if kind == "vu_plan":
                last = seen[np.maximum(upto - 1, 0)]
                age = ((opens - last) / np.timedelta64(1, "D")).astype(float)
                f["vu_plan_age"] = np.where(upto > 0, np.minimum(age, WINDOW_DAYS), np.nan).astype(np.float32)
            rows.append(f)
        if rows:
            per_kind.append(pd.concat(rows, ignore_index=True).set_index(["entity_id", "session"]))
    if not per_kind:
        return pd.DataFrame(columns=["entity_id", "session", *NEW])
    return pd.concat(per_kind, axis=1).reset_index()


__all__ = ["KINDS", "NEW", "WINDOW_DAYS", "features"]
