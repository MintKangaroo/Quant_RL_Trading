"""시행 P1-a′ — 스타일 중립 선택: 시총 구간 안에서 고른다(docs/protocols/p1a-prime-2026-10.md).

    nice -n 10 taskset -c 0-3 .venv/bin/python tools/trial_p1a_prime.py caps     # 시총 순위 캐시(창고 읽기, 수익 없음)
    nice -n 10 taskset -c 0-3 .venv/bin/python tools/trial_p1a_prime.py check    # 등록 전 점검 ①②③ — 수익을 출력하지 않는다
    nice -n 10 taskset -c 0-3 .venv/bin/python tools/trial_p1a_prime.py run [--save]   # 판정(등록 해시 고정 뒤에만)

점수는 C0 이음 예측(`diag_hedged_sleeve.kr_preds`, 시드 0~4) — 새 학습 없음. 롱 다리는 진단의 `long_leg`(EMA5 → 상위 N ·
완충 3N · R10 · 동일가중 · 드리프트 · 편도 비용) 그대로이고, 다른 것은 **그날 구간 밖 종목을 점수에서 지운다**는 것 하나다.
대조 K-EW·K-CAP 은 같은 구간·같은 재조정일·같은 비용의 신호 없는 포트다(점검 ⑨).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import ic as ic_module  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools.diag_bear_2022 import in_window, realized_end  # noqa: E402
from tools.diag_hedged_sleeve import (  # noqa: E402
    KR_LABELS,
    PRICE_START,
    WINDOWS,
    ic_series,
    kr_preds,
    labels,
)
from tools.trial_overlay import ANN, ONE_WAY_COST  # noqa: E402

PROTOCOL = Path("docs/protocols/p1a-prime-2026-10.md")
#: 등록 문서 sha256 앞 16자 — **사용자 승인 뒤** 적는다. None 이면 `run` 은 거부한다(점검만 허용).
PROTOCOL_HASH: str | None = None
OUT = Path("data/_diag/p1a-prime")
CAPS = OUT / "caps-rank.parquet"  # invariant-allow: data-access — 시행 작업 캐시(창고 market_stats 를 store.get 으로 읽어 만든 것)
ZONES: dict[str, tuple[int, int]] = {"A1": (1, 200), "A2": (201, 700)}
N, MULT, EVERY = 24, 3, 10
CAP_LIMIT = 0.30
SEEDS = (0, 1, 2, 3, 4)
#: 등록 전 점검 문턱 — 액티브 셰어(대 K-EW) 5% 미만 · 구간 안 IC 0.02 미만이면 그 변형은 측정하지 않는다.
MIN_ACTIVE_SHARE, MIN_IC = 0.05, 0.02
#: 채택 기준(변형별, 넷 다): ① 연 ≥ K-EW + 1%p · NW t ≥ 2.0 ② 시드 4/5 ③ 국면 넷 ≥ K-EW − 1%p ④ MDD 2%p · 회전 ≤ 현행 × 1.2
GATE_ANN, GATE_T, GATE_SHARE, GATE_REGIME, GATE_MDD, GATE_TURN = 0.01, 2.0, 4, -0.01, 0.02, 1.2
REGIMES = ("하락", "반등", "박스", "급등")


# --------------------------------------------------------------------------- 구간


def build_caps(store: Store, sessions: list[date]) -> pd.DataFrame:
    """세션별 시총(`market_stats` metric=market_cap) — 달마다 끊어 읽는다(그 달 끝 as_of). 반환 session·entity_id·cap."""
    parts = []
    months = sorted({(d.year, d.month) for d in sessions})
    for y, m in months:
        lo = date(y, m, 1)
        hi = date(y + (m == 12), m % 12 + 1, 1)
        as_of = datetime.combine(hi, time(0), tzinfo=UTC)
        f = store.get("market_stats", as_of=as_of, lookback=(hi - lo).days + 1, until=as_of, market="KR",
                      columns=["entity_id", "valid_from", "metric", "value"])
        f = f[f["metric"] == "market_cap"]
        f["session"] = pd.to_datetime(f["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
        f = f[(f["session"] >= lo) & (f["session"] < hi)]
        parts.append(f.groupby(["session", "entity_id"], as_index=False)["value"].last().rename(columns={"value": "cap"}))
        print(f"  {y}-{m:02d} 시총 {len(parts[-1]):,}행", flush=True)
    caps = pd.concat(parts, ignore_index=True)
    return caps[caps["cap"] > 0]


def zone_members(caps: pd.DataFrame, trad: dict[date, set[str]]) -> dict[str, dict[date, set[str]]]:
    """구간별 세션별 종목 — 그날 **거래가능 명단 안** 시총 내림차순 순위(1부터)가 구간에 드는 종목."""
    out: dict[str, dict[date, set[str]]] = {z: {} for z in ZONES}
    for day, part in caps.groupby("session"):
        ok = trad.get(day)
        if ok:
            part = part[part["entity_id"].isin(ok)]
        ranked = part.sort_values("cap", ascending=False)["entity_id"].tolist()
        for z, (lo, hi) in ZONES.items():
            out[z][day] = set(ranked[lo - 1:hi])
    return out


def members_frame(members: dict[date, set[str]]) -> pd.DataFrame:
    """구간 명단을 (session, entity_id) 표로 — 예측과 merge 해 거른다(행마다 파이썬 루프를 돌지 않는다)."""
    return pd.DataFrame([(d, e) for d, names in members.items() for e in names], columns=["session", "entity_id"])


def mask_zone(wide: pd.DataFrame, members: dict[date, set[str]]) -> pd.DataFrame:
    """그날 구간 밖 종목의 점수를 지운다(NaN). 구간 정보가 없는 세션은 통째로 비운다 — 조용히 전 유니버스로 새지 않는다."""
    out = wide.copy()
    for day in out.index:
        keep = members.get(day, set())
        out.loc[day, ~out.columns.isin(list(keep))] = np.nan
    return out


# --------------------------------------------------------------------------- 신호 없는 대조


def capped(w: pd.Series, limit: float) -> pd.Series:
    """비중 상한 — 넘친 몫을 나머지에 비례로 다시 나눈다(반복). 종목 수 × 상한 < 1 이면 동일가중."""
    w = w / w.sum()
    if len(w) * limit < 1.0:
        return pd.Series(1.0 / len(w), index=w.index)
    for _ in range(100):
        over = w > limit + 1e-12
        if not over.any():
            break
        extra = float((w[over] - limit).sum())
        w[over] = limit
        rest = ~over & (w < limit)
        w[rest] += extra * w[rest] / w[rest].sum()
    return w


def static_book(days: list[date], ret: pd.DataFrame, target, *, every: int) -> tuple[pd.Series, pd.Series]:
    """신호 없는 포트 — 재조정일(``long_leg`` 와 같은 세기: 수익 축에 있는 세션의 0·every·…번째)에 ``target(day)`` 비중,
    그 사이 드리프트, 편도 비용. 반환 (일수익, 회전)."""
    prev = None
    out, turns = {}, {}
    step = -1
    for day in days:
        if day not in ret.index:
            continue
        step += 1
        if prev is not None and step % every != 0:
            w = prev
            dr = ret.loc[day].reindex(w.index).fillna(0.0)
            out[day], turns[day] = float((w * dr).sum()), 0.0
            drift = w * (1 + dr)
            prev = drift / drift.sum() if drift.sum() > 0 else w
            continue
        w = target(day)
        if w is None or w.empty:
            continue
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        t = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else 1.0
        out[day], turns[day] = float((w * dr).sum() - ONE_WAY_COST * t), t
        drift = w * (1 + dr)
        prev = drift / drift.sum() if drift.sum() > 0 else w
    return pd.Series(out).sort_index(), pd.Series(turns).sort_index()


def ew_target(members: dict[date, set[str]]):
    def f(day: date) -> pd.Series | None:
        names = sorted(members.get(day, ()))
        return pd.Series(1.0 / len(names), index=names) if names else None
    return f


def cap_target(members: dict[date, set[str]], caps: pd.DataFrame):
    by_day = {d: p.set_index("entity_id")["cap"] for d, p in caps.groupby("session")}

    def f(day: date) -> pd.Series | None:
        names = sorted(members.get(day, ()))
        if not names or day not in by_day:
            return None
        return capped(by_day[day].reindex(names).dropna().astype(float), CAP_LIMIT)
    return f


# --------------------------------------------------------------------------- 판정


def leg_stats(daily: pd.Series, turn: pd.Series, wdays: dict[str, list[date]]) -> dict[str, float]:
    full = wdays["전체"]
    r = daily.reindex(full).fillna(0.0)
    nav = (1.0 + r).cumprod()
    out = {"ann": float(r.mean() * ANN), "mdd": float((nav / nav.cummax() - 1.0).min()),
           "turn": float(turn.reindex(full).fillna(0.0).sum() * ANN / len(full))}
    for g in REGIMES:
        out[g] = float(daily.reindex(wdays[g]).fillna(0.0).mean() * ANN) if wdays[g] else float("nan")
    return out


def judge(res: dict[str, dict[str, list[dict[str, float]]]], ctrl: dict[str, dict[str, dict[str, float]]],
          daily: dict[str, pd.Series], kew_daily: dict[str, pd.Series], cur_turn: float) -> tuple[list[str], str]:
    """변형별 기준 넷. res[변형] = 시드 순서 지표, ctrl[변형] = {K-EW, K-CAP} 지표(시드 무관), daily[변형] = 시드 평균 일수익."""
    lines, passed = [], []
    for z in res:
        rows, kew = res[z], ctrl[z]["K-EW"]
        ann = float(np.mean([r["ann"] for r in rows]))
        d = ann - kew["ann"]
        t = float(ic_module.newey_west_t(daily[z] - kew_daily[z], lag=4))
        wins = sum(r["ann"] > kew["ann"] for r in rows)
        reg = {g: float(np.mean([r[g] for r in rows])) - kew[g] for g in REGIMES}
        mdd = float(np.mean([r["mdd"] for r in rows]))
        turn = float(np.mean([r["turn"] for r in rows]))
        c = (d >= GATE_ANN and t >= GATE_T, wins >= GATE_SHARE,
             all(np.isfinite(v) and v >= GATE_REGIME for v in reg.values()),
             kew["mdd"] - mdd <= GATE_MDD and turn <= cur_turn * GATE_TURN)
        lines.append(f"{z}: 연 {ann:+.1%} 대 K-EW {kew['ann']:+.1%} ({d:+.1%}p, NW t {t:+.2f}) {rkit.mark(c[0])} · "
                     f"시드 {wins}/{len(rows)} {rkit.mark(c[1])} · 국면 " + " / ".join(f"{g} {v:+.1%}p" for g, v in reg.items())
                     + f" {rkit.mark(c[2])} · MDD {mdd:.1%} 대 {kew['mdd']:.1%} · 회전 {turn:.1f} 대 현행 {cur_turn:.1f} {rkit.mark(c[3])}")
        lines.append(f"  기록: {z} − K-CAP {ann - ctrl[z]['K-CAP']['ann']:+.1%}p · K-EW 회전 {kew['turn']:.1f}")
        if all(c):
            passed.append((z, ann / max(1e-9, float(np.std(daily[z] - kew_daily[z]) * np.sqrt(ANN)))))
    if not passed:
        return lines, "기각"
    best = max(passed, key=lambda p: p[1])[0]
    return lines, f"채택 후보 {best} — ①~④ 통과({', '.join(p[0] for p in passed)}), 확정은 금고 second 창"


# --------------------------------------------------------------------------- 명령


def _market(store: Store, sessions: list[date]):  # type: ignore[no-untyped-def]
    ret, bench, trad = rkit.market_data(store, [PRICE_START, *sessions])
    return ret, bench, trad


def cmd_caps(store: Store) -> int:
    sessions = sorted(set(kr_preds(0)["session"]))
    OUT.mkdir(parents=True, exist_ok=True)
    caps = build_caps(store, sessions)
    caps.to_parquet(CAPS)  # invariant-allow: data-access — 시행 작업 캐시
    print(f"→ {CAPS} · {len(caps):,}행 · 세션 {caps['session'].nunique()}", flush=True)
    return 0


def cmd_check(store: Store) -> int:
    """등록 전 점검 ①②③ — **수익을 출력하지 않는다.** ① 구간 종목 수 ② 액티브 셰어(재조정일 평균) ③ 구간 안 IC(시드 평균)."""
    from tools.diag_style_hedge_cost import (
        long_leg,  # noqa: F401 — 같은 선정 규칙을 쓰는지 import 로 고정
    )
    from tools.trial_selection_smoothing import pick_mult

    caps = pd.read_parquet(CAPS)  # invariant-allow: data-access — 시행 작업 캐시
    pred0 = kr_preds(0)
    sessions = sorted(set(pred0["session"]))
    _, _, trad = _market(store, sessions)
    members = zone_members(caps, trad)
    y = labels(KR_LABELS, "KR")
    report: dict[str, dict[str, float]] = {}
    for z in ZONES:
        counts = pd.Series({d: len(members[z].get(d, ())) for d in sessions})
        zone_frame = members_frame(members[z])
        ics, shares = [], []
        for s in SEEDS:
            pred = pred0 if s == 0 else kr_preds(s)
            inside = pred.merge(zone_frame, on=["session", "entity_id"])
            ics.append(float(ic_series(inside, y).mean()))
            if s == 0:
                wide = mask_zone(pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index()
                                 .ewm(span=rkit.SPAN).mean(), members[z])
                held: list[str] = []
                for day in wide.index[::EVERY]:
                    row = wide.loc[day].dropna()
                    if row.empty:
                        continue
                    held = pick_mult(held, row.sort_values(ascending=False).index, N, MULT)
                    k = len(members[z][day])
                    shares.append(0.5 * (len(held) * abs(1 / len(held) - 1 / k) + (k - len(held)) / k))
        report[z] = {"n_min": int(counts.min()), "n_med": float(counts.median()), "n_max": int(counts.max()),
                     "empty": int((counts == 0).sum()), "active_share": float(np.mean(shares)), "ic": float(np.mean(ics)),
                     "ic_min": float(min(ics)), "ic_max": float(max(ics))}
        r = report[z]
        go = r["active_share"] >= MIN_ACTIVE_SHARE and r["ic"] >= MIN_IC and r["empty"] == 0
        print(f"{z} 순위 {ZONES[z][0]}~{ZONES[z][1]}: ① 종목 수 {r['n_min']}~{r['n_max']}(중앙 {r['n_med']:.0f}) · 빈 세션 {r['empty']} · "
              f"② 액티브 셰어 {r['active_share']:.0%} (≥ 5%) · ③ 구간 안 IC {r['ic']:+.4f} (시드 {r['ic_min']:+.4f}~{r['ic_max']:+.4f}, ≥ 0.02)"
              f" → {'측정' if go else '측정하지 않음'}", flush=True)
    (OUT / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def cmd_run(store: Store, *, save: bool) -> int:
    from tools.diag_style_hedge_cost import long_leg

    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else None
    if PROTOCOL_HASH is None or digest != PROTOCOL_HASH:
        print(f"판정 거부 — 등록 해시가 고정되지 않았거나 문서가 바뀌었다(고정 {PROTOCOL_HASH} · 지금 {digest}). "
              "허용되는 것은 caps·check 뿐이다.", flush=True)
        return 2
    check = json.loads((OUT / "check.json").read_text())
    caps = pd.read_parquet(CAPS)  # invariant-allow: data-access — 시행 작업 캐시
    sessions = sorted(set(kr_preds(0)["session"]))
    ret, bench, trad = _market(store, sessions)
    members = zone_members(caps, trad)
    spans = realized_end(sessions, list(ret.index))
    res: dict[str, list[dict[str, float]]] = {z: [] for z in ZONES}
    legs: dict[str, list[pd.Series]] = {z: [] for z in ZONES}
    cur: list[dict[str, float]] = []
    days: list[date] = []
    wdays: dict[str, list[date]] = {}
    for s in SEEDS:
        wide = kr_preds(s).pivot_table(index="session", columns="entity_id", values="pred").sort_index().ewm(span=rkit.SPAN).mean()
        d0, t0, _ = long_leg(wide, ret, trad, N, every=EVERY, mult=MULT, theta=None)
        if not days:
            days = list(d0.index)
            wdays = {g: in_window(pd.Index(days), spans, w) for g, w in WINDOWS.items()}
        cur.append(leg_stats(d0, t0, wdays))
        for z in ZONES:
            daily, turn, _ = long_leg(mask_zone(wide, members[z]), ret, trad, N, every=EVERY, mult=MULT, theta=None)
            res[z].append(leg_stats(daily, turn, wdays))
            legs[z].append(daily)
        del wide
        print(f"  seed{s} 끝", flush=True)
    ctrl: dict[str, dict[str, dict[str, float]]] = {}
    kew_daily: dict[str, pd.Series] = {}
    for z in ZONES:
        ew_d, ew_t = static_book(sessions, ret, ew_target(members[z]), every=EVERY)
        cap_d, cap_t = static_book(sessions, ret, cap_target(members[z], caps), every=EVERY)
        ctrl[z] = {"K-EW": leg_stats(ew_d, ew_t, wdays), "K-CAP": leg_stats(cap_d, cap_t, wdays)}
        kew_daily[z] = ew_d.reindex(wdays["전체"]).fillna(0.0)
    daily = {z: pd.DataFrame(v).T.reindex(wdays["전체"]).fillna(0.0).mean(axis=1) for z, v in
             ((z, {i: s for i, s in enumerate(legs[z])}) for z in ZONES)}
    skip = [z for z in ZONES if not (check[z]["active_share"] >= MIN_ACTIVE_SHARE and check[z]["ic"] >= MIN_IC
                                     and check[z]["empty"] == 0)]
    if skip:
        print(f"등록 전 점검에 걸린 변형 {skip} — 측정하지 않는다(예산은 변형 수 그대로 센다)", flush=True)
    lines, verdict = judge({z: res[z] for z in ZONES if z not in skip}, ctrl, daily, kew_daily,
                           float(np.mean([c["turn"] for c in cur])))
    lines.append(f"기록 현행(전 유니버스 상위 24) 연 {np.mean([c['ann'] for c in cur]):+.1%} · 판정 세션 {len(wdays['전체'])}")
    print("\n".join(lines) + f"\n판정: {verdict}", flush=True)
    if save:
        rkit.record(store, entity="p1a-prime-2026-10:P1A", source="trial_p1a_prime", family="selection",
                    digest=digest, verdict=verdict, lines=["P1-a′ · 변형 2(A1·A2) · W-fa2021", *lines])
    else:
        print("--save 없음 — 창고에 적지 않았다", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("caps")
    sub.add_parser("check")
    run = sub.add_parser("run")
    run.add_argument("--save", action="store_true")
    parser.add_argument("--root", default="data")
    args = parser.parse_args(argv)
    store = Store(root=Path(args.root))
    if args.cmd == "caps":
        return cmd_caps(store)
    if args.cmd == "check":
        return cmd_check(store)
    return cmd_run(store, save=args.save)


if __name__ == "__main__":
    raise SystemExit(main())
