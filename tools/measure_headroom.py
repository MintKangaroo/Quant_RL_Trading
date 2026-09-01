"""여지 지도 — **어느 결정에 아직 배울 것이 남아 있나**.

3회차 파일럿이 "상위 24 안에서 배분을 바꿔 얻을 알파가 없다"를 확정한 뒤
(반영률 0.95 로 운전대를 잡고도 균등가중을 못 이겼다), 다음 질문은 "그럼 어디에
배울 것이 있나" 다. 집행 하나만 파는 것은 시야가 좁다.

**방법: 미래를 아는 오라클과의 격차를 잰다.** 결정 지점마다 완벽한 선택이 얼마나
더 벌었는지 재면, 그 격차가 곧 **학습의 상한**이다. 격차가 작으면 어떤 알고리즘도
소용없고(3회차의 배분이 그랬다), 크면 거기가 팔 곳이다.

오라클은 미래를 보므로 **달성 불가능한 상한**이다. 이 표는 "이만큼 벌 수 있다"가
아니라 **"여기는 아무리 잘해도 이 이상은 없다"**를 말한다. 상한이 0 에 가까운 곳에
RL 을 붙이는 것이 지난 세 회차에서 한 일이다.

재는 결정 지점:

    선정   상위 N 을 고르는 결정  — 우리 점수 vs 완벽 선택 vs 유니버스 평균
    배분   고른 것에 비중 주는 결정 — 균등 vs 완벽 비중 (3회차가 여기서 막혔다)
    노출   그날 얼마나 들어갈지    — 고정 vs 완벽한 0/1 타이밍

    python tools/measure_headroom.py --market KR --sessions 120 --horizon 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.selector.combine import combined_scores  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

#: 홀드아웃 금고. **이 날짜 이후는 열지 않는다** — 진단이라도 마찬가지다.
#: 여기서 본 것이 나중 판정의 기준을 물들인다(self-improvement.md).
HOLDOUT_START = pd.Timestamp("2026-07-01").date()

#: 선행수익 절대값 상한. 이보다 큰 움직임은 알파가 아니라 사건(기업행위·거래정지
#: 재개·데이터 오류)으로 보고 제외한다. 오라클은 상위만 고르므로 오류 하나가 표를
#: 통째로 지배한다 — 첫 실행이 그래서 5일 256% 라는 값을 냈다.
MAX_MOVE = 0.5

#: 신호를 훑을 창(일). 백필한 과거까지 닿아야 하지만, 무한정 넓히면 표가
#: 메모리에 안 들어간다 — 2년이면 충분하다.
SIGNAL_LOOKBACK = 730

#: 무작위 기준선을 몇 번 뽑아 평균낼지. 한 번이면 그 표본의 운이 기준선이 된다.
RANDOM_DRAWS = 20


def _weights(store: Store, *, as_of, market: str) -> dict[str, float]:
    frame = store.get("analyst_weights", as_of=as_of, lookback=120)
    if frame.empty:
        return {}
    frame = frame[frame["market"].astype(str) == market]
    frame = frame.sort_values("observed_at").drop_duplicates(
        subset=["entity_id"], keep="last"
    )
    return {
        str(r.entity_id): float(r.weight)
        for r in frame.itertuples(index=False)
        if float(r.weight) > 0
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KR")
    parser.add_argument("--sessions", type=int, default=120)
    parser.add_argument("--horizon", type=int, default=5, help="선행 수익률 창(거래일)")
    parser.add_argument("--top", type=int, default=24)
    args = parser.parse_args(argv)

    load_env()
    now = LiveClock().now()
    store = Store(root=Path("data"))

    weights = _weights(store, as_of=now, market=args.market)
    if not weights:
        print(f"{args.market} 가중치를 받은 Analyst 가 없다 — 잴 수 없다.", file=sys.stderr)
        return 2
    print(f"가중치: {weights}")

    prefix = f"{args.market}:"
    # **창은 신호가 있는 구간까지 닿아야 한다.** sessions+horizon+40 으로 잡으면
    # 오늘부터 그만큼만 읽는데, 백필로 채운 신호는 훨씬 과거에 있다 — 미장 신호가
    # 2025-08~2026-02 인데 시세 창이 2026-02-13 까지라 겹치는 세션이 0 이었다
    # (2026-09-02 실측: "신호와 가격이 겹치는 세션이 없다"). 신호를 먼저 훑어
    # 그 시작일까지 닿게 창을 늘린다.
    lookback = args.sessions + args.horizon + 40
    # 신호를 **한 번만** 읽고 그 범위로 시세 창을 정한다. 예전에는 여기서
    # lookback*3 로 훑고 아래에서 또 읽어 같은 표를 두 번 메모리에 올렸다 —
    # 미장 460만 행에서 RSS 8.5GB 가 됐고 램 가드가 내렸다(2026-09-02 07:44).
    signals = store.get("signals", as_of=now, lookback=SIGNAL_LOOKBACK)
    signals = signals[signals["entity_id"].astype(str).str.startswith(prefix)].copy()
    signals["day"] = pd.to_datetime(signals["valid_from"]).dt.date
    signals = signals[signals["day"] < HOLDOUT_START]
    # 판정에 쓸 세션만 남긴다 — 오래된 것까지 들고 있을 이유가 없다.
    keep_days = sorted(signals["day"].unique())[-args.sessions:]
    signals = signals[signals["day"].isin(set(keep_days))]
    if not signals.empty:
        span = (pd.Timestamp(now).date() - min(keep_days)).days
        lookback = max(lookback, span + args.horizon + 40)
    # **수정주가를 쓴다.** 액면분할·병합이 안 반영되면 5일에 +900% 같은 가짜
    # 수익이 생기고, 오라클은 정확히 그것만 골라낸다(첫 실행에서 완벽 선정이
    # 25,589bp = 5일 256% 로 나왔다 — 알파가 아니라 데이터 사고였다).
    prices = read_prices(
        store, as_of=now, lookback=lookback, columns=["close"], adjusted=True
    )
    prices = prices[prices["entity_id"].astype(str).str.startswith(prefix)].copy()
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    wide = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last")
    wide = wide.sort_index()
    # 홀드아웃은 열지 않는다.
    wide = wide[wide.index < HOLDOUT_START]
    if len(wide) < args.horizon + 10:
        print("가격 세션이 모자라다.", file=sys.stderr)
        return 2
    forward = wide.shift(-args.horizon) / wide - 1.0
    # 수정주가로도 남는 극단값을 자른다 — 거래정지 후 재개, 정정 안 된 기업행위,
    # 종가 0. **오라클은 상위만 고르므로 남은 오류 하나가 표 전체를 지배한다.**
    # 자르는 것 자체가 판단이라 값을 밝혀 둔다: 5거래일에 ±MAX_MOVE 를 넘는 것은
    # 알파가 아니라 사건으로 본다.
    forward = forward.mask(forward.abs() > MAX_MOVE)

    days = sorted(set(signals["day"]) & set(wide.index))[-args.sessions :]
    if not days:
        print("신호와 가격이 겹치는 세션이 없다.", file=sys.stderr)
        return 2

    rng = np.random.default_rng(0)
    rows: list[dict] = []
    pooled: list[tuple[pd.DataFrame, pd.Series, object]] = []
    for day in days:
        fwd = forward.loc[day].dropna()
        if len(fwd) < args.top * 2:
            continue
        todays = signals[signals["day"] == day]
        score = combined_scores(todays, weights)
        score = score[score.index.isin(fwd.index)]
        if len(score) < args.top * 2:
            continue

        ours = fwd[score.head(args.top).index]
        universe = fwd[score.index]
        oracle = fwd[score.index].nlargest(args.top)
        worst = fwd[score.index].nsmallest(args.top)
        # 무작위는 **여러 번 뽑아 평균낸다.** 한 번만 뽑으면 그 표본의 운이
        # 기준선이 되고, 실제로 첫 실행에서 무작위가 유니버스를 +37bp 이겼다.
        rand = float(np.mean([
            fwd[rng.choice(score.index, size=args.top, replace=False)].mean()
            for _ in range(RANDOM_DRAWS)
        ]))

        # **학습가능 상한을 위한 재료를 모은다.** 완벽 선정은 미래를 보므로
        # 도달 불가능하다. 그런데 "지금 가진 신호를 **사후 최적으로 가중**하면
        # 얼마나 되나" 는 다르다 — 그것이 선형 모델이 이 피처로 도달할 수 있는
        # 천장이고, 우리 고정 가중치가 거기서 얼마나 떨어져 있는지가 진짜 여지다.
        panel = todays.pivot_table(
            index="entity_id", columns="analyst", values="score", aggfunc="last"
        )
        panel = panel.reindex(score.index).dropna(how="all")
        if not panel.empty:
            aligned = fwd.reindex(panel.index)
            keep = aligned.notna()
            if keep.sum() >= args.top * 2:
                pooled.append((panel[keep], aligned[keep], day))

        # 배분: 같은 상위 N 안에서 균등 vs 완벽 비중(전액을 최선 하나에).
        rows.append({
            "day": day,
            "universe_n": len(score),
            "ours": ours.mean(),
            "universe": universe.mean(),
            "oracle": oracle.mean(),
            "worst": worst.mean(),
            "random": rand,
            "alloc_equal": ours.mean(),
            "alloc_oracle": ours.max(),
        })

    if not rows:
        print("잴 수 있는 세션이 없다.", file=sys.stderr)
        return 2
    df = pd.DataFrame(rows)
    n = len(df)

    def bps(x: float) -> float:
        return x * 10000.0

    print(f"\n=== 여지 지도 · {args.market} · {n}세션 · 선행 {args.horizon}거래일 ===")
    print(f"(홀드아웃 {HOLDOUT_START} 이후는 열지 않았다 · 상위 {args.top}종목)\n")

    print(f"{'':16}{'평균 선행수익':>12}{'vs 유니버스':>12}")
    print("─" * 42)
    for key, label in (
        ("oracle", "완벽 선정"),
        ("ours", "우리 점수"),
        ("random", "무작위"),
        ("universe", "유니버스 전체"),
        ("worst", "최악 선정"),
    ):
        edge = bps(df[key].mean() - df["universe"].mean())
        print(f"{label:16}{bps(df[key].mean()):>10.1f}bp{edge:>+10.1f}bp")

    got = df["ours"].mean() - df["universe"].mean()
    room = df["oracle"].mean() - df["ours"].mean()
    total = df["oracle"].mean() - df["universe"].mean()
    print(f"\n【선정】 우리가 잡은 것 {bps(got):+.1f}bp · **남은 여지 {bps(room):+.1f}bp**"
          f" (전체 가능폭의 {got / total * 100 if total else float('nan'):.1f}% 획득)")

    alloc_room = df["alloc_oracle"].mean() - df["alloc_equal"].mean()
    print(f"【배분】 균등 {bps(df['alloc_equal'].mean()):+.1f}bp → 완벽 "
          f"{bps(df['alloc_oracle'].mean()):+.1f}bp · **남은 여지 {bps(alloc_room):+.1f}bp**")
    print("        (상한은 '전액을 최선 한 종목에' 라 현실적으로 도달 불가 —"
          " 그런데도 3회차는 이 여지를 못 건드렸다)")

    pos = df["ours"].clip(lower=0.0)
    exposure_room = pos.mean() - df["ours"].mean()
    print(f"【노출】 항상 투자 {bps(df['ours'].mean()):+.1f}bp → 완벽한 0/1 타이밍 "
          f"{bps(pos.mean()):+.1f}bp · **남은 여지 {bps(exposure_room):+.1f}bp**")
    print(f"        (우리 상위 {args.top} 수익이 음수인 세션 "
          f"{(df['ours'] < 0).mean() * 100:.0f}% 를 전부 피했다고 가정)")

    # 학습가능 상한 — **사후 최적 선형가중**. 세션마다 점수를 표준화해 풀고
    # 전 구간을 한 번에 회귀한다(in-sample). 미래를 보고 맞춘 가중치라 이것도
    # 상한이지만, 완벽 선정과 달리 **지금 가진 신호만으로** 만든 상한이다.
    if pooled:
        analysts = sorted({c for panel, _, _ in pooled for c in panel.columns})
        xs, ys, keys = [], [], []
        for panel, fwd_r, day in pooled:
            x = panel.reindex(columns=analysts)
            x = x.sub(x.mean()).div(x.std().replace(0.0, np.nan))  # 세션 내 표준화
            xs.append(x.fillna(0.0)); ys.append(fwd_r); keys.append((day, x.index))
        big_x = pd.concat(xs).to_numpy(dtype=float)
        big_y = pd.concat(ys).to_numpy(dtype=float)
        beta, *_ = np.linalg.lstsq(big_x, big_y, rcond=None)
        fitted = []
        for (day, idx), x in zip(keys, xs, strict=False):
            pred = pd.Series(x.to_numpy(dtype=float) @ beta, index=idx)
            row = df[df["day"] == day]
            if row.empty:
                continue
            target = next(f for p, f, d in pooled if d == day)
            fitted.append(target[pred.nlargest(args.top).index].mean())
        if fitted:
            best_linear = float(np.mean(fitted))
            gain = best_linear - df["ours"].mean()
            print(f"\n【학습가능 상한】 지금 신호를 사후 최적 가중하면 "
                  f"{bps(best_linear):+.1f}bp (우리 {bps(df['ours'].mean()):+.1f}bp)"
                  f" · **여지 {bps(gain):+.1f}bp**")
            print(f"        Analyst {len(analysts)}개 선형결합, in-sample 최적."
                  " 완벽 선정과 달리 **지금 가진 재료만으로** 만든 상한이다.")

    print("\n오라클은 미래를 본다 — **달성 가능한 값이 아니라 상한**이다.")
    print("여지가 0 에 가까운 곳에 RL 을 붙이면 3회차가 반복된다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
