"""공시 원문 과거 백필(tools/backfill_filing_texts.py)의 경계. 네트워크를 타지 않는다.

1. 일 몫은 장부로 센다 — 호출 **전에** 세고, 몫에 닿으면 받은 만큼 적재하고 멈춘다(rc 0). 다음 날은 새 장부다.
2. 모든 DART 호출(목록 페이지까지)이 장부를 지난다 — `_call` 에서 센다.
3. 차례: 수시공시 → 정기보고서 → 2020-08~2021-08 목록 → 그 1년 정기보고서. 정규 수집기 창은 겹쳐 덮되 틈을 남기지 않는다.
4. 규약은 정규 수집기와 같다: 원문은 파일, 창고엔 경로만 정정본(observed_at = 받은 시각). 목록의 observed_at 은 접수일 18:00 KST.
5. 조용한 실패 금지: DART 가 막으면 rc 3(받은 것은 남는다), 받을 것이 있는데 0건이면 rc 1, 파싱 실패는 장부에 남고 세 번이면 건너뛴다.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from quant_rl_trading.collectors import dart_documents as docs
from quant_rl_trading.collectors.dart_source import DartUnavailable
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store
from tools import backfill_filing_texts as tool

NOW = datetime(2026, 10, 2, 15, 45, tzinfo=UTC)          # 2026-10-03 00:45 KST
STOP = datetime(2026, 10, 2, 22, 30, tzinfo=UTC)         # 07:30 KST
TODAY = date(2026, 10, 3)


def zipped(body: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.xml", f"<html><p>{body}</p></html>".encode())
    return buffer.getvalue()


def doc_row(doc_id: str, day: date, title: str, doc_type: str, raw_path: str | None = None) -> dict:
    stamp = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return {"entity_id": "KR:005930", "valid_from": stamp, "observed_at": stamp + timedelta(hours=9),
            "source": "dart", "revision": 0, "doc_id": doc_id, "doc_type": doc_type, "title": title,
            "filer": "삼성전자", "url": "u", "raw_path": raw_path}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(root=tmp_path / "warehouse")
    store.seed_config_defaults()
    store.append(docs.DOCUMENTS, [
        doc_row("E2", date(2023, 5, 2), "유상증자결정", "dilution"),
        doc_row("E1", date(2022, 3, 2), "[기재정정]단일판매ㆍ공급계약체결", "contract"),
        doc_row("P1", date(2023, 5, 15), "분기보고서 (2023.03)", "earnings"),
        doc_row("X1", date(2023, 5, 3), "사업보고서제출기한연장신고서", "earnings"),   # 정기보고서가 아니라 수시공시 쪽
        doc_row("O1", date(2023, 5, 4), "기타경영사항", "other"),                      # 수집 대상 아님
        doc_row("D1", date(2022, 6, 1), "주권매매거래정지", "distress", raw_path="data/_docs/dart/x.txt.gz"),  # 이미 받음
        doc_row("R1", date(2026, 9, 1), "유상증자결정", "dilution"),                   # 정규 수집기 몫
    ], ingest_run_id="seed")
    return store


class FakeSource:
    """`MeteredDartSource` 처럼 호출마다 장부를 센다."""

    def __init__(self, ledger: tool.QuotaLedger, replies: dict[str, object] | None = None,
                 listing: dict[tuple[date, str], list[dict]] | None = None) -> None:
        self.ledger = ledger
        self.replies = replies or {}
        self.listing = listing or {}
        self.calls: list[str] = []

    def document(self, rcept_no: str) -> bytes:
        self.ledger.take()
        self.calls.append(rcept_no)
        reply = self.replies.get(rcept_no, zipped(f"본문 {rcept_no}"))
        if isinstance(reply, Exception):
            raise reply
        return reply  # type: ignore[return-value]

    def filings(self, *, day: date, corp_class: str) -> list[dict]:
        self.ledger.take()
        self.calls.append(f"list:{day}:{corp_class}")
        return self.listing.get((day, corp_class), [])


def make(store: Store, tmp_path: Path, *, cap: int = 100, replies=None, listing=None, day: date = TODAY,
         now: datetime = NOW, busy=lambda: False, phases: tuple[str, ...] = tool.PHASES,
         ) -> tuple[tool.Backfill, FakeSource, list[str]]:
    ledger = tool.QuotaLedger(tmp_path / "quota", day, cap)
    source = FakeSource(ledger, replies, listing)
    lines: list[str] = []
    job = tool.Backfill(store=store, source=source, clock=ReplayClock(now), ledger=ledger, stop_at=STOP,
                        text_root=tmp_path / "texts", failures_path=tmp_path / "quota" / "failures.jsonl",
                        prior_list_done=tmp_path / "quota" / "prior-list.done", phases=phases,
                        sleep=lambda _s: None, busy=busy, log=lines.append)
    return job, source, lines


def latest(store: Store) -> pd.DataFrame:
    frame = store.get(docs.DOCUMENTS, as_of=NOW + timedelta(days=1), lookback=3000)
    return frame.sort_values("revision").groupby("doc_id").tail(1).set_index("doc_id")


def text_calls(source: FakeSource) -> list[str]:
    return [c for c in source.calls if not c.startswith("list:")]


# -- 장부 ------------------------------------------------------------------------------


def test_장부는_호출_전에_세고_몫에서_막고_다음날은_새로_센다(tmp_path: Path) -> None:
    ledger = tool.QuotaLedger(tmp_path, TODAY, 2)
    ledger.take()
    ledger.take()
    with pytest.raises(tool.QuotaReached):
        ledger.take()
    assert tool.QuotaLedger(tmp_path, TODAY, 2).used == 2          # 파일에 남는다 — 같은 날 두 번째 실행도 안다
    assert tool.QuotaLedger(tmp_path, TODAY + timedelta(days=1), 2).used == 0


def test_MeteredDartSource_는_목록_페이지까지_모든_호출을_센다(tmp_path: Path) -> None:
    pages = {"1": {"status": "000", "total_page": 2, "list": [{"rcept_no": "a"}]},
             "2": {"status": "000", "total_page": 2, "list": [{"rcept_no": "b"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/list.json"):
            return httpx.Response(200, json=pages[request.url.params["page_no"]])
        return httpx.Response(200, content=zipped("본문"))

    ledger = tool.QuotaLedger(tmp_path, TODAY, 3)
    source = tool.MeteredDartSource(api_key="k", ledger=ledger, transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    assert len(source.filings(day=date(2020, 9, 1), corp_class="Y")) == 2
    assert ledger.used == 2
    assert source.document("x")
    assert ledger.used == 3
    with pytest.raises(tool.QuotaReached):                         # 몫을 넘는 호출은 HTTP 를 타지 않는다
        source.document("y")
    assert ledger.used == 3


# -- 고르기 ----------------------------------------------------------------------------


def test_정기보고서_판별() -> None:
    assert tool.is_periodic("분기보고서 (2023.03)")
    assert tool.is_periodic("[기재정정]사업보고서 (2022.12)")
    assert tool.is_periodic("반기보고서")
    assert not tool.is_periodic("사업보고서제출기한연장신고서")
    assert not tool.is_periodic("유상증자결정")


def test_천장은_정규_수집기_창과_겹친다_틈이_없다() -> None:
    top = tool.ceiling(NOW)
    regular_floor = (NOW + timedelta(hours=9)).date() - timedelta(days=tool.REGULAR_WINDOW_DAYS)
    assert top > regular_floor


# -- 한 회차 ---------------------------------------------------------------------------


def test_수시공시_다음_정기보고서_차례로_받고_창고엔_경로만_정정본으로(store: Store, tmp_path: Path) -> None:
    job, source, lines = make(store, tmp_path, listing={})
    assert job.run() == 0
    calls = text_calls(source)
    # 수시공시(최근 것부터) → 정기보고서. 이미 받은 D1·대상 아닌 O1·정규 창의 R1 은 안 부른다.
    assert calls == ["X1", "E2", "E1", "P1"]
    rows = latest(store)
    assert rows.loc["E2", "revision"] == 1
    path = Path(rows.loc["E2", "raw_path"])
    assert path.exists() and "본문 E2" in docs.read_text(path)
    assert rows.loc["E2", "observed_at"] == NOW                     # 받은 시각 — 정규 수집기와 같은 규약
    assert rows.loc["E2", "title"] == "유상증자결정"                 # 옛 열을 그대로 옮긴다
    assert pd.isna(rows.loc["R1", "raw_path"]) or rows.loc["R1", "raw_path"] in ("", None)
    assert any("[periodic] 적재" in line for line in lines)


def test_몫에_닿으면_받은_만큼_남기고_rc0_다음날_잇는다(store: Store, tmp_path: Path) -> None:
    job, source, _ = make(store, tmp_path, cap=2)
    assert job.run() == 0
    assert job.night.stop == "quota"
    assert text_calls(source) == ["X1", "E2"]
    again, source2, _ = make(store, tmp_path, cap=2)                # 같은 날 다시 — 몫이 없다
    assert again.run() == 0
    assert source2.calls == []
    nxt, source3, _ = make(store, tmp_path, cap=10, day=TODAY + timedelta(days=1), listing={})
    assert nxt.run() == 0
    assert text_calls(source3) == ["E1", "P1"]                      # 받은 것은 건너뛴다(이어받기)


def test_DART_가_막으면_rc3_받은_것은_남는다(store: Store, tmp_path: Path) -> None:
    job, source, lines = make(store, tmp_path, replies={"E2": DartUnavailable("status=020 한도")})
    assert job.run() == 3
    assert text_calls(source) == ["X1", "E2"]
    assert str(latest(store).loc["X1", "raw_path"]).endswith(".txt.gz")
    assert any("DART 가 막았다" in line for line in lines)


def test_원문_없음은_표식으로_남고_파싱_실패는_장부에_세번이면_건너뛴다(store: Store, tmp_path: Path) -> None:
    replies = {"X1": b"", "E2": b"not a zip"}
    for night in range(3):
        job, _, _ = make(store, tmp_path, replies=replies, day=TODAY + timedelta(days=night), listing={})
        assert job.run() == 0
    assert latest(store).loc["X1", "raw_path"] == docs.NO_TEXT
    failures = [json.loads(x) for x in (tmp_path / "quota" / "failures.jsonl").read_text().splitlines()]
    assert [f["doc_id"] for f in failures] == ["E2", "E2", "E2"]
    job, source, _ = make(store, tmp_path, replies=replies, day=TODAY + timedelta(days=5), listing={})
    job.run()
    assert "E2" not in source.calls


def test_받을_것이_있는데_0건이면_rc1(store: Store, tmp_path: Path) -> None:
    bad = {k: b"not a zip" for k in ("X1", "E2", "E1", "P1")}
    job, _, lines = make(store, tmp_path, replies=bad, phases=("events", "periodic"))
    assert job.run() == 1
    assert any("한 건도 못 받았다" in line for line in lines)


def test_마감_시각이면_멈춘다(store: Store, tmp_path: Path) -> None:
    job, source, _ = make(store, tmp_path, now=STOP)
    job.run()
    assert source.calls == []
    assert job.night.stop == "deadline"


def test_정규_수집기가_도는_동안은_부르지_않고_기다린다(store: Store, tmp_path: Path) -> None:
    state = {"n": 2}

    def busy() -> bool:
        state["n"] -= 1
        return state["n"] >= 0

    job, source, lines = make(store, tmp_path, busy=busy, listing={})
    assert job.run() == 0
    assert any("기다린다" in line for line in lines)
    assert text_calls(source)[0] == "X1"


def test_전년_목록은_접수일_18시_KST_관측으로_들어오고_그해_정기보고서를_받는다(store: Store, tmp_path: Path) -> None:
    day = date(2020, 8, 14)
    listing = {(day, "Y"): [{"stock_code": "005930", "rcept_no": "20200814000001", "rcept_dt": "20200814",
                              "report_nm": "반기보고서 (2020.06)", "flr_nm": "삼성전자"},
                             {"stock_code": "005930", "rcept_no": "20200814000002", "rcept_dt": "20200814",
                              "report_nm": "유상증자결정", "flr_nm": "삼성전자"}]}
    job, source, _ = make(store, tmp_path, cap=2000, listing=listing)
    assert job.run() == 0
    listed = [c for c in source.calls if c.startswith("list:")]
    assert len(listed) == ((tool.PRIOR_LIST_END - tool.PRIOR_FLOOR).days + 1) * 2
    # 그 1년은 정기보고서만 원문을 받는다.
    assert "20200814000001" in source.calls and "20200814000002" not in source.calls
    frame = store.get(docs.DOCUMENTS, as_of=datetime(2020, 8, 14, 9, 1, tzinfo=UTC), lookback=30)
    assert "20200814000001" in set(frame["doc_id"])                 # 18:00 KST = 09:00 UTC 부터 보인다
    before = store.get(docs.DOCUMENTS, as_of=datetime(2020, 8, 14, 8, 59, tzinfo=UTC), lookback=30)
    assert "20200814000001" not in set(before["doc_id"])
    # 다음 밤은 목록을 다시 묻지 않는다 — 공시 없는 날(휴일)은 매니페스트에 안 남으므로 한 바퀴 표식으로 끝낸다.
    assert (tmp_path / "quota" / "prior-list.done").exists()
    nxt, source2, _ = make(store, tmp_path, cap=2000, listing=listing, day=TODAY + timedelta(days=1))
    assert nxt.run() == 0
    assert source2.calls == []
