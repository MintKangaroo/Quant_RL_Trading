"""조각 파일 합치기 — **행이 하나도 변하지 않아야 한다.**

이 검사가 지키는 것은 성능이 아니라 정직함이다. 합치기는 append-only 창고에서
유일하게 기존 파일을 지우는 작업이라, "합쳤더니 조회 결과가 달라졌다" 가
일어나면 창고를 믿을 근거가 없어진다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from quant_rl_trading.store import paths
from quant_rl_trading.store.compact import compact_table, rewrite_manifests

NOW = datetime(2026, 8, 12, 6, 40, tzinfo=UTC)


def _flow(entity: str, moment: datetime, investor: str, value: float) -> dict[str, object]:
    return {
        "entity_id": entity,
        "valid_from": moment,
        "observed_at": moment,
        "source": "test",
        "market": "KR",
        "investor": investor,
        "net_value": value,
        "net_volume": value / 10.0,
        "is_final": True,
    }


def _seed(store, days: int = 3, entities: int = 5):  # type: ignore[no-untyped-def]
    """**종목마다 적재를 따로 돌린다** — 실제로 파일 109만 개를 만든 그 방식이다."""
    for index in range(entities):
        entity = f"KR:{index:06d}"
        rows = [
            _flow(entity, NOW - timedelta(days=offset), investor, 1_000.0 * (index + 1))
            for offset in range(days)
            for investor in ("foreign", "institution")
        ]
        store.append("flows", rows, ingest_run_id=f"bf-flows-{entity}")
    return store


def _read_all(store, as_of: datetime):  # type: ignore[no-untyped-def]
    frame = store.get("flows", as_of=as_of)
    return frame.sort_values(["entity_id", "valid_from", "investor"]).reset_index(drop=True)


def test_합쳐도_조회_결과가_같다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store)
    before = _read_all(store, NOW)
    assert not before.empty

    report = compact_table(Path(store.root), "flows", apply=True)

    after = _read_all(store, NOW)
    assert report.files_before > report.files_after
    assert after.equals(before)


def test_파티션마다_파일이_하나가_된다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store, days=3, entities=5)
    root = Path(store.root)

    # 종목 5개가 각자 적재됐으니 파티션마다 5개다.
    partitions = sorted(paths.curated_dir(root, "flows").glob("observed_date=*"))
    assert partitions
    assert all(len(list(part.glob("*.parquet"))) == 5 for part in partitions)

    compact_table(root, "flows", apply=True)

    assert all(len(list(part.glob("*.parquet"))) == 1 for part in partitions)


def test_세보기만_하면_한_바이트도_안_쓴다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store)
    root = Path(store.root)
    listing = sorted(path.name for path in paths.curated_dir(root, "flows").rglob("*.parquet"))

    report = compact_table(root, "flows", apply=False)

    assert report.files_before > report.files_after  # 무엇을 하게 될지는 센다
    after = sorted(path.name for path in paths.curated_dir(root, "flows").rglob("*.parquet"))
    assert after == listing


def test_다시_합쳐도_아무_일도_없다(store) -> None:  # type: ignore[no-untyped-def]
    """멱등성. 크론이나 사람이 두 번 돌리는 일은 반드시 일어난다."""
    _seed(store)
    root = Path(store.root)
    compact_table(root, "flows", apply=True)
    before = _read_all(store, NOW)

    again = compact_table(root, "flows", apply=True)

    assert again.touched == ()  # 손댈 것이 없다
    assert _read_all(store, NOW).equals(before)


def test_합친_뒤에도_같은_적재를_다시_받지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    """멱등성은 매니페스트가 지킨다. 합치기가 그걸 깨면 중복 적재가 열린다."""
    _seed(store, days=2, entities=2)
    compact_table(Path(store.root), "flows", apply=True)

    assert store.ingest_run_recorded("flows", "bf-flows-KR:000000")


def test_매니페스트가_지워진_파일을_가리키지_않는다(store) -> None:  # type: ignore[no-untyped-def]
    _seed(store, days=2, entities=3)
    root = Path(store.root)
    compact_table(root, "flows", apply=True)

    rewrite_manifests(root, "flows", apply=True)

    import json

    directory = root / paths.CURATED / paths.MANIFESTS / "flows"
    for manifest in directory.glob("*.json"):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in payload["files"]:
            assert (root / entry).exists(), f"{manifest.name} 이 없는 파일을 가리킨다: {entry}"
