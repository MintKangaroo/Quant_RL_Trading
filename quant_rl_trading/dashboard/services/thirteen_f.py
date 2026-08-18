"""13F 탭 집계 — **미국 기관이 분기말에 무엇을 들고 있었나.**

`services/market.py` 가 "지금 시장이 어떤가" 를 본다면 이 화면은 "큰손들이
무엇을 들고 있다고 신고했나" 를 본다. 두 질문의 시제가 다르다는 것이 이
화면의 전부다.

## 화면이 반드시 말해야 하는 것 — 낡음

13F 는 분기말 기준이고 마감이 45일이다. 실측(2026-08-18):

    버크셔 2026-06-30 보유  →  2026-08-14 공개  →  45일 지연

그 45일 사이에 다 팔았을 수도 있다. **"지금 버크셔가 애플을 22% 들고 있다"
가 아니라 "6월 30일에 그랬다고 8월 14일에 신고했다" 다.** 그래서 모든 표에
기준일과 지연 일수를 같이 띄운다 — 숫자만 크게 띄우면 사람은 그것을 현재로
읽는다.

## 접힌 줄 수를 보여주는 이유

13F 는 자회사 운용역별로 줄을 나눠 낸다. 버크셔 2026 Q2 는 89줄인데 실제
보유는 29종목이고, 애플만 12줄이었다. 수집기가 CUSIP 으로 접었고 그
사실(`folded_rows`)을 화면에 남긴다 — **합산은 가공이고, 가공한 숫자는
가공했다고 말해야 한다.**

## 변화(신규·청산·증감)는 두 분기가 있어야 한다

한 분기만 받은 기관은 변화를 못 낸다. 그때 "변화 없음" 이 아니라 **"직전
분기가 없다"** 로 적는다 — 이 저장소가 반복해서 낸 "안 물어봤다" 와 "없다"
를 뒤섞는 결함이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_rl_trading.store import Store

TABLE = "filings_13f"

#: 기관당 화면에 띄우는 상위 보유. 전부 띄우면 르네상스 3,140종목이 화면을
#: 통째로 먹는다. 나머지는 "그 외 N종목" 한 줄로 접는다.
TOP_ROWS = 15

#: 창고를 얼마나 거슬러 읽나. 13F 는 분기 데이터라 두 분기(180일)를 보려면
#: 넉넉해야 한다. 정정 신고(13F-HR/A)도 늦게 들어온다.
LOOKBACK_DAYS = 500


def _frame(store: Store, *, as_of: datetime) -> pd.DataFrame:
    frame = store.get(TABLE, as_of=as_of, lookback=LOOKBACK_DAYS)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], utc=True)
    return frame


def filers(store: Store, *, as_of: datetime) -> list[dict[str, Any]]:
    """기관 목록 — 최신 분기 기준으로 규모 순."""
    frame = _frame(store, as_of=as_of)
    if frame.empty:
        return []
    out: list[dict[str, Any]] = []
    for cik, group in frame.groupby("filer_cik"):
        latest = group["valid_from"].max()
        current = group[group["valid_from"] == latest]
        out.append({
            "filer_cik": str(cik),
            "filer_name": str(current["filer_name"].iloc[0]),
            "report_date": latest.date().isoformat(),
            "lag_days": int(current["lag_days"].iloc[0]),
            "holdings": int(len(current)),
            "total_usd": float(current["value_usd"].sum()),
            # 몇 분기를 갖고 있나. 1이면 변화를 못 낸다.
            "quarters": int(group["valid_from"].nunique()),
        })
    return sorted(out, key=lambda row: -row["total_usd"])


def holdings(store: Store, *, as_of: datetime, filer_cik: str) -> dict[str, Any]:
    """한 기관의 최신 분기 보유 + 직전 분기 대비 변화."""
    frame = _frame(store, as_of=as_of)
    if frame.empty:
        return {"rows": [], "note": "13F 를 아직 한 건도 안 받았다."}
    mine = frame[frame["filer_cik"].astype(str) == str(filer_cik)]
    if mine.empty:
        return {"rows": [], "note": f"CIK {filer_cik} 의 신고가 창고에 없다."}

    quarters = sorted(mine["valid_from"].unique())
    latest = quarters[-1]
    current = mine[mine["valid_from"] == latest]
    total = float(current["value_usd"].sum())

    # 직전 분기. **없으면 "변화 없음" 이 아니라 "못 잰다" 다.**
    previous = mine[mine["valid_from"] == quarters[-2]] if len(quarters) >= 2 else None
    before = (
        dict(zip(previous["cusip"], previous["shares"], strict=False))
        if previous is not None else {}
    )

    ranked = current.sort_values("value_usd", ascending=False)
    rows: list[dict[str, Any]] = []
    for _, row in ranked.head(TOP_ROWS).iterrows():
        prior = before.get(row["cusip"])
        if previous is None:
            change, change_pct = "미측정", None
        elif prior is None:
            change, change_pct = "신규", None
        elif prior == 0:
            change, change_pct = "신규", None
        else:
            delta = (float(row["shares"]) - float(prior)) / float(prior)
            change_pct = delta
            change = "유지" if abs(delta) < 0.005 else ("증가" if delta > 0 else "감소")
        rows.append({
            "entity_id": str(row["entity_id"]),
            "issuer": str(row["issuer"]),
            "cusip": str(row["cusip"]),
            "value_usd": float(row["value_usd"]),
            "shares": float(row["shares"]),
            "weight": float(row["weight"]),
            "folded_rows": int(row["folded_rows"]),
            "change": change,
            "change_pct": change_pct,
        })

    # 청산 — 직전에 있었는데 이번에 없다. 상위 보유만 보면 안 보이는 사실이다.
    closed: list[dict[str, Any]] = []
    if previous is not None:
        now_cusips = set(current["cusip"])
        gone = previous[~previous["cusip"].isin(now_cusips)]
        for _, row in gone.sort_values("value_usd", ascending=False).head(8).iterrows():
            closed.append({
                "issuer": str(row["issuer"]),
                "cusip": str(row["cusip"]),
                "value_usd": float(row["value_usd"]),
            })

    rest = len(current) - len(rows)
    return {
        "filer_name": str(current["filer_name"].iloc[0]),
        "report_date": pd.Timestamp(latest).date().isoformat(),
        "previous_date": (
            pd.Timestamp(quarters[-2]).date().isoformat() if previous is not None else None
        ),
        "lag_days": int(current["lag_days"].iloc[0]),
        "total_usd": total,
        "holdings": int(len(current)),
        "rest": int(rest) if rest > 0 else 0,
        "folded_total": int(current["folded_rows"].sum()),
        "rows": rows,
        "closed": closed,
        "note": (
            None if previous is not None
            else "직전 분기가 창고에 없다 — 변화를 못 잰다. "
                 "'변화 없음' 이 아니라 '아직 못 잰다' 이다."
        ),
    }


def consensus(store: Store, *, as_of: datetime) -> list[dict[str, Any]]:
    """여러 기관이 겹쳐 든 종목. **최신 분기끼리만 겹친다.**

    분기가 다른 신고를 섞으면 "세 기관이 들고 있다" 가 서로 다른 시점의
    사실이 된다. 기관마다 최신 분기를 쓰되 그 분기가 언제인지 같이 준다.
    """
    frame = _frame(store, as_of=as_of)
    if frame.empty:
        return []
    latest_rows = []
    for _, group in frame.groupby("filer_cik"):
        latest_rows.append(group[group["valid_from"] == group["valid_from"].max()])
    current = pd.concat(latest_rows)

    out: list[dict[str, Any]] = []
    for cusip, group in current.groupby("cusip"):
        if len(group) < 2:
            continue
        out.append({
            "issuer": str(group["issuer"].iloc[0]),
            "cusip": str(cusip),
            "entity_id": str(group["entity_id"].iloc[0]),
            "filers": int(len(group)),
            "total_usd": float(group["value_usd"].sum()),
            "names": sorted(str(v) for v in group["filer_name"]),
            "max_weight": float(group["weight"].max()),
            "dates": sorted({pd.Timestamp(v).date().isoformat() for v in group["valid_from"]}),
        })
    return sorted(out, key=lambda row: (-row["filers"], -row["total_usd"]))[:20]
