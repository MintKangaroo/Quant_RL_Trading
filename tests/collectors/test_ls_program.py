"""LS t1636 프로그램매매 — 페이지를 끝까지 넘기는가, 겹친 종목을 접는가."""
from __future__ import annotations

from datetime import UTC, datetime

from quant_rl_trading.collectors import ls_program as program
from quant_rl_trading.store.tables import table_names


class FakeClient:
    """LS 연속조회는 **본문 cts_idx + 헤더 tr_cont** 를 같이 줘야 다음 쪽이 온다(2026-09-21 실측)."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages
        self.calls: list[tuple[int, str]] = []

    def request_tr(self, path: str, tr: str, body: dict, *, tr_cont: str = "N",
                   tr_cont_key: str = "", with_headers: bool = False) -> dict:
        assert (path, tr) == (program.PATH, program.TR)
        assert with_headers, "헤더를 안 받으면 '이어서' 표시를 못 읽는다"
        block = body[f"{tr}InBlock"]
        assert block["gubun2"] == "0", "순매수 정렬이면 장중에 페이지 사이로 종목이 샌다"
        cts = int(block["cts_idx"])
        self.calls.append((cts, tr_cont))
        index = cts // 20
        rows = self.pages[index] if index < len(self.pages) else []
        more = index + 1 < len(self.pages)
        return {f"{tr}OutBlock1": rows,
                f"{tr}OutBlock": {"cts_idx": (index + 1) * 20 if more else cts},
                "_cont": {"tr_cont": "Y" if more else "N", "tr_cont_key": "0"}}


def item(code: str, net: str) -> dict:
    return {"shcode": code, "svalue": net, "stksvalue": "10", "offervalue": "4", "svolume": "1",
            "stksvolume": "2", "offervolume": "1", "sgta": "100", "mkcap_cmpr_val": "0.01",
            "price": "1,000", "volume": "5"}


def test_끝까지_넘기고_겹친_종목은_마지막_값으로_접는다() -> None:
    client = FakeClient([
        [item("005930", "6"), item("000660", "3")],
        [item("000660", "9"), item("035420", "-2")],
        [item("207940", "1")],
    ])
    items = program.fetch_board(client, "0")
    assert client.calls == [(0, "N"), (20, "Y"), (40, "Y")], "본문 cts_idx 와 헤더 tr_cont 를 같이 줘야 한다"
    assert {i["shcode"]: i["svalue"] for i in items} == {
        "005930": "6", "000660": "9", "035420": "-2", "207940": "1"}


def test_같은_쪽이_반복되면_멈춘다() -> None:
    """초당 1건 제한에서 무한 루프는 그날 수집 전체를 태운다."""
    same = [item("005930", "1")]
    client = FakeClient([same, same, same, same])
    program.fetch_board(client, "0")
    assert len(client.calls) == 2


def test_행은_수집_시각을_관측_시각으로_갖는다() -> None:
    session = datetime(2026, 9, 21, 6, 30, tzinfo=UTC)
    seen = datetime(2026, 9, 21, 6, 36, 12, tzinfo=UTC)
    rows = program.normalize([item("005930", "6")], board="KOSPI", valid_from=session, observed_at=seen)
    row = rows[0]
    assert row["entity_id"] == "KR:005930" and row["observed_at"] == seen != row["valid_from"]
    assert row["net_value"] == 6.0 and row["price"] == 1000.0
    assert program.TABLE in table_names()
