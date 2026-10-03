"""seed_config 는 덮어쓰기가 얹힌 샌드박스를 거부한다 — 2026-10-03 data/_z2_shadow 에서 int(NaN) 으로 죽던 자리."""
from quant_rl_trading.store import OVERRIDES_FILE
from tools import seed_config


def test_refuses_sandbox_with_overrides(tmp_path, capsys):  # type: ignore[no-untyped-def]
    (tmp_path / OVERRIDES_FILE).write_text("allocator.baseline: float_cap\n", encoding="utf-8")
    assert seed_config.main(["--store", str(tmp_path), "--apply"]) == 2
    assert OVERRIDES_FILE in capsys.readouterr().err
    # 아무것도 심지 않았다 — 설정 표가 만들어지지도 않았다.
    assert not (tmp_path / "curated" / "config").exists()


def test_plain_store_still_seeds(tmp_path):  # type: ignore[no-untyped-def]
    assert seed_config.main(["--store", str(tmp_path), "--apply"]) == 0
    assert (tmp_path / "curated" / "config").exists()
