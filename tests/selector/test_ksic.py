"""KSIC 롤업 — **535개 코드는 섹터가 아니라 거의 종목이다.**"""

from __future__ import annotations

from quant_rl_trading.selector import ksic


def test_자릿수로_접는다() -> None:
    """세세분류든 소분류든 앞 두 자리가 중분류다. 대응표가 필요 없다."""
    assert ksic.roll_up("KSIC:26410") == ksic.roll_up("KSIC:262") == "제조:전자·전기"
    assert ksic.roll_up("KSIC:64992") == "금융·보험"
    assert ksic.roll_up("KSIC:58221") == "정보통신"
    assert ksic.roll_up("KSIC:70113") == "전문·과학·기술"
    assert ksic.roll_up("KSIC:212") == "제조:석유·화학·의약"


def test_접두사가_없어도_읽는다() -> None:
    assert ksic.roll_up("26410") == "제조:전자·전기"


def test_제조업은_한_덩어리로_두지_않는다() -> None:
    """대분류만 쓰면 제조업 하나가 유니버스의 61% 다(2026-08-21 실측).

    그 8칸을 반도체와 제약과 자동차가 나눠 쓰게 되는데, 셋은 같이 움직이지
    않는다. 상한이 걸리긴 하는데 걸려야 할 곳에 안 걸린다.
    """
    groups = {
        ksic.roll_up(code) for code in ("KSIC:26410", "KSIC:21210", "KSIC:30121")
    }
    assert len(groups) == 3


def test_모르는_코드는_기타로_채우지_않는다() -> None:
    """채우면 상관 없는 종목들이 한 섹터로 묶여 상한이 엉뚱한 것을 자른다."""
    assert ksic.roll_up("KSIC:") is None
    assert ksic.roll_up("우량기업부") is None
    assert ksic.roll_up("KSIC:04") is None  # 04 는 KSIC 에 없는 중분류
    assert ksic.roll_up_map({"KR:000100": "우량기업부"}) == {}


def test_맵으로_접는다() -> None:
    rolled = ksic.roll_up_map(
        {"KR:000100": "KSIC:26410", "KR:000200": "KSIC:272", "KR:000300": "x"}
    )
    assert rolled == {"KR:000100": "제조:전자·전기", "KR:000200": "제조:전자·전기"}
