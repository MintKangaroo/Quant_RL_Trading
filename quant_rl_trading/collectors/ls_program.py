"""종목별 프로그램매매 — LS ``t1636`` (국장).

## 왜 t1636 인가

종목별 추이(``t1637``)는 **당일 분 단위**만 주고 종목당 1콜이라 전 종목에 47분이 든다.
``t1636`` 은 종목별 하루 합계를 **20종목씩 순위 페이지**로 준다 — ``cts_idx`` 로 끝까지 넘기면
전 시장이 140콜 남짓(초당 1건 제한, 약 3분)이다. 2026-09-21 실호출로 확인했다.

## 과거는 못 받는다

이 TR 에는 날짜 인자가 없다 — **지금 값**뿐이다. 그래서 매일 장 마감 직후 받아 쌓는다.
하루를 놓치면 그날은 영영 없다(재수집 불가). 놓친 날을 0 으로 채우지 않는다 — 없는 것은 없는 것이다.

## 정렬은 시가총액비중(gubun2=0)

순매수 상위로 정렬하면 장중에 순위가 바뀌어 페이지 사이에 종목이 빠지거나 겹친다. 시총 비중은
몇 분 사이에 안 바뀐다. 그래도 겹치면 마지막 값을 쓴다(종목코드로 접는다).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

PATH = "/stock/program"
TR = "t1636"
TABLE = "program_trading"
SOURCE = "ls-t1636"
BOARDS = {"0": "KOSPI", "1": "KOSDAQ"}
#: 안전장치. 20종목 × 120쪽 = 2,400종목 — 한 시장의 상장 종목 수를 넘는다.
MAX_PAGES = 120


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_board(client: Any, gubun: str) -> list[dict[str, Any]]:
    """한 시장의 전 종목. ``cts_idx`` 가 더 안 나아가거나 빈 쪽이 오면 끝이다."""
    rows: dict[str, dict[str, Any]] = {}
    cts: Any = " "
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        body = {f"{TR}InBlock": {
            "gubun": gubun, "gubun1": "1", "gubun2": "0", "shcode": "", "cts_idx": cts, "exchgubun": "K",
        }}
        data = client.request_tr(PATH, TR, body)
        page = data.get(f"{TR}OutBlock1") or []
        if not page:
            break
        for item in page:
            code = str(item.get("shcode") or "").strip()
            if code:
                rows[code] = item
        nxt = (data.get(f"{TR}OutBlock") or {}).get("cts_idx")
        key = str(nxt).strip()
        if not key or key in ("0", "") or key in seen:
            break
        seen.add(key)
        cts = nxt
    return list(rows.values())


def normalize(items: list[dict[str, Any]], *, board: str, valid_from: datetime,
              observed_at: datetime) -> list[dict[str, Any]]:
    out = []
    for item in items:
        code = str(item.get("shcode") or "").strip()
        if not code:
            continue
        out.append({
            "entity_id": f"KR:{code}",
            "valid_from": valid_from,
            "observed_at": observed_at,
            "source": SOURCE,
            "market": "KR",
            "board": board,
            "net_value": _number(item.get("svalue")),
            "buy_value": _number(item.get("stksvalue")),
            "sell_value": _number(item.get("offervalue")),
            "net_volume": _number(item.get("svolume")),
            "buy_volume": _number(item.get("stksvolume")),
            "sell_volume": _number(item.get("offervolume")),
            "market_cap": _number(item.get("sgta")),
            "mkcap_ratio": _number(item.get("mkcap_cmpr_val")),
            "price": _number(item.get("price")),
            "volume": _number(item.get("volume")),
        })
    return out


def run_id(day: Any) -> str:
    return f"program-ls-KR-{day:%Y%m%d}"
