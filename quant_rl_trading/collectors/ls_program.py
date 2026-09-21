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
    """한 시장의 전 종목. **연속조회는 본문 + 헤더를 같이 줘야 한다.**

    2026-09-21 실측으로 조합을 확인했다(하나만으로는 첫 20행이 반복된다):

        본문 cts_idx=0  · 헤더 tr_cont=N  → 삼성전자…현대모비스 (OutBlock.cts_idx=20)
        본문 cts_idx=20 · 헤더 tr_cont=N  → **같은 쪽**
        본문 cts_idx=0  · 헤더 tr_cont=Y  → **같은 쪽**
        본문 cts_idx=20 · 헤더 tr_cont=Y  → LG전자…삼성중공업 (OutBlock.cts_idx=40)  ← 이것

    즉 다음 쪽의 위치는 **응답 OutBlock 의 cts_idx** 가 들고, 헤더 ``tr_cont='Y'`` 는 "이어서" 라는 표시다.
    """
    rows: dict[str, dict[str, Any]] = {}
    cts, cont = 0, "N"
    for _ in range(MAX_PAGES):
        body = {f"{TR}InBlock": {
            "gubun": gubun, "gubun1": "1", "gubun2": "0", "shcode": "", "cts_idx": cts, "exchgubun": "K",
        }}
        data = client.request_tr(PATH, TR, body, tr_cont=cont, tr_cont_key="", with_headers=True)
        page = data.get(f"{TR}OutBlock1") or []
        if not page:
            break
        fresh = 0
        for item in page:
            code = str(item.get("shcode") or "").strip()
            if code:
                fresh += int(code not in rows)
                rows[code] = item
        nxt = (data.get(f"{TR}OutBlock") or {}).get("cts_idx")
        header = (data.get("_cont") or {}).get("tr_cont", "N")
        try:
            nxt_i = int(str(nxt).strip())
        except (TypeError, ValueError):
            break
        # 새 종목이 하나도 없으면 멈춘다 — 같은 쪽을 영원히 도는 것을 막는다(초당 1건 제한).
        if str(header).upper() != "Y" or nxt_i <= cts or fresh == 0:
            break
        cts, cont = nxt_i, "Y"
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
