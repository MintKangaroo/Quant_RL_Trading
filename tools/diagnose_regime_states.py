"""regime 의 레짐별 성적이 진짜인지 가른다 — 전반/후반으로 쪼개서.

    .venv/bin/python tools/diagnose_regime_states.py

`docs/ic-diagnosis.md` §1 이 volatile 128세션에서 초과 -0.80%(t -2.46)를
보고했다. **한 구간에서 나온 유의성은 그 자체로 근거가 못 된다** — 표본을
반으로 갈라 양쪽에서 같은 부호가 나오는지 본다. 한쪽에만 있으면 그것은
구조가 아니라 그 시기의 사건이다.

가중치를 고치기 위한 도구가 아니다. **이 표본의 결과에 맞춰 `REGIME_WEIGHTS`
를 뒤집는 것은 표본 적합**이고 이 저장소가 여러 곳에서 금지한다
(`analysts/event.py`·`regime.py` 독스트링). 여기서 하는 일은 "고칠 값을
찾는 것" 이 아니라 **"고칠 만한 사실이 있는지 확인하는 것"** 이다.
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
from tools.report_ic_diagnosis import (  # noqa: E402
    MARKET,
    forward_return_panel,
    scores,
    section,
)

HORIZON = 5
QUANTILE = 0.2


def conditional(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """레짐별 (상위20% − 유니버스) 초과수익. 단위는 %."""
    rows = []
    for state, group in frame.groupby("state"):
        per_session = []
        for _, day in group.groupby("session"):
            if len(day) < 20:
                continue
            top = day[day["score"] >= day["score"].quantile(1 - QUANTILE)]
            per_session.append(
                {"top": top["forward_return"].mean(), "uni": day["forward_return"].mean()}
            )
        if not per_session:
            continue
        panel = pd.DataFrame(per_session)
        excess = panel["top"] - panel["uni"]
        rows.append(
            {
                "구간": label,
                "state": state,
                "세션": len(panel),
                "초과%": round(float(excess.mean()) * 100, 4),
                "t": round(newey_west_t(excess, lag=HORIZON - 1), 2),
                "유니버스%": round(float(panel["uni"].mean()) * 100, 4),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    state = pd.read_pickle(CACHE_DIR / f"regime-state-{MARKET}.pkl")
    frame = scores("regime").merge(
        forward_return_panel(HORIZON), on=["entity_id", "session"]
    )
    frame = frame.merge(state, on="session", how="left")

    sessions = sorted(frame["session"].unique())
    mid = sessions[len(sessions) // 2]
    first = frame[frame["session"] < mid]
    second = frame[frame["session"] >= mid]

    section("1. 전체 · 전반 · 후반 — 부호가 양쪽에서 같은가")
    whole = conditional(frame, "전체")
    early = conditional(first, f"전반(~{mid})")
    late = conditional(second, f"후반({mid}~)")
    merged = pd.concat([whole, early, late])
    print(merged.pivot(index="state", columns="구간", values=["초과%", "t", "세션"]).to_string())

    section("2. 레짐 자체의 성질 — 그 국면에 시장이 무엇을 했나")
    per_state = []
    for name, group in frame.groupby("state"):
        daily = group.groupby("session")["forward_return"].mean()
        per_state.append(
            {
                "state": name,
                "세션": len(daily),
                "유니버스 5일수익%": round(float(daily.mean()) * 100, 4),
                "그 수익의 t": round(newey_west_t(daily, lag=HORIZON - 1), 2),
                "흩어짐%": round(float(daily.std()) * 100, 3),
            }
        )
    print(pd.DataFrame(per_state).set_index("state").to_string())

    section("3. 에피소드 단위로 다시 — 세션은 독립 관측이 아니다")
    print("  레짐은 연속으로 뭉쳐 있다. 128세션이 128개의 관측이 아니라")
    print("  연속 구간(에피소드) 몇 개다. 에피소드마다 평균을 내고 그 사이에서 t 를 낸다.\n")
    ordered = state.sort_values("session").reset_index(drop=True)
    episode = (ordered["state"] != ordered["state"].shift()).cumsum()
    ordered["episode"] = episode
    tagged = frame.merge(ordered[["session", "episode"]], on="session", how="left")

    per_session_excess = []
    for (session, st, ep), day in tagged.groupby(["session", "state", "episode"]):
        if len(day) < 20:
            continue
        top = day[day["score"] >= day["score"].quantile(1 - QUANTILE)]
        per_session_excess.append(
            {
                "session": session, "state": st, "episode": ep,
                "excess": float(top["forward_return"].mean() - day["forward_return"].mean()),
            }
        )
    daily = pd.DataFrame(per_session_excess)
    episodes = daily.groupby(["state", "episode"])["excess"].mean().reset_index()

    rows = []
    for st, group in episodes.groupby("state"):
        values = group["excess"].to_numpy() * 100
        n = len(values)
        t = float(values.mean() / (values.std(ddof=1) / np.sqrt(n))) if n > 1 and values.std(ddof=1) > 0 else float("nan")
        rows.append(
            {
                "state": st,
                "에피소드 수": n,
                "에피소드 평균 초과%": round(float(values.mean()), 4),
                "에피소드간 t": round(t, 2),
                "양수 에피소드": f"{int((values > 0).sum())}/{n}",
            }
        )
    print(pd.DataFrame(rows).set_index("state").to_string())

    section("4. 레짐 전환 빈도 — 상태가 얼마나 자주 바뀌나")
    seq = state.sort_values("session")["state"].to_numpy()
    switches = int((seq[1:] != seq[:-1]).sum())
    runs = []
    current, length = seq[0], 1
    for value in seq[1:]:
        if value == current:
            length += 1
        else:
            runs.append((current, length))
            current, length = value, 1
    runs.append((current, length))
    print(f"  전환 {switches}회 / {len(seq)}세션 · 평균 지속 {len(seq) / max(len(runs), 1):.1f}세션")
    run_frame = pd.DataFrame(runs, columns=["state", "지속"])
    print(run_frame.groupby("state")["지속"].agg(["count", "mean", "max"]).round(1).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
