"""캐시 지문이 세는 설정 목록이 **낡지 않는지** 본다.

`allocator/cache.CONFIG_DEPENDENCIES` 는 손으로 적은 목록이고, 손으로 적은
목록은 낡는다. 낡는 방향이 둘인데 위험이 전혀 다르다.

- **빠뜨림** — 환경이 읽는 키가 목록에 없다. 그 키를 바꾸면 환경은 달라지는데
  캐시는 유효하다고 말한다. 예외도 로그도 없고, 그 위에서 며칠 학습한 결과가
  아무 데도 안 뜨는 거짓이 된다. **이 파일이 막는 것은 이쪽이다.**
- **과잉** — 안 읽는 키가 목록에 있다. 캐시가 괜히 깨진다. 시끄럽고 안전하다.

그래서 시험이 코드를 훑는다. ``allocator/env.py`` · ``allocator/cache.py`` ·
``tools/build_rl_cache.py`` 에서 시작해 import 로 닿는 모듈을 모으고, 거기서
``store.config(...)`` 로 읽는 이름을 전부 뽑아 목록이 덮는지 본다. 새 키를
읽는 코드가 들어오면 여기서 깨지고, 깨진 사람은 목록에 한 줄 더하면 된다.

**닫힘은 import 기준이라 실제로 안 도는 코드까지 들어온다.** 그 편이 맞다 —
"저 코드는 이 경로에서 안 돈다" 를 사람이 판단하기 시작하면, 그 판단이 한 번
틀렸을 때 위의 조용한 사고가 된다.

이름을 만들어 읽는 자리(``f"universe.{key}"``)는 리터럴 앞머리만 뽑아
**섹션이 통째로 선언돼 있는지**를 본다. 만들어지는 이름을 정적으로는 못 세므로
그 섹션은 통째로 지문에 들어가야 한다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from quant_rl_trading.allocator.cache import CONFIG_DEPENDENCIES, depends_on

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 닫힘의 출발점. 캐시를 **쓰는 쪽**(env)과 **굽는 쪽**(도구) 둘 다다 — 굽는
#: 쪽만 보면 환경이 읽는 임계치를 놓치고, 쓰는 쪽만 보면 굽는 자본을 놓친다.
ROOTS = (
    "quant_rl_trading.allocator.env",
    "quant_rl_trading.allocator.cache",
    "tools.build_rl_cache",
)

#: ``store.config("이름")`` / ``self.store.config("이름")``.
LITERAL = re.compile(r"""config\(\s*["']([^"']+)["']""")

#: ``store.config(f"universe.{key}")`` 의 앞머리.
DYNAMIC = re.compile(r"""config\(\s*f["']([^"'{]*)""")


def _module_path(module: str) -> Path | None:
    base = REPO_ROOT / Path(module.replace(".", "/"))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _closure() -> dict[str, Path]:
    """저장소 안에서 import 로 닿는 모듈.

    ``from pkg import name`` 은 **되도록 그 서브모듈로** 따라간다. 패키지
    ``__init__`` 으로만 따라가면 그 패키지의 형제 모듈이 전부 딸려 와, 목록이
    실제 경로와 무관하게 부풀어 오른다.
    """
    seen: dict[str, Path] = {}
    stack = list(ROOTS)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                stack += [
                    alias.name
                    for alias in node.names
                    if alias.name.split(".")[0] in ("quant_rl_trading", "tools")
                ]
            elif isinstance(node, ast.ImportFrom):
                head = (node.module or "").split(".")[0]
                if head not in ("quant_rl_trading", "tools"):
                    continue
                for alias in node.names:
                    sub = f"{node.module}.{alias.name}"
                    sub_path = _module_path(sub)
                    stack.append(
                        sub
                        if sub_path is not None and sub_path.name != "__init__.py"
                        else str(node.module)
                    )
    return seen


def _reads() -> dict[str, set[str]]:
    """모듈 → 그 모듈이 읽는 설정 이름. 만들어 읽는 이름은 앞머리로 들어온다."""
    out: dict[str, set[str]] = {}
    for module, path in _closure().items():
        source = path.read_text(encoding="utf-8")
        names = set(LITERAL.findall(source))
        names |= {prefix.rstrip(".") for prefix in DYNAMIC.findall(source) if prefix}
        if names:
            out[module] = names
    return out


def test_읽는_설정이_전부_지문에_들어간다() -> None:
    """빠뜨림을 막는다. **이 시험이 깨지면 목록에 한 줄 더하는 것이 정답이다.**

    "그 키는 캐시에 영향 없다" 는 판단으로 목록 밖에 두지 마라 — 영향이 없는
    것과, 영향이 있는데 아직 안 겪은 것은 겉모습이 같다.
    """
    missing = {
        module: sorted(name for name in names if not depends_on(name))
        for module, names in _reads().items()
    }
    missing = {module: names for module, names in missing.items() if names}
    assert not missing, (
        "지문이 안 세는 설정을 읽는다 — CONFIG_DEPENDENCIES 에 더해라: " f"{missing}"
    )


def test_지문_목록에_안_읽는_이름이_남아_있지_않다() -> None:
    """과잉을 막는다. 위험하지는 않지만, 안 읽는 이름이 남아 있으면 목록이
    "무엇에 의존하나" 를 더는 말해주지 않는다 — 그러면 다음 사람이 목록을
    안 믿고, 안 믿는 목록은 안 고쳐진다."""
    read = {name for names in _reads().values() for name in names}
    stale = [
        dependency
        for dependency in CONFIG_DEPENDENCIES
        if not any(name == dependency or name.startswith(f"{dependency}.") for name in read)
    ]
    assert not stale, f"아무도 안 읽는 이름이 목록에 있다: {stale}"


def test_계좌_설정은_지문_밖이다() -> None:
    """이 목록을 만든 사건 자체를 못으로 박는다 (2026-08-22).

    브로커 계좌 두 줄을 창고에 심었더니 구워 둔 캐시가 통째로 무효가 됐다.
    이 경로는 계좌를 읽지 않는다 — Executor 가 읽는다.
    """
    for name in (
        "execution.account_mode",
        "execution.live_account_fingerprint",
        "execution.live_account_fingerprint_paper",
        "execution.live_account_fingerprint_us",
        "execution.live_trading",
    ):
        assert not depends_on(name), f"{name} 이 지문에 들어 있다"
