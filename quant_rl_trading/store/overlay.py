"""오버레이 창고 — 읽기는 실제 창고, 쓰기는 임시 루트.

백테스트는 ``signals`` · ``trades`` · ``nav_daily`` 를 쓴다. 그것을 실제 창고에
쓰면 **모의 체결이 실전 장부에 섞이고**, 회계는 ``trades`` 를 출처로 가리지
않으므로 한 번 섞이면 되돌릴 방법이 append-only 창고에 없다.

그래서 쓰는 테이블만 빈 디렉터리로 새로 만들고, 나머지는 실제 창고 디렉터리로
**심볼릭 링크**한다. 4.3GB 를 복사하지 않으면서 쓰기가 원본에 닿지 않는다.

``Store(root=...)`` 하나로 들어가므로 **백테스트 코드에는 분기가 없다** —
바뀌는 것은 Clock 과 루트뿐이다 (불변식 5).

명세: docs/design/backtest.md §4
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from quant_rl_trading.store import paths
from quant_rl_trading.store.errors import StoreError
from quant_rl_trading.store.tables import table_names


@dataclass(frozen=True)
class Overlay:
    """읽기는 원본, 쓰기는 사본. ``root`` 를 ``Store`` 에 넣어 쓴다."""

    root: Path
    source: Path
    writable: frozenset[str]

    def clear(self) -> None:
        """쓰기 테이블을 비운다. **원본은 링크 너머라 지워지지 않는다.**

        링크를 따라가 지우면 실제 창고가 사라진다. 그래서 링크는 링크째
        지우고(``unlink``), 지운 자리에 다시 링크를 건다.
        """
        for table in sorted(self.writable):
            target = paths.curated_dir(self.root, table)
            if target.is_symlink():  # 있을 수 없지만, 있으면 원본을 지우게 된다
                raise StoreError(f"{table}: 쓰기 테이블이 링크다. 지우면 원본이 사라진다")
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
        # **적재 이력도 같이 지운다.** 데이터만 비우면 ``ingest_run_id`` 가
        # 남고, run_id 는 결정론적이라(``backtest-seed-2025-09-15``) 다시 돌린
        # 실행이 통째로 "이미 적재됨" 으로 건너뛰어진다. 그때 증상은 예외가
        # 아니라 **NAV 0 원인 백테스트**다 — 자본 입금 한 행이 조용히 씹히고,
        # 후보 0 · 매매 0 으로 며칠이 지나간다. 실측으로 확인했다.
        _reset_manifests(self.root, self.writable, wipe=True)


def build(*, root: Path, source: Path, writable: set[str] | frozenset[str]) -> Overlay:
    """오버레이 루트를 만든다. 이미 있으면 그대로 쓴다(이어 돌리기).

    ``writable`` 에 없는 테이블은 실제 창고를 그대로 본다. 새 테이블이 창고에
    생기면 다음 호출에서 링크가 추가된다 — 빠진 테이블을 조용히 빈 것으로
    보이게 두면 백테스트가 "데이터가 없어서" 아무것도 안 사고, 그건 성적이
    아니라 고장이다.
    """
    unknown = sorted(set(writable) - set(table_names()))
    if unknown:
        raise StoreError(f"창고에 없는 테이블을 쓰기로 지정했다: {unknown}")

    curated = root / paths.CURATED
    curated.mkdir(parents=True, exist_ok=True)

    for table in table_names():
        target = curated / table
        if table in writable:
            if target.is_symlink():
                target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            continue
        origin = paths.curated_dir(source, table)
        if not origin.is_dir() or target.exists():
            # 원본에 없는 테이블은 링크할 것이 없다. 조회는 빈 프레임을 준다.
            continue
        target.symlink_to(origin.resolve(), target_is_directory=True)

    _reset_manifests(root, writable)
    (curated / paths.STAGING).mkdir(parents=True, exist_ok=True)
    return Overlay(root=root, source=source, writable=frozenset(writable))


def _reset_manifests(
    root: Path, writable: set[str] | frozenset[str], *, wipe: bool = False
) -> None:
    """적재 이력. 쓰기 테이블만 새 것을 쓰고, 나머지는 원본을 본다.

    ``ingest_run_id`` 중복 판정이 이 파일들로 이뤄진다. 쓰기 테이블의 이력을
    원본에서 물려받으면, 백테스트가 낸 첫 주문이 "이미 적재됨" 으로 조용히
    건너뛰어진다.

    ``wipe`` 는 **이 오버레이가 스스로 쌓은 이력까지** 지운다(``clear``).
    링크만 끊는 것으로는 부족하다 — 이어 돌리기(``build``)는 그 이력이
    남아야 하고, 새로 돌리기는 남으면 안 된다. 둘이 같은 함수를 쓰되
    갈리는 지점이 여기다.
    """
    manifests = root / paths.CURATED / paths.MANIFESTS
    manifests.mkdir(parents=True, exist_ok=True)
    for table in writable:
        directory = manifests / table
        if directory.is_symlink():
            directory.unlink()
        elif wipe:
            shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)
