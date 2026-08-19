"""노출 제어 — **얼마나 살지**를 정한다. 무엇을 살지가 아니다 (태스크 #39).

전략 순서 ①→③→② 의 ③이다. ①은 `risk` 를 알파에서 제약으로 옮긴 것
(`constraints.py`), ②는 횡단면 피처 교체이고 후순위다.

## chart 가 여기로 온 이유

`chart` 의 고유 피처 여섯은 **횡단면 랭크 IC 가 전부 0** 이었다(KR 300세션):

    momentum_20 +0.0000   momentum_60 -0.0095   reversal_5   -0.0007
    ma_gap      +0.0023   volume_surge +0.0118  range_position +0.0029

한때 보이던 IC +0.0197 은 섞여 있던 `low_volatility`(risk 의 피처)에서 빌린
것이었다. 그래서 상태(state) 피처 여덟을 새로 만들어 다시 쟀다(2026-08-18,
KR 300세션·82만 행). **횡단면은 여전히 전부 미달이다** — 최고가
`bb_squeeze` +0.0180.

**그런데 하나가 살아남았다.** 변동성 압축은 이후 변동성을 맞힌다:

    bb_squeeze 5분위 → 이후 20일 변동성비
    0.857 → 0.940 → 1.014 → 1.073 → 1.179     완전 단조

`range_compression` 도 0.821 → 1.164 로 같다. 그런데 **평균수익과 승률은
분위 간 차이가 없다.** 즉 압축은 "곧 크게 움직인다" 를 말하지 "어느 쪽으로"
를 말하지 않는다.

**종목을 줄세우는 데는 못 쓰고, 얼마나 들지 정하는 데는 쓴다.** 이 모듈이
그 자리다.

## 세 축을 곱하지 않고 **가장 낮은 것을 따른다**

노출을 정하는 신호가 셋이다 — 추세(지수 이평)·국면(regime)·변동성(압축).
곱하면 셋이 조금씩 낮을 때 0.7×0.8×0.8 = 0.45 가 되어, **아무도 위험하다고
말하지 않았는데 반토막**이 난다. 평균을 내면 반대로 하나가 강하게 경고해도
나머지 둘이 묻어 버린다.

최솟값을 쓰면 "가장 걱정하는 축이 정한다" 가 된다. 그리고 **어느 축이
정했는지 이름이 남는다** — 노출이 줄어든 날 이유를 못 대면 그 장치는
운영할 수 없다.

## 왜 현금 비중이 아니라 배수인가

`allocator.cash_buffer` 는 체결·수수료 오차를 흡수하는 **회계적 여유**다.
그 자리에 국면 판단을 섞으면 두 가지가 한 숫자에 들어가고, 나중에 "현금이
왜 30% 지" 를 물었을 때 답이 갈린다. 노출 제어는 **투자 가능액에 곱하는
배수**로 따로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from quant_rl_trading.store import Store

INDICES = "indices"

#: 추세를 재는 이평 창(거래일). 200일은 이 판단에 흔히 쓰는 창이고, 우리
#: 지수 이력(코스피 2020-08~)이 충분히 덮는다.
TREND_WINDOW = 200

#: 지수 계열을 읽는 창. 이평 창보다 넉넉해야 첫날부터 값이 나온다.
INDEX_LOOKBACK_DAYS = 400

#: 변동성 압축을 재는 창과, 그 값을 백분위로 바꿀 기준 창.
#: **절대폭이 아니라 자기 과거 대비**로 재는 것이 핵심이다 — 절대폭으로 재면
#: 저변동 종목만 뽑혀 `low_volatility` 를 복제하게 된다(측정에서 확인:
#: 이 정의로 risk 와의 상관이 |ρ| ≤ 0.14 에 머물렀다).
SQUEEZE_WINDOW = 20
SQUEEZE_BASELINE = 120

#: 이보다 작은 변동성은 **압축이 아니라 데이터 이상**으로 본다. 지수 일간
#: 변동성 0.0001(=1bp)은 실제 시장에 없다 — 종가가 며칠 같았다는 뜻이고,
#: 그건 거래정지나 수집 결손이다.
FLAT_EPSILON = 1e-4

#: 노출을 이 아래로는 내리지 않는다. 0 까지 내리면 그날 포트폴리오가 통째로
#: 현금이 되고, 되돌아올 때 그 구간을 통째로 놓친다. 방어는 참여를 줄이는
#: 것이지 나가는 것이 아니다 — 나가는 판단은 킬스위치가 따로 한다.
FLOOR = 0.30


@dataclass(frozen=True)
class ExposureParams:
    """노출 제어 임계치. 전부 `store.config` 에서 온다 (불변식 10)."""

    #: 지수가 이평 아래일 때의 노출 배수. 1.0 이면 추세 축을 끈다.
    below_trend: float
    #: 국면별 배수. `analysts/regime.py` 의 상태 이름을 그대로 쓴다.
    regime_scale: dict[str, float]
    #: 변동성 압축 상위 백분위에 들면 이 배수. 압축은 **방향이 아니라 크기**를
    #: 예고하므로, 방향을 모른 채 크게 걸지 않는다는 뜻이다.
    squeezed: float
    #: 압축으로 볼 백분위 문턱(작을수록 좁은 밴드).
    squeeze_quantile: float

    @classmethod
    def from_store(cls, store: Store, *, as_of: datetime) -> ExposureParams:
        """창고에서 읽는다.

        **중첩 dict 는 평탄한 키로 저장된다** — `regime_scale: {bull: 1.0}` 이
        `regime_scale.bull` 한 행이 된다(config 표가 키·값 두 열이라 그렇다).
        그래서 여기서 다시 접어 준다. 이걸 모르고 `section["regime_scale"]` 을
        찾으면 KeyError 가 나고, 그 예외는 세션 전체를 멈춘다.
        """
        section = dict(store.config("exposure", as_of=as_of))
        prefix = "regime_scale."
        scales = {
            key[len(prefix):]: float(value)
            for key, value in section.items()
            if key.startswith(prefix)
        }
        return cls(
            below_trend=float(section["below_trend"]),
            regime_scale=scales,
            squeezed=float(section["squeezed"]),
            squeeze_quantile=float(section["squeeze_quantile"]),
        )


@dataclass
class ExposureDecision:
    """노출 배수와 **누가 정했는지**.

    이유를 안 남기면 노출이 줄어든 날 설명할 수 없고, 설명 못 하는 방어는
    다음에 누가 꺼 버린다.
    """

    scale: float = 1.0
    driver: str = "full"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"scale": self.scale, "driver": self.driver, "notes": list(self.notes)}


def _index_close(store: Store, *, as_of: datetime, entity_id: str) -> pd.Series:
    """지수 종가 계열. 없으면 빈 Series — 지어내지 않는다."""
    frame = store.get(
        INDICES,
        as_of=as_of,
        entity=entity_id,
        lookback=INDEX_LOOKBACK_DAYS,
        columns=["close", "valid_from"],
    )
    if frame.empty:
        return pd.Series(dtype=float)
    ordered = frame.sort_values("valid_from")
    closes = ordered["close"].astype(float)
    # 종가 0 은 휴장일 행이다. 그대로 두면 이평이 통째로 내려앉아 "추세 아래"
    # 가 거짓으로 켜진다 (2026-06-03·07-17 실측 사고와 같은 원인).
    return closes[closes > 0.0]


def trend_scale(
    store: Store, *, as_of: datetime, index_id: str, params: ExposureParams
) -> tuple[float, str | None]:
    """지수가 장기 이평 아래면 노출을 줄인다.

    **이것이 chart 가 원래 잘하는 일이다** — 종목 줄세우기가 아니라 시점.
    """
    closes = _index_close(store, as_of=as_of, entity_id=index_id)
    if len(closes) < TREND_WINDOW:
        return 1.0, (
            f"추세 축 미적용 — {index_id} 종가가 {len(closes)}개로 "
            f"{TREND_WINDOW}일 이평에 모자란다"
        )
    ma = float(closes.tail(TREND_WINDOW).mean())
    last = float(closes.iloc[-1])
    if ma <= 0:
        return 1.0, "추세 축 미적용 — 이평이 0 이하다"
    if last < ma:
        gap = last / ma - 1.0
        return params.below_trend, (
            f"지수가 {TREND_WINDOW}일 이평 아래 ({last:,.1f} vs {ma:,.1f}, {gap:+.1%})"
        )
    return 1.0, None


def squeeze_scale(
    store: Store, *, as_of: datetime, index_id: str, params: ExposureParams
) -> tuple[float, str | None]:
    """변동성이 압축돼 있으면 노출을 줄인다.

    ## 왜 압축에서 줄이나 — 직관과 반대로 보인다

    압축은 **조용한 구간**이라 위험해 보이지 않는다. 그런데 실측(82만 행)에서
    압축 상위 분위의 이후 20일 변동성비가 1.179 로 가장 컸다 — 조용한 구간
    뒤에 크게 움직인다. 그리고 **어느 쪽으로 움직일지는 못 맞힌다**(평균수익·
    승률이 분위 간 차이 없음).

    방향을 모른 채 크기만 커진다면 걸어 둔 돈을 줄이는 것이 맞다.
    """
    closes = _index_close(store, as_of=as_of, entity_id=index_id)
    if len(closes) < SQUEEZE_BASELINE + SQUEEZE_WINDOW:
        return 1.0, None

    returns = closes.pct_change().dropna()
    band = returns.rolling(SQUEEZE_WINDOW).std()
    recent = band.dropna()
    if len(recent) < SQUEEZE_BASELINE:
        return 1.0, None

    current = float(recent.iloc[-1])
    baseline = recent.tail(SQUEEZE_BASELINE)
    threshold = float(baseline.quantile(params.squeeze_quantile))
    if not np.isfinite(current) or not np.isfinite(threshold):
        return 1.0, None

    # **변동성 0 은 압축이 아니라 데이터 이상이다.** 지수가 며칠 같은 값이면
    # 거래정지이거나 수집이 같은 행을 반복한 것이지, 시장이 조용한 것이 아니다.
    # 안 가르면 결손 구간마다 노출이 조용히 줄고, 그건 수집 사고가 매매 결정이
    # 되는 것이다 — 이 저장소가 반복해서 막아 온 종류의 고장이다.
    if current <= FLAT_EPSILON:
        return 1.0, (
            f"압축 축 미적용 — 20일 변동성이 {current:.6f} 로 사실상 0 이다. "
            "거래정지나 수집 결손을 의심할 것"
        )
    if current <= threshold:
        return params.squeezed, (
            f"변동성 압축 — 20일 변동성이 자기 과거 120세션의 "
            f"하위 {params.squeeze_quantile:.0%} ({current:.4f} ≤ {threshold:.4f})"
        )
    return 1.0, None


def regime_scale(state: str, params: ExposureParams) -> tuple[float, str | None]:
    """국면별 배수. 모르는 상태는 **1.0** 이다.

    `unknown` 에서 노출을 줄이면, 지수 이력이 짧은 구간(백테스트 초입)마다
    까닭 없이 절반만 사게 된다. 모른다는 것은 위험하다는 뜻이 아니다.
    """
    scale = float(params.regime_scale.get(state, 1.0))
    if scale >= 1.0:
        return 1.0, None
    return scale, f"국면 {state}"


def decide(
    store: Store,
    *,
    as_of: datetime,
    index_id: str,
    regime_state: str,
    params: ExposureParams,
) -> ExposureDecision:
    """노출 배수 하나와 그 이유.

    **셋 중 가장 낮은 것을 따른다** — 곱하면 아무도 위험하다고 말하지 않았는데
    반토막이 나고, 평균을 내면 한 축의 경고가 나머지에 묻힌다.
    """
    decision = ExposureDecision()
    axes: list[tuple[str, float, str | None]] = []

    scale, note = trend_scale(store, as_of=as_of, index_id=index_id, params=params)
    axes.append(("trend", scale, note))
    scale, note = regime_scale(regime_state, params)
    axes.append(("regime", scale, note))
    scale, note = squeeze_scale(store, as_of=as_of, index_id=index_id, params=params)
    axes.append(("squeeze", scale, note))

    for _, _, note in axes:
        if note:
            decision.notes.append(note)

    name, lowest, _ = min(axes, key=lambda item: item[1])
    if lowest >= 1.0:
        return decision

    # 바닥을 둔다. 방어는 참여를 줄이는 것이지 나가는 것이 아니다.
    decision.scale = max(FLOOR, lowest)
    decision.driver = name
    if lowest < FLOOR:
        decision.notes.append(f"바닥 {FLOOR:.0%} 에서 멈춤 (계산값 {lowest:.0%})")
    return decision


def apply(weights: dict[str, float], decision: ExposureDecision) -> dict[str, float]:
    """목표 비중에 배수를 곱한다. **줄어든 몫은 현금이다.**

    비중을 다시 정규화하지 않는다 — 정규화하면 합이 도로 1 이 되어 노출을
    줄인 것이 사라진다. 이 함수가 있는 이유가 그 실수를 막는 것이다.
    """
    if decision.scale >= 1.0:
        return dict(weights)
    return {entity: value * decision.scale for entity, value in weights.items()}
