"""13F 를 **종목 축**으로 읽는 단 하나의 자리.

``filings_13f`` 의 ``entity_id`` 는 ``CUSIP:02079K305`` 다. 창고의 다른 미장
테이블은 전부 ``US:GOOGL`` 이다. 그래서 13F 는 있는데도 어떤 조인에도 안
걸렸다. 이 파일이 ``security_ids`` 를 사이에 끼워 그 둘을 잇는다.

## 왜 게이트(``Store.get``) 안에서 안 하나

``store/prices.py`` 와 같은 이유다. 게이트가 행을 말없이 바꾸면 "창고에
무엇이 들어 있나" 를 게이트로 물을 수 없게 된다 — 화면의 13F 탭은 **원본
그대로**(CUSIP 축, 매핑 실패 포함)를 봐야 하고, 실제로 그렇게 보고 있다.
그래서 원본은 그대로 두고 읽기 헬퍼를 하나 둔다.

## 안 붙는 것은 안 붙은 채로 남긴다

억지로 붙이지 않는다. 세 경우를 전부 **버리고** 지나간다.

- ``security_ids`` 에 없는 CUSIP — 채권·옵션·비상장·미국 밖 상장
- 한 CUSIP 이 티커 **여럿**에 붙는 경우 — 아무거나 고르면 그게 조용히 틀린
  매핑이다
- 매핑은 됐지만 그 분기의 매핑 커버리지가 임계치 아래인 경우 — 아래 참고

## 커버리지 게이트가 왜 필요한가

매핑은 스냅샷이라 **낡는다.** 새 분기에 새로 편입된 종목의 CUSIP 은
``tools/map_cusip_tickers.py`` 를 다시 돌리기 전까지 매핑이 없다. 그러면 그
분기는 조용히 **반쪽만** 들어오고, 남은 반쪽으로 잰 횡단면 순위는 시장이
아니라 "매핑이 오래된 종목 집합" 을 재는 것이 된다. 행이 0 이 되는 게
아니라 **그럴듯하게 줄어들기 때문에** 아무도 못 알아챈다 — flow_kr 의
``MIN_COVERAGE`` 와 같은 종류의 방어다.

기준은 종목 수가 아니라 **금액 비중**이다. 13F 는 꼬리에 아주 작은 보유가
길게 붙어 있어서(RenTech 한 분기 3,200종목) 종목 수로 재면 큰 것 하나가
빠져도 티가 안 난다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant_rl_trading.store.errors import ConfigNotFound

if TYPE_CHECKING:  # 순환 import 를 만들지 않는다 — store 패키지가 이 모듈을 부른다
    from quant_rl_trading.store import Store

FILINGS = "filings_13f"
SECURITY_IDS = "security_ids"

#: 13F 를 읽을 기본 창(달력일). 분기 데이터라 넉넉해야 한다 — 8분기 남짓.
DEFAULT_LOOKBACK_DAYS = 800

#: 분기별 매핑 커버리지 하한. 창고에 키가 없을 때만 쓰는 값이다.
MIN_MAPPED_VALUE_KEY = "thirteen_f.min_mapped_value"
MIN_MAPPED_VALUE_FALLBACK = 0.8

_FILING_COLUMNS = [
    "entity_id",
    "valid_from",
    "observed_at",
    "filer_cik",
    "filer_name",
    "cusip",
    "issuer",
    "value_usd",
    "shares",
    "weight",
]


def cusip_to_entity(store: Store, *, as_of: datetime) -> pd.DataFrame:
    """CUSIP → ``US:티커``. 애매한 것은 아예 빼고 돌려준다.

    **lookback 을 주지 않는다.** 이 표의 ``valid_from`` 은 2015년 기준시점에
    박혀 있어서(collectors/security_ids.py 참고) 창을 좁히는 순간 파티션
    프루닝에 통째로 잘려 나가고, 그러면 "매핑이 없다" 와 "매핑을 잘라냈다"
    가 똑같이 빈 프레임으로 보인다.
    """
    frame = store.get(
        SECURITY_IDS,
        as_of=as_of,
        columns=["entity_id", "id_value", "security_type"],
    )
    if frame.empty:
        return frame
    counts = frame.groupby("id_value")["entity_id"].nunique()
    ambiguous = set(counts[counts > 1].index)
    return frame[~frame["id_value"].isin(ambiguous)].drop_duplicates("id_value")


def read_institutional_holdings(
    store: Store,
    *,
    as_of: datetime,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    min_mapped_value: float | None = None,
) -> pd.DataFrame:
    """13F 를 종목 축으로. 못 붙인 행은 **빠진 채로** 돌아온다.

    돌려주는 것은 원본 한 줄에 티커를 붙인 것이다(기관×종목×분기). 종목
    단위로 접는 것은 ``by_security`` 가 한다 — 접는 방식이 쓰는 쪽마다 다를
    수 있어서(금액이냐 기관 수냐) 여기서 정하지 않는다.
    """
    filings = store.get(FILINGS, as_of=as_of, lookback=lookback, columns=_FILING_COLUMNS)
    if filings.empty:
        return filings.assign(mapped_entity_id=pd.Series(dtype=str))

    mapping = cusip_to_entity(store, as_of=as_of)
    if mapping.empty:
        return filings.iloc[0:0].assign(mapped_entity_id=pd.Series(dtype=str))

    lookup = mapping.set_index("id_value")
    filings = filings.copy()
    filings["cusip"] = filings["cusip"].astype(str).str.strip().str.upper()
    filings["mapped_entity_id"] = filings["cusip"].map(lookup["entity_id"])
    filings["security_type"] = filings["cusip"].map(lookup["security_type"])

    floor = _min_mapped_value(store, as_of=as_of, override=min_mapped_value)
    covered = _covered_quarters(filings, floor)
    mapped = filings[filings["mapped_entity_id"].notna()]
    mapped = mapped[mapped["valid_from"].isin(covered)]

    # entity_id 를 바꿔서 돌려준다. 원본 CUSIP 은 ``cusip`` 에 그대로 남아
    # 있으므로 되짚을 수 있다 — 두 축을 다 들고 있어야 검증이 된다.
    return mapped.assign(entity_id=mapped["mapped_entity_id"]).reset_index(drop=True)


def mapping_coverage(
    store: Store, *, as_of: datetime, lookback: int = DEFAULT_LOOKBACK_DAYS
) -> pd.DataFrame:
    """분기별 매핑 커버리지. **게이트에 걸리기 전의 사실**을 보여준다.

    운영에서 물어야 할 질문은 "왜 종목이 줄었나" 이고, 그 답은 걸러진 뒤의
    프레임에는 남아 있지 않다.
    """
    filings = store.get(FILINGS, as_of=as_of, lookback=lookback, columns=_FILING_COLUMNS)
    if filings.empty:
        return pd.DataFrame(columns=["valid_from", "rows", "mapped_rows", "mapped_value"])

    mapping = cusip_to_entity(store, as_of=as_of)
    lookup = set(mapping["id_value"]) if not mapping.empty else set()
    filings = filings.copy()
    filings["cusip"] = filings["cusip"].astype(str).str.strip().str.upper()
    filings["is_mapped"] = filings["cusip"].isin(lookup)

    grouped = filings.groupby("valid_from")
    return pd.DataFrame({
        "rows": grouped.size(),
        "mapped_rows": grouped["is_mapped"].sum(),
        "cusips": grouped["cusip"].nunique(),
        "mapped_value": grouped.apply(
            lambda part: float(part.loc[part["is_mapped"], "value_usd"].sum())
            / float(part["value_usd"].sum() or 1.0),
            include_groups=False,
        ),
    }).reset_index()


def by_security(holdings: pd.DataFrame) -> pd.DataFrame:
    """종목×분기로 접는다.

    ``value_usd`` 는 **기관들의 보유액 합**이고 ``filers`` 는 몇 곳이 들고
    있었나다. 둘은 다른 신호다 — 한 기관이 크게 든 것과 여러 기관이 나눠
    든 것을 같다고 보면 안 된다.

    ``weight`` 는 **합치지 않는다.** 그것은 각 기관 포트폴리오 안의 비중이라
    분모가 서로 다르고, 더하면 아무 뜻이 없는 숫자가 된다. 대신 최대값을
    남긴다 — "어느 기관에게 이 종목이 얼마나 큰 자리였나" 는 뜻이 산다.
    """
    if holdings.empty:
        return pd.DataFrame(
            columns=["entity_id", "valid_from", "observed_at", "value_usd", "shares",
                     "filers", "max_weight"]
        )
    grouped = holdings.groupby(["entity_id", "valid_from"], as_index=False).agg(
        observed_at=("observed_at", "max"),
        value_usd=("value_usd", "sum"),
        shares=("shares", "sum"),
        filers=("filer_cik", "nunique"),
        max_weight=("weight", "max"),
    )
    return grouped.sort_values(["valid_from", "value_usd"], ascending=[True, False])


# -----------------------------------------------------------------------------


def _min_mapped_value(store: Store, *, as_of: datetime, override: float | None) -> float:
    if override is not None:
        return override
    try:
        return float(store.config(MIN_MAPPED_VALUE_KEY, as_of=as_of))
    except ConfigNotFound:
        # 임계치는 창고가 정본이지만, 키가 아직 안 심긴 창고(테스트·새 배포)
        # 에서 읽기가 통째로 죽으면 안 된다.
        return MIN_MAPPED_VALUE_FALLBACK


def _covered_quarters(filings: pd.DataFrame, floor: float) -> set:
    """매핑 금액 비중이 하한을 넘는 분기만."""
    kept = set()
    for quarter, part in filings.groupby("valid_from"):
        total = float(part["value_usd"].sum())
        if total <= 0:
            continue
        mapped = float(part.loc[part["mapped_entity_id"].notna(), "value_usd"].sum())
        if mapped / total >= floor:
            kept.add(quarter)
    return kept
