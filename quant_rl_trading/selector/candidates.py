"""후보 선정 — selector.md §5 의 여섯 단계. **순서가 곧 설계다.**

    1. 유니버스 필터        살 수 있는 것만
    2. 합성 점수            죽은 Analyst 는 자동으로 빠진다
    3. News·SNS 거부 제외   거부 상한 30%
    4. 상관 페널티          이미 뽑은 것과 겹치면 감점
    5. 섹터 상한
    6. 상위 N개

**3번이 4번보다 먼저다.** 상관 계산은 비싸고, 거부될 종목까지 계산할 이유가
없다.

## 거부에 상한을 두는 이유

News·SNS 는 매수 금지만 할 수 있고 매도 권한은 없다(불변식: 금지 사항).
그래도 하루에 후보 전부를 막아 버리면 그날 포트폴리오가 통째로 현금이 된다.
LLM 판정 하나가 펀드를 멈추게 두지 않는다 — **상한을 넘으면 심각도 높은
것부터** 자르고 나머지는 통과시키되, 잘린 사실을 흔적에 남긴다.

## 흔적을 남긴다

각 단계에서 무엇이 왜 빠졌는지 ``SelectionTrace`` 에 쌓는다. Decision Trace
화면이 이걸 읽는다. **후보가 0개인 날 이유를 말할 수 없으면 그 시스템은
운영할 수 없다.**
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

import pandas as pd

from quant_rl_trading.store.prices import read_prices

if TYPE_CHECKING:
    from quant_rl_trading.store import Store
from quant_rl_trading.store.errors import ConfigNotFound

VERDICTS = "verdicts"
SECTORS = "sectors"

#: 섹터를 읽는 창. **None = 창을 안 건다** (2026-08-30 수정).
#:
#: 30일이었다. `sectors` 는 `reference_data=True` 인 참조 표라 게이트가
#: ``valid_from`` 으로 걸리는데(store/reader.py), DART 업종은 **2021-08-11 한 날짜로**
#: 백필돼 있다. 30일 창은 그 행을 통째로 잘라 **섹터 지도를 조용히 비웠다** —
#: 예외도 경고도 없이 빈 dict 가 돌아왔고, 그 위에서
#:   · 팩터 공분산(`portfolio/factor_model._build`)이 None → risk_parity 가
#:     **매일 스코어 비례로 폴백**(2026-08-30 실측: 76/76 세션 `risk_parity:fallback`,
#:     그래서 §7 비교의 두 팔 NAV 가 소수점까지 같았다)
#:   · 섹터 하방베타 상한·섹터 집중 제약도 같은 이유로 무력
#: 이 셋이 전부 "안 걸린 것" 이 아니라 "잴 수 없었던 것" 이다.
#:
#: 참조 표에 창을 거는 것 자체가 틀렸다. 업종은 사건이 아니라 속성이라
#: "최근 30일에 관측된 업종" 이라는 질문이 성립하지 않는다.
SECTOR_LOOKBACK_DAYS: int | None = None

#: 상관을 재는 창(거래일). selector.md §5-4.
CORRELATION_WINDOW = 60

#: 거부가 후보의 이 비율을 넘으면 심각도 낮은 것부터 살린다.
DEFAULT_REJECTION_CAP = 0.30


@dataclass(frozen=True)
class SelectionParams:
    n_candidates: int
    corr_threshold: float
    corr_penalty: float
    sector_cap: float
    rejection_cap: float = DEFAULT_REJECTION_CAP
    #: 완충 구간 — 보유 종목이 이 순위 안이면 남긴다. 0 이면 완충 없음(매일 재선정).
    exit_rank: int = 0
    #: 비용 인지 교체 문턱(단면 z, selector.md §5 6번). 0 이면 끈다 — 완충 밖으로 떨어진 보유를 그대로 판다(옛 동작).
    swap_min_z: float = 0.0

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime, market: str | None = None) -> SelectionParams:
        try:
            swap_min_z = float(market_config(store, "selector.swap_min_z", as_of=as_of, market=market))
        except ConfigNotFound:
            swap_min_z = 0.0
        return cls(
            n_candidates=int(store.config("selector.n_candidates", as_of=as_of)),
            corr_threshold=float(store.config("selector.corr_threshold", as_of=as_of)),
            corr_penalty=float(store.config("selector.corr_penalty", as_of=as_of)),
            sector_cap=float(store.config("selector.sector_cap", as_of=as_of)),
            exit_rank=int(market_config(store, "selector.exit_rank", as_of=as_of, market=market)),
            swap_min_z=swap_min_z,
        )


def market_config(store: Store, name: str, *, as_of: datetime, market: str | None) -> Any:
    """시장별 값이 있으면 그것(`name_us`), 없으면 공통값.

    시행 N(국장, 채택)과 R(미장, 기각)이 갈렸다(2026-09-04). 같은 키를 두 시장이 읽으면 한쪽 판정이
    다른 쪽에 강제된다. 시장별 키는 **접미사**로 둔다 — 없으면 공통값으로 물러서므로 새 시장을 붙일 때
    키를 전부 복제할 필요가 없다.
    """
    if market:
        try:
            return store.config(f"{name}_{str(market).lower()}", as_of=as_of)
        except ConfigNotFound:
            pass
    return store.config(name, as_of=as_of)


@dataclass
class SelectionTrace:
    """왜 이 후보들인가. 단계마다 남긴다."""

    counts: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: 단계가 잰 값(예: 위험 하한 임계). 화면이 "임계 얼마에 걸렸나" 를 문구가 아니라 숫자로 읽는다.
    measures: dict[str, float] = field(default_factory=dict)

    def stage(self, name: str, remaining: int) -> None:
        self.counts[name] = remaining

    def drop(self, entity_id: str, reason: str) -> None:
        self.dropped.setdefault(entity_id, reason)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def measure(self, name: str, value: float) -> None:
        self.measures[name] = float(value)


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    score: float
    raw_score: float
    sector: str | None = None

    @property
    def penalized(self) -> bool:
        return self.score != self.raw_score


def rejected_entities(
    store: Store, *, as_of: datetime, universe: Sequence[str], cap: float, trace: SelectionTrace
) -> set[str]:
    """News·SNS 가 매수를 막은 종목. **상한까지만 반영한다.**

    ``expires_at`` 이 지난 거부는 효력이 없다 — 영구 차단은 존재할 수 없다는
    것이 verdicts 스키마의 규칙이다.
    """
    if not universe:
        return set()
    frame = store.get(VERDICTS, as_of=as_of, lookback=30)
    if frame.empty:
        return set()

    live = frame[
        frame["entity_id"].isin(list(universe))
        & (frame["decision"] == "reject")
        & (frame["expires_at"].isna() | (frame["expires_at"] > pd.Timestamp(as_of)))
    ]
    if live.empty:
        return set()

    ranked = live.sort_values("severity", ascending=False)
    limit = max(1, int(len(universe) * cap))
    entities = list(dict.fromkeys(str(value) for value in ranked["entity_id"]))
    if len(entities) > limit:
        trace.note(
            f"거부 {len(entities)}건이 상한({limit}건)을 넘어 심각도 높은 순으로 "
            f"{limit}건만 반영했다. LLM 판정 하나가 펀드를 멈추게 두지 않는다"
        )
        entities = entities[:limit]
    for entity in entities:
        trace.drop(entity, "News·SNS 매수 금지")
    return set(entities)


def correlation_matrix(
    store: Store, *, as_of: datetime, entities: Sequence[str], market: str
) -> pd.DataFrame:
    """일간 수익률 상관. 관측이 모자란 종목은 행렬에서 빠진다.

    빠진 종목은 페널티를 못 받는데, 그건 **상관이 낮다는 뜻이 아니라 모른다는
    뜻이다.** 모르는 것을 0 으로 채우면 신규 상장주가 항상 분산 효과가 큰
    것처럼 보인다 — 그래서 여기서는 채우지 않고, 호출부가 '모름' 을 흔적에 남긴다.

    **시세는 ``read_prices`` 로만 읽는다.** 휴장일의 종가 0 이 한 행이라도
    남으면 전 종목이 같은 날 -100% 가 되고, 그 공통 하루가 60일 상관을 통째로
    지배한다. 그 결과 후보 절반이 음수 알파로 뒤집혔다.

    실측 — **KR 상위 300종목**(entity_id 순), ``CORRELATION_WINDOW`` = 60,
    비대각 쌍 전체의 평균. 2026-06-03(지방선거)·2026-07-17(제헌절)이 창에
    들어오는 날을 골랐다:

    | as_of | 있는 그대로 | 0 행 제거 | \\|corr\\|>0.7 인 쌍 |
    |---|---|---|---|
    | 2026-06-02 | +0.282 | +0.282 | 1.5% → 1.5% (0 세션이 창 밖) |
    | 2026-06-04 | +0.878 | **+0.246** | 91.1% → **0.7%** |
    | 2026-08-14 | +0.920 | **+0.302** | 94.0% → **3.5%** |

    **종목 집합과 as_of 를 바꾸면 숫자가 달라진다.** 같은 사고를 500종목·다른
    날짜로 재면 0.168 → 0.644 가 나온 적도 있다 — 방향과 배율이 재현의 기준이지
    소수점이 아니다 (``store/prices.py`` 참조).
    """
    if not entities:
        return pd.DataFrame()
    prices = read_prices(
        store,
        as_of=as_of,
        entity=list(entities),
        lookback=CORRELATION_WINDOW * 2,
        market=market,
        columns=["close"],
        # 상관은 수익률로 잰다 — **보정가여야 한다.** 분할 하루가 그 종목에만
        # -90% 를 찍으면 그 종목은 나머지 전부와 상관이 낮게 나와, 상관 상한을
        # 무사통과해 후보에 남는다. 종가 0 세션과 방향만 반대인 같은 사고다.
        adjusted=True,
    )
    if prices.empty:
        return pd.DataFrame()
    wide = (
        prices.sort_values("valid_from")
        .pivot_table(index="valid_from", columns="entity_id", values="close")
        .tail(CORRELATION_WINDOW + 1)
    )
    returns = wide.pct_change(fill_method=None).dropna(how="all")
    # 관측이 절반도 없는 종목의 상관은 우연이다.
    enough = returns.columns[returns.notna().sum() >= CORRELATION_WINDOW // 2]
    return returns[enough].corr()


def sector_map(
    store: Store,
    *,
    as_of: datetime,
    entities: Sequence[str],
    market: str,
    source: str,
    lookback: int | None = SECTOR_LOOKBACK_DAYS,
) -> dict[str, str]:
    """{entity_id: 섹터}. 종목마다 **as_of 이전 가장 최근** 관측 하나.

    ``source`` 는 기본값이 없다. `sectors` 에는 서로 다른 분류체계가 함께
    산다 — KRX 소속부(`krx_openapi`)는 업종이 아니라 시장 세부 구분이고,
    DART 표준산업분류(`dart_company`)가 진짜 업종이다. 섞어서 접으면 종목마다
    어느 체계의 값이 나왔는지 알 수 없는 dict 이 만들어지고, 그걸로 건 섹터
    상한은 무엇을 분산시킨 것인지 아무도 말할 수 없다. 그래서 고르게 한다.

    이중시간이 핵심이다 — ``store.get`` 이 ``observed_at <= as_of`` 를 이미
    걸러 주므로, 남은 것 중 ``valid_from`` 이 가장 늦은 행을 고르면 그
    시점에 알 수 있었던 섹터가 나온다. 종목이 업종을 옮긴 뒤에 조회하면
    새 섹터가, 옮기기 전 시점을 조회하면 옛 섹터가 나온다.

    섹터를 모르는 종목은 dict 에 아예 없다. **None 으로도, "기타" 로도
    채우지 않는다** — 채우면 그 종목들이 selector 에서 한 섹터로 묶여 섹터
    상한이 엉뚱한 종목을 걸러내게 된다 (candidates.select 는 sectors.get 이
    None 이면 그 종목엔 상한을 적용하지 않는다).
    """
    if not entities:
        return {}
    frame = store.get(SECTORS, as_of=as_of, entity=list(entities), lookback=lookback)
    if frame.empty:
        return {}
    frame = frame[(frame["market"] == market) & (frame["source"] == source)]
    if frame.empty:
        return {}
    latest = frame.sort_values("valid_from").groupby("entity_id").tail(1)
    return {
        str(row["entity_id"]): str(row["sector"])
        for row in latest.to_dict(orient="records")
        if row["sector"]
    }


def select(
    *,
    scores: pd.Series,
    params: SelectionParams,
    correlations: pd.DataFrame | None = None,
    sectors: Mapping[str, str] | None = None,
    trace: SelectionTrace | None = None,
    held: Iterable[str] | None = None,
) -> list[Candidate]:
    """4~6단계. 점수 높은 순으로 훑으며 상관 감점과 섹터 상한을 적용한다.

    **감점은 순위를 바꾼다.** 감점 후 점수가 다음 종목보다 낮아지면 그 종목이
    먼저 뽑혀야 하므로, 매번 남은 것 중 가장 높은 것을 다시 고른다.
    """
    trace = trace or SelectionTrace()
    remaining = {entity: float(score) for entity, score in scores.items()}
    raw = dict(remaining)
    chosen: list[Candidate] = []
    sector_counts: dict[str, int] = {}
    sector_limit = max(1, int(params.n_candidates * params.sector_cap))
    #: 완충 밖으로 떨어진 보유(점수는 있다) — 비용 인지 교체가 켜져 있으면 아래에서 진입과 짝지어 남길지 정한다.
    stale: list[str] = []
    n_kept = 0

    # **완충 구간** (selector.md §5). ``held`` 는 지금 보유 중인 종목이고,
    # ``params.exit_rank`` 안에 있으면 먼저 후보에 남긴다. 25위↔24위가 하루걸러
    # 자리를 바꾸는 회전을 막는 자리다 — 상관 감점은 새로 들어오는 종목에만 건다.
    if held and params.exit_rank > 0:
        order = sorted(remaining, key=lambda key: remaining[key], reverse=True)
        rank = {entity: index + 1 for index, entity in enumerate(order)}
        kept = sorted(
            (e for e in dict.fromkeys(held) if rank.get(e, 10**9) <= params.exit_rank),
            key=lambda e: rank[e],
        )
        for entity in kept[: params.n_candidates]:
            score = remaining.pop(entity)
            sector = sectors.get(entity) if sectors else None
            chosen.append(Candidate(entity, score=score, raw_score=raw[entity], sector=sector))
            if sector is not None:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        dropped = [e for e in dict.fromkeys(held) if e in rank and rank[e] > params.exit_rank]
        stale, n_kept = dropped, len(chosen)
        if kept or dropped:
            trace.note(
                f"완충: 보유 {len(kept)}종목 유지(순위 ≤ {params.exit_rank}), "
                f"{len(dropped)}종목 퇴출"
            )

    while remaining and len(chosen) < params.n_candidates:
        entity = max(remaining, key=lambda key: remaining[key])
        score = remaining.pop(entity)
        sector = sectors.get(entity) if sectors else None

        if sector is not None and sector_counts.get(sector, 0) >= sector_limit:
            trace.drop(entity, f"섹터 상한({sector} {sector_limit}종목)")
            continue

        chosen.append(Candidate(entity, score=score, raw_score=raw[entity], sector=sector))
        if sector is not None:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        if correlations is None or entity not in correlations.columns:
            continue
        # 이미 뽑힌 것과 겹치는 종목을 감점한다. 분산되지 않은 포트폴리오는
        # 분산이 아니라 레버리지다.
        column = correlations[entity]
        for other in list(remaining):
            if other not in column.index:
                continue
            value = column[other]
            if pd.notna(value) and float(value) > params.corr_threshold:
                remaining[other] -= params.corr_penalty
                trace.note(f"{other}: {entity} 와 상관 {float(value):.2f} — 감점")

    if params.swap_min_z > 0 and stale:
        chosen = _swap_only_if_better(
            chosen, n_kept=n_kept, stale=stale, scores=scores, raw=raw, params=params, sectors=sectors, trace=trace,
        )

    trace.stage("selected", len(chosen))
    return chosen


def _swap_only_if_better(
    chosen: list[Candidate],
    *,
    n_kept: int,
    stale: Sequence[str],
    scores: pd.Series,
    raw: Mapping[str, float],
    params: SelectionParams,
    sectors: Mapping[str, str] | None,
    trace: SelectionTrace,
) -> list[Candidate]:
    """비용 인지 교체 (selector.md §5 6번, 진단 `style-hedge-and-cost.md` §1 ② CA(θ)).

    ``chosen`` 은 완충이 남긴 보유 ``n_kept`` 개 뒤에 새 진입이 붙은 목록이다. 떨어진 보유 ``stale`` 가운데 진입으로 다시
    뽑힌 것은 공통으로 본다. 빈 자리는 진입 위부터 채우고, 남은 진입(z 내림)과 떨어진 보유(z 오름)를 짝지어
    z 차이가 문턱 이상인 짝만 바꾼다 — 처음 미달인 짝에서 멈추고 나머지 보유는 남긴다.
    """
    values = scores.astype(float)
    spread = float(values.std())
    if not spread > 0:
        trace.note("비용 인지 교체: 점수가 흩어지지 않아 z 를 못 잰다 — 완충 규칙 그대로")
        return chosen
    z = (values - float(values.mean())) / spread
    # 앞 ``n_kept`` 개가 완충 보유다. 그 뒤 진입 가운데 떨어진 보유가 다시 뽑혔으면 그것도 공통이다(판 적이 없다).
    stale_set = set(stale)
    common = chosen[:n_kept] + [item for item in chosen[n_kept:] if item.entity_id in stale_set]
    picked = {item.entity_id for item in common}
    rest = sorted((e for e in stale if e not in picked), key=lambda e: float(z[e]))
    if not rest:
        return chosen
    entrants = sorted(
        (item for item in chosen[n_kept:] if item.entity_id not in stale_set),
        key=lambda item: -float(z[item.entity_id]),
    )
    free = max(0, params.n_candidates - len(common) - len(rest))
    fill, pool = entrants[:free], entrants[free:]
    swapped = 0
    for out, incoming in zip(rest, pool, strict=False):
        if float(z[incoming.entity_id]) - float(z[out]) < params.swap_min_z:
            break
        swapped += 1
    retained = [
        Candidate(entity, score=float(raw[entity]), raw_score=float(raw[entity]),
                  sector=sectors.get(entity) if sectors else None)
        for entity in rest[swapped:]
    ]
    trace.note(
        f"비용 인지 교체(θ={params.swap_min_z:g}): 떨어진 보유 {len(rest)}종목 중 {swapped}종목만 교체, "
        f"{len(retained)}종목 유지 · 빈 자리 {len(fill)}종목 진입"
    )
    trace.measure("swap_retained", len(retained))
    return (common + retained + fill + pool[:swapped])[: params.n_candidates]
