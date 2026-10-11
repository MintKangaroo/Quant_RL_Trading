"""진단 — 새 정보원 후보 N3 "KRX 개별주식선물 괴리·미결제" 탐색 IC (docs/diag/n3-stock-futures-2026-10-11.md).

**진단이지 시행이 아니다** — 합격선이 없고 `research_trials` 에 적지 않는다. 창고에 쓰지 않는다.
배경: docs/diag/source-scan-2026-10-10.md N3 · n2-n3-feasibility-2026-10-11.md §2. 재료: 창고 ``stock_futures``(data-contract §4-3).

    QUANT_RL_DUCKDB_MEMORY_LIMIT=1GB QUANT_RL_DUCKDB_THREADS=2 nice -n 10 .venv/bin/python tools/diag_n3_futures.py

**금고 규칙** — 수익·라벨은 END(2026-06-30) 까지만. 모든 읽기는 ``as_of=END``·``until=END``, 라벨 c(t+6)/c(t+1) 은 t+6 이
END 안일 때만 생기고 마지막 라벨 세션이 END 를 넘으면 멈춘다(``guard``). data/_past_vault 는 열지 않는다.

피처(모두 하루 늦춤 — 결정일 t 에는 t−1 까지 관측된 값; 창고 observed_at 이 실제 도착 d+1 아침보다 이르게 찍힌다, data-contract §5-0):
- 대표 월물 = 그날 미결제 최대 월물(동률·전부 0이면 가까운 월물). 괴리 ``basis_raw`` = 정산가/현물가 − 1.
- ``basis_ann`` = basis_raw × 365 / max(잔존일, 7) — 잔존일 정규화(만기 = 결제월 두 번째 목요일). 금리·배당 보정은 안 한다:
  금리 캐리는 같은 날 같은 만기면 전 종목 공통이라 횡단면 순위에서 빠지고, 배당은 종목별이라 남는다 → 아래 두 변형.
- ``basis_dev60`` = (횡단면 중앙값을 뺀 basis_ann) − 그 종목 60세션 평균 — 종목 고유 수준(배당 등)을 뺀 최근 변화.
- ``dbasis5`` = 횡단면 중앙값을 뺀 basis_ann 의 5세션 변화.
- ``oi_mcap`` = 전 월물 미결제 × 승수 × 현물가 / 시총. ``doi5``·``doi20`` = 미결제 변화(5·20세션) × 승수 × 현물가 / 시총.
  승수는 데이터에서 잰다(거래대금 / (거래량 × 종가) 중앙값).
- 외국인 선물 순매수는 이 API 에 없어 생략.

매핑 검산: ``spot_price`` 와 그날 기초자산 원주가 종가가 0.5% 안에서 맞는 (종목, 날)만 쓴다.
통제·잔차화·NW t 는 tools/diag_n1_issuance.py · tools/diag_ta_features.py 관례(부분 IC 는 피처·라벨 둘 다 잔차화).
"""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from quant_rl_trading.analysts import ic as ic_module
from quant_rl_trading.store import Store
from tools import diag_ta_features as T
from tools.diag_n1_issuance import load_shares

END = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
END_DAY = date(2026, 6, 30)
PANEL_START = date(2021, 8, 11)
START_DECISION = date(2022, 3, 2)
BAN = (date(2023, 11, 6), date(2025, 3, 30))
PERIODS = (("전체", date(2000, 1, 1), date(2100, 1, 1)), ("금지 전", date(2000, 1, 1), date(2023, 11, 3)),
           ("금지기", *BAN), ("재개 후", date(2025, 3, 31), date(2100, 1, 1)))
#: 견고성 — 배당 창(12/1~1/15 결정일)을 뺀 전체. ``cut`` 이 이 표식을 알아본다.
NODIV = date(1900, 1, 1)
PERIODS = (*PERIODS, ("배당창 제외", NODIV, NODIV))
FEATURES = ("basis_raw", "basis_ann", "basis_dev60", "dbasis5", "oi_mcap", "doi5", "doi20")
MIN_N = 50
OUT_CSV = Path("logs/diag-n3-futures-20261011.csv")


def guard(days: list[date]) -> None:
    if days and max(days) > END_DAY:
        raise SystemExit(f"금고 침범: {max(days)} > {END_DAY} — 멈춘다")


def second_thursday(month: str) -> date:
    y, m = int(month[:4]), int(month[4:])
    d = date(y, m, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(days=7)


# --------------------------------------------------------------------------- 읽기·피처


def load_futures(store: Store) -> pd.DataFrame:
    f = store.get("stock_futures", as_of=END, until=END, lookback=(END.date() - PANEL_START).days + 5, market="KR",
                  columns=["entity_id", "valid_from", "underlying_id", "map_method", "contract_month", "close", "settle_price",
                           "spot_price", "volume", "value", "open_interest", "product_code", "prod_nm"])
    f["day"] = pd.to_datetime(f["valid_from"]).dt.date
    guard(list(f["day"].unique()))
    return f


def build(f: pd.DataFrame, d: dict[str, pd.DataFrame], shares: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict]:
    idx, cols = d["close"].index, d["close"].columns
    stats: dict = {"rows": len(f), "days": f["day"].nunique(), "first_day": str(min(f["day"])), "last_day": str(max(f["day"]))}
    prods = f.drop_duplicates(["prod_nm"])
    stats["products"] = len(prods)
    stats["products_mapped"] = int(prods["underlying_id"].notna().sum())
    stats["rows_mapped"] = float(f["underlying_id"].notna().mean())
    stats["map_methods"] = f.drop_duplicates(["prod_nm", "underlying_id"])["map_method"].value_counts().to_dict()
    f = f[f["underlying_id"].notna() & (f["spot_price"] > 0) & (f["settle_price"] > 0)].copy()
    # 승수 실측
    tr = f[(f["volume"] > 0) & (f["close"] > 0)]
    mult = float(np.median(tr["value"] / (tr["volume"] * tr["close"])))
    stats["multiplier"] = mult
    # 매핑 검산: 현물가 == 원주가 종가
    cr = d["close_raw"].stack().rename("close_raw").reset_index()
    cr.columns = ["day", "underlying_id", "close_raw"]
    spot = f.drop_duplicates(["day", "underlying_id"])[["day", "underlying_id", "spot_price", "prod_nm"]]
    spot = spot.merge(cr, on=["day", "underlying_id"], how="left")
    spot = spot[spot["close_raw"].notna()]
    spot["ok"] = (spot["spot_price"] / spot["close_raw"] - 1).abs() <= 0.005
    stats["spot_check_pairs"] = len(spot)
    stats["spot_match"] = float(spot["ok"].mean())
    bad = spot[~spot["ok"]].groupby("prod_nm").size().sort_values(ascending=False)
    stats["spot_mismatch_top"] = bad.head(10).to_dict()
    good = spot[spot["ok"]][["day", "underlying_id"]]
    f = f.merge(good, on=["day", "underlying_id"], how="inner")
    # 대표 월물
    f["expiry"] = f["contract_month"].map(second_thursday)
    f["tau"] = [(e - x).days for e, x in zip(f["expiry"], f["day"], strict=True)]
    f = f[f["tau"] >= 0]
    f = f.sort_values(["day", "underlying_id", "open_interest", "tau"], ascending=[True, True, False, True])
    front = f.drop_duplicates(["day", "underlying_id"], keep="first").copy()
    front["basis_raw"] = front["settle_price"] / front["spot_price"] - 1.0
    front["basis_ann"] = front["basis_raw"] * 365.0 / np.maximum(front["tau"], 7)
    oi = f.groupby(["day", "underlying_id"])["open_interest"].sum().rename("oi_tot").reset_index()
    front = front.merge(oi, on=["day", "underlying_id"])
    stats["front_tau_median"] = float(front["tau"].median())

    def wide(col: str) -> pd.DataFrame:
        return front.pivot_table(index="day", columns="underlying_id", values=col, aggfunc="last").reindex(index=idx, columns=cols)

    braw, bann, oit, sp = wide("basis_raw"), wide("basis_ann"), wide("oi_tot"), wide("spot_price")
    mcap = d["close_raw"] * shares.ffill(limit=5)
    mcap = mcap.where(mcap > 0)
    xs = bann.sub(bann.median(axis=1), axis=0)
    out = {
        "basis_raw": braw,
        "basis_ann": bann,
        "basis_dev60": xs - xs.rolling(60, min_periods=40).mean(),
        "dbasis5": xs - xs.shift(5),
        "oi_mcap": oit * mult * sp / mcap,
        "doi5": (oit - oit.shift(5)) * mult * sp / mcap,
        "doi20": (oit - oit.shift(20)) * mult * sp / mcap,
    }
    stats["names_per_day"] = float(bann.notna().sum(axis=1)[bann.notna().sum(axis=1) > 0].mean())
    return {k: v.shift(1).replace([np.inf, -np.inf], np.nan) for k, v in out.items()}, stats


def controls(d: dict[str, pd.DataFrame], shares: pd.DataFrame) -> dict[str, pd.DataFrame]:
    c = d["close"]
    r = c / c.shift(1) - 1.0
    mcap = d["close_raw"] * shares.ffill(limit=5)
    mkt = r.clip(-0.5, 1.0).mean(axis=1)
    return {
        "_rev5": c / c.shift(5) - 1.0,
        "_mom60": c / c.shift(60) - 1.0,
        "_lowvol": -r.ewm(span=60, min_periods=60).std(),
        "_liq": np.log(T.rmean(d["value"], 20)),
        "_logcap": np.log(mcap.where(mcap > 0)),
        "_beta": r.rolling(120, min_periods=100).cov(mkt).div(mkt.rolling(120, min_periods=100).var(), axis=0),
    }


# --------------------------------------------------------------------------- 측정


def ic_rows(x: pd.DataFrame, y: pd.DataFrame) -> pd.Series:
    m = x.notna() & y.notna()
    xr, yr = x.where(m).rank(axis=1), y.where(m).rank(axis=1)
    xc, yc = xr.sub(xr.mean(axis=1), axis=0), yr.sub(yr.mean(axis=1), axis=0)
    ic = (xc * yc).sum(axis=1) / np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
    return ic[m.sum(axis=1) >= MIN_N].dropna()


def partial_ic(x: pd.DataFrame, y: pd.DataFrame, ctrl: list[pd.DataFrame]) -> pd.Series:
    """일별 부분 순위상관 — 피처·라벨 둘 다 통제 순위에 잔차화(diag_n1_issuance.partial_ic 와 같은 식, 최소 표본만 MIN_N)."""
    out = {}
    for day in x.index:
        frame = pd.concat([x.loc[day], y.loc[day]] + [c.loc[day] for c in ctrl], axis=1).dropna()
        if len(frame) < MIN_N:
            continue
        r = frame.rank(pct=True).to_numpy()
        a = np.column_stack([np.ones(len(r)), r[:, 2:]])
        res = r[:, :2] - a @ np.linalg.lstsq(a, r[:, :2], rcond=None)[0]
        den = np.sqrt((res[:, 0] ** 2).sum() * (res[:, 1] ** 2).sum())
        if den > 0:
            out[day] = float((res[:, 0] * res[:, 1]).sum() / den)
    return pd.Series(out, dtype=float)


def deciles(x: pd.DataFrame, y: pd.DataFrame) -> pd.DataFrame:
    """날별 십분위 초과(동일가중 대비) — 행 = 날, 열 = D1..D10."""
    m = x.notna() & y.notna()
    pr = x.where(m).rank(axis=1, pct=True, method="first")
    yy = y.where(m)
    mean = yy.mean(axis=1)
    cols = {}
    for k in range(10):
        sel = (pr > k / 10) & (pr <= (k + 1) / 10)
        cols[f"D{k + 1}"] = yy.where(sel).mean(axis=1) - mean
    return pd.DataFrame(cols)[m.sum(axis=1) >= MIN_N]


def nw(s: pd.Series) -> float:
    return float(ic_module.newey_west_t(s, lag=4)) if len(s) > 10 else float("nan")


def no_div_window(x: date) -> bool:
    """배당 기준일(국장 대부분 12월 말) 앞뒤 — 12/1~1/15 결정일. 이 창의 괴리는 배당 차감이 섞인다."""
    return not ((x.month == 12) or (x.month == 1 and x.day <= 15))


def cut(s: pd.Series | pd.DataFrame, a: date, b: date) -> pd.Series | pd.DataFrame:
    if a == NODIV:
        return s[[no_div_window(x) for x in s.index]]
    return s[(s.index >= a) & (s.index <= b)]


def measure(F: dict[str, pd.DataFrame], label: pd.DataFrame, ctrl: list[pd.DataFrame], scope: str) -> pd.DataFrame:
    rows = []
    for name in FEATURES:
        x = F[name]
        ic = ic_rows(x, label)
        pic = partial_ic(x, label, ctrl)
        dec = deciles(x, label)
        for pn, a, b in PERIODS:
            i, p, dd = cut(ic, a, b), cut(pic, a, b), cut(dec, a, b)
            d1, d10 = dd["D1"], dd["D10"]
            rows.append({"scope": scope, "feature": name, "period": pn, "n_days": len(i),
                         "n_names": float(x.notna().where(label.notna()).sum(axis=1).loc[i.index].mean()) if len(i) else np.nan,
                         "IC": float(i.mean()), "t": nw(i), "편IC": float(p.mean()), "편t": nw(p),
                         "D1%": float(d1.mean() * 100), "D1_t": nw(d1), "D10%": float(d10.mean() * 100), "D10_t": nw(d10),
                         "D10-D1%": float((d10 - d1).mean() * 100), "D10-D1_t": nw((d10 - d1).dropna())})
    return pd.DataFrame(rows)


def main() -> int:
    store = Store(root=Path("data"))
    d = T.load(store, END, PANEL_START)
    c, v = d["close"], d["value"]
    guard(list(c.index))
    idx, cols = c.index, c.columns
    shares = load_shares(store, idx, cols)
    fut = load_futures(store)
    F, stats = build(fut, d, shares)
    for k, val in stats.items():
        print(f"{k}: {val}")
    C = controls(d, shares)

    label = c.shift(-6) / c.shift(-1) - 1.0
    label = label.where(label.abs() <= 0.5)
    uni = (T.rmean(v, 20) >= T.MIN_VALUE) & (c.notna().rolling(120, min_periods=1).sum() >= 120) & F["basis_ann"].notna()
    days = [x for x in idx if x >= START_DECISION]
    label = label.where(uni).loc[days]
    label = label[label.notna().sum(axis=1) >= MIN_N]
    days = list(label.index)
    sess = list(idx)
    last_label_day = sess[sess.index(days[-1]) + 6]
    guard([last_label_day])
    print(f"\n결정일 {days[0]}~{days[-1]} {len(days)}일 · 라벨 마지막 세션 {last_label_day} · 평균 우주 {label.notna().sum(axis=1).mean():.0f}")

    Fd = {k: x.loc[days] for k, x in F.items()}
    Cd = [C[k].loc[days] for k in ("_rev5", "_mom60", "_lowvol", "_liq", "_logcap", "_beta")]

    # 스타일 겹침
    print("\n=== 스타일 겹침(일별 횡단면 순위상관 평균, 선물 우주) ===")
    sty = pd.DataFrame(index=list(FEATURES), columns=["_rev5", "_mom60", "_lowvol", "_liq", "_logcap", "_beta", "basis_ann", "oi_mcap"], dtype=float)
    for a in FEATURES:
        for b in sty.columns:
            xb = Fd[b] if b in Fd else C[b].loc[days]
            sty.loc[a, b] = np.nan if a == b else float(ic_rows(Fd[a].where(label.notna()), xb.where(label.notna())).mean())
    print(sty.round(3).to_string())

    from tools.diag_ta_round2 import k200_mask
    k200 = k200_mask(store, days, cols)
    k200_first = min(k for k in days if k200.loc[k].any()) if k200.any().any() else None
    print(f"\nK200 구성 스냅샷 첫 적용일: {k200_first}")
    res = [measure(Fd, label, Cd, "선물 전체")]
    lk = label.where(k200)
    res.append(measure(Fd, lk, Cd, "K200 안"))
    out = pd.concat(res, ignore_index=True)
    pd.set_option("display.width", 250)
    print("\n=== 탐색 IC (5세션 순위 IC · NW t lag4 · 편IC = rev5·mom60·lowvol·liq·logcap·beta 통제) ===")
    print(out.round(4).to_string(index=False))
    out.to_csv(OUT_CSV, index=False)
    sty.to_csv(OUT_CSV.with_name("diag-n3-futures-style-20261011.csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
