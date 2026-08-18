"""불변식 정적 가드 — CLAUDE.md 불변식 1번(데이터 게이트)과 2번(Clock 주입).

CLAUDE.md 의 지시는 권고다. 이 파일은 강제다.
pytest(tests/invariants/)와 Claude Code hook 이 **같은 함수**를 호출한다.
검사 로직이 두 벌이 되면 한쪽만 통과하는 코드가 반드시 생긴다.

grep 이 아니라 AST 를 쓴다. 주석·docstring·문자열 안의 ``datetime.now()`` 를
위반으로 세면 가드를 끄고 싶어지고, 꺼진 가드는 없는 것만 못하다.

사용:
    python tools/invariant_guard.py                 # 기본 루트 전체 검사
    python tools/invariant_guard.py --paths a.py b/ # 지정 경로만
    python tools/invariant_guard.py --hook          # stdin 으로 hook JSON 수신

예외를 두는 방법은 두 가지뿐이다.
1. 경로 면제 — ``store/`` 는 데이터 게이트 그 자체이므로 Parquet/DuckDB 를 만진다.
2. 라인 면제 — 해당 라인에 ``# invariant-allow: <rule>`` 주석을 단다.
   벽시계는 ``quant_rl_trading/replay/clock.py`` 의 LiveClock 한 지점에서만 허용된다.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 검사 대상 루트. tests/ 는 제외한다 — 테스트는 가짜 과거 데이터를 직접
#: 만들어 심어야 하고, 그게 데이터 계약 테스트의 본질이기 때문이다.
DEFAULT_ROOTS = ("quant_rl_trading", "tools", "scripts")

RULE_WALLCLOCK = "wallclock"
RULE_DATA_ACCESS = "data-access"
RULE_PRICE_READ = "price-read"
RULE_PRICE_ADJUST = "price-adjust"

ALLOW_MARKER = "# invariant-allow:"

#: 데이터 게이트 자신. 여기서만 Parquet/DuckDB 를 직접 연다.
DATA_GATE_PREFIX = "quant_rl_trading/store"

# -----------------------------------------------------------------------------
# 불변식 2 — datetime.now() 직접 호출 금지. 시간은 Clock 주입으로만.
# -----------------------------------------------------------------------------
WALLCLOCK_QUALIFIED = frozenset({
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "time.time",
    "time.time_ns",
    "time.localtime",
    "time.gmtime",
    "pandas.Timestamp.now",
    "pandas.Timestamp.today",
    "pandas.Timestamp.utcnow",
})

#: import 를 해석하지 못했을 때(``from x import *`` 등) 쓰는 꼬리 매칭.
WALLCLOCK_SUFFIX = frozenset({
    "datetime.now",
    "datetime.utcnow",
    "datetime.today",
    "date.today",
    "time.time",
    "Timestamp.now",
    "Timestamp.utcnow",
    "Timestamp.today",
})

# -----------------------------------------------------------------------------
# 불변식 1 — 모든 데이터 접근은 store.get(table, as_of=...) 을 경유한다.
# -----------------------------------------------------------------------------
DATA_ACCESS_QUALIFIED = frozenset({
    "duckdb.connect",
    "duckdb.execute",
    "duckdb.sql",
    "duckdb.query",
    "duckdb.read_parquet",
    "duckdb.read_csv",
    "pandas.read_parquet",
    "polars.read_parquet",
    "polars.scan_parquet",
    "pyarrow.parquet.read_table",  # invariant-allow: data-access
    "pyarrow.parquet.write_table",  # invariant-allow: data-access
    "pyarrow.parquet.ParquetFile",  # invariant-allow: data-access
    "pyarrow.parquet.ParquetDataset",  # invariant-allow: data-access
    "pyarrow.dataset.dataset",
})

#: 어떤 객체에 붙어 있든 이 메서드 이름 자체가 게이트 우회다.
DATA_ACCESS_METHODS = frozenset({
    "read_parquet",
    "scan_parquet",
    "to_parquet",
    "write_parquet",
    "write_table",
    "ParquetFile",
    "ParquetDataset",
})

#: 이 모듈들은 store/ 밖에서 import 할 이유가 없다.
DATA_ACCESS_MODULES = frozenset({
    "duckdb",
    "pyarrow.parquet",  # invariant-allow: data-access
    "pyarrow.dataset",
})

PARQUET_SUFFIX = ".parquet"  # invariant-allow: data-access

# -----------------------------------------------------------------------------
# 시세 읽기 — prices 는 store.prices.read_prices 를 경유한다.
# -----------------------------------------------------------------------------
# 창고의 ``prices`` 에는 전 종목 종가가 0 인 휴장일 세션이 있다(2026-06-03·
# 2026-07-17). 그 행이 한 줄만 남아도 ``pct_change`` 가 전 종목을 같은 날
# -100% 로 만들고, 그 공통 하루가 60일 상관행렬을 지배한다 — 실측으로 상관
# 상한을 넘는 쌍이 3.5% → 94.0% 로 뛰었고 후보 절반이 음수 알파로 뒤집혔다.
#
# **헬퍼를 두는 것만으로는 안 막힌다.** 다음 사람이 ``store.get("prices", ...)``
# 를 한 번 더 쓰면 그 경로만 조용히 오염된다. 그래서 규칙으로 막는다.
PRICES_TABLE = "prices"

#: ``store.get(PRICES, ...)`` 처럼 상수로 부르는 관례도 잡는다. 이 저장소는
#: 모듈마다 ``PRICES = "prices"`` 를 두는 쪽이 더 흔하다.
PRICES_ALIASES = frozenset({"PRICES"})

#: 시세를 읽는 게이트 메서드. ``latest_by_entity`` 도 같은 행을 준다.
PRICE_READ_METHODS = frozenset({"get", "latest_by_entity"})

# -----------------------------------------------------------------------------
# 시세로 수익률을 만들면 기업행위를 보정한다.
# -----------------------------------------------------------------------------
# 창고에 든 것은 **원주가**다. 액면분할·무상증자·감자·주식병합이 보정되지
# 않으면 실제 손실이 아닌 가격 급변이 수익률로 계산되고, 창이 250일이면 사건
# 하나가 그 뒤 250세션을 오염시킨다 (collectors/corporate_actions.py).
#
# 5년 국장 기준으로 유니버스의 4분의 1이 닿는다. **급락 필터로는 못 잡는다** —
# 5% 무상증자는 가격이 5% 내려갈 뿐이다.
#
# 규칙은 **파일 단위**다. 한 파일이 시세를 읽고 그것으로 수익률을 만드는데
# 보정을 한 번도 안 켰으면 위반이다. 표현식 단위로 추적하면(어느 프레임이
# 어느 read_prices 에서 왔나) 오탐이 쏟아지고, 오탐이 나오면 사람은 규칙을
# 끈다 — price-read 규칙에서 배운 것이다.
#
# 원주가로 수익률을 내야 하는 자리가 실제로 있다(체결가 대비 실측 등). 그런
# 곳은 ``# invariant-allow: price-adjust`` 를 달고 **왜인지 적는다.**

#: 시세를 읽는 헬퍼. 이 파일이 시세를 다룬다는 표지다.
PRICE_HELPERS = frozenset({"read_prices", "drop_dead_sessions"})

#: 수익률을 만드는 호출. 이게 있으면 그 파일은 보정을 켰어야 한다.
RETURN_METHODS = frozenset({"pct_change"})

#: 보정을 켰다는 증거. ``read_prices(..., adjusted=True)`` 이거나 프레임을
#: 손에 들고 ``adjust(...)`` 를 부르거나 — 둘 다 같은 규칙 한 벌을 탄다.
ADJUST_CALL = "adjust"
ADJUST_KEYWORD = "adjusted"


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    symbol: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}]  {self.symbol} — {self.message}"


def _dotted_name(node: ast.expr) -> str | None:
    """``a.b.c`` 형태의 표현식을 점 표기 문자열로 되돌린다. 아니면 None."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _build_alias_table(tree: ast.AST) -> dict[str, str]:
    """지역 이름 → 정규화된 모듈 경로.

    ``import pandas as pd`` 는 ``pd -> pandas``,
    ``from datetime import datetime`` 은 ``datetime -> datetime.datetime`` 이 된다.
    이게 있어야 ``dt.now()`` 같은 별칭 우회를 잡는다.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve(dotted: str, aliases: dict[str, str]) -> str:
    head, _, tail = dotted.partition(".")
    target = aliases.get(head)
    if target is None:
        return dotted
    return f"{target}.{tail}" if tail else target


def _allowed_lines(source: str, rule: str) -> set[int]:
    marker = f"{ALLOW_MARKER} {rule}"
    return {
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if marker in line
    }


def _is_data_gate(path: str) -> bool:
    # 경계는 디렉터리 단위다. ``quant_rl_trading/storefront/`` 가 면제되면 안 된다.
    normalized = path.replace("\\", "/")
    return normalized == DATA_GATE_PREFIX or normalized.startswith(f"{DATA_GATE_PREFIX}/")


def _reads_prices(node: ast.Call) -> bool:
    """이 호출의 첫 인자가 ``prices`` 테이블인가.

    문자열 리터럴과 ``PRICES`` 상수 두 관례를 다 본다. 두 번째 인자부터는
    보지 않는다 — 게이트의 테이블 인자는 언제나 첫 자리이고, 넓히면
    ``config.get("prices_note")`` 같은 무관한 호출이 걸린다.

    ``as_of`` 를 함께 요구한다. 그게 게이트 호출의 지문이고, 없으면
    ``report.counts.get("prices", 0)`` 같은 평범한 dict 조회까지 잡힌다 —
    실제로 그렇게 잡혔다. 오탐이 한 번 나오면 사람은 규칙을 끄고 싶어진다.
    """
    if not node.args:
        return False
    if not any(keyword.arg == "as_of" for keyword in node.keywords):
        return False
    first = node.args[0]
    if isinstance(first, ast.Constant):
        return first.value == PRICES_TABLE
    return isinstance(first, ast.Name) and first.id in PRICES_ALIASES


def scan_source(source: str, path: str) -> list[Violation]:
    """소스 한 벌을 검사한다. ``path`` 는 레포 기준 상대 경로여야 한다.

    파싱에 실패하면 위반 대신 빈 목록을 돌려준다 — 문법 오류는 이 가드가
    보고할 문제가 아니라 ruff/pytest 가 보고할 문제다.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    aliases = _build_alias_table(tree)
    exempt: dict[str, set[int]] = {
        RULE_WALLCLOCK: _allowed_lines(source, RULE_WALLCLOCK),
        RULE_DATA_ACCESS: _allowed_lines(source, RULE_DATA_ACCESS),
        RULE_PRICE_READ: _allowed_lines(source, RULE_PRICE_READ),
        RULE_PRICE_ADJUST: _allowed_lines(source, RULE_PRICE_ADJUST),
    }
    in_data_gate = _is_data_gate(path)
    found: list[Violation] = []

    # price-adjust 는 파일 단위 규칙이라 한 번 훑고 나서 판정한다.
    reads_prices = False
    adjusts_prices = False
    return_calls: list[tuple[ast.Call, str]] = []

    def report(node: ast.AST, rule: str, symbol: str, message: str) -> None:
        line = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", None) or line
        if any(candidate in exempt[rule] for candidate in range(line, end + 1)):
            return
        found.append(Violation(path, line, rule, symbol, message))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # price-adjust 수집은 **점 표기 해석보다 먼저** 한다.
            # ``frame["close"].pct_change()`` 는 중간이 Subscript 라
            # ``_dotted_name`` 이 None 을 내고, 아래 ``continue`` 에 걸려
            # 통째로 안 보인다 — 실제로 그래서 규칙이 한 건도 안 잡혔다.
            if isinstance(node.func, ast.Attribute) and node.func.attr in RETURN_METHODS:
                return_calls.append((node, f"...{node.func.attr}"))

            dotted = _dotted_name(node.func)
            if dotted is None:
                continue
            resolved = _resolve(dotted, aliases)
            leaf = dotted.rsplit(".", 1)[-1]
            tail = ".".join(dotted.split(".")[-2:])

            if resolved in WALLCLOCK_QUALIFIED or tail in WALLCLOCK_SUFFIX:
                report(
                    node,
                    RULE_WALLCLOCK,
                    f"{dotted}()",
                    "벽시계 직접 호출. 시간은 Clock 주입으로만 얻는다 (불변식 2)",
                )

            if not in_data_gate and leaf in PRICE_READ_METHODS and _reads_prices(node):
                report(
                    node,
                    RULE_PRICE_READ,
                    f"{dotted}({PRICES_TABLE!r}, ...)",
                    "시세는 store.prices.read_prices 를 경유한다 — "
                    "휴장일 종가 0 이 수익률·상관을 통째로 뒤집는다",
                )

            if leaf in PRICE_HELPERS:
                reads_prices = True
            if leaf == ADJUST_CALL or any(
                keyword.arg == ADJUST_KEYWORD for keyword in node.keywords
            ):
                adjusts_prices = True
            if not in_data_gate and (
                resolved in DATA_ACCESS_QUALIFIED or leaf in DATA_ACCESS_METHODS
            ):
                report(
                    node,
                    RULE_DATA_ACCESS,
                    f"{dotted}()",
                    "Parquet/DuckDB 직접 접근. store.get(table, as_of=...) 경유 (불변식 1)",
                )

        elif isinstance(node, ast.Import) and not in_data_gate:
            for alias in node.names:
                if alias.name in DATA_ACCESS_MODULES:
                    report(
                        node,
                        RULE_DATA_ACCESS,
                        f"import {alias.name}",
                        "store/ 밖에서 저장소 드라이버를 import 할 이유가 없다 (불변식 1)",
                    )

        elif isinstance(node, ast.ImportFrom) and not in_data_gate:
            module = node.module or ""
            if module in DATA_ACCESS_MODULES:
                report(
                    node,
                    RULE_DATA_ACCESS,
                    f"from {module} import ...",
                    "store/ 밖에서 저장소 드라이버를 import 할 이유가 없다 (불변식 1)",
                )
            else:
                for alias in node.names:
                    if alias.name in DATA_ACCESS_METHODS:
                        report(
                            node,
                            RULE_DATA_ACCESS,
                            f"from {module} import {alias.name}",
                            "Parquet 직접 접근 함수 import (불변식 1)",
                        )

        elif isinstance(node, ast.Constant) and not in_data_gate:
            if isinstance(node.value, str) and PARQUET_SUFFIX in node.value:
                report(
                    node,
                    RULE_DATA_ACCESS,
                    node.value,
                    "store/ 밖에서 Parquet 경로를 다룬다 (불변식 1)",
                )

    if not in_data_gate and reads_prices and not adjusts_prices:
        for node, dotted in return_calls:
            report(
                node,
                RULE_PRICE_ADJUST,
                f"{dotted}()",
                "원주가로 수익률을 만든다 — 액면분할·무상증자·감자가 그대로 "
                "수익률이 된다. read_prices(..., adjusted=True) 를 쓰거나 "
                "store.prices.adjust() 로 접어라 "
                "(collectors/corporate_actions.py)",
            )

    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def scan_file(path: Path) -> list[Violation]:
    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        relative = path.as_posix()
    return scan_source(path.read_text(encoding="utf-8"), relative)


def iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        elif path.suffix == ".py" and path.is_file():
            yield path


def scan_paths(paths: Iterable[Path]) -> list[Violation]:
    found: list[Violation] = []
    for file in iter_python_files(paths):
        found.extend(scan_file(file))
    return found


def scan_repo() -> list[Violation]:
    """기본 루트 전체를 검사한다. tests/invariants/ 가 이 함수를 부른다."""
    return scan_paths(REPO_ROOT / root for root in DEFAULT_ROOTS)


def _in_scan_scope(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return relative.parts[0] in DEFAULT_ROOTS if relative.parts else False


def _run_hook() -> int:
    """Claude Code PostToolUse hook. 방금 수정된 파일 하나만 본다."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return 0

    path = Path(raw)
    if path.suffix != ".py" or not path.is_file() or not _in_scan_scope(path):
        return 0

    found = scan_file(path)
    if not found:
        return 0

    print("불변식 위반 — 커밋 전에 고쳐야 한다:", file=sys.stderr)
    for violation in found:
        print(f"  {violation}", file=sys.stderr)
    # exit 2 = 이 메시지를 모델에게 되돌려준다.
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="stdin 으로 hook JSON 을 받는다")
    parser.add_argument("--paths", nargs="*", type=Path, help="검사할 경로 (기본: 전체 루트)")
    args = parser.parse_args(argv)

    if args.hook:
        return _run_hook()

    found = scan_paths(args.paths) if args.paths else scan_repo()
    for violation in found:
        print(violation)
    if found:
        print(f"\n위반 {len(found)}건.")
        return 1
    print("불변식 위반 0건.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
