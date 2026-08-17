"""이미 적재된 (시장, as_of) 는 **측정 전에** 건너뛴다.

## 왜 테스트가 있나

``measure_ic`` 의 run_id 는 ``ic-{market}-{as_of}`` 라 (시장, as_of) 만으로
정해진다. 즉 재실행이면 **적재가 막힐 것을 시작 시점에 이미 알 수 있다.**

그런데 확인이 마지막 ``store.append`` 에만 있었다. 그래서 워크포워드
재실행이 Analyst 6종을 **4시간 반 동안 다시 계산한 뒤** 마지막 줄에서
``DuplicateIngestRun`` 으로 튕겼다 (2026-08-17 실측).

중복 거부 자체는 옳다 — append-only 창고에서 같은 run_id 를 두 번 쓰면
안 된다. 틀린 것은 **막힐 걸 알면서 일을 다 하고 나서 막히는 것**이다.
어제 ``trades`` 에서 잡은 것과 같은 종류다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import pytest

from tools import measure_ic

AS_OF = "2025-09-16T15:40:00+09:00"


@pytest.fixture
def seeded(store):  # type: ignore[no-untyped-def]
    """그 as_of 로 이미 한 번 적재된 창고."""
    store.seed_config_defaults()
    moment = datetime(2025, 9, 16, 6, 40, tzinfo=UTC)
    store.append(
        "analyst_weights",
        [{
            "entity_id": "risk", "valid_from": moment, "observed_at": moment,
            "source": "ic-measure", "market": "KR", "ic": 0.08, "weight": 1.0,
        }],
        ingest_run_id="ic-KR-20250916T154000",
    )
    return store


def _argv(root) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        "--analyst", "risk", "--market", "KR", "--sessions", "300",
        "--as-of", AS_OF, "--data-root", str(root), "--save",
    ]


def test_이미_적재됐으면_측정을_아예_안_한다(seeded, capsys) -> None:
    """**측정 함수가 한 번도 안 불려야 한다.** 돌고 나서 건너뛰면 의미가 없다."""
    with (
        mock.patch.object(measure_ic, "build_store", return_value=seeded),
        mock.patch.object(measure_ic, "measure") as measured,
    ):
        code = measure_ic.main(_argv(seeded.root))

    assert code == 0
    assert measured.call_count == 0, "이미 적재됐는데 측정을 돌렸다"
    assert "이미 적재됐다" in capsys.readouterr().out


def test_건너뛸_때_다음_수를_알려준다(seeded, capsys) -> None:
    """"건너뛴다" 만 찍으면 다시 재고 싶을 때 무엇을 해야 하는지 모른다."""
    with (
        mock.patch.object(measure_ic, "build_store", return_value=seeded),
        mock.patch.object(measure_ic, "measure"),
    ):
        measure_ic.main(_argv(seeded.root))

    out = capsys.readouterr().out
    assert "--as-of" in out
    assert "--save" in out


def test_적재_이력이_없으면_측정한다(store) -> None:
    """건너뛰기가 정상 경로까지 막으면 IC 를 영영 못 잰다.

    측정 결과를 가짜로 두므로 렌더·적재도 함께 막는다 — 여기서 보려는 것은
    **측정이 불렸는가** 하나다.
    """
    store.seed_config_defaults()
    with (
        mock.patch.object(measure_ic, "build_store", return_value=store),
        mock.patch.object(measure_ic, "measure") as measured,
        mock.patch.object(measure_ic, "render", return_value=""),
        mock.patch.object(store, "append", return_value=1) as appended,
    ):
        measure_ic.main(_argv(store.root))

    assert measured.call_count == 1
    # 이력이 없었으므로 이번에는 실제로 적재까지 간다.
    assert appended.call_count == 1


def test_save_없이는_건너뛰지_않는다(seeded) -> None:
    """--save 없이 돌리는 것은 **숫자만 다시 보려는** 것이다. 적재를 안 하니
    중복될 일도 없고, 막으면 재측정 자체가 불가능해진다."""
    argv = [a for a in _argv(seeded.root) if a != "--save"]
    with (
        mock.patch.object(measure_ic, "build_store", return_value=seeded),
        mock.patch.object(measure_ic, "measure") as measured,
        mock.patch.object(measure_ic, "render", return_value=""),
        mock.patch.object(seeded, "append", return_value=1) as appended,
    ):
        measure_ic.main(argv)

    assert measured.call_count == 1
    assert appended.call_count == 0, "--save 가 없는데 적재했다"
