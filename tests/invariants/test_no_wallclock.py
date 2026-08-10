"""불변식 2 — datetime.now() 직접 호출 금지. 시간은 Clock 주입으로만 얻는다.

이 파일은 두 가지를 검증한다.
1. 가드가 실제로 위반을 잡는가 (죽은 가드 방지)
2. 레포 전체에 위반이 0건인가

1번이 없으면 LS_KR 의 실패를 그대로 반복한다 — 검사는 돌아갔는데 아무것도
잡지 않았고, 아무도 그 사실을 몰랐다.
"""

import pytest

from tools.invariant_guard import RULE_WALLCLOCK, scan_repo, scan_source

pytestmark = pytest.mark.invariant

MODULE = "lattice/analysts/chart.py"


VIOLATIONS = {
    "datetime.now": "from datetime import datetime\nx = datetime.now()\n",
    "datetime.utcnow": "from datetime import datetime\nx = datetime.utcnow()\n",
    "module alias": "import datetime as dt\nx = dt.datetime.now()\n",
    "date.today": "from datetime import date\nx = date.today()\n",
    "time.time": "import time\nx = time.time()\n",
    "bare time import": "from time import time\nx = time()\n",
    "pandas timestamp": "import pandas as pd\nx = pd.Timestamp.now()\n",
    "nested in function": (
        "from datetime import datetime\n"
        "def score(bars):\n"
        "    if bars:\n"
        "        return datetime.now()\n"
        "    return None\n"
    ),
}

CLEAN = {
    "clock injection": (
        "def score(bars, clock):\n"
        "    return clock.now()\n"
    ),
    "docstring mention": (
        '"""datetime.now() 는 쓰지 않는다."""\n'
        "def score(bars, clock):\n"
        "    return clock.now()\n"
    ),
    "comment mention": "# datetime.now() 금지\nx = 1\n",
    "string literal mention": 'MESSAGE = "datetime.now() 를 호출하지 마라"\n',
    "instance method named now": (
        "def score(bars, session):\n"
        "    return session.now()\n"
    ),
}


@pytest.mark.parametrize("source", VIOLATIONS.values(), ids=list(VIOLATIONS))
def test_wallclock_call_is_detected(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_WALLCLOCK]
    assert found, f"가드가 벽시계 호출을 놓쳤다:\n{source}"


@pytest.mark.parametrize("source", CLEAN.values(), ids=list(CLEAN))
def test_clean_source_is_not_flagged(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_WALLCLOCK]
    assert not found, f"오탐:\n{source}\n{found}"


def test_allow_comment_exempts_the_line() -> None:
    """LiveClock 한 곳만 벽시계를 읽는다. 그 면제가 실제로 동작해야 한다."""
    source = (
        "from datetime import UTC, datetime\n"
        "class LiveClock:\n"
        "    def now(self):\n"
        "        return datetime.now(UTC)  # invariant-allow: wallclock\n"
    )
    found = [v for v in scan_source(source, "lattice/replay/clock.py") if v.rule == RULE_WALLCLOCK]
    assert not found, found


def test_allow_comment_does_not_leak_to_other_lines() -> None:
    """면제는 라인 단위다. 파일 전체가 열리면 안 된다."""
    source = (
        "from datetime import datetime\n"
        "a = datetime.now()  # invariant-allow: wallclock\n"
        "b = datetime.now()\n"
    )
    found = [v for v in scan_source(source, "lattice/replay/clock.py") if v.rule == RULE_WALLCLOCK]
    assert [v.line for v in found] == [3], found


def test_repo_has_no_wallclock_calls() -> None:
    found = [v for v in scan_repo() if v.rule == RULE_WALLCLOCK]
    assert not found, "\n".join(str(v) for v in found)
