"""창고 경로 → 운용 모드. **명단은 여기 하나뿐이다.**

화면에서 가능한 가장 비싼 오해가 모드를 잘못 읽는 것이다 — shadow 창고를
보면서 실전이라고 믿는 것. 그래서 사람이 설정을 기억하게 두지 않고 **창고
경로에서 유도**해 배지로 띄운다.

메일도 같은 판정을 쓴다. 화면과 메일이 각자 판정하면 언젠가 한쪽만 새
창고 이름을 배우고, 그때 둘 중 어느 쪽이 맞는지 알 방법이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: 배지에 그대로 쓰는 값. LIVE 만 "돈이 오간다".
LIVE = "LIVE"
SHADOW = "SHADOW"
BACKTEST = "BACKTEST"
DEMO = "DEMO"


@dataclass(frozen=True)
class Mode:
    code: str
    note: str

    @property
    def is_live(self) -> bool:
        return self.code == LIVE


def of(root: str | Path) -> Mode:
    """창고 경로가 말하는 모드.

    ``data/_demo`` 를 LIVE 로 보여주면 안 된다 — 보유와 주문은 심은 것이고
    시세만 진짜다 (``tools/seed_demo.py``).
    """
    text = str(root)
    if text.endswith("_shadow"):
        return Mode(SHADOW, "모의 운용 — 돈이 오가지 않는다")
    if "_backtest" in text:
        return Mode(BACKTEST, "백테스트 샌드박스")
    if "_demo" in text:
        return Mode(DEMO, "화면 확인용 — 보유·주문은 심은 것이다")
    return Mode(LIVE, "실전 창고")
