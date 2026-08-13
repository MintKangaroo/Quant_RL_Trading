"""불변식 1 — 모든 데이터 접근은 store.get(table, as_of=...) 을 경유한다.

Parquet 를 직접 읽으면 ``observed_at <= as_of`` 강제가 통째로 우회된다.
그 순간 백테스트는 미래를 보고, 성적표는 거짓이 된다.

여기서도 가드 자체를 먼저 시험한다.
"""

import pytest

from tools.invariant_guard import RULE_DATA_ACCESS, scan_repo, scan_source

pytestmark = pytest.mark.invariant

MODULE = "quant_rl_trading/analysts/chart.py"
GATE = "quant_rl_trading/store/reader.py"


VIOLATIONS = {
    "duckdb connect": "import duckdb\ncon = duckdb.connect('warehouse.db')\n",
    "duckdb import only": "import duckdb\n",
    "duckdb from-import": "from duckdb import connect\ncon = connect('warehouse.db')\n",
    "pandas read": "import pandas as pd\ndf = pd.read_parquet('bars')\n",
    "pandas from-import": "from pandas import read_parquet\ndf = read_parquet('bars')\n",
    "polars scan": "import polars as pl\nlf = pl.scan_parquet('bars')\n",
    "pyarrow parquet": "import pyarrow.parquet as pq\nt = pq.read_table('bars')\n",
    "dataframe write": "def dump(df):\n    df.to_parquet('out')\n",
    "path literal": "PATH = 'data/curated/bars.parquet'\n",
}

CLEAN = {
    "store gateway": (
        "from quant_rl_trading import store\n"
        "def score(as_of):\n"
        "    return store.get('bars', as_of=as_of)\n"
    ),
    "docstring mention": '"""Parquet 은 store 를 통해서만 읽는다."""\nx = 1\n',
    "unrelated read": "import json\ndef load(path):\n    return json.loads(path)\n",
}


@pytest.mark.parametrize("source", VIOLATIONS.values(), ids=list(VIOLATIONS))
def test_direct_data_access_is_detected(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_DATA_ACCESS]
    assert found, f"가드가 직접 접근을 놓쳤다:\n{source}"


@pytest.mark.parametrize("source", CLEAN.values(), ids=list(CLEAN))
def test_clean_source_is_not_flagged(source: str) -> None:
    found = [v for v in scan_source(source, MODULE) if v.rule == RULE_DATA_ACCESS]
    assert not found, f"오탐:\n{source}\n{found}"


@pytest.mark.parametrize("source", VIOLATIONS.values(), ids=list(VIOLATIONS))
def test_store_package_is_exempt(source: str) -> None:
    """store/ 는 데이터 게이트 그 자체다. 여기서만 드라이버를 만진다."""
    found = [v for v in scan_source(source, GATE) if v.rule == RULE_DATA_ACCESS]
    assert not found, found


def test_exemption_is_scoped_to_store_only() -> None:
    """경로 면제가 store/ 밖으로 새면 안 된다."""
    source = "import duckdb\ncon = duckdb.connect('warehouse.db')\n"
    assert not scan_source(source, "quant_rl_trading/store/nested/reader.py")
    assert scan_source(source, "quant_rl_trading/storefront/reader.py")


def test_repo_has_no_direct_data_access() -> None:
    found = [v for v in scan_repo() if v.rule == RULE_DATA_ACCESS]
    assert not found, "\n".join(str(v) for v in found)
