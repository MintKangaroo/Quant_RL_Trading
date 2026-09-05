"""risk Analyst 가 저변동성 팩터를 다시 학습한 것에 불과한가.

    .venv/bin/python tools/diagnose_risk_analyst.py --sessions 30

`risk` 는 IC 1위(+0.073)이자 KR·US 양쪽에서 살아남은 유일한 Analyst 다. 그
성적이 **세 축의 합성**에서 온 것인지, 아니면 `low_volatility` 하나가 전부이고
나머지는 장식인지 가른다.

세 축(`analysts/risk.py`)::

    low_volatility 0.45 · liquidity 0.35 · low_beta 0.20

세션을 고르게 뽑아 그 시점 as_of 로 피처를 다시 만들고, 합성 점수와 각 축의
순위상관 · 각 축 단독 IC 를 잰다. 전 세션을 돌지 않는 이유는 답이 표본
수에 민감하지 않기 때문이다 — 피처 간 상관은 세션마다 거의 같다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts import ic  # noqa: E402
from quant_rl_trading.analysts.risk import WEIGHTS, RiskAnalyst  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market  # noqa: E402
from quant_rl_trading.collectors.publication import publication_policy  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock, ReplayClock  # noqa: E402
from tools.backfill import build_store, load_env  # noqa: E402
from tools.diagnose_ic import CACHE_DIR, newey_west_t  # noqa: E402

MARKET = "KR"
HORIZON = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=30, help="고르게 뽑을 세션 수")
    args = parser.parse_args(argv)

    load_env()
    store = build_store(None)
    market = Market(MARKET)
    policy = publication_policy(store, market, clock=LiveClock())

    calendar = list(pd.read_pickle(CACHE_DIR / f"calendar-{MARKET}.pkl")["session"])
    picks = [calendar[index] for index in np.linspace(0, len(calendar) - 1, args.sessions).astype(int)]

    analyst = RiskAnalyst(store, LiveClock(), market=market)
    frames: list[pd.DataFrame] = []
    for index, session in enumerate(picks, start=1):
        as_of = policy.for_session(session)
        analyst.clock = ReplayClock(as_of)
        features = analyst.features(as_of)
        if features.empty:
            continue
        features = features.copy()
        features["score"] = analyst.raw_score(features)
        frames.append(features.assign(session=session).reset_index(names="entity_id"))
        if index % 10 == 0:
            print(f"  … {index}/{len(picks)}", flush=True)

    panel = pd.concat(frames, ignore_index=True)
    columns = [name for name in WEIGHTS if name in panel.columns]

    print(f"\n표본 {panel['session'].nunique()}세션 · {len(panel):,}행")

    print("\n[합성 점수와 각 축의 순위상관 — 일별 횡단면의 평균]")
    rows = []
    for name in columns:
        values = [
            day[name].corr(day["score"], method="spearman")
            for _, day in panel.groupby("session")
            if len(day) > 30
        ]
        rows.append({"축": name, "가중치": WEIGHTS[name], "score 와의 상관": np.nanmean(values)})
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    print("\n[축끼리의 상관]")
    mats = [
        day[columns].corr(method="spearman").to_numpy()
        for _, day in panel.groupby("session")
        if len(day) > 30
    ]
    print(
        pd.DataFrame(np.nanmean(mats, axis=0), index=columns, columns=columns)
        .round(3)
        .to_string()
    )

    print("\n[각 축 단독 IC — 같은 타깃(전방 5일 초과수익의 횡단면 z)]")
    targets = pd.read_pickle(CACHE_DIR / f"targets-{MARKET}-h{HORIZON}.pkl")
    merged = panel.merge(targets, on=["entity_id", "session"], how="inner")
    for name in [*columns, "score"]:
        sub = merged.loc[:, ["session", name, "target"]].rename(columns={name: "score"})
        daily = ic.daily_ic(sub.dropna())
        print(
            f"  {name:16s} IC {daily.mean():+.4f} · t {newey_west_t(daily, lag=HORIZON - 1):+.2f}"
            f" · {len(daily)}일"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
