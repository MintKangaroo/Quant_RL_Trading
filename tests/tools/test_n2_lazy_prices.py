"""N2(Lazy Prices) 진단 — 섹션 절단·`&cr;` 풀기·제목 해석·과거 수집 도구의 순수 함수."""
from __future__ import annotations

import gzip
import json
from datetime import date

import numpy as np
import pandas as pd

from tools import collect_n2_periodic as C
from tools import diag_n2_lazy_prices as N

BODY = "가나다라 마바사 " * 60


def _doc() -> str:
    return "\n".join([
        "목 차", "I. 회사의 개요", "II. 사업의 내용", "III. 재무에 관한 사항",  # 목차(짧은 구간)
        "I. 회사의 개요", "개요 " * 100,
        "II. 사업의 내용&cr;1. 사업의 개요", BODY,
        "III. 재무에 관한 사항", "I. 유동자산", "숫자 " * 50,  # 재무제표 안의 작은 로마 숫자 — 끝으로 안 친다
        "IV. 이사의 경영진단 및 분석의견", "경영 진단 " * 80,
        "V. 회계감사인의 감사의견 등", "XI. 그 밖에 투자자 보호를 위하여 필요한 사항", "우발 채무 소송 " * 60,
        "【 전문가의 확인 】", "감사보고서 본문 " * 100,
    ])


def test_clean_turns_cr_entity_into_newline() -> None:
    assert N.clean("가&cr;나&CR;다") == "가\n나\n다"


def test_sections_picks_body_not_toc_and_stops_at_confirmation() -> None:
    sec = N.sections(_doc())
    assert sec["biz"].startswith("II. 사업의 내용") and "1. 사업의 개요" in sec["biz"]
    assert "III. 재무" not in sec["biz"]
    assert sec["mdna"].startswith("IV. 이사의 경영진단") and "V. 회계감사인" not in sec["mdna"]
    assert sec["risk"].startswith("XI. 그 밖에") and "전문가의 확인" not in sec["risk"] and "감사보고서" not in sec["risk"]


def test_sections_short_or_missing_is_empty() -> None:
    sec = N.sections("II. 사업의 내용\n짧다\nIII. 재무")
    assert sec == {"biz": "", "mdna": "", "risk": ""}


def test_roman() -> None:
    assert [N.roman(x) for x in ("I", "IV", "IX", "XI", "XII", "XIV")] == [1, 4, 9, 11, 12, 14]


def test_parse_title() -> None:
    assert N.parse_title("사업보고서 (2022.12)") == ("A", 2022, 12, False)
    assert N.parse_title("분기보고서 (2023.03)") == ("Q", 2023, 3, False)
    assert N.parse_title("[기재정정]반기보고서 (2023.06)") == ("H", 2023, 6, True)
    assert N.parse_title("사업보고서제출기한연장신고서") is None


def test_similarity_identical_and_disjoint() -> None:
    c, j = N.similarity("가나 다라 가나", "가나 다라 가나")
    assert abs(c - 1.0) < 1e-12 and j == 1.0
    c, j = N.similarity("가나 다라", "마바 사아")
    assert c == 0.0 and j == 0.0
    assert all(np.isnan(x) for x in N.similarity("", "가나"))


def test_entity_pairs_match_same_kind_previous_year(tmp_path) -> None:
    def put(name: str, text: str) -> str:
        p = tmp_path / f"{name}.json.gz"
        with gzip.open(p, "wt", encoding="utf-8") as h:
            json.dump({"biz": text, "risk": text, "mdna": text}, h)
        return str(p)

    rows = [
        {"entity_id": "KR:000001", "rcept_no": "a22", "day": date(2022, 3, 20), "kind": "A", "fy": 2021, "fm": 12, "path": put("a22", BODY), "src": "extra"},
        {"entity_id": "KR:000001", "rcept_no": "q22", "day": date(2022, 5, 15), "kind": "Q", "fy": 2022, "fm": 3, "path": put("q22", BODY), "src": "extra"},
        {"entity_id": "KR:000001", "rcept_no": "a23", "day": date(2023, 3, 20), "kind": "A", "fy": 2022, "fm": 12, "path": put("a23", BODY + " 새로운 위험 소송 "), "src": "extra"},
    ]
    out = {r["rcept_no"]: r for r in N._entity_pairs(rows)}
    assert "cos_all" not in out["a22"] and "cos_all" not in out["q22"]  # 직전 동종 없음
    assert out["a23"]["prev_rcept_no"] == "a22"
    assert 0.9 < out["a23"]["cos_all"] < 1.0 and "cos_mdna" in out["a23"]


def test_panel_holds_until_next_report_and_lags_one_session() -> None:
    sessions = np.array([date(2024, 1, d) for d in range(1, 11)])
    cols = pd.Index(["KR:000001"])
    feat = pd.DataFrame([
        {"entity_id": "KR:000001", "day": date(2024, 1, 2), "kind": "Q", "cos_all": 0.9},
        {"entity_id": "KR:000001", "day": date(2024, 1, 6), "kind": "Q", "cos_all": np.nan},  # 짝 없는 새 보고서
    ])
    v, stale = N.panel(feat, "cos_all", sessions, cols, 130, annual_only=False)
    col = v["KR:000001"]
    assert np.isnan(col.iloc[1]) and col.iloc[2] == 0.9  # 접수일(1/2) 다음 세션(1/3)부터
    assert col.iloc[5] == 0.9 and np.isnan(col.iloc[6])  # 1/6 접수 → 1/7 부터 결측(옛 값을 끌지 않음)
    assert stale["KR:000001"].iloc[4] == 2


def test_months_newest_first_and_clipped() -> None:
    m = C.months(date(2016, 1, 1), date(2021, 8, 16))
    assert m[0] == (date(2021, 8, 1), date(2021, 8, 16))
    assert m[-1] == (date(2016, 1, 1), date(2016, 1, 31))
    assert len(m) == 68


def test_is_target_originals_with_stock_code_only() -> None:
    assert C.is_target({"report_nm": "사업보고서 (2019.12)", "stock_code": "005930"})
    assert not C.is_target({"report_nm": "[기재정정]사업보고서 (2019.12)", "stock_code": "005930"})
    assert not C.is_target({"report_nm": "사업보고서 (2019.12)", "stock_code": ""})
    assert not C.is_target({"report_nm": "주요사항보고서(유상증자결정)", "stock_code": "005930"})


def test_tx_backfill_done_reads_last_reason(tmp_path) -> None:
    (tmp_path / "dart-backfill-202610.log").write_text("… 멈춘 이유 quota · …\n… 멈춘 이유 done · 남은 약 0건\n")
    assert C.tx_backfill_done(tmp_path) == (True, "done")
    (tmp_path / "dart-backfill-202611.log").write_text("… 멈춘 이유 blocked · …\n")
    assert C.tx_backfill_done(tmp_path) == (False, "blocked")
