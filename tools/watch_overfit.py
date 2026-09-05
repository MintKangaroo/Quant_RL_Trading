"""학습 중 과적합 전조 감시 — `rl_updates` 만 본다 (창고 = 유일한 출입구).

    .venv/bin/python tools/watch_overfit.py --run m4-round2-s0-c1-r6

진짜 OOS 는 학습 중에 못 잰다(학습 구간 뒤는 홀드아웃 금고). 대신 정책이 **외우기
시작할 때 먼저 움직이는 것들**을 본다. 각 항목은 최근 50업데이트 대 그 앞 50업데이트다.

    entropy           급락 = 조기 수렴 (rl-training.md §10). 100업데이트에 5% 넘게 떨어지면 경고
    concentration_sum Dirichlet 집중도 합. 2배 넘게 뛰면 정책이 한 답에 몰린다
    policy_churn      일간 비중 교체율. 보상이 오르는데 교체율도 오르면 회전 비용이 따라온다
    cash_weight       단조 증가 = 도망 (1회차 증상)
    reward vs EV      보상은 오르는데 EV 가 0 근처로 무너지면 가치함수가 못 따라오는 것 —
                      외운 궤적에서만 보상이 나오는 전조
    reward 분산       최근 50 의 표준편차가 앞 50 의 절반 이하로 줄면 같은 궤적을 반복하는 것
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.store import Store  # noqa: E402

WINDOW = 50


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)  # invariant-allow: wallclock
    frame = Store(root=Path(args.root)).get("rl_updates", as_of=now, lookback=30)
    # 되살린 판(run-cN)은 같은 학습의 연장이다 — 접미사를 떼고 계열 전체를 본다.
    # 같은 update 번호가 두 판에 있으면(죽기 전 몇 개) 나중 기록을 쓴다.
    base = re.sub(r"-c\d+$", "", args.run)
    frame = frame[frame["entity_id"].str.match(rf"^{re.escape(base)}(-c\d+)?$")]
    frame = frame.sort_values(["update", "observed_at"]).drop_duplicates("update", keep="last")
    if len(frame) < 2 * WINDOW:
        print(f"{args.run}: {len(frame)}업데이트 — 비교하려면 {2 * WINDOW} 필요")
        return 0
    recent, before = frame.tail(WINDOW), frame.iloc[-2 * WINDOW:-WINDOW]
    m = lambda f, c: float(f[c].mean())  # noqa: E731
    flags: list[str] = []
    ent_r, ent_b = m(recent, "entropy"), m(before, "entropy")
    ent_drop = (ent_b - ent_r) / abs(ent_b) if ent_b else 0.0
    if ent_drop > 0.05:
        flags.append(f"엔트로피 {ent_drop:.1%} 급락 ({ent_b:.1f}→{ent_r:.1f})")
    conc_r, conc_b = m(recent, "concentration_sum"), m(before, "concentration_sum")
    if conc_b > 0 and conc_r / conc_b > 2.0:
        flags.append(f"집중도 합 {conc_b:.1f}→{conc_r:.1f} (×{conc_r / conc_b:.1f})")
    churn_r, churn_b = m(recent, "policy_churn"), m(before, "policy_churn")
    rew_r, rew_b = m(recent, "episode_reward"), m(before, "episode_reward")
    if rew_r > rew_b and churn_r > churn_b * 1.3:
        flags.append(f"보상↑ 인데 교체율 {churn_b:.3f}→{churn_r:.3f} (×{churn_r / churn_b:.2f})")
    cash = frame["cash_weight"].rolling(WINDOW).mean().dropna()
    if len(cash) >= WINDOW and cash.iloc[-1] > cash.iloc[-WINDOW] + 0.03 and cash.diff().tail(WINDOW).gt(0).mean() > 0.8:
        flags.append(f"현금 단조증가 {cash.iloc[-WINDOW]:.3f}→{cash.iloc[-1]:.3f}")
    ev_r, ev_b = m(recent, "explained_variance"), m(before, "explained_variance")
    if rew_r > rew_b and ev_r < 0.1 and ev_b >= 0.3:
        flags.append(f"보상↑ 인데 EV {ev_b:+.2f}→{ev_r:+.2f} 붕괴")
    sd_r, sd_b = float(recent["episode_reward"].std()), float(before["episode_reward"].std())
    if sd_b > 0 and sd_r < 0.5 * sd_b:
        flags.append(f"보상 분산 {sd_b:.5f}→{sd_r:.5f} (반 이하) — 같은 궤적 반복 의심")

    last = int(frame["update"].iloc[-1])
    print(f"{args.run} · {last}업데이트 · 최근{WINDOW} vs 앞{WINDOW}")
    print(f"  보상 {rew_b:+.5f}→{rew_r:+.5f} · EV {ev_b:+.2f}→{ev_r:+.2f} · ent {ent_b:.1f}→{ent_r:.1f} · "
          f"집중도 {conc_b:.1f}→{conc_r:.1f} · 교체율 {churn_b:.3f}→{churn_r:.3f} · 현금 {m(before, 'cash_weight'):.3f}→{m(recent, 'cash_weight'):.3f} · 보상sd {sd_b:.5f}→{sd_r:.5f}")
    if flags:
        print("  ⚠️ 과적합 전조:")
        for flag in flags:
            print(f"    - {flag}")
        return 1
    print("  전조 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
