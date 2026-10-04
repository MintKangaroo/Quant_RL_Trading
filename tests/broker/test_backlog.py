"""broker/backlog.py — 과거 주문일 지정 대사 (CSPAQ13700).

네트워크를 쓰지 않는다. 조회 행은 2026-09-18 모의계좌 실응답의 모양을 그대로 베꼈다.
고정하는 것 넷:

1. 체결 확정 — 정정 사슬의 부모·자식 체결을 합쳐 적고, ``valid_from`` 은 **그날의 체결 시각**이다.
2. 취소 확정 — 취소확인 행의 수량이 잔량과 같을 때만 취소로 분류한다.
3. 증거 없음은 그대로 둔다 — 번호 없음·종목/수량 불일치·후보 날짜 둘 다 맞음.
4. 이미 적힌 체결을 두 번 세지 않는다.
5. 번호 없는 조각 — 우리 번호로 설명된 행은 후보가 아니고, 같은 종목 행이 전부 설명될 때만 미도착이다.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_rl_trading.broker import backlog
from quant_rl_trading.broker.fills import FillState, sync_fills
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.schemas.order import Side
from tools.reconcile_backlog import classify

DAY = date(2026, 9, 18)
SEOUL = ZoneInfo("Asia/Seoul")

#: 부모 주문 9882(121주 중 80주 체결) + 정정확인 10868(41주 체결) = 원주문 121주.
CHAIN = [
    {"OrdNo": 9882, "OrgOrdNo": 0, "IsuNo": "A136490", "BnsTpCode": "2", "MrcTpNm": "정상",
     "OrdQty": 121, "ExecQty": 80, "AllExecQty": 80, "ExecPrc": "10161.25",
     "OrdPrc": "10230.00", "OrdTime": "11:01:04", "LastExecTime": "11:14:35"},
    {"OrdNo": 10868, "OrgOrdNo": 9882, "IsuNo": "A136490", "BnsTpCode": "2", "MrcTpNm": "정정확인",
     "OrdQty": 41, "ExecQty": 41, "AllExecQty": 41, "ExecPrc": "10141.95",
     "OrdPrc": "10170.00", "OrdTime": "11:20:32", "LastExecTime": "11:26:23"},
]

#: 체결 없이 취소로 닫힌 주문 — 부모 44(97주, 정정취소 대기) + 취소확인 3734(97주).
CANCELLED = [
    {"OrdNo": 44, "OrgOrdNo": 0, "IsuNo": "A020000", "BnsTpCode": "2", "MrcTpNm": "정상",
     "OrdQty": 97, "ExecQty": 0, "AllExecQty": 0, "ExecPrc": "0.00", "OrdPrc": "15960.00",
     "OrdTime": "08:41:39", "LastExecTime": ":  :"},
    {"OrdNo": 3734, "OrgOrdNo": 44, "IsuNo": "A020000", "BnsTpCode": "2", "MrcTpNm": "취소확인",
     "OrdQty": 97, "ExecQty": 0, "AllExecQty": 0, "ExecPrc": "0.00", "OrdPrc": "0.00",
     "OrdTime": "09:20:23", "LastExecTime": ":  :"},
]


def candidate(
    *,
    order_id: str = "KR-2026-09-17|KR:136490|2",
    entity: str = "KR:136490",
    side: Side = Side.BUY,
    quantity: float = 121,
    broker_order_no: str = "9882",
    status: str = "cancel_unknown",
    days: tuple[date, ...] = (DAY,),
    bound: bool = True,
    filled: float = 0.0,
) -> backlog.Candidate:
    return backlog.Candidate(
        order_id=order_id,
        entity_id=entity,
        side=side,
        quantity=quantity,
        broker_order_no=broker_order_no,
        status=status,
        bound_day=DAY if bound else None,
        bound_fingerprint="fingerprint" if bound else "",
        days=days,
        filled=filled,
        confirmed_cancel=0.0,
    )


# -- 1. 체결 확정 --------------------------------------------------------------


def test_사슬_체결을_합쳐_체결시각으로_적는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    folded, raw = backlog.index_day(CHAIN)
    item = candidate()
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.reason == ""
    assert match.cumulative == 121
    assert classify(match)[0] == "체결"

    as_of = ts(2026, 9, 27, 4)
    result = sync_fills(
        store, object(), ReplayClock(as_of), as_of=as_of,
        pending=[backlog.pending_for(match)],
        queries={"KR": backlog.dated_query(DAY, folded)},
    )
    (outcome,) = result.outcomes
    assert outcome.state is FillState.RECORDED
    assert outcome.fill is not None
    assert outcome.fill.quantity == 121
    # 수량 가중평균이다 — 마지막 조각의 단가가 아니다.
    expected = (80 * 10161.25 + 41 * 10141.95) / 121
    assert abs(outcome.fill.price - expected) < 1e-6

    trades = store.get("trades", as_of=as_of)
    assert len(trades) == 1
    # **그날의 체결 시각**이 valid_from 이다. 오늘로 적으면 그날의 NAV 가 거짓이 된다.
    assert trades["valid_from"].iloc[0].tz_convert(SEOUL).strftime("%Y-%m-%d %H:%M:%S") == (
        "2026-09-18 11:26:23"
    )
    assert trades["observed_at"].iloc[0].tz_convert(SEOUL).date() == date(2026, 9, 27)


def test_체결시각이_없으면_체결을_적지_않는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    rows = [{**CHAIN[0], "LastExecTime": ":  :", "ExecTrxTime": ":  :"}]
    folded, raw = backlog.index_day(rows)
    item = candidate(quantity=121)
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    as_of = ts(2026, 9, 27, 4)
    result = sync_fills(
        store, object(), ReplayClock(as_of), as_of=as_of,
        pending=[backlog.pending_for(match)],
        queries={"KR": backlog.dated_query(DAY, folded)},
    )
    assert result.outcomes[0].state is FillState.UNKNOWN
    assert "체결 시각" in result.outcomes[0].detail
    assert store.get("trades", as_of=as_of).empty


# -- 2. 취소 확정 --------------------------------------------------------------


def test_취소확인이_잔량과_같으면_취소로_분류한다() -> None:
    folded, raw = backlog.index_day(CANCELLED)
    item = candidate(entity="KR:020000", quantity=97, broker_order_no="44")
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.cumulative == 0
    assert match.cancelled == 97
    assert classify(match)[0] == "취소"


def test_부분취소는_미확정으로_남긴다() -> None:
    rows = [CANCELLED[0], {**CANCELLED[1], "OrdQty": 50}]
    folded, raw = backlog.index_day(rows)
    item = candidate(entity="KR:020000", quantity=97, broker_order_no="44")
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert classify(match)[0] == "부분취소"


def test_전송_저널이_없으면_취소를_표기하지_않는다() -> None:
    folded, raw = backlog.index_day(CANCELLED)
    item = candidate(entity="KR:020000", quantity=97, broker_order_no="44", bound=False)
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert classify(match)[0] == "취소·저널없음"


# -- 3. 증거 없음은 그대로 둔다 ------------------------------------------------


def test_주문번호가_없으면_자동으로_해제하지_않는다() -> None:
    folded, raw = backlog.index_day(CHAIN)
    item = candidate(broker_order_no="")
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.day is None
    assert "주문번호 없음" in match.reason
    # 같은 종목·방향·수량 행은 **사람 확인용 후보로만** 보여 준다.
    assert [str(row["OrdNo"]) for row in match.orphans] == ["9882"]
    assert classify(match)[0] == "증거없음"


def test_종목이_다르면_같은_번호라도_우리_주문이_아니다() -> None:
    folded, raw = backlog.index_day(CHAIN)
    item = candidate(entity="KR:005930")
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.day is None
    assert "종목 불일치" in match.reason


def test_주문수량이_다르면_거부한다() -> None:
    folded, raw = backlog.index_day(CHAIN)
    item = candidate(quantity=120)
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.day is None
    assert "주문수량 불일치" in match.reason


def test_방향이_다르면_거부한다() -> None:
    folded, raw = backlog.index_day(CHAIN)
    item = candidate(side=Side.SELL)
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.day is None
    assert "방향 불일치" in match.reason


def test_후보_날짜_둘이_다_맞으면_사람_확인이다() -> None:
    """주문번호는 날짜마다 다시 센다 — 두 날이 다 맞으면 어느 쪽인지 알 수 없다."""
    other = date(2026, 9, 17)
    folded, raw = backlog.index_day(CHAIN)
    item = candidate(days=(other, DAY), bound=False)
    (match,) = backlog.match_all([item], {DAY: (folded, raw), other: (folded, raw)})
    assert match.day is None
    assert "둘 이상" in match.reason


def test_그날_조회에_번호가_없으면_미확정이다() -> None:
    folded, raw = backlog.index_day(CANCELLED)
    item = candidate()
    (match,) = backlog.match_all([item], {DAY: (folded, raw)})
    assert match.day is None
    assert "주문번호가 없다" in match.reason


# -- 4. 이중계상 금지 ----------------------------------------------------------


def test_스냅샷_정정이_있으면_체결을_적지_않는다() -> None:
    """스냅샷 대사가 이미 넣은 체결을 또 적으면 포지션이 두 배가 된다."""
    folded, raw = backlog.index_day(CHAIN)
    (match,) = backlog.match_all([candidate()], {DAY: (folded, raw)})
    assert classify(match)[0] == "체결"
    kind, detail = classify(match, {(DAY, "KR:136490"): 121.0})
    assert kind == "체결·스냅샷중복"
    assert "이중계상" in detail
    # 다른 날·다른 종목의 정정은 이 체결과 무관하다.
    assert classify(match, {(date(2026, 9, 17), "KR:136490"): 121.0})[0] == "체결"
    assert classify(match, {(DAY, "KR:005930"): 121.0})[0] == "체결"


def test_스냅샷_정정을_주문일과_종목으로_읽는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    as_of = ts(2026, 9, 27, 4)
    store.append(
        "trades",
        [
            {
                "entity_id": "KR:136490", "valid_from": ts(day.year, day.month, day.day, 6),
                "observed_at": ts(2026, 9, 18, 6), "source": "snapshot_reconcile", "market": "KR",
                "side": side, "quantity": quantity, "price": 10000.0, "currency": "KRW",
                "fee": 0.0, "tax": 0.0, "order_id": f"snapshot-recon-{day}|KR:136490",
            }
            # 한 날·한 종목에 정정은 한 행이다(자연키가 그 셋이다). 매도는 음수로 읽는다.
            for day, side, quantity in (
                (DAY, "buy", 121.0),
                (date(2026, 9, 17), "sell", 21.0),
            )
        ],
        ingest_run_id="test-snapshot",
        source="snapshot_reconcile",
    )
    assert backlog.snapshot_corrections(store, as_of=as_of) == {
        (DAY, "KR:136490"): 121.0,
        (date(2026, 9, 17), "KR:136490"): -21.0,
    }


def test_이미_적힌_체결은_두_번_세지_않는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    store.seed_config_defaults()
    folded, raw = backlog.index_day(CHAIN)
    as_of = ts(2026, 9, 27, 4)
    for expected in (FillState.RECORDED, FillState.UNCHANGED):
        (match,) = backlog.match_all([candidate()], {DAY: (folded, raw)})
        result = sync_fills(
            store, object(), ReplayClock(as_of), as_of=as_of,
            pending=[backlog.pending_for(match)],
            queries={"KR": backlog.dated_query(DAY, folded)},
        )
        assert result.outcomes[0].state is expected
    trades = store.get("trades", as_of=as_of)
    assert len(trades) == 1
    assert trades["quantity"].sum() == 121


def test_부분체결이_장부에_있으면_차분만_적는다(store, ts) -> None:  # type: ignore[no-untyped-def]
    """80주가 이미 적혀 있으면 정정확인분 41주만 새로 적는다."""
    store.seed_config_defaults()
    folded, raw = backlog.index_day(CHAIN)
    as_of = ts(2026, 9, 27, 4)
    first = [{**CHAIN[0]}]
    partial_folded, partial_raw = backlog.index_day(first)
    (match,) = backlog.match_all([candidate()], {DAY: (partial_folded, partial_raw)})
    sync_fills(
        store, object(), ReplayClock(as_of), as_of=as_of,
        pending=[backlog.pending_for(match)],
        queries={"KR": backlog.dated_query(DAY, partial_folded)},
    )
    (match,) = backlog.match_all([candidate(filled=80)], {DAY: (folded, raw)})
    result = sync_fills(
        store, object(), ReplayClock(as_of), as_of=as_of,
        pending=[backlog.pending_for(match)],
        queries={"KR": backlog.dated_query(DAY, folded)},
    )
    assert result.outcomes[0].state is FillState.RECORDED
    assert result.outcomes[0].fill is not None
    assert result.outcomes[0].fill.quantity == 41
    trades = store.get("trades", as_of=as_of)
    assert trades["quantity"].sum() == 121
    # 신규 체결 단가는 누적 평균가가 아니라 (누적대금 − 기록대금) / 신규수량이다.
    assert abs(result.outcomes[0].fill.price - 10141.95) < 1e-6


# -- 5. 번호 없는 조각 — 미도착 확인 (2026-10-04, KR:005945 실측 재현) ----------------
#
# 9/22 KR-2026-09-21 세션: 조각 0 은 08:40 에 나가 번호 86 으로 72주 체결, 조각 1 은 10:00:41
# 전송 응답 미수신(킬스위치)으로 ``submitting`` 에 남았다. 증권사 그날 005945 주문은 86 하나뿐.
# 9/27 미리보기는 86 을 조각 1 의 "후보" 로 보여 줬다 — 86 은 조각 0 의 번호다.

KILL_DAY = date(2026, 9, 22)
KILL_SESSION = "KR-2026-09-21"
ROW_86 = {"OrdNo": 86, "OrgOrdNo": 0, "IsuNo": "A005945", "BnsTpCode": "2", "MrcTpNm": "정상",
          "OrdQty": 72, "ExecQty": 72, "AllExecQty": 72, "ExecPrc": "23050.00", "OrdPrc": "23100.00",
          "OrdTime": "08:40:44", "LastExecTime": "09:00:08"}
ROW_OTHER = {"OrdNo": 8320, "OrgOrdNo": 0, "IsuNo": "A005385", "BnsTpCode": "2", "MrcTpNm": "정상",
             "OrdQty": 20, "ExecQty": 0, "AllExecQty": 0, "ExecPrc": "0.00", "OrdPrc": "174500.00",
             "OrdTime": "10:00:40", "LastExecTime": ":  :"}


def _kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


def _seed_kill_day(store) -> None:  # type: ignore[no-untyped-def]
    base = {"entity_id": "KR:005945", "source": "executor", "market": "KR", "session_id": KILL_SESSION,
            "side": "buy", "quantity": 72.0, "limit_price": 23100.0, "target_weight": 0.089}
    store.append(
        "orders",
        [
            {**base, "slice_seq": 0, "valid_from": _kst(date(2026, 9, 21), 16),
             "observed_at": _kst(KILL_DAY, 9, 20), "status": "filled", "reason": "broker_order_no=86"},
            {**base, "slice_seq": 1, "valid_from": _kst(KILL_DAY, 10),
             "observed_at": _kst(KILL_DAY, 10, 1), "status": "submitting", "reason": ""},
        ],
        ingest_run_id="kill-day-orders",
    )
    store.append(
        "trades",
        [{"entity_id": "KR:005945", "valid_from": _kst(KILL_DAY, 9, 20), "observed_at": _kst(KILL_DAY, 9, 21),
          "source": "broker", "market": "KR", "side": "buy", "quantity": 72.0, "price": 23050.0,
          "currency": "KRW", "fee": 0.0, "tax": 0.0, "order_id": f"{KILL_SESSION}|KR:005945|0#72"}],
        ingest_run_id="kill-day-trades",
        source="broker",
    )


def _kill_candidate(store):  # type: ignore[no-untyped-def]
    (item,) = backlog.candidates(store, as_of=_kst(date(2026, 10, 4), 12), market="KR", today=date(2026, 10, 4))
    assert item.order_id == f"{KILL_SESSION}|KR:005945|1"
    return item


def test_이미_우리_번호로_설명된_행은_후보로_보이지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    _seed_kill_day(store)
    item = _kill_candidate(store)
    as_of = _kst(date(2026, 10, 4), 12)
    day = {KILL_DAY: backlog.index_day([ROW_86, ROW_OTHER])}
    known = backlog.known_orders(store, as_of=as_of)
    assert backlog.explain(ROW_86, KILL_DAY, known) == f"{KILL_SESSION}|KR:005945|0"
    (match,) = backlog.match_all([item], day, known)
    assert "주문번호 없음" in match.reason
    assert match.orphans == ()
    # known 을 안 주면 예전처럼 보인다 — 9/27 오해가 이것이었다.
    (old,) = backlog.match_all([item], day)
    assert [str(r["OrdNo"]) for r in old.orphans] == ["86"]


def test_같은_종목_행이_전부_설명되면_미도착을_확인한다(store) -> None:  # type: ignore[no-untyped-def]
    _seed_kill_day(store)
    item = _kill_candidate(store)
    known = backlog.known_orders(store, as_of=_kst(date(2026, 10, 4), 12))
    result = backlog.check_not_received(item, {KILL_DAY: backlog.index_day([ROW_86, ROW_OTHER])}, known)
    assert result.confirmed, result.refusal
    assert result.queried == {KILL_DAY: 2}
    assert result.same_symbol == ((KILL_DAY, "86", f"{KILL_SESSION}|KR:005945|0"),)
    assert "86=KR-2026-09-21|KR:005945|0" in result.reason()


def test_설명_안_되는_같은_종목_행이_있으면_거부한다(store) -> None:  # type: ignore[no-untyped-def]
    """응답만 못 받고 실제로 들어간 경우 — 그 행이 이 조각일 수 있다."""
    _seed_kill_day(store)
    item = _kill_candidate(store)
    stranger = {**ROW_86, "OrdNo": 8315, "OrdTime": "10:00:41"}
    known = backlog.known_orders(store, as_of=_kst(date(2026, 10, 4), 12))
    result = backlog.check_not_received(item, {KILL_DAY: backlog.index_day([ROW_86, stranger])}, known)
    assert not result.confirmed
    assert "8315" in result.refusal


def test_다른_날의_같은_번호는_설명이_아니다(store) -> None:  # type: ignore[no-untyped-def]
    """번호는 날짜마다 다시 센다 — 9/23 의 86 은 조각 0 의 86 이 아니다."""
    _seed_kill_day(store)
    known = backlog.known_orders(store, as_of=_kst(date(2026, 10, 4), 12))
    assert backlog.explain(ROW_86, date(2026, 9, 24), known) == ""
    # 정정·취소 사슬의 자식은 원주문번호로 설명된다(수량은 줄어 있다).
    child = {**ROW_86, "OrdNo": 9000, "OrgOrdNo": 86, "OrdQty": 30, "MrcTpNm": "취소확인"}
    assert backlog.explain(child, KILL_DAY, known) == f"{KILL_SESSION}|KR:005945|0"


def test_조회_못_한_날이_있으면_거부한다(store) -> None:  # type: ignore[no-untyped-def]
    _seed_kill_day(store)
    item = _kill_candidate(store)
    known = backlog.known_orders(store, as_of=_kst(date(2026, 10, 4), 12))
    result = backlog.check_not_received(item, {}, known)
    assert not result.confirmed
    assert "조회 못 한" in result.refusal


def test_미도착_확인은_rejected_revision_을_append_한다(store) -> None:  # type: ignore[no-untyped-def]
    _seed_kill_day(store)
    item = _kill_candidate(store)
    now = _kst(date(2026, 10, 4), 14)
    known = backlog.known_orders(store, as_of=now)
    result = backlog.check_not_received(item, {KILL_DAY: backlog.index_day([ROW_86])}, known)
    assert backlog.record_not_received(store, ReplayClock(now), result) == 1
    orders = store.get("orders", as_of=now)
    row = orders[orders["slice_seq"] == 1].iloc[0]
    assert row["status"] == "rejected"
    assert int(row["revision"]) == 1
    assert row["reason"].startswith("미도착 확인")
    # 옛 시점 조회는 옛 상태 그대로 — 정정은 덮어쓰기가 아니다.
    before = store.get("orders", as_of=_kst(date(2026, 10, 4), 12))
    assert before[before["slice_seq"] == 1].iloc[0]["status"] == "submitting"
    # 체결 장부는 그대로 72주, 닫힌 조각은 더는 대사 대상이 아니다.
    assert store.get("trades", as_of=now)["quantity"].sum() == 72
    assert backlog.candidates(store, as_of=now, market="KR", today=date(2026, 10, 4)) == []
    # 판정 뒤 상태가 바뀌었으면(여기선 이미 닫힘) 다시 적지 않는다.
    with pytest.raises(ValueError):
        backlog.record_not_received(store, ReplayClock(now), result)
