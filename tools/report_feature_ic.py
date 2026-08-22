"""피처 단위 IC 전수 측정 — 34개를 한 번에 재고 다중검정을 보정한다.

    .venv/bin/python tools/report_feature_ic.py

**사전 등록이 먼저다.** `docs/feature-registry.md` 가 재기 전에 커밋됐고,
아래 `EXPECTED` 는 그 문서를 코드로 옮긴 것이다. 두 곳이 갈라지면 문서가
옳다 — 문서가 먼저 있었기 때문이다.

## 왜 다중검정을 보정하나

34개를 유의수준 5%로 재면 **참인 신호가 하나도 없어도 1.7개가 통과한다.**
그중 가장 좋은 것을 골라 "찾았다" 고 말하면 그건 알파가 아니라 잡음에 이름을
붙인 것이고, 다음 구간에서 사라진다. Benjamini-Hochberg 로 FDR 10% 선을
같이 내서, 통과선을 넘긴 것만 채택 후보로 부른다.

## 왜 부호를 미리 박아 두나

측정 결과가 예상과 반대로 유의하게 나오면 그것은 **가설이 틀렸다는 증거**이지
"부호를 뒤집어 쓰면 되는 신호" 가 아니다. 뒤집어 쓰는 것은 표본에 사후로
맞추는 일이고 `analysts/ic.py` 가 금지한다. 그래서 부호를 코드가 들고 있고,
표에 "예상과 반대" 를 따로 찍는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.diagnose_ic import CACHE_DIR, newey_west_t  # noqa: E402
from tools.report_ic_diagnosis import MARKET, section, targets  # noqa: E402
from tools.report_signal_combination import daily_ic_of  # noqa: E402

HORIZON = 5

#: BH 절차의 FDR 상한.
FDR = 0.10

#: 보통의 유의선. 사전 등록 문서에 t ≥ 2 로 적혀 있다.
T_GATE = 2.0

#: 사전 등록한 예상 부호. `docs/feature-registry.md` 를 옮긴 것이다.
#: ``0`` 은 **방향을 등록하지 않았다**는 뜻이다(regime — 상태마다 부호가 뒤집혀
#: 무조건부 방향이 설계상 없다). 0 인 피처는 채택 후보로 세우지 않는다.
EXPECTED: dict[str, dict[str, int]] = {
    "chart": {
        "momentum_20": +1, "momentum_60": +1, "reversal_5": -1,
        "ma_gap": +1, "range_position": +1,
    },
    "volume": {"volume_surge": +1},
    "risk": {"low_volatility": +1, "liquidity": +1, "low_beta": +1},
    "flow_kr": {
        "foreign_5": +1, "foreign_20": +1, "institution_20": +1,
        "retail_20": -1, "foreign_persistence": +1,
    },
    "fundamental": {
        "earnings_yield": +1, "book_to_market": +1, "sales_to_price": +1,
        "roe": +1, "operating_margin": +1, "low_leverage": +1,
        "current_ratio": +1, "revenue_growth": +1, "profit_growth": +1,
    },
    "event": {
        "buyback": +1, "distress": +1, "dilution": +1,
        "dividend": +1, "contract": +1, "maturity": +1,
    },
    "regime": {
        "beta": 0, "idio_volatility": 0, "index_correlation": 0,
        "downside_beta": 0, "rate_steadiness": 0,
    },
}


def two_sided_p(t: float, n: int) -> float:
    """t 값의 양측 p. 정규근사다 — n 이 285 이상이라 t 분포와 사실상 같다."""
    if not np.isfinite(t):
        return float("nan")
    from math import erfc, sqrt

    return float(erfc(abs(t) / sqrt(2.0)))


def benjamini_hochberg(p_values: pd.Series, *, fdr: float) -> pd.Series:
    """BH 절차. 통과면 True.

    p 를 오름차순으로 세우고 ``p(i) <= i/m * fdr`` 를 만족하는 **가장 큰 i**
    까지를 통과로 본다. 본페로니처럼 전부 죽이지 않으면서 거짓 발견의 비율을
    묶는다 — 우리가 묻는 것이 "이 중에 진짜가 있나" 이지 "이 하나가 확실한가"
    가 아니기 때문이다.
    """
    clean = p_values.dropna().sort_values()
    m = len(clean)
    if m == 0:
        return pd.Series(False, index=p_values.index)
    thresholds = np.arange(1, m + 1) / m * fdr
    passed = clean.to_numpy() <= thresholds
    cutoff = np.max(np.nonzero(passed)[0]) if passed.any() else -1
    winners = set(clean.index[: cutoff + 1]) if cutoff >= 0 else set()
    return pd.Series([index in winners for index in p_values.index], index=p_values.index)


def measure(name: str, target: pd.DataFrame) -> pd.DataFrame:
    path = CACHE_DIR / f"features-{name}-{MARKET}.pkl"
    if not path.exists():
        print(f"  {name}: 피처 캐시가 없다 — 건너뛴다")
        return pd.DataFrame()
    features = pd.read_pickle(path)
    columns = [c for c in EXPECTED[name] if c in features.columns]
    missing = [c for c in EXPECTED[name] if c not in features.columns]
    if missing:
        # **조용히 넘어가지 않는다.** 사전 등록한 피처가 캐시에 없다는 것은
        # 그 피처가 그 구간에 한 번도 안 만들어졌다는 뜻이고, 그 자체가 사실이다.
        print(f"  {name}: 캐시에 없는 등록 피처 {missing}")

    panel = features.merge(target, on=["entity_id", "session"], how="inner")
    rows = []
    for column in columns:
        daily = daily_ic_of(panel, column)
        if daily.empty:
            rows.append({"analyst": name, "feature": column, "일수": 0})
            continue
        t = newey_west_t(daily, lag=HORIZON - 1)
        ic_value = float(daily.mean())
        expected = EXPECTED[name][column]
        rows.append({
            "analyst": name,
            "feature": column,
            "IC": round(ic_value, 4),
            "t(NW)": round(t, 2),
            "예상": {1: "+", -1: "−", 0: "미등록"}[expected],
            "부호일치": (
                "—" if expected == 0
                else ("○" if np.sign(ic_value) == expected else "✗")
            ),
            "일수": int(daily.size),
            "_p": two_sided_p(t, int(daily.size)),
            "_expected": expected,
            "_ic": ic_value,
            "_t": t,
        })
    return pd.DataFrame(rows)


def main() -> int:
    target = targets(HORIZON)
    frames = [measure(name, target) for name in EXPECTED]
    table = pd.concat([f for f in frames if not f.empty], ignore_index=True)

    # **방향을 등록한 것만 다중검정에 넣는다.** regime 다섯은 대조군이라
    # 후보 집합에 들어가지 않는다 — 넣으면 분모만 키워 나머지를 죽인다.
    registered = table[table["_expected"] != 0].copy()
    registered["_bh"] = benjamini_hochberg(registered["_p"], fdr=FDR)

    section(f"1. 방향을 등록한 피처 {len(registered)}개 — IC · t · 다중검정")
    view = registered.sort_values("_t", key=abs, ascending=False)
    print(
        view[["analyst", "feature", "IC", "t(NW)", "예상", "부호일치", "일수"]]
        .assign(**{
            f"BH(FDR {FDR:.0%})": np.where(view["_bh"], "통과", ""),
            "t≥2": np.where(view["_t"].abs() >= T_GATE, "○", ""),
        })
        .to_string(index=False)
    )

    section("2. 채택 후보 — 부호가 맞고 · t ≥ 2 이고 · BH 를 통과한 것")
    winners = registered[
        (registered["부호일치"] == "○")
        & (registered["_t"].abs() >= T_GATE)
        & registered["_bh"]
    ]
    if winners.empty:
        print("  없다. **34개를 재서 하나도 안 나온 것도 결과다.**")
    else:
        print(winners[["analyst", "feature", "IC", "t(NW)"]].to_string(index=False))

    section("3. 예상과 반대로 유의한 것 — 가설이 틀렸다는 증거")
    print("  **부호를 뒤집어 쓰지 않는다.** 표본에 사후로 맞추는 일이다 (ic.py).")
    wrong = registered[
        (registered["부호일치"] == "✗") & (registered["_t"].abs() >= T_GATE)
    ]
    print(
        "  없다"
        if wrong.empty
        else wrong[["analyst", "feature", "IC", "t(NW)", "예상"]].to_string(index=False)
    )

    section("4. regime 다섯 — 대조군 (방향 미등록, 채택 후보 아님)")
    control = table[table["_expected"] == 0]
    if control.empty:
        print("  캐시가 없다")
    else:
        print(control[["feature", "IC", "t(NW)", "일수"]].to_string(index=False))

    section("5. Analyst 별 요약 — 안 되는 피처가 가중치를 얼마나 쥐고 있나")
    for name, group in registered.groupby("analyst"):
        good = group[(group["부호일치"] == "○") & (group["_t"].abs() >= T_GATE)]
        print(
            f"  {name:12s} 피처 {len(group)}개 · 부호 맞음 "
            f"{int((group['부호일치'] == '○').sum())}개 · t≥2 {len(good)}개"
            + (f" ({', '.join(good['feature'])})" if not good.empty else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
