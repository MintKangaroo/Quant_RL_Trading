"""LS t1511 지수 보충 — 세션 판정과 행 변환."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tools.collect_indices_ls import completed_session, rows_from_client


def _kst(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)


def test_마감_뒤면_오늘_장중이면_없음_개장_전이면_전_거래일() -> None:
    thu = date(2026, 8, 27)
    assert completed_session(_kst(2026, 8, 27, 16, 0)) == thu
    assert completed_session(_kst(2026, 8, 27, 11, 0)) is None
    assert completed_session(_kst(2026, 8, 28, 6, 0)) == thu
    # 토요일 아침 → 금요일
    assert completed_session(_kst(2026, 8, 29, 6, 0)) == date(2026, 8, 28)


class _Client:
    def request_tr(self, path, tr, payload):
        code = payload["t1511InBlock"]["upcode"]
        if code == "301":
            return {"t1511OutBlock": {"pricejisu": "0"}}  # 코스닥 응답 없음
        return {"t1511OutBlock": {
            "pricejisu": "6912.37", "openjisu": "6996.12", "highjisu": "6996.12",
            "lowjisu": "6841.88", "volume": 265457, "value": 22468982,
        }}


def test_t1511_응답을_indices_행으로_바꾼다() -> None:
    now = _kst(2026, 8, 27, 16, 5)
    rows = rows_from_client(_Client(), day=date(2026, 8, 27), observed_at=now)
    assert [r["entity_id"] for r in rows] == ["KR:IDX:KOSPI"]
    row = rows[0]
    assert row["close"] == 6912.37 and row["open"] == 6996.12 and row["low"] == 6841.88
    assert row["valid_from"] == datetime(2026, 8, 27, tzinfo=UTC)
    assert row["observed_at"] == now and row["source"] == "ls_t1511" and row["board"] == "KOSPI"
