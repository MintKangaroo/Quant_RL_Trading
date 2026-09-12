"""공시 원문 수집의 경계.

1. 선언된 charset 을 믿지 않는다 — euc-kr 이라 적어 두고 UTF-8 을 주는 문서가 있다.
2. 본문은 창고가 아니라 파일로 간다. 창고에는 **경로만** 정정본으로 들어간다.
3. 이미 받은 것은 다음 회차에서 빠진다 — 이어받기 상태를 따로 들지 않는다.
4. DART 가 막으면 거기서 멈추고 **받은 만큼** 남긴다. 계속 두드리지 않는다.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_rl_trading.collectors import dart_documents as docs
from quant_rl_trading.collectors.dart_source import DartUnavailable
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store
from tools import collect_filing_texts as tool

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)
FILED = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)
EARLIER = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)


def zipped(body: bytes, name: str = "20260911900698.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, body)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path) -> Store:  # type: ignore[no-untyped-def]
    store = Store(root=tmp_path / "warehouse")
    store.append(
        docs.DOCUMENTS,
        [
            {
                "entity_id": "KR:210120",
                "valid_from": FILED,
                "observed_at": FILED,
                "source": "dart",
                "revision": 0,
                "doc_id": "20260911900698",
                "doc_type": "distress",
                "title": "불성실공시법인지정예고",
                "filer": "테스트",
                "url": "https://dart.fss.or.kr/x",
                "raw_path": None,
            }
        ],
        ingest_run_id="seed",
    )
    return store


class FakeSource:
    def __init__(self, replies: dict[str, object]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    def document(self, rcept_no: str) -> bytes:
        self.calls.append(rcept_no)
        reply = self.replies.get(rcept_no, b"")
        if isinstance(reply, Exception):
            raise reply
        return reply  # type: ignore[return-value]


def test_선언된_charset_을_믿지_않는다() -> None:
    """헤더는 euc-kr 인데 바이트는 UTF-8 이다 (2026-09-12 실측)."""
    markup = '<meta charset="euc-kr"><p>불성실공시법인 지정예고</p>'.encode()
    assert "불성실공시법인 지정예고" in docs.to_text(docs.decode(markup))


def test_표와_태그를_걷고_문단은_남긴다() -> None:
    text = docs.to_text("<style>.a{}</style><table><tr><td>가</td></tr><tr><td>나</td></tr></table>")
    assert ".a{}" not in text
    assert "가" in text and "나" in text
    assert "\n" in text


def test_본문은_파일로_가고_창고엔_경로만_남는다(store, tmp_path) -> None:
    root = tmp_path / "docs"
    source = FakeSource({"20260911900698": zipped("<p>거래정지 해제</p>".encode())})
    saved, empty, total = tool.collect(
        store, source, ReplayClock(NOW),
        limit=10, doc_types=("distress",), lookback_days=30, root=root, sleep=lambda _: None,
    )
    assert (saved, empty, total) == (1, 0, 1)

    frame = store.get(docs.DOCUMENTS, as_of=NOW)
    row = frame.sort_values("revision").iloc[-1]
    assert row["revision"] == 1
    assert row["doc_type"] == "distress" and row["title"] == "불성실공시법인지정예고"
    path = Path(str(row["raw_path"]))
    assert path.exists() and path.suffix == ".gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert "거래정지 해제" in handle.read()
    # 본문이 창고 행에 섞여 들어가지 않았다.
    assert "거래정지 해제" not in str(row.to_dict())


def test_이미_받은_것은_다음_회차에서_빠진다(store, tmp_path) -> None:
    root = tmp_path / "docs"
    source = FakeSource({"20260911900698": zipped(b"<p>x</p>")})
    tool.collect(
        store, source, ReplayClock(NOW),
        limit=10, doc_types=("distress",), lookback_days=30, root=root, sleep=lambda _: None,
    )
    again = tool.collect(
        store, source, ReplayClock(NOW),
        limit=10, doc_types=("distress",), lookback_days=30, root=root, sleep=lambda _: None,
    )
    assert again == (0, 0, 0)
    assert source.calls == ["20260911900698"], "이미 받은 공시를 다시 두드렸다"


def test_DART_가_막으면_거기서_멈추고_받은_만큼_남긴다(store, tmp_path) -> None:
    store.append(
        docs.DOCUMENTS,
        [
            {
                # **하루 앞선다** — pending 은 최근 것부터 주므로 막히는 쪽이 두 번째다.
                "entity_id": "KR:000001", "valid_from": EARLIER, "observed_at": EARLIER,
                "source": "dart", "revision": 0, "doc_id": "20260911000001",
                "doc_type": "distress", "title": "관리종목지정", "filer": "t",
                "url": "u", "raw_path": None,
            }
        ],
        ingest_run_id="seed2",
    )
    source = FakeSource({
        "20260911900698": zipped(b"<p>a</p>"),
        "20260911000001": DartUnavailable("document.xml 원문 대신 응답을 받았다: 020"),
    })
    saved, _, total = tool.collect(
        store, source, ReplayClock(NOW),
        limit=10, doc_types=("distress",), lookback_days=30,
        root=tmp_path / "docs", sleep=lambda _: None,
    )
    assert total == 2 and saved == 1, "막힌 뒤에도 계속 두드렸거나 받은 것을 버렸다"
    assert len(source.calls) == 2


def test_원문이_없는_공시는_실패가_아니다(store, tmp_path) -> None:
    """DART 가 013(데이터 없음)을 주면 빈 바이트다 — 정정본을 만들지 않는다."""
    source = FakeSource({"20260911900698": b""})
    saved, empty, total = tool.collect(
        store, source, ReplayClock(NOW),
        limit=10, doc_types=("distress",), lookback_days=30,
        root=tmp_path / "docs", sleep=lambda _: None,
    )
    assert (saved, empty, total) == (0, 1, 1)
    assert len(store.get(docs.DOCUMENTS, as_of=NOW)) == 1
