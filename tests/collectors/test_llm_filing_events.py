"""LLM 공시 추출(시행 L1)의 경계 — 네트워크 없이 모의 클라이언트로.

1. 스키마 검증: 열거·범위·값/단위 짝·**원문 인용**을 코드가 본다. 틀리면 한 번 다시 묻고, 또 틀리면 failed 행.
2. 같은 입력은 다시 부르지 않는다(agent_cache).
3. 예산을 넘기거나 예산 키가 없으면 부르지 않고 멈춘다(도구 rc=2).
4. observed_at = **추출 시각**. 과거 공시를 오늘 뽑아도 과거 as_of 조회에는 안 잡힌다(누수 방지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from quant_rl_trading.collectors import dart_documents as docs
from quant_rl_trading.collectors import llm_filing_events as l1
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store
from tools import extract_filing_events as tool

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
FILED = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
OLD = datetime(2025, 3, 14, 0, 0, tzinfo=UTC)
TITLE = "주요사항보고서(자기주식취득결정)"
TEXT = (
    "자기주식 취득 결정\n1. 취득예정주식(주)\n보통주식\n1,970,000\n기타주식\n-\n"
    "2. 취득예정금액(원)\n보통주식\n19,759,100,000\n기타주식\n-\n5. 취득목적\n주가 안정 및 주주가치 제고\n"
)
GOOD = {
    "direction": 1, "magnitude_value": 19_759_100_000,
    "magnitude_unit": "krw", "vs_prior": "none", "confidence": 0.9,
    "evidence": "취득예정금액(원) 19,759,100,000",
}
EVENT = "treasury_buy"


@dataclass
class _Block:
    type: str
    input: dict
    id: str = "toolu_1"


@dataclass
class _Usage:
    input_tokens: int = 2000
    output_tokens: int = 150
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _Response:
    content: list
    usage: _Usage = field(default_factory=_Usage)
    id: str = "msg_test"


class _Client:
    """답을 차례로 준다. 부른 인자를 남긴다."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers) or [GOOD]
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        return _Response(content=[_Block("tool_use", dict(answer))], id=f"msg_{len(self.calls)}")


def _document(store: Store, tmp_path: Path, *, doc_id: str, filed: datetime, text: str = TEXT,
              title: str = TITLE, doc_type: str = "buyback") -> None:
    path = docs.text_path(doc_id, filed, root=tmp_path / "_docs")
    docs.save_text(text, path)
    store.append(docs.DOCUMENTS, [{
        "entity_id": "KR:123456", "valid_from": filed, "observed_at": filed, "source": "dart",
        "revision": 1, "doc_id": doc_id, "doc_type": doc_type, "title": title, "filer": "",
        "url": "", "raw_path": str(path),
    }], ingest_run_id=f"doc-{doc_id}", source="dart")


def _todo(store: Store, lookback: int = 30):  # type: ignore[no-untyped-def]
    frame = store.get(docs.DOCUMENTS, as_of=NOW, lookback=lookback, market="KR")
    return l1.targets(frame)


#: 테스트 세계의 축적 시작일 — 정정 픽스처(원공시 8/04)가 건너뛰어지지 않게 앞으로 당긴다.
#: 실제 값(l1.FORWARD_START)은 test_amendment_of_a_pre_start_original_is_skipped 가 쓴다.
TEST_START = datetime(2026, 1, 1, tzinfo=UTC)


def _extractor(store: Store, client: _Client, **budget: Any) -> l1.FilingEventExtractor:
    store.seed_config_defaults()
    budget.setdefault("forward_start", TEST_START)
    return l1.FilingEventExtractor(store, ReplayClock(NOW), l1.Budget.from_store(store, as_of=NOW), client=client,
                                   **budget)


# -- 1. 스키마 ---------------------------------------------------------------------------------


def test_validate_accepts_the_fixed_schema() -> None:
    event = l1.validate(dict(GOOD), TEXT, event_type=EVENT)
    assert event["direction"] == 1 and event["magnitude"] == 19_759_100_000 and event["magnitude_unit"] == "krw"
    assert "event_type" not in event   # 종류는 코드가 행에 적는다


@pytest.mark.parametrize("patch, reason", [
    ({"direction": 2}, "direction"),
    ({"direction": True}, "direction"),
    ({"confidence": 1.4}, "confidence"),
    ({"magnitude_unit": "none"}, "어긋난다"),                       # 값이 있는데 단위가 none
    ({"magnitude_value": None}, "어긋난다"),                         # 단위가 있는데 값이 없다
    ({"magnitude_unit": "pct_of_sales"}, "허용 단위"),               # 자사주에 매출액 대비 단위
    ({"vs_prior": "surprise"}, "vs_prior"),
    ({"magnitude_value": 19_760_000_000, "evidence": "취득예정금액 19,760,000,000"}, "원문 숫자에 없다"),  # 반올림
    ({"evidence": "취득예정금액(원) 19,759,100,000 · 전년 30.00"}, "evidence 의 숫자"),                    # 지어낸 숫자
    ({"evidence": "취득예정금액(원)"}, "magnitude"),                  # 근거에 크기 숫자가 없다
    ({"event_type": "treasury_buy"}, "키 불일치"),                     # 종류는 LLM 이 안 낸다
])
def test_validate_rejects(patch: dict, reason: str) -> None:
    with pytest.raises(l1.SchemaError, match=reason):
        l1.validate({**GOOD, **patch}, TEXT, event_type=EVENT)


def test_number_check_ignores_formatting_not_values() -> None:
    # v1 을 떨어뜨린 서식 차이 — (주)·(원) 빼기·콜론·단위 덧붙이기 — 는 통과한다.
    ok = {**GOOD, "evidence": "취득예정금액: 19,759,100,000원 (보통주식 1,970,000주)"}
    assert l1.validate(ok, TEXT, event_type=EVENT)["evidence"]


def test_krw_may_use_table_scale_but_not_invent() -> None:
    text = "취득예정금액(천원)\n19,759,100\n취득예정주식(주)\n1,970,000\n"
    ok = {**GOOD, "magnitude_value": 19_759_100_000, "magnitude_unit": "krw", "evidence": "취득예정금액(천원) 19,759,100"}
    assert l1.validate(ok, text, event_type="treasury_buy")["magnitude"] == 19_759_100_000
    shares = {**GOOD, "magnitude_value": 1_970_000, "magnitude_unit": "pct_of_shares",
              "evidence": "취득예정주식(주) 1,970,000"}
    with pytest.raises(l1.SchemaError, match="범위"):         # 주식수를 비율 칸에 — v1 표본의 오류
        l1.validate(shares, text, event_type="treasury_buy")
    small = {**GOOD, "magnitude_value": 19_759, "magnitude_unit": "krw", "evidence": "19,759"}
    with pytest.raises(l1.SchemaError, match="범위|원문"):
        l1.validate(small, text, event_type="treasury_buy")


def test_pct_change_sign_may_differ_from_text_notation() -> None:
    text = "영업이익 | 69,786,868 | 54,542,330 | 15,244,538 | △27.95"
    ok = {**GOOD, "direction": -1, "magnitude_value": -27.95, "magnitude_unit": "pct_change",
          "evidence": "영업이익 증감비율 △27.95"}
    assert l1.validate(ok, text, event_type="prelim_earnings")["magnitude"] == -27.95


@pytest.mark.parametrize("title, expected", [
    ("단일판매ㆍ공급계약체결", "supply_contract"),
    ("[기재정정]단일판매ㆍ공급계약체결", "supply_contract"),
    ("단일판매ㆍ공급계약해지", "contract_termination"),
    ("매출액또는손익구조30%(대규모법인은15%)이상변경", "pl_change"),
    ("연결재무제표기준영업(잠정)실적(공정공시)", "prelim_earnings"),
    ("[기재정정]주요사항보고서(유상증자결정)", "rights_offering"),       # v1 표본에서 LLM 이 사채로 틀린 것
    ("주요사항보고서(전환사채권발행결정)", "convertible_issue"),
    ("주요사항보고서(신주인수권부사채권발행결정)", "convertible_issue"),
    ("주요사항보고서(자기주식취득결정)", "treasury_buy"),
    ("주요사항보고서(자기주식취득신탁계약체결결정)", "treasury_buy"),
    ("주요사항보고서(자기주식취득신탁계약해지결정)", "treasury_trust_end"),
    ("주요사항보고서(자기주식처분결정)", "treasury_sell"),
    ("주식소각결정(자기주식소각)", "treasury_cancel"),
    ("주식소각결정", "treasury_cancel"),                                # DART 실제 제목 — 자기주식 글자가 없다
    ("[기재정정]주식소각결정", "treasury_cancel"),
    ("기타경영사항(자율공시)(제5회무기명식이권부무보증사모전환사채소각결정의건)", "other"),  # 사채 소각은 자사주가 아니다
    ("최대주주변경", "control_change"),
    ("[기재정정]최대주주변경을수반하는주식양수도계약체결", "control_change"),
    ("[기재정정]최대주주변경을수반하는주식담보제공계약체결", "other"),     # 담보 계약은 변경이 아니다
])
def test_event_type_is_decided_by_code_from_title(title: str, expected: str) -> None:
    assert l1.event_type_of(title) == expected


def test_schema_failure_retries_once_then_records_failed(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    bad = {**GOOD, "evidence": "취득예정금액 99,999"}
    client = _Client(bad, bad)
    rows = _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 2                      # 첫 답 + 재시도 한 번
    retry = client.calls[1]["messages"][-1]["content"][0]
    assert retry["type"] == "tool_result" and retry["is_error"] and "원문에 없다" in retry["content"]
    assert rows[0]["status"] == "failed" and rows[0]["direction"] is None and "원문에 없다" in rows[0]["error"]
    assert rows[0]["event_type"] == EVENT   # 실패 행도 코드가 정한 종류는 남는다


def test_retry_that_fixes_the_answer_is_ok(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client({**GOOD, "direction": 5}, GOOD)
    rows = _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 2 and rows[0]["status"] == "ok" and rows[0]["direction"] == 1


def test_call_is_pinned_model_temperature_zero_forced_tool(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    rows = _extractor(store, client).run(_todo(store))
    call = client.calls[0]
    assert call["model"] == l1.MODEL and call["temperature"] == 0 and "thinking" not in call
    assert call["tool_choice"] == {"type": "tool", "name": "record_filing_event"}
    assert rows[0]["prompt_hash"] == l1.PROMPT_HASH and rows[0]["model"] == l1.MODEL
    content = call["messages"][0]["content"]
    assert content.startswith(f"사건 종류(코드가 정함): {EVENT}")
    assert "- 2. 취득예정금액(원) | 보통주식 | 19,759,100,000" in content   # 후보 칸이 원문 앞에 간다
    # 날짜·종목코드는 입력에 안 준다.
    assert "123456" not in call["messages"][0]["content"] and "2026-09-25" not in call["messages"][0]["content"]


def test_too_long_is_not_called_and_not_truncated(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED, text="가" * (l1.MAX_INPUT_CHARS + 1))
    client = _Client()
    rows = _extractor(store, client).run(_todo(store))
    assert client.calls == [] and rows[0]["status"] == "too_long"


# -- 2. 캐시 -----------------------------------------------------------------------------------


def _lose_table_write(monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
    """캐시는 적혔는데 표 적재 전에 죽은 회차를 흉내 낸다."""
    real = store.append

    def append(table: str, rows: Any, **kwargs: Any) -> int:
        if table == l1.TABLE:
            raise RuntimeError("적재 전에 죽었다")
        return real(table, rows, **kwargs)

    monkeypatch.setattr(store, "append", append)


def test_same_input_is_answered_by_cache(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    with monkeypatch.context() as patch:
        _lose_table_write(patch, store)
        with pytest.raises(RuntimeError):
            _extractor(store, client).run(_todo(store))
    second = _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 1 and second[0]["direction"] == 1
    usage = store.get("llm_usage", as_of=NOW, lookback=5)
    assert len(usage) == 1 and usage.iloc[0]["agent"] == l1.AGENT


def test_same_text_under_another_doc_id_is_answered_by_cache(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    _document(store, tmp_path, doc_id="D2", filed=FILED)   # 원문·제목이 같은 공시(재공시)
    client = _Client()
    extractor = _extractor(store, client)
    rows = extractor.run(_todo(store))
    assert len(client.calls) == 1 and extractor.cache_hits == 1 and len(rows) == 2


def test_rerun_of_an_extracted_doc_does_nothing(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    _extractor(store, client).run(_todo(store))
    again = _extractor(store, client)
    assert again.run(_todo(store)) == [] and again.superseded == 1 and len(client.calls) == 1


def test_failed_answer_is_cached_too(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    bad = {**GOOD, "vs_prior": "nope"}
    client = _Client(bad)
    with monkeypatch.context() as patch:
        _lose_table_write(patch, store)
        with pytest.raises(RuntimeError):
            _extractor(store, client).run(_todo(store))
    rows = _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 2   # 첫 회차의 두 번뿐 — 두 번째 회차는 캐시가 failed 를 돌려준다
    assert rows[0]["status"] == "failed"


def test_tool_skips_docs_already_extracted_with_this_prompt(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    _extractor(store, _Client()).run(_todo(store))
    assert tool._done(store, ReplayClock(NOW), lookback=30) == {"D1"}


# -- 정정 공시 ---------------------------------------------------------------------------------

AMENDED_TEXT = (
    "정정신고(보고)\n정정일자\n2026-09-25\n1. 정정관련 공시서류\n주요사항보고서(자기주식취득결정)\n"
    "2. 정정관련 공시서류제출일\n2026-08-04\n" + TEXT.replace("19,759,100,000", "21,000,000,000")
)
AMENDED_TITLE = "[기재정정]주요사항보고서(자기주식취득결정)"
AMENDED = {**GOOD, "magnitude_value": 21_000_000_000, "evidence": "취득예정금액(원) 21,000,000,000"}
ORIGINAL = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


def test_first_filed_reads_both_dart_formats() -> None:
    from datetime import date

    assert l1.first_filed(AMENDED_TEXT) == date(2026, 8, 4)
    assert l1.first_filed("2. 정정대상 공시서류의 최초제출일 :\n2026년 09월 15일") == date(2026, 9, 15)
    assert l1.first_filed("정정 사유만 있다") is None


def test_amendment_is_a_revision_of_the_original_event(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="ORIG", filed=ORIGINAL)
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    amended = AMENDED
    client = _Client(GOOD, amended)
    rows = _extractor(store, client).run(_todo(store, lookback=90))
    assert [(r["doc_id"], r["source_doc_id"], r["revision"], r["amended"]) for r in rows] == [
        ("ORIG", "ORIG", 0, False), ("ORIG", "AMEND", 1, True)]
    assert rows[1]["valid_from"] == ORIGINAL        # 사건은 원공시 접수일에 걸린다
    seen = l1.load(store, as_of=NOW, lookback=90)
    assert len(seen) == 1 and seen.iloc[0]["magnitude"] == 21_000_000_000   # 최신 정정 하나만 — 두 번 세지 않는다


def test_amendment_in_a_later_run_bumps_the_revision(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="ORIG", filed=ORIGINAL)
    _extractor(store, _Client()).run(_todo(store, lookback=90))
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    later = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
    store.seed_config_defaults()
    extractor = l1.FilingEventExtractor(store, ReplayClock(later), l1.Budget.from_store(store, as_of=later),
                                        client=_Client(AMENDED), forward_start=TEST_START)
    todo = tool.l1.targets(store.get(docs.DOCUMENTS, as_of=later, lookback=90, market="KR"),
                           done=tool._done(store, ReplayClock(later), lookback=90))
    rows = extractor.run(todo)
    assert [(r["doc_id"], r["revision"]) for r in rows] == [("ORIG", 1)]


def test_amendment_of_a_pre_start_original_is_skipped(store: Store, tmp_path: Path) -> None:
    """원공시가 축적 시작일(9/29) 전인 정정은 부르지 않는다 — 그 사건은 forward_only 가 어차피 뺀다."""
    _document(store, tmp_path, doc_id="ORIG", filed=ORIGINAL)
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    frame = store.get(docs.DOCUMENTS, as_of=NOW, lookback=90, market="KR")
    client = _Client(AMENDED)
    extractor = _extractor(store, client, forward_start=l1.FORWARD_START)
    assert extractor.run(l1.targets(frame[frame["doc_id"] == "AMEND"])) == []
    assert extractor.skipped_pre_start == 1 and client.calls == []


def test_orphan_amendment_uses_the_filed_date_in_its_text(store: Store, tmp_path: Path) -> None:
    # 원공시가 목록에 없다(고아). 원문에 적힌 제출일(8/04)이 시작일 전이면 건너뛰고, 못 읽으면 부른다.
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    _document(store, tmp_path, doc_id="AMEND2", filed=FILED, text=TEXT + "\n정정", title=AMENDED_TITLE)
    client = _Client(AMENDED, GOOD)
    extractor = _extractor(store, client, forward_start=l1.FORWARD_START)
    rows = extractor.run(_todo(store))
    assert extractor.skipped_pre_start == 1 and [r["source_doc_id"] for r in rows] == ["AMEND2"]


def test_new_original_after_start_is_not_skipped(store: Store, tmp_path: Path) -> None:
    after = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    _document(store, tmp_path, doc_id="NEW", filed=after)
    extractor = _extractor(store, _Client(), forward_start=l1.FORWARD_START)
    later = datetime(2026, 10, 1, tzinfo=UTC)
    extractor.clock = ReplayClock(later)
    rows = extractor.run(l1.targets(store.get(docs.DOCUMENTS, as_of=later, lookback=30, market="KR")))
    assert [r["doc_id"] for r in rows] == ["NEW"] and extractor.skipped_pre_start == 0


def test_amendment_of_an_amendment_climbs_to_the_original(store: Store, tmp_path: Path) -> None:
    """단일판매 서식의 '정정관련 공시서류제출일' 은 직전 정정의 날짜다 — 그 정정의 원문을 읽어 한 단계 더 오른다."""
    first_amend = AMENDED_TEXT.replace("2026-09-25", "2026-08-20")        # 8/20 정정 → 원공시 8/04
    second_amend = AMENDED_TEXT.replace("2026-08-04", "2026-08-20")       # 9/25 정정 → 직전 정정 8/20
    _document(store, tmp_path, doc_id="ORIG", filed=ORIGINAL, title="주요사항보고서(자기주식취득결정)(자율공시)")
    _document(store, tmp_path, doc_id="AMEND1", filed=datetime(2026, 8, 20, tzinfo=UTC), text=first_amend,
              title=AMENDED_TITLE)
    _document(store, tmp_path, doc_id="AMEND2", filed=FILED, text=second_amend, title=AMENDED_TITLE)
    frame = store.get(docs.DOCUMENTS, as_of=NOW, lookback=90, market="KR")
    amended = AMENDED
    rows = _extractor(store, _Client(amended)).run(l1.targets(frame[frame["doc_id"] == "AMEND2"]))
    assert rows[0]["doc_id"] == "ORIG" and rows[0]["valid_from"] == ORIGINAL and rows[0]["amended"]


def test_amendment_without_a_findable_original_is_an_orphan(store: Store, tmp_path: Path) -> None:
    # 같은 날 같은 제목 원공시가 둘 — 어느 쪽 정정인지 모른다. 짐작해서 묶지 않는다.
    _document(store, tmp_path, doc_id="ORIG1", filed=ORIGINAL)
    _document(store, tmp_path, doc_id="ORIG2", filed=ORIGINAL + timedelta(hours=3))
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    amended = AMENDED
    rows = _extractor(store, _Client(GOOD, GOOD, amended)).run(_todo(store, lookback=90))
    orphan = [r for r in rows if r["source_doc_id"] == "AMEND"][0]
    assert orphan["doc_id"] == "AMEND" and orphan["amended"] and orphan["revision"] == 0
    assert set(l1.load(store, as_of=NOW, lookback=90)["doc_id"]) == {"ORIG1", "ORIG2"}   # 고아는 사건으로 안 센다


def test_original_after_its_amendment_does_not_overwrite(store: Store, tmp_path: Path) -> None:
    """정정이 먼저 적혔으면(원공시 원문이 늦게 받아진 경우) 원공시는 적지 않는다 — 최신 정정을 덮지 않게."""
    _document(store, tmp_path, doc_id="ORIG", filed=ORIGINAL)
    _document(store, tmp_path, doc_id="AMEND", filed=FILED, text=AMENDED_TEXT, title=AMENDED_TITLE)
    amended = AMENDED
    client = _Client(amended)
    extractor = _extractor(store, client)
    frame = store.get(docs.DOCUMENTS, as_of=NOW, lookback=90, market="KR")
    extractor.run(l1.targets(frame[frame["doc_id"] == "AMEND"]))
    extractor.run(l1.targets(frame[frame["doc_id"] == "ORIG"]))
    assert len(client.calls) == 1 and extractor.superseded == 1
    assert l1.load(store, as_of=NOW, lookback=90).iloc[0]["magnitude"] == 21_000_000_000


# -- 3. 예산 -----------------------------------------------------------------------------------


def test_budget_exhausted_stops_without_calling(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    extractor = _extractor(store, client)
    extractor.budget.agent_spent = extractor.budget.agent_usd  # type: ignore[assignment]
    rows = extractor.run(_todo(store))
    assert client.calls == [] and rows == [] and "소진" in (extractor.stopped or "")


def test_budget_is_charged_as_calls_happen(store: Store, tmp_path: Path) -> None:
    for i in range(3):   # 원문이 같으면 캐시 키가 같다 — 건마다 다르게
        _document(store, tmp_path, doc_id=f"D{i}", filed=FILED, text=TEXT + f"\n접수 {i}")
    client = _Client()
    extractor = _extractor(store, client)
    # 한 건 값(2000×$1 + 150×$5)/1e6 = $0.00275 — 두 건 뒤에 넘도록 예산을 둔다.
    extractor.budget.agent_usd = 0.005
    rows = extractor.run(_todo(store))
    assert len(client.calls) == 2 and len(rows) == 2 and extractor.stopped


def test_missing_budget_key_fails_closed(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    extractor = l1.FilingEventExtractor(store, ReplayClock(NOW), l1.Budget.from_store(store, as_of=NOW),
                                        client=client)   # 설정 시딩 전
    assert extractor.run(_todo(store)) == [] and client.calls == []
    assert "예산 키" in (extractor.stopped or "")


def test_tool_rc_is_2_on_budget_stop(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    store.seed_config_defaults()
    monkeypatch.setattr(l1.Budget, "blocked", lambda self: "추출 예산 소진 $20.00 / $20.00")
    assert tool.run(store, ReplayClock(NOW), lookback=30, limit=0, api_key="x") == 2


def test_month_spend_counts_only_this_agent_this_month(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    _extractor(store, _Client()).run(_todo(store))
    assert l1.month_spend(store, as_of=NOW) == pytest.approx(0.00275)
    assert l1.month_spend(store, as_of=datetime(2026, 10, 2, tzinfo=UTC)) == 0.0   # 다음 달은 0 부터


# -- 4. 누수 -----------------------------------------------------------------------------------


def test_observed_at_is_extraction_time_not_filing_time(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    rows = _extractor(store, _Client()).run(_todo(store))
    assert rows[0]["valid_from"] == FILED and rows[0]["observed_at"] == NOW
    stored = store.get(l1.TABLE, as_of=NOW, lookback=30)
    assert stored.iloc[0]["observed_at"] == NOW


def test_backfilled_old_filing_is_invisible_to_past_as_of(store: Store, tmp_path: Path) -> None:
    """2025-03 공시를 오늘 뽑았다 — 2025~2026 어느 과거 판정 시점에서도 안 보여야 한다."""
    _document(store, tmp_path, doc_id="OLD", filed=OLD)
    _extractor(store, _Client()).run(_todo(store, lookback=600))
    for as_of in (datetime(2025, 3, 20, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC),
                  datetime(2026, 9, 28, 11, 59, tzinfo=UTC)):
        assert l1.load(store, as_of=as_of, lookback=600).empty, as_of
    assert len(l1.load(store, as_of=NOW, lookback=600)) == 1


def test_forward_only_drops_backfill_even_when_visible(store: Store, tmp_path: Path) -> None:
    """판정 시점(2027)에는 백필 행도 observed_at ≤ as_of 라 보인다 — 접수일로 한 번 더 자른다."""
    _document(store, tmp_path, doc_id="OLD", filed=OLD)
    _document(store, tmp_path, doc_id="NEW", filed=FILED)
    _extractor(store, _Client()).run(_todo(store, lookback=600))
    later = datetime(2027, 1, 15, tzinfo=UTC)
    seen = l1.load(store, as_of=later, lookback=700)
    assert set(seen["doc_id"]) == {"OLD", "NEW"}
    kept = l1.forward_only(seen, start=datetime(2026, 9, 1, tzinfo=UTC))
    assert set(kept["doc_id"]) == {"NEW"}


def test_cache_rows_are_invisible_to_past_as_of(store: Store, tmp_path: Path) -> None:
    """캐시 행도 observed_at = 추출 시각이다 — 과거 as_of 로는 LLM 출력이 어떤 표에서도 안 보인다."""
    _document(store, tmp_path, doc_id="OLD", filed=OLD)
    _extractor(store, _Client()).run(_todo(store, lookback=600))
    for as_of in (datetime(2025, 6, 1, tzinfo=UTC), datetime(2026, 9, 28, 11, 59, tzinfo=UTC)):
        assert store.get("agent_cache", as_of=as_of, entity="KR:123456").empty, as_of
        assert l1.load(store, as_of=as_of, lookback=600).empty, as_of
    cached = store.get("agent_cache", as_of=NOW, entity="KR:123456")
    assert len(cached) == 1 and cached.iloc[0]["observed_at"] == NOW and cached.iloc[0]["valid_from"] == OLD


def test_cache_is_found_by_key_on_a_later_day(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """다음 날 다시 돌려도(시계가 달라도) 키로 찾아 재호출하지 않는다."""
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    with monkeypatch.context() as patch:
        _lose_table_write(patch, store)
        with pytest.raises(RuntimeError):
            _extractor(store, client).run(_todo(store))
    later = datetime(2026, 9, 29, 17, 40, tzinfo=UTC)
    again = l1.FilingEventExtractor(store, ReplayClock(later), l1.Budget.from_store(store, as_of=later),
                                    client=client)
    rows = again.run(_todo(store))
    assert len(client.calls) == 1 and again.cache_hits == 1
    assert rows[0]["status"] == "ok" and rows[0]["observed_at"] == later


def test_changed_prompt_misses_the_cache(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    _extractor(store, client).run(_todo(store))
    monkeypatch.setattr(l1, "SYSTEM", l1.SYSTEM + " ")
    _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 2


def test_sonnet_is_called_without_sampling_params_and_keyed_apart(store: Store, tmp_path: Path) -> None:
    """Sonnet 5 는 온도를 거절한다 — 온도 없이, 사고를 끄고(도구 강제). 지문이 달라 Haiku 행·캐시와 섞이지 않는다."""
    _document(store, tmp_path, doc_id="D1", filed=FILED)
    client = _Client()
    store.seed_config_defaults()
    extractor = l1.FilingEventExtractor(store, ReplayClock(NOW),
                                        l1.Budget.from_store(store, as_of=NOW, model="claude-sonnet-5"),
                                        client=client, model="claude-sonnet-5")
    rows = extractor.run(_todo(store))
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5" and "temperature" not in call
    assert call["thinking"] == {"type": "disabled"}
    assert rows[0]["prompt_hash"] == l1.prompt_hash("claude-sonnet-5") != l1.PROMPT_HASH
    assert l1.load(store, as_of=NOW, lookback=30).empty     # 운영 지문(Haiku)으로 읽으면 안 보인다


# -- 대상 고르기 -------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type, title, expected", [
    ("contract", "단일판매ㆍ공급계약체결", False),                     # G13 파서가 있다
    ("contract", "[기재정정]단일판매ㆍ공급계약해지", False),
    ("pl_change", "매출액또는손익구조30%(대규모법인은15%)이상변경", False),  # G12 파서가 있다
    ("other", "매출액또는손익구조30%(대규모법인은15%)이상변경", False),     # 분류가 달라도 종류로 뺀다
    ("dilution", "주요사항보고서(유상증자결정)", True),
    ("buyback", "주요사항보고서(자기주식취득결정)", True),
    ("dilution", "유상증자또는주식관련사채등의청약결과(자율공시)", False),
    ("buyback", "자기주식취득결과보고서", False),
    ("earnings", "연결재무제표기준영업(잠정)실적(공정공시)", True),
    ("earnings", "분기보고서 (2026.06)", False),
    ("other", "최대주주변경", True),
    ("other", "주요사항보고서(자기주식처분결정)", True),
    ("other", "주식소각결정", True),                                     # DART 의 자사주 소각 제목이 이것이다(2026-09-28 실측 91건)
    ("other", "기타시장안내", False),
])
def test_is_target(doc_type: str, title: str, expected: bool) -> None:
    assert l1.is_target(doc_type, title) is expected


def test_prompt_hash_changes_with_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    before = l1.prompt_hash()
    monkeypatch.setattr(l1, "SYSTEM", l1.SYSTEM + " ")
    assert l1.prompt_hash() != before


# -- 파서 분담 · 후보 칸 (v3) -------------------------------------------------------------------

PRELIM_G8 = (  # G8 파서가 읽는 신형 서식 — prelim_earnings 가 가진다
    "1. 연결실적내용\n구분(단위 : 백만원, %)\n매출액\n당해실적\n88,770\n80,832\n9.8\n-\n80,467\n10.3\n-\n"
    "영업이익\n당해실적\n15,185\n6,078\n149.8\n-\n19,975\n-24.0\n-\n"
)
PRELIM_SALES_ONLY = (  # 월간 수주·매출 공시 — 영업이익 칸이 비었다(G8 밖)
    "1. 실적내용\n단위 : 백만원, %\n매출액\n당해실적\n1,606,222\n2,075,919\n-22.63\n-\n1,345,615\n19.37\n-\n"
    "영업이익\n당해실적\n-\n-\n-\n-\n-\n-\n-\n"
)
PRELIM_BLANK = PRELIM_SALES_ONLY.replace("1,606,222\n2,075,919\n-22.63\n-\n1,345,615\n19.37", "-\n-\n-\n-\n-\n-")
PRELIM_TITLE = "영업(잠정)실적(공정공시)"


def test_prelim_read_by_g8_or_blank_is_not_sent_to_llm(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="G8", filed=FILED, text=PRELIM_G8, title=PRELIM_TITLE, doc_type="earnings")
    _document(store, tmp_path, doc_id="BLANK", filed=FILED, text=PRELIM_BLANK, title=PRELIM_TITLE, doc_type="earnings")
    _document(store, tmp_path, doc_id="SALES", filed=FILED, text=PRELIM_SALES_ONLY, title=PRELIM_TITLE,
              doc_type="earnings")
    answer = {"direction": 1, "vs_prior": "better", "confidence": 0.8, "evidence": "매출액 당해실적 1,606,222 · 전년동기 1,345,615"}
    client = _Client(answer)
    extractor = _extractor(store, client)
    rows = extractor.run(_todo(store))
    assert [r["source_doc_id"] for r in rows] == ["SALES"] and extractor.parser_covered == 2
    assert len(client.calls) == 1 and rows[0]["event_type"] == "prelim_earnings"
    # G8 밖이라도 매출 증감은 G8 파서가 읽는다 — 크기는 코드, LLM 은 방향만(v4).
    assert rows[0]["magnitude"] == 19.37 and rows[0]["magnitude_source"] == "code"
    assert client.calls[0]["tool_choice"]["name"] == "record_filing_direction"


RIGHTS = (
    "유상증자 결정\n1. 신주의 종류와 수\n보통주식 (주)\n6,000,000\n기타주식 (주)\n-\n"
    "3. 증자전 발행주식총수 (주)\n보통주식 (주)\n30,534,735\n기타주식 (주)\n-\n4. 자금조달의 목적\n"
    "시설자금 (원)\n-\n운영자금 (원)\n2,000,000,000\n채무상환자금 (원)\n-\n"
    "타법인 증권취득자금 (원)\n2,734,000,000\n기타자금 (원)\n-\n5. 증자방식\n제3자배정증자\n"
    "20. 기타 투자판단에 참고할 사항\n본 유상증자는 제3자배정 방식의 사모 유상증자로서, 운영자금 및 타법인 증권취득자금에 사용합니다.\n"
)


def test_rights_offering_ratio_and_total_are_computed_by_code() -> None:
    derived = l1.derived_values("rights_offering", RIGHTS)
    assert derived == {"발행주식 대비(%)": 19.65, "조달 총액(원)": 4_734_000_000.0}
    lines = l1.candidate_lines("rights_offering", RIGHTS)
    assert lines[0].startswith("1. 신주의 종류와 수 | 보통주식 (주) | 6,000,000")
    assert all(len(line) <= l1.CANDIDATE_MAX_CHARS for line in lines)
    assert not any(line.startswith("본 유상증자는") for line in lines)   # 설명 문단은 칸 이름이 아니다


def test_code_computed_value_is_allowed_but_model_computed_is_not() -> None:
    answer = {**GOOD, "direction": -1, "magnitude_value": 19.65, "magnitude_unit": "pct_of_shares",
              "evidence": "[코드 계산] 발행주식 대비(%) 19.65"}
    derived = l1.derived_values("rights_offering", RIGHTS).values()
    assert l1.validate(answer, RIGHTS, event_type="rights_offering", derived=derived)["magnitude"] == 19.65
    with pytest.raises(l1.SchemaError, match="원문 숫자에 없다"):     # 코드가 주지 않았으면 LLM 계산으로 본다
        l1.validate(answer, RIGHTS, event_type="rights_offering")


def test_rights_offering_message_carries_code_values(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="R1", filed=FILED, text=RIGHTS, title="주요사항보고서(유상증자결정)",
              doc_type="dilution")
    answer = {**GOOD, "direction": -1, "magnitude_value": 19.65, "magnitude_unit": "pct_of_shares",
              "evidence": "[코드 계산] 발행주식 대비(%) 19.65"}
    client = _Client(answer)
    rows = _extractor(store, client).run(_todo(store))
    content = client.calls[0]["messages"][0]["content"]
    assert "- [코드 계산] 발행주식 대비(%): 19.65" in content and "- [코드 계산] 조달 총액(원): 4,734,000,000" in content
    assert rows[0]["status"] == "ok" and rows[0]["magnitude"] == 19.65


# -- 크기를 코드가 읽는 칸 (v4) ------------------------------------------------------------------

TRUST_END = (
    "자기주식취득 신탁계약 해지 결정\n1. 계약금액(원)\n해지 전\n2,000,000,000\n해지 후\n-\n"
    "3. 해지목적\n신탁계약 만료에 따른 계약해지\n6. 해지 전 자기주식 보유현황\n보통주식\n514,839\n비율(%)\n9.80\n"
)
TRUST_TITLE = "주요사항보고서(자기주식취득신탁계약해지결정)"
DIRECTION_ONLY = {"direction": 0, "vs_prior": "none", "confidence": 0.8, "evidence": "신탁계약 만료에 따른 계약해지"}


def test_code_magnitude_reads_prelim_through_the_g8_parser() -> None:
    sales = l1.code_magnitude("prelim_earnings", PRELIM_SALES_ONLY)
    assert sales == l1.CodeMagnitude(19.37, "pct_change", "매출액 당해실적 전년동기 대비(%)")
    op = l1.code_magnitude("prelim_earnings", PRELIM_G8)          # 영업이익이 있으면 영업이익
    assert op is not None and op.label.startswith("영업이익") and op.value == round((15_185 / 19_975 - 1) * 100, 2)
    loss_base = PRELIM_SALES_ONLY.replace("1,345,615", "-1,345,615")
    assert l1.code_magnitude("prelim_earnings", loss_base) is None  # 적자 기준 증감률은 단정하지 않는다
    assert l1.code_magnitude("prelim_earnings", "단위 없는 글") is None


def test_code_magnitude_reads_trust_contract_amount_not_holdings() -> None:
    got = l1.code_magnitude("treasury_trust_end", TRUST_END)
    assert got == l1.CodeMagnitude(2_000_000_000.0, "krw", "계약금액(원) 해지 전")   # 보유현황 9.80 이 아니다
    assert l1.code_magnitude("treasury_trust_end", "해지목적\n만료") is None
    assert l1.code_magnitude("rights_offering", TRUST_END) is None                     # 규칙이 없는 종류


def test_code_magnitude_uses_direction_tool_and_records_source(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="T1", filed=FILED, text=TRUST_END, title=TRUST_TITLE)
    client = _Client(DIRECTION_ONLY)
    rows = _extractor(store, client).run(_todo(store))
    call = client.calls[0]
    assert call["tools"] == [l1.DIRECTION_TOOL] and call["tool_choice"]["name"] == "record_filing_direction"
    assert "크기(코드가 정함): 계약금액(원) 해지 전 = 2,000,000,000.00 krw" in call["messages"][0]["content"]
    row = rows[0]
    assert (row["magnitude"], row["magnitude_unit"], row["magnitude_source"], row["direction"]) == (
        2_000_000_000.0, "krw", "code", 0)


def test_llm_cannot_write_a_magnitude_when_code_read_it(store: Store, tmp_path: Path) -> None:
    _document(store, tmp_path, doc_id="T1", filed=FILED, text=TRUST_END, title=TRUST_TITLE)
    sneaky = {**DIRECTION_ONLY, "magnitude_value": 9.8, "magnitude_unit": "pct_of_shares"}
    client = _Client(sneaky, DIRECTION_ONLY)
    rows = _extractor(store, client).run(_todo(store))
    assert len(client.calls) == 2 and "키 불일치" in client.calls[1]["messages"][-1]["content"][0]["content"]
    assert rows[0]["magnitude"] == 2_000_000_000.0 and rows[0]["magnitude_source"] == "code"


def test_llm_reads_magnitude_when_code_cannot(store: Store, tmp_path: Path) -> None:
    text = "자기주식취득 신탁계약 해지 결정\n3. 해지목적\n신탁계약 만료\n해지금액\n1,500,000,000\n"
    _document(store, tmp_path, doc_id="T2", filed=FILED, text=text, title=TRUST_TITLE)
    answer = {**GOOD, "direction": 0, "magnitude_value": 1_500_000_000, "evidence": "해지금액 1,500,000,000"}
    client = _Client(answer)
    rows = _extractor(store, client).run(_todo(store))
    assert client.calls[0]["tool_choice"]["name"] == "record_filing_event"
    assert rows[0]["magnitude"] == 1_500_000_000 and rows[0]["magnitude_source"] == "llm"


def test_prompt_hash_covers_code_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    before = l1.prompt_hash()
    monkeypatch.setattr(l1, "CODE_MAGNITUDE_RULES", {**l1.CODE_MAGNITUDE_RULES, "treasury_sell": "새 규칙"})
    assert l1.prompt_hash() != before
