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

import json
from collections.abc import Sequence

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from quant_rl_trading.store.errors import ConfigNotFound

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
    #: 국면 배수 확인 기간(세션). 최근 N 세션 배수의 최솟값 — 낮추기는 즉시, 올리기는
    #: N 연속 확인. 1 이면 현재 국면만 본다.
    regime_confirm_sessions: int = 1
    #: **노출 데드밴드** (시행 U 채택, 2026-09-18). 새 배수가 지금 적용 중인 배수와
    #: 이만큼 차이 나지 않으면 그대로 둔다. 0 이면 끈다.
    deadband: float = 0.0
    #: 밴드를 **올릴 때만** 거는가. 낮추기는 즉시 — 현행 `regime_confirm_sessions` 와 같은 정신.
    deadband_asymmetric: bool = True

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
            regime_confirm_sessions=int(section.get("regime_confirm_sessions", 1)),
            deadband=float(section.get("deadband", 0.0)),
            deadband_asymmetric=bool(section.get("deadband_asymmetric", True)),
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


#: 밴드 비교의 부동소수 여유. **`1.0 - 0.8` 은 0.19999999999999996 이다** — 밴드 0.20 과
#: 견주면 "20% 차이" 가 밴드 **안**으로 판정돼 0.8 이 위로 못 올라가는 흡수 상태가 된다.
#: 0.8 은 압축 축의 값이라 실전에서 가장 흔한 배수다(모의 장부 9/01·02·03·07·08 전부 0.8).
#: 그러면 압축이 한 번 걸린 뒤로 영영 80% 노출에 눌러앉는다 — 규칙이 뜻한 바가 아니다.
BAND_EPSILON = 1e-9

#: 직전 적용 배수를 찾는 창(달력일). 연휴를 넉넉히 넘긴다 — 못 찾으면 밴드를 안 건다.
HELD_LOOKBACK_DAYS = 20
EVENTS = "events"


def held_scale(store: Store, *, as_of: datetime, market: str) -> float | None:
    """**지금 적용 중인** 노출 배수. 직전 세션이 실제로 쓴 값이다. 없으면 None.

    데드밴드는 "새 값이 지금 값과 얼마나 다른가" 를 묻는 장치라 기준점이 필요한데,
    노출 결정은 원래 상태가 없었다(매 세션 처음부터 계산). 그 기준점을 **저널에서**
    읽는다 — 별도 상태 표를 만들면 그것이 창고와 어긋날 때 아무도 모른다.

    못 찾으면 None 을 주고 호출부는 밴드를 걸지 않는다. **모르면 원래대로**가
    안전한 쪽이다 — 기준 없이 밴드를 걸면 첫 세션이 1.0 에 붙박인다.

    ## 알려진 한계 — 더러운 샌드박스에 백테스트를 다시 돌리면

    `EventLog.flush` 는 같은 run_id 가 이미 있으면 **아무것도 안 쓴다**(재실행·이어돌리기를
    위해서다). 그래서 `data/_backtest` 같은 상주 샌드박스에 **설정을 바꾸고 다시 돌리면**
    이 함수가 **앞 실행의 배수**를 읽는다 — 두 번째 실행은 옛 파라미터의 노출 경로를 조용히
    재현한다. 이 저널을 결정에 되먹이기 전에는 낡은 행이 무해했는데, 이제는 아니다.

    라이브(모의계좌)는 영향이 없다: 같은 날 세션을 다시 돌려도 `valid_from < as_of` 가
    그날 자기 이벤트를 걸러내고, 읽는 값은 어제 실제로 적용한 배수 그대로다.

    **처방**: 노출·데드밴드 설정을 바꾼 뒤 백테스트는 **깨끗한 샌드박스**에서 돌린다.
    근본 수정(루프가 직전 세션의 배수를 직접 넘기고, 첫 세션만 저널을 본다)은 별건으로 남겼다.
    """
    frame = store.get(EVENTS, as_of=as_of, lookback=HELD_LOOKBACK_DAYS)
    if frame.empty:
        return None
    prefix = f"session-{market}-"
    rows = frame[
        (frame["stage"] == "exposure")
        & frame["entity_id"].astype(str).str.startswith(prefix)
        & (frame["valid_from"] < as_of)
    ]
    if rows.empty:
        return None
    latest = rows.sort_values(["valid_from", "seq"]).iloc[-1]
    payload = latest["payload"]
    try:
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        value = float(data["scale"])
    except (TypeError, ValueError, KeyError):
        return None
    return value if 0.0 < value <= 1.0 else None


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


def regime_scale(
    state: str, params: ExposureParams, recent_states: Sequence[str] = (),
) -> tuple[float, str | None]:
    """국면별 배수. 모르는 상태는 **1.0** 이다.

    `unknown` 에서 노출을 줄이면, 지수 이력이 짧은 구간(백테스트 초입)마다
    까닭 없이 절반만 사게 된다. 모른다는 것은 위험하다는 뜻이 아니다.

    ``recent_states`` 는 직전 세션들의 국면(오래된 것부터). **최근 N 세션 배수의
    최솟값**을 쓴다(N = ``regime_confirm_sessions``): 낮추는 것은 즉시, 올리는 것은
    N 세션 연속 확인 뒤. crisis 와 volatile 은 20일 모멘텀 부호 하나로 갈리는데,
    그 부호가 0 근처에서 하루걸러 뒤집히면 포트폴리오 절반을 팔았다 사는 왕복이
    난다(2026-08-22~27 shadow 실측: 익스포저 73%→38%→복원).
    """
    window = max(1, int(params.regime_confirm_sessions))
    states = [*recent_states, state][-window:]
    scales = {s: float(params.regime_scale.get(s, 1.0)) for s in states}
    lowest_state = min(states, key=lambda s: scales[s])
    scale = scales[lowest_state]
    if scale >= 1.0:
        return 1.0, None
    if lowest_state != state:
        return scale, f"국면 {state} (직전 {lowest_state} 확인 중 — {window}세션 연속이어야 올린다)"
    return scale, f"국면 {state}"


def decide(
    store: Store,
    *,
    as_of: datetime,
    index_id: str,
    regime_state: str,
    params: ExposureParams,
    recent_regime_states: Sequence[str] = (),
    held: float | None = None,
) -> ExposureDecision:
    """노출 배수 하나와 그 이유.

    **셋 중 가장 낮은 것을 따른다** — 곱하면 아무도 위험하다고 말하지 않았는데
    반토막이 나고, 평균을 내면 한 축의 경고가 나머지에 묻힌다.
    """
    decision = ExposureDecision()
    axes: list[tuple[str, float, str | None]] = []

    scale, note = trend_scale(store, as_of=as_of, index_id=index_id, params=params)
    axes.append(("trend", scale, note))
    scale, note = regime_scale(regime_state, params, recent_regime_states)
    axes.append(("regime", scale, note))
    scale, note = squeeze_scale(store, as_of=as_of, index_id=index_id, params=params)
    axes.append(("squeeze", scale, note))

    for _, _, note in axes:
        if note:
            decision.notes.append(note)

    name, lowest, _ = min(axes, key=lambda item: item[1])
    if lowest >= 1.0:
        proposed, driver = 1.0, "full"
    else:
        # 바닥을 둔다. 방어는 참여를 줄이는 것이지 나가는 것이 아니다.
        proposed, driver = max(FLOOR, lowest), name
        if lowest < FLOOR:
            decision.notes.append(f"바닥 {FLOOR:.0%} 에서 멈춤 (계산값 {lowest:.0%})")

    # **데드밴드** (시행 U 채택, 2026-09-18). 배수가 조금 움직일 때마다 따라가면
    # 장부의 그만큼이 왕복한다 — 모의계좌 실측으로 거래의 76%가 이 축에서 났고,
    # 판정 창 360세션에서 밴드 0.20(내릴 때는 즉시)이 수익·MDD·전환 셋 다 현행보다
    # 나았다(+9.4% vs +9.2% · −15.8% vs −17.7% · 26회 vs 42회).
    if params.deadband > 0.0 and held is not None:
        falling = proposed < held
        gap = abs(proposed - held)
        if not (params.deadband_asymmetric and falling) and gap < params.deadband - BAND_EPSILON:
            decision.notes.append(
                f"데드밴드 — 새 배수 {proposed:.0%} 가 적용 중 {held:.0%} 와 "
                f"{gap:.0%} 차이라 유지 (밴드 {params.deadband:.0%})"
            )
            decision.scale = held
            decision.driver = "deadband"
            return decision

    decision.scale = proposed
    decision.driver = driver
    return decision


def apply(weights: dict[str, float], decision: ExposureDecision) -> dict[str, float]:
    """목표 비중에 배수를 곱한다. **줄어든 몫은 현금이다.**

    비중을 다시 정규화하지 않는다 — 정규화하면 합이 도로 1 이 되어 노출을
    줄인 것이 사라진다. 이 함수가 있는 이유가 그 실수를 막는 것이다.
    """
    if decision.scale >= 1.0:
        return dict(weights)
    return {entity: value * decision.scale for entity, value in weights.items()}


# --------------------------------------------------------------------------- AI v2 — 학습 부품이 적은 노출 행동

ACTIONS = "exposure_actions"
#: 행동을 찾는 창(달력일). 연휴를 넘기되 너무 오래된 행동을 쓰지 않게.
ACTION_LOOKBACK_DAYS = 10
#: **그 세션의 행동만 쓴다.** 부품은 행동을 세션과 같은 시각(`snapshot_moment`)으로 적는다 — 그보다 이만큼 넘게 오래됐으면
#: 다른 세션의 행동이다. 예전엔 창 안의 최신 행을 날짜 검사 없이 써서, 22:50 부품이 죽으면 금요일 배수로 월·화·수를 돌았다
#: (2026-09-26 감사). 연휴에 같은 세션이 반복되면 as_of 도 그 세션이라 여전히 맞는다.
ACTION_MAX_AGE = timedelta(hours=12)


def source_config(store: Store, *, as_of: datetime) -> str:
    """`exposure.source` — "rule"(기본, 옛 동작) 또는 학습 부품 이름(예: hmm-v1). 샌드박스 덮어쓰기로만 켠다."""
    try:
        value = str(store.config("exposure.source", as_of=as_of) or "rule")
    except ConfigNotFound:
        return "rule"
    return value or "rule"


def learned_decision(store: Store, *, as_of: datetime, market: str, source: str) -> ExposureDecision | None:
    """학습 부품이 **세션 전에** 적어 둔 노출 배수를 읽는다. 없으면 None — 호출자가 규칙으로 물러선다(이유를 남긴다)."""
    frame = store.get(ACTIONS, as_of=as_of, lookback=ACTION_LOOKBACK_DAYS, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "source", "scale"])
    if frame.empty:
        return None
    rows = frame[(frame["source"] == source) & (frame["entity_id"].astype(str) == str(market))]
    if rows.empty:
        return None
    latest = rows.sort_values(["valid_from", "observed_at"]).iloc[-1]
    if pd.Timestamp(latest["valid_from"]) < pd.Timestamp(as_of) - ACTION_MAX_AGE:
        return None   # 낡은 행동 — 호출자가 "행동이 없다 — 규칙으로 물러섰다" 를 남긴다
    scale = float(latest["scale"])
    return ExposureDecision(scale=min(1.0, max(FLOOR, scale)), driver=f"learned:{source}",
                            notes=[f"{source} 노출 {scale:.2f} ({pd.Timestamp(latest['valid_from']).date()})"])
