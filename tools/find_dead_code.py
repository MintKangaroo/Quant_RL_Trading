"""완전히 안 불리는 공개 함수·메서드를 찾는다 — 정적 가드.

    uv run python tools/find_dead_code.py
    uv run python tools/find_dead_code.py --paths quant_rl_trading/selector

**이건 "호출부 0건" 결함의 일부만 잡는다.** 오늘 나온 진짜 사고들(§ 아래 참고)은
대부분 "테스트에서만 불린다"·"조건이 실전에서 참이 될 수 없다"·"도구는 있는데
스케줄러가 안 부른다" 였다 — 이 셋은 정적 분석만으로는 오탐이 너무 많아
기계적으로 못 잡는다(사람이 walk_forward.sh 같은 걸 봐야 한다).

이 가드가 잡는 건 그보다 좁고 확실한 것 하나다: **테스트를 포함해 저장소
어디에서도 이름이 다시 등장하지 않는 공개 함수·메서드.** 자기 파일 안에서만
쓰이는 건 정상(내부 헬퍼)이라 죽은 게 아니다 — 그래서 "정의 줄 말고 어디서든
한 번이라도 더 나오면" 통과시킨다.

## 오탐을 막는 제외 목록

- **던더 메서드**(``__bool__`` 등)는 암묵적으로 호출된다(``if x:`` 가
  ``__bool__`` 을 부르지만 이름이 텍스트에 안 나온다). 이름 검색으로는 판정할
  수 없어 전부 제외한다.
- **Flask 라우트**(``dashboard/api/*.py``, ``dashboard/app.py``)는 데코레이터가
  등록하고 프레임워크가 이름이 아니라 URL 로 부른다. 텍스트에 두 번째 등장이
  없는 게 정상이라 제외한다.
- ``__init__.py`` 는 스캔하되, ``__all__`` 로 내보낸 이름이 실제로 다른 곳에서
  임포트되는지는 이름 검색으로 그대로 잡힌다(재수출 자체는 사용으로 안 친다).

## 판정을 못 믿을 때

이름이 흔하면(``run``·``build`` 등) 오탐이 나올 수 있다 — 다른 파일의 같은
이름이 우연히 매치될 수 있어서다(이건 반대 방향 오탐: 죽은 걸 살아있다고
잘못 판정 — 안전한 쪽이라 그대로 둔다). 반대로 진짜 죽었다고 나온 것은 실제로
`grep -rn '\\bNAME\\b'` 로 사람이 한 번 더 확인할 것.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 정의를 찾는 대상. tests/ 는 뺀다 — 테스트 헬퍼가 테스트 안에서만 불리는
#: 건 정상이고, 여기서 찾는 건 "프로덕션 코드인데 아무도 안 부른다" 이다.
DEFAULT_ROOTS = ("quant_rl_trading", "tools")

#: 이름이 다시 나오는지 볼 대상. 여기는 tests/ 도 포함한다 — "테스트에서만
#: 불린다" 는 이 가드의 범위 밖이지만(오탐이 너무 많다), 최소한 "테스트조차
#: 안 부른다" 는 걸러야 하니 테스트도 등장 여부 판정에는 넣는다.
SEARCH_ROOTS = ("quant_rl_trading", "tools", "scripts", "tests")

#: 암묵적으로만 호출되는 던더. 텍스트 검색으로 판정 불가능해 통째로 제외.
DUNDER_SKIP = frozenset({
    "__init__", "__post_init__", "__repr__", "__str__", "__eq__", "__len__",
    "__hash__", "__bool__", "__iter__", "__next__", "__enter__", "__exit__",
    "__call__", "__lt__", "__le__", "__gt__", "__ge__", "__getitem__",
    "__setitem__", "__contains__",
})

#: Flask 가 이름이 아니라 URL 로 부르는 것들. 데코레이터가 등록이고, 텍스트에
#: 이름이 두 번 안 나오는 게 정상이다.
ROUTE_FILE_PATTERNS = ("dashboard/api/", "dashboard/app.py")


@dataclass(frozen=True)
class Finding:
    path: str
    cls: str | None
    name: str
    lineno: int

    def label(self) -> str:
        qualified = f"{self.cls}.{self.name}" if self.cls else self.name
        return f"{self.path}:{self.lineno} {qualified}"


def _is_route_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(pattern in normalized for pattern in ROUTE_FILE_PATTERNS)


def _collect_candidates(roots: tuple[str, ...]) -> list[Finding]:
    candidates: list[Finding] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if _is_route_file(rel):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if _skip_name(node.name):
                        continue
                    candidates.append(Finding(rel, None, node.name, node.lineno))
                elif isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if _skip_name(sub.name):
                                continue
                            candidates.append(Finding(rel, node.name, sub.name, sub.lineno))
    return candidates


def _skip_name(name: str) -> bool:
    if name in DUNDER_SKIP:
        return True
    if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
        return True  # 이름 앞 밑줄 하나 = 이미 "내부용" 이라 밝힌 것. 대상 밖.
    # 위 목록에 없는 던더도 암묵 호출 가능성이 있어 통째로 뺀다.
    return name.startswith("__") and name.endswith("__")


def _referenced_elsewhere(name: str, defining_path: str, search_roots: tuple[str, ...]) -> bool:
    pattern = rf"\b{re.escape(name)}\b"
    out = subprocess.run(
        ["grep", "-rlP", pattern, *search_roots],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    files = {line.strip() for line in out.splitlines() if line.strip()}
    files.discard(defining_path)
    if files:
        return True
    # 같은 파일 안에서 정의 줄 말고 또 나오면(내부 헬퍼) 죽은 게 아니다.
    full_path = REPO_ROOT / defining_path
    text = full_path.read_text(encoding="utf-8")
    hits = len(re.findall(pattern, text))
    return hits > 1


def find_dead(roots: tuple[str, ...] = DEFAULT_ROOTS) -> list[Finding]:
    dead: list[Finding] = []
    for candidate in _collect_candidates(roots):
        if not _referenced_elsewhere(candidate.name, candidate.path, SEARCH_ROOTS):
            dead.append(candidate)
    return dead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--paths", nargs="*", default=list(DEFAULT_ROOTS),
        help="스캔할 디렉터리(레포 루트 기준). 기본: quant_rl_trading tools",
    )
    args = parser.parse_args(argv)

    dead = find_dead(tuple(args.paths))
    if not dead:
        print("죽은 코드 없음(이 가드가 잡는 범위 안에서는).")
        return 0

    print(f"완전히 안 불리는 공개 함수·메서드 {len(dead)}개:\n")
    for item in dead:
        print(f"  {item.label()}")
    print(
        "\n주의: 이 목록은 '전혀 안 불림' 만 잡는다. '테스트에서만 불림'·"
        "'조건이 실전에서 참이 될 수 없음'·'스케줄러가 안 부름' 은 이 가드"
        "범위 밖이다 — 사람이 봐야 한다."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
