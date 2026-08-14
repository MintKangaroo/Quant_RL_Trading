"""tools/find_dead_code.py — 정적 가드가 뭘 잡고 뭘 안 잡는지.

가짜 패키지를 ``tmp_path`` 에 만들어 네 가지를 확인한다.

1. 정말 아무 데서도 안 불리는 함수 → 잡힌다
2. 같은 파일 안에서 다른 함수가 부르는 함수(내부 헬퍼) → 안 잡힌다(정상)
3. 다른 파일이 임포트해 부르는 함수 → 안 잡힌다(정상)
4. ``__bool__`` 같은 던더 → 애초에 대상에서 빠진다(암묵 호출이라 텍스트로
   판정 불가능하기 때문 — 오탐을 만드느니 아예 빼는 쪽을 택했다)
"""

from __future__ import annotations

from pathlib import Path

from tools.find_dead_code import find_dead


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_아무데서도_안_불리는_함수는_잡힌다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(
        tmp_path / "pkg" / "orphan.py",
        "def truly_orphaned():\n    return 1\n",
    )
    import tools.find_dead_code as module

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SEARCH_ROOTS", ("pkg",))
    dead = find_dead(("pkg",))
    names = {item.name for item in dead}
    assert "truly_orphaned" in names


def test_같은_파일_안에서_불리면_안_잡힌다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(
        tmp_path / "pkg" / "helper.py",
        "def internal_helper():\n    return 1\n\n\n"
        "def public_entry():\n    return internal_helper() + 1\n",
    )
    import tools.find_dead_code as module

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SEARCH_ROOTS", ("pkg",))
    dead = find_dead(("pkg",))
    names = {item.name for item in dead}
    # public_entry 는 아무도 안 불러 진짜 죽었다 — 그건 잡혀야 정상이다.
    # internal_helper 는 같은 파일 안에서 public_entry 가 부르므로 안 잡혀야 한다.
    assert "internal_helper" not in names
    assert "public_entry" in names


def test_다른_파일이_불러쓰면_안_잡힌다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(
        tmp_path / "pkg" / "producer.py",
        "def shared_thing():\n    return 1\n",
    )
    _write(
        tmp_path / "pkg" / "consumer.py",
        "from pkg.producer import shared_thing\n\n\ndef caller():\n    return shared_thing()\n",
    )
    import tools.find_dead_code as module

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SEARCH_ROOTS", ("pkg",))
    dead = find_dead(("pkg",))
    names = {item.name for item in dead}
    assert "shared_thing" not in names


def test_던더는_대상에서_빠진다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(
        tmp_path / "pkg" / "gate.py",
        "class Result:\n    def __bool__(self):\n        return False\n",
    )
    import tools.find_dead_code as module

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SEARCH_ROOTS", ("pkg",))
    dead = find_dead(("pkg",))
    names = {item.name for item in dead}
    assert "__bool__" not in names  # 암묵 호출이라 애초에 후보에서 제외


def test_flask_라우트_파일은_스캔에서_빠진다(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(
        tmp_path / "pkg" / "dashboard" / "api" / "market.py",
        "def market_summary():\n    return {}\n",
    )
    import tools.find_dead_code as module

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SEARCH_ROOTS", ("pkg",))
    dead = find_dead(("pkg",))
    names = {item.name for item in dead}
    assert "market_summary" not in names  # 라우트 등록은 데코레이터가 한다
