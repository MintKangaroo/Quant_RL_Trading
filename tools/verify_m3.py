"""M3 완료 기준 검증기.

    uv run python tools/verify_m3.py

``docs/milestones.md`` 의 M3 완료 기준 5개를 하나씩 확인하고, 각 항목마다
근거가 된 명령과 숫자를 출력한다. ``tools/verify_m1.py`` 와 같은 구조
(``Check`` 데이터클래스·근거 문자열·종료코드 규약)를 그대로 따른다 —
새 양식을 발명하지 않는다. **체크박스를 손으로 켜지 않는다.**

## M1 검증기와 다른 점 하나 — 상태가 셋이다

M1 은 통과/실패 둘이면 됐다. M3 는 다르다. 지금 5개 중 다수가 **아직 잴 수
없다** — 백테스트가 완주하지 못했고, 실전 투입도 아직 안 했다. 그 상태를
FAIL 로 적으면 "고장났다" 로 읽히고, PASS 로 적으면 거짓이 된다. 그래서
``Check.status`` 는 ``PASS`` · ``FAIL`` · ``미측정`` 셋 중 하나다:

- ``PASS``   — 쟀고 통과했다
- ``FAIL``   — 쟀고 못 넘었다
- ``미측정`` — 아직 잴 수 없다(선행 조건이 안 갖춰졌다). **왜** 못 재는지,
  무엇이 갖춰지면 잴 수 있는지를 근거에 적는다 — 그게 이 상태의 실제 가치다.

## 함정 — "아무것도 안 하면 통과한다" 는 M1 의 교훈을 그대로 가져온다

M1 검증기는 "서로 다른 결과 100종 / 100일" 을 같이 세서, 아무것도 안 하는
구현도 "두 번 실행해서 같다" 는 통과한다는 함정을 막았다. M3 에서 같은
함정은 이렇게 나타난다:

- **매매 0건이면 MDD 는 언제나 0%.** OOS 판정에서 거래가 없었던 구간을
  섞으면 "완벽한 성적" 이 아니라 "아무것도 안 한 결과" 다.
- **NAV 가 초기값에 고정돼 있으면 무사고가 아니라 미검증이다.** shadow 가
  10 일을 채워도, 그 열흘의 NAV 가 전부 같은 값이면 손익 경로가 한 번도
  안 돈 것이다 — ``docs/milestones.md`` 가 2026-08-13·14 이틀에 대해 정확히
  이 표현을 썼다("무사고가 아니라 미검증").
- **시뮬레이션 체결로 모델을 검증하면 정의상 오차 0 이다.** 슬리피지 검증은
  ``tools/measure_slippage.py`` 를 그대로 불러 쓴다 — 그 도구가 이미 이
  함정(``source=backtest`` 로는 PASS 를 안 낸다)을 막아 뒀다. 다시
  구현하지 않는다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import ConfigNotFound, Store, overlay  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.measure_slippage import measure as measure_slippage  # noqa: E402
from tools.measure_slippage import render as render_slippage  # noqa: E402

_STATUSES = ("PASS", "FAIL", "미측정", "이월")


@dataclass
class Check:
    name: str
    status: str
    evidence: list[str]

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"알 수 없는 상태: {self.status!r} (허용: {_STATUSES})")

    def render(self) -> str:
        lines = [f"[{self.status}] {self.name}"]
        lines += [f"       {line}" for line in self.evidence]
        return "\n".join(lines)


def _run(command: list[str]) -> tuple[int, str]:
    finished = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    return finished.returncode, (finished.stdout + finished.stderr).strip()


# -----------------------------------------------------------------------------
# 기준 1 — shadow 10거래일 무사고
# -----------------------------------------------------------------------------

#: docs/milestones.md: "shadow 카운트를 0 으로 되돌렸다 (2026-08-14)." —
#: 그 전 이틀은 D+1 체결 단계가 실제로 안 돈 날이라 미검증이지 무사고가 아니다.
SHADOW_RESTART_DATE = date(2026, 8, 14)
REQUIRED_SHADOW_DAYS = 10
SHADOW_SANDBOX = REPO_ROOT / "data" / "_shadow"

#: **판단이 반쪽이었던 날.** 파이프라인은 끝까지 돌았고 킬스위치도 안 울렸지만,
#: 그날 세션이 설계된 Analyst 를 다 못 쓰고 결정을 냈다. 사고가 아니라고 하기엔
#: 그 결정의 근거가 절반이다.
#:
#: 2026-08-20 — event · fundamental · regime 이 MemoryError 로 죽어 6종 중
#: 3종(chart · flow_kr · risk)으로 후보를 골랐다. 18:51 shadow 세션이 그
#: 상태로 돌았다(원인·수정: 커밋 39fd493).
#:
#: **왜 창고를 읽어 자동으로 판정하지 않는가.** 그럴 수 없기 때문이다.
#: 같은 날 19:03 에 세 Analyst 를 되살려 신호를 채웠고, 그 뒤로 `signals` 는
#: 그날을 6종으로 보여준다 — **정정본이 사고의 흔적을 지웠다.** 창고를 읽는
#: 판정기는 이 날을 영영 못 잡는다.
#:
#: 그래서 여기 손으로 적는다. 다음 사고를 손으로 적지 않으려면 실패를
#: **일어난 순간 데이터로 남겨야** 한다 — 지금은 rc 와 로그에만 있다.
DEGRADED_SESSIONS: dict[date, str] = {
    date(2026, 8, 20): "Analyst 3종(event·fundamental·regime)이 죽어 6종 중 3종으로 판단",
}

#: 세션 하나가 정상적으로 끝까지 돈 것으로 보려면 이 네 단계가 다 있어야
#: 한다(backtest/loop.py 가 매 세션 이 순서로 기록한다). 하나라도 빠지면
#: 파이프라인이 도중에 죽은 것이다 — 그 자체가 사고다.
REQUIRED_STAGES = {"observe", "select", "allocate", "execute"}


def _shadow_store(live_root: Path) -> Store | None:
    """shadow 샌드박스를 읽기 전용으로 연다. 없으면 None."""
    if not SHADOW_SANDBOX.exists():
        return None
    layer = overlay.build(root=SHADOW_SANDBOX, source=live_root, writable=frozenset())
    return Store(root=layer.root)


#: 원 단위 잡음. 이보다 작은 차이는 "NAV 가 움직였다" 의 근거로 안 쓴다.
_NAV_EPSILON = 0.005


def _days(days: set[date]) -> str:
    """근거에 찍는 날짜 목록. ``repr`` 로 두면 ``datetime.date(2026, 8, 14)`` 가
    그대로 새어 나와 사람이 읽을 것이 아니게 된다."""
    return ", ".join(day.isoformat() for day in sorted(days))


def _stale_pnl_days(
    shadow: Store, as_of: datetime, lookback: int, filled_days: set[date]
) -> tuple[set[date], list[str]]:
    """체결이 있었는데 **손익 경로가 안 돈** 날들. 그리고 그 근거 문장.

    ## 왜 "NAV 가 전부 같으면 정체" 가 아닌가

    처음 판정은 이랬다 — nav_daily 값이 전부 같으면 손익 경로가 안 돈 것이다.
    거칠다. **매수만 있고 종가가 안 움직인 하루는 NAV 가 거의 안 변하는 것이
    정상이다** (현금↓ + 주식평가↑ 로 상쇄되고 수수료만큼만 준다). 실제
    2026-08-14 이 그랬다: 올바른 NAV 는 9,999,654.76 으로 초기 자본과 345원
    차이였다. 반올림 자리 하나만 달랐어도 "정체" 판정이 통째로 뒤집힌다.

    그래서 NAV 값의 **다양성**이 아니라 그날 회계가 체결을 **반영했는지**를
    본다. 두 가지 중 하나면 반영된 것이다:

    1. **보유 평가액이 0 이 아니다** — 산 주식이 그날 값으로 평가됐다.
       체결이 장부에 안 들어가면 이 값이 정확히 0 이다.
    2. **NAV 가 누적 입금액과 다르다** — 수수료든 평가손익이든 손익 경로를
       한 번은 지났다는 뜻이다. 입금만 있고 아무 일도 없으면 정확히 같다.

    둘 다 아니면 그 날은 "무사고" 가 아니라 **미검증**이다.
    """
    notes: list[str] = []
    if not filled_days:
        return set(), ["체결일이 없어 손익 경로를 잴 대상이 없다"]

    # nav_daily 는 market 컬럼이 없는 단일 펀드 표라 market= 을 넘기면
    # SchemaViolation 이 난다.
    nav = shadow.get(
        "nav_daily", as_of=as_of, lookback=lookback,
        columns=["nav", "cash_krw", "cash_usd", "equity_kr", "equity_us", "fx_rate"],
    )
    flows = shadow.get(
        "capital_flows", as_of=as_of, lookback=lookback, columns=["currency", "amount"]
    )

    by_day = {row["valid_from"].date(): row for row in nav.to_dict("records")}
    stale: set[date] = set()
    for day in sorted(filled_days):
        row = by_day.get(day)
        if row is None:
            stale.add(day)
            notes.append(f"{day} 체결이 있는데 nav_daily 행이 없다 — 스냅샷이 안 남았다")
            continue

        equity = float(row["equity_kr"]) + float(row["equity_us"])
        rate = float(row["fx_rate"])
        deposited = 0.0
        for flow in flows.to_dict("records"):
            if flow["valid_from"].date() > day:
                continue
            amount = float(flow["amount"])
            deposited += amount if str(flow["currency"]) == "KRW" else amount * rate
        nav_value = float(row["nav"])
        moved = abs(nav_value - deposited) > _NAV_EPSILON

        if equity <= 0.0 and not moved:
            stale.add(day)
            notes.append(
                f"{day} 손익 경로 미검증 — 체결이 있는데 보유 평가액 0원이고 "
                f"NAV {nav_value:,.2f} 가 누적 입금 {deposited:,.2f} 과 같다. "
                "체결이 장부에 안 들어간 것이다(무사고가 아니라 미검증)."
            )
            continue
        notes.append(
            f"{day} 손익 경로 확인 — 보유 평가 {equity:,.0f}원, "
            f"NAV {nav_value:,.2f} vs 누적 입금 {deposited:,.2f}"
        )
    return stale, notes


def check_shadow(live_store: Store, as_of: datetime) -> Check:
    name = f"shadow {REQUIRED_SHADOW_DAYS}거래일 무사고"
    shadow = _shadow_store(live_store.root)
    if shadow is None:
        return Check(
            name, "미측정", [f"{SHADOW_SANDBOX} 가 없다 — shadow 가 아직 한 번도 안 돌았다"]
        )

    lookback = max((as_of.date() - SHADOW_RESTART_DATE).days + 5, 5)

    events = shadow.get("events", as_of=as_of, lookback=lookback)
    events = events[events["entity_id"].astype(str).str.startswith("session-")]
    events = events[events["valid_from"].dt.date >= SHADOW_RESTART_DATE]
    if events.empty:
        return Check(
            name, "미측정",
            [f"{SHADOW_RESTART_DATE.isoformat()} 재시작 이후 세션 기록이 없다"],
        )

    by_day: dict[date, set[str]] = {}
    for row in events.to_dict(orient="records"):
        day = row["valid_from"].date()
        by_day.setdefault(day, set()).add(str(row["stage"]))
    incomplete_days = {day for day, stages in by_day.items() if not stages >= REQUIRED_STAGES}

    trades = shadow.get("trades", as_of=as_of, lookback=lookback, columns=["quantity"])
    filled_days: set[date] = set()
    if not trades.empty:
        trades = trades[trades["valid_from"].dt.date >= SHADOW_RESTART_DATE]
        filled_days = set(trades["valid_from"].dt.date.unique())

    killswitch = shadow.get("killswitch", as_of=as_of, lookback=lookback, columns=["state"])
    engaged_days: set[date] = set()
    if not killswitch.empty:
        killswitch = killswitch[killswitch["valid_from"].dt.date >= SHADOW_RESTART_DATE]
        engaged = killswitch[killswitch["state"] == "ENGAGED"]
        engaged_days = set(engaged["valid_from"].dt.date.unique())

    try:
        stale_days, nav_notes = _stale_pnl_days(shadow, as_of, lookback, filled_days)
    except Exception as exc:
        stale_days, nav_notes = set(filled_days), [f"nav_daily 를 못 읽었다: {exc}"]

    degraded_days = {day for day in DEGRADED_SESSIONS if day in filled_days}
    incidents = incomplete_days | engaged_days | degraded_days
    verified_days = filled_days - incidents - stale_days

    evidence = [
        f"{SHADOW_RESTART_DATE.isoformat()} 재시작 이후 세션 {len(by_day)}일 "
        f"({sorted(by_day)[0]} ~ {sorted(by_day)[-1]})",
        f"체결 발생일 {len(filled_days)}일: {_days(filled_days)}",
        f"파이프라인 중도 종료 {len(incomplete_days)}일"
        + (f": {_days(incomplete_days)}" if incomplete_days else ""),
        f"킬스위치 발동 {len(engaged_days)}일"
        + (f": {_days(engaged_days)}" if engaged_days else ""),
        *[
            f"판단 반쪽 {day.isoformat()}: {DEGRADED_SESSIONS[day]}"
            for day in sorted(degraded_days)
        ],
        *nav_notes,
        f"→ 검증된 무사고 체결일 {len(verified_days)}/{REQUIRED_SHADOW_DAYS}"
        + (f": {_days(verified_days)}" if verified_days else ""),
    ]

    if incidents:
        return Check(name, "FAIL", evidence)
    if len(verified_days) < REQUIRED_SHADOW_DAYS:
        return Check(name, "미측정", evidence)
    return Check(name, "PASS", evidence)


# -----------------------------------------------------------------------------
# 기준 2 — OOS 백테스트 MDD 20% 이내
# -----------------------------------------------------------------------------

BACKTEST_SANDBOX = REPO_ROOT / "data" / "_backtest"

#: 완주하지 못한(중간에 죽은) 결과를 완주로 오인하지 않기 위한 두 문턱.
#: 값 자체보다 **의도**가 근거다 — docs/milestones.md M3 §진행 이 OOM 으로
#: 죽은 워크포워드를 보고했고(2026-08 기준 재실행 중), 그 흔적은 nav_daily
#: 최신 관측일이 오늘보다 한참 전이거나 구간이 짧게 끊긴 모습으로 남는다.
STALE_DAYS_THRESHOLD = 14
MIN_OOS_SPAN_DAYS = 180


def check_oos_mdd(live_store: Store, as_of: datetime, market: str) -> Check:
    name = "OOS 백테스트 MDD 20% 이내"
    if not BACKTEST_SANDBOX.exists():
        return Check(
            name, "미측정", [f"{BACKTEST_SANDBOX} 가 없다 — OOS 백테스트가 아직 한 번도 안 돌았다"]
        )

    bt_store = Store(root=BACKTEST_SANDBOX)
    try:
        nav = bt_store.get("nav_daily", as_of=as_of, lookback=3650, columns=["nav", "drawdown"])
    except Exception as exc:
        return Check(name, "미측정", [f"nav_daily 를 못 읽는다(스키마 드리프트로 보인다): {exc}"])
    if nav.empty:
        return Check(name, "미측정", [f"{BACKTEST_SANDBOX} 의 nav_daily 가 비어 있다"])

    nav = nav.sort_values("valid_from")
    start, end = nav["valid_from"].iloc[0], nav["valid_from"].iloc[-1]
    span_days = (end - start).days
    staleness_days = (as_of - end.to_pydatetime()).days
    mdd = float(nav["drawdown"].min())  # 가장 깊은 낙폭. 음수다.

    try:
        gate = float(live_store.config("backtest.mdd_promotion_gate", as_of=as_of))
    except ConfigNotFound:
        gate = 0.20  # config/quant_rl_trading.yaml 기본값 — 못 읽으면 참고용으로만 쓴다

    evidence = [
        f"$ Store(root={BACKTEST_SANDBOX}).get('nav_daily', columns=['nav','drawdown'])",
        f"nav_daily {len(nav)}행, 구간 {start.date()} ~ {end.date()} ({span_days}일)",
        f"as_of 대비 최신 관측 {staleness_days}일 전",
        f"MDD 실측 {mdd:.1%} (승격 게이트 -{gate:.0%}, config backtest.mdd_promotion_gate)",
    ]
    if nav["nav"].nunique() <= 1:
        evidence.append(
            "NAV 가 한 값으로 고정 — 매매가 없었다는 뜻이다. "
            "MDD 0% 는 완벽한 성적이 아니라 미검증이다"
        )
        return Check(name, "미측정", evidence)
    if staleness_days > STALE_DAYS_THRESHOLD or span_days < MIN_OOS_SPAN_DAYS:
        evidence.append(
            f"워크포워드가 완주하지 못한 것으로 보인다"
            f"(최신 관측이 {staleness_days}일 전이거나 구간이 {MIN_OOS_SPAN_DAYS}일 미만) — "
            "이 MDD 는 참고치일 뿐 판정에 쓰지 않는다"
        )
        return Check(name, "미측정", evidence)

    return Check(name, "PASS" if mdd >= -gate else "FAIL", evidence)


# -----------------------------------------------------------------------------
# 기준 3 — 킬스위치 실제 발동 테스트 통과
# -----------------------------------------------------------------------------


def check_killswitch_tests() -> Check:
    name = "킬스위치 실제 발동 테스트 통과 — tests/executor/test_executor.py"
    command = ["uv", "run", "pytest", "tests/executor/test_executor.py", "-o", "addopts=", "-q"]
    code, output = _run(command)
    summary = [line for line in output.splitlines() if " passed" in line or " failed" in line]
    evidence = [f"$ {' '.join(command)}", *(summary or output.splitlines()[-5:])]
    return Check(name, "PASS" if code == 0 else "FAIL", evidence)


# -----------------------------------------------------------------------------
# 기준 4 — 실전 소액 투입 (배선 검증 목적)
# -----------------------------------------------------------------------------

#: broker/fills.py 가 실계좌 체결에 붙이는 source 값.
BROKER_SOURCE = "broker"


def check_live_trade(store: Store, as_of: datetime, market: str) -> Check:
    name = "실전 소액 투입 (배선 검증 목적)"
    trades = store.get(
        "trades", as_of=as_of, lookback=3650, market=market,
        columns=["side", "quantity", "price", "source"],
    )
    if trades.empty:
        return Check(
            name, "미측정",
            [f"trades 0행(market={market}) — 아직 어떤 체결도 없다(모의·실전 모두)"],
        )

    by_source = trades["source"].value_counts().to_dict()
    broker = trades[trades["source"] == BROKER_SOURCE]
    evidence = ["체결 출처: " + ", ".join(f"{k} {v}행" for k, v in sorted(by_source.items()))]
    if broker.empty:
        evidence.append(
            f"source={BROKER_SOURCE} 체결이 없다 — 실전 appkey 로 아직 주문을 내보내지 않았다"
            "(모의투자는 주문 TR 자체가 막혀 있다, docs/milestones.md M3 §2)"
        )
        return Check(name, "미측정", evidence)

    latest = broker.sort_values("valid_from").iloc[-1]
    evidence.append(
        f"실전 체결 {len(broker)}건, 최근: {latest['valid_from']} "
        f"{latest['side']} {latest['quantity']}@{latest['price']}"
    )
    return Check(name, "PASS", evidence)


# -----------------------------------------------------------------------------
# 기준 5 — 슬리피지 실측이 모델 예측의 ±30% 이내
# -----------------------------------------------------------------------------

_SLIPPAGE_EXIT_STATUS = {0: "PASS", 1: "FAIL", 2: "미측정"}

#: 판정에 필요한 최소 표본. ``measure_slippage`` 도 30건 미만이면 "통과로
#: 읽지 마라" 를 찍는다.
MIN_SLIPPAGE_SAMPLES = 30


def check_slippage(store: Store, as_of: datetime, market: str) -> Check:
    """``tools/measure_slippage.py`` 를 그대로 불러 쓴다 — 다시 구현하지 않는다.

    ## 이 기준은 M3 를 막지 않는다 (사용자 결정 2026-08-17)

    표본을 쌓으려면 실전을 돌려야 하고, 실전을 돌리려면 M3 를 닫아야 한다 —
    **순환이다.** 게다가 배선 검증용 1주 주문은 시장에 충격을 안 줘서, 그
    체결로 잰 오차는 전략의 체결 비용을 말해주지 않는다. 모델이 예측하는
    충격은 거의 0 인데 실제로는 스프레드 절반이 붙어, 비율로 보면 통과하든
    실패하든 숫자가 아무 뜻이 없다.

    그래서 M3 는 **"배선이 도는가"** 까지만 보고(기준 4번이 그것이다),
    **"모델이 현실과 맞는가" 는 실제 물량이 60거래일 쌓인 뒤 자본 증액
    게이트에서 판정한다** — ``tools/verify_capital_gate.py``.

    **검증을 뺀 것이 아니라 옮긴 것이다.** 옮긴 자리가 더 세다: 거기서는
    표본 30건 미만이면 통과를 아예 안 준다. 그리고 그 게이트는 이 결정 전까지
    ``config.capital`` 에 값만 있고 **읽는 코드가 0건**이었다 — 이 결정과
    함께 검증기를 만들었다. 안 그러면 "미룬다" 가 "영영 안 잰다" 가 된다.

    표본이 충분해지면 이 검사도 다시 실질 판정을 낸다. 이월은 **영구 면제가
    아니다.**
    """
    name = "슬리피지 실측이 모델 예측의 ±30% 이내"
    measurements, notes = measure_slippage(
        store, as_of=as_of, market=market, lookback=60, source=BROKER_SOURCE
    )
    text, code = render_slippage(measurements, notes, source=BROKER_SOURCE)
    lines = text.splitlines()
    if len(measurements) < MIN_SLIPPAGE_SAMPLES:
        return Check(
            name, "이월",
            [
                *lines,
                f"표본 {len(measurements)}건 < {MIN_SLIPPAGE_SAMPLES}건.",
                "→ 자본 증액 게이트로 이월한다 (tools/verify_capital_gate.py).",
                "  표본을 쌓으려면 실전을 돌려야 하고 실전을 돌리려면 M3 를",
                "  닫아야 한다 — 순환이라 M3 를 막지 않는다. 검증을 뺀 것이",
                "  아니라 표본이 쌓이는 자리로 옮긴 것이다.",
            ],
        )
    return Check(name, _SLIPPAGE_EXIT_STATUS[code], lines)


# -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(argv)

    load_env()
    store = build_store(args.data_root)
    as_of = LiveClock().now()

    print(f"M3 완료 기준 검증 — as_of {as_of.isoformat()}, 창고 {store.root}\n")

    checks = [
        check_shadow(store, as_of),
        check_oos_mdd(store, as_of, args.market),
        check_killswitch_tests(),
        check_live_trade(store, as_of, args.market),
        check_slippage(store, as_of, args.market),
    ]

    for check in checks:
        print(check.render())
        print()

    failed = [c for c in checks if c.status == "FAIL"]
    unmeasured = [c for c in checks if c.status == "미측정"]
    passed = [c for c in checks if c.status == "PASS"]
    # **이월은 M3 완주를 막지 않는다.** 다른 게이트에서 판정하기로 한 것이라
    # 미측정과 다르다 — 미측정은 "여기서 재야 하는데 못 쟀다" 이고, 이월은
    # "여기서 잴 자리가 아니다" 다. 둘을 같이 세면 영원히 안 닫힌다.
    deferred = [c for c in checks if c.status == "이월"]

    print(
        f"PASS {len(passed)} · FAIL {len(failed)} · 미측정 {len(unmeasured)} · "
        f"이월 {len(deferred)} / {len(checks)}"
    )
    for check in deferred:
        print(f"  이월: {check.name} → tools/verify_capital_gate.py")
    if failed:
        print("실패 — M4 로 넘어가지 않는다.")
        for c in failed:
            print(f"  - {c.name}")
        return 1
    if unmeasured:
        print("아직 잴 수 없는 항목이 있다 — 통과도 실패도 아니다.")
        for c in unmeasured:
            print(f"  - {c.name}")
        return 2
    print(f"M3 완료 기준 {len(checks)}개 전부 통과.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
