"""대량보유(5% 룰) 정규화 — fixture 는 실제 majorstock.json 응답의 모양이다(2026-09-27 실측).

여기서 지키는 것은 셋이다: **두 시각의 규약**(접수일 09:00 / 목록 첫 관측), 보고자·목적 분류 규칙,
그리고 **같은 (접수번호, 보고자) 가 두 줄로 와도 창고에 중복을 밀어넣지 않는 것**.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from quant_rl_trading.collectors import dart_major_holders as mh

FIXTURE = Path(__file__).parent / "fixtures" / "majorstock_sample.json"
SINCE = date(2021, 1, 1)


def _payload() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["list"]


def _rows(**kwargs) -> list[dict]:
    return mh.normalize("005930", _payload(), since=SINCE, **kwargs)


def test_두_시각은_접수일_09시와_18시다() -> None:
    rows = _rows()
    row = next(r for r in rows if r["rcept_no"] == "20250310000111")
    assert row["valid_from"] == datetime(2025, 3, 10, 9, 0, tzinfo=mh.SEOUL)
    assert row["observed_at"] == datetime(2025, 3, 10, 18, 0, tzinfo=mh.SEOUL)
    assert row["entity_id"] == "KR:005930" and row["market"] == "KR"


def test_공시목록이_준_관측시각이_기본값을_이긴다() -> None:
    """원문·API 를 언제 받았는지는 상관없다 — 시장이 그 공시를 안 시각이 목록에 있다."""
    listed = datetime(2025, 3, 10, 16, 30, tzinfo=mh.SEOUL)
    rows = _rows(observed_by_receipt={"20250310000111": listed})
    row = next(r for r in rows if r["rcept_no"] == "20250310000111")
    assert row["observed_at"] == listed
    # 목록에 없는 접수번호는 18:00 으로 떨어진다(관측을 앞당기지 않는 쪽)
    other = next(r for r in rows if r["rcept_no"] == "20250620000222")
    assert other["observed_at"] == datetime(2025, 6, 20, 18, 0, tzinfo=mh.SEOUL)


def test_since_와_after_로_구간을_자른다() -> None:
    assert not [r for r in _rows() if r["rcept_no"] == "20200105000444"]  # since 앞
    later = _rows(after=date(2025, 6, 20))
    assert {r["rcept_no"] for r in later} == {"20250901000333"}


def test_같은_접수번호와_보고자는_한_행이다() -> None:
    """fixture 에 20250901000333/홍길동 이 두 줄 있다 — 자연키가 같으므로 한 행이어야 한다."""
    rows = _rows()
    keys = [(r["rcept_no"], r["reporter"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_비율도_증감도_없는_행은_버린다() -> None:
    assert not [r for r in _rows() if r["reporter"] == "값없는보고자"]


def test_숫자와_부호를_그대로_옮긴다() -> None:
    row = next(r for r in _rows() if r["reporter"] == "국민연금공단")
    assert row["ratio"] == 7.21 and row["ratio_change"] == -0.20
    assert row["shares"] == 430_000_000.0 and row["shares_change"] == -12_000_000.0
    assert row["contract_ratio"] is None  # "-" 는 결측이다, 0 이 아니다


def test_보고자_분류() -> None:
    got = {r["reporter"]: r["reporter_class"] for r in _rows()}
    assert got["국민연금공단"] == "nps"
    assert got["얼라인파트너스자산운용"] == "institution"
    assert got["홍길동"] == "individual"
    assert got["삼성물산"] == "corporate"
    # 첫 스모크에서 개인으로 떨어졌던 이름들 — 기관 표지에 맨 '투자'·'에셋'·'조합' 을 넣어 고쳤다
    for name in ("시너지아이비투자", "지케이에셋", "카이케이빅 점프업투자조합"):
        assert mh.reporter_class(name) == "institution", name
    assert mh.reporter_class("BlackRockFundAdvisors") == "institution"  # 라틴보다 기관 표지가 먼저다
    assert mh.reporter_class("ChugokuMarinePaints,Ltd.") == "foreign"
    assert mh.reporter_class("호라이즌아이엠") == "corporate"  # 순한글 다섯 자 이상은 개인이 아니다


def test_보유목적은_보고구분과_사유의_대리변수다() -> None:
    got = {r["reporter"]: r["purpose"] for r in _rows()}
    assert got["국민연금공단"] == "simple"              # 약식보고 자격 자체가 단순투자의 증거
    assert got["얼라인파트너스자산운용"] == "management"  # 사유에 경영참가·주주제안
    assert got["삼성물산"] == "general"                 # 일반보고인데 사유가 목적을 말하지 않는다
    # "모르겠다" 를 단순투자로 접지 않는다
    assert mh.purpose("일반", "주요계약의 변경") == "general"


def test_사유의_개행을_한_줄로_접는다() -> None:
    row = next(r for r in _rows() if r["reporter"] == "삼성물산")
    assert row["report_resn"] == "보유주식수 변동 / 보유주식등에 관한 계약의 변경"


def test_묶음_run_id_는_실행시각과_순번으로_결정된다() -> None:
    now = datetime(2026, 9, 27, 21, 5, 3, tzinfo=mh.SEOUL)
    assert mh.batch_run_id(3, now) == "major-holders-20260927T210503-b003"
