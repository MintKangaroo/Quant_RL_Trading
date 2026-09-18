"""모의계좌 수익 분해 — **왜 이겼나/졌나**를 항목으로 가른다.

    .venv/bin/python tools/attribute_returns.py                    # 8/28 이후 전체
    .venv/bin/python tools/attribute_returns.py --from 2026-09-01 --daily

## 왜 필요한가

2026-11-25 판정은 "이겼다/졌다" 만 낸다. 이유가 없으면 프로젝트 재정의를 장님으로 한다.
화면도 스스로 "Gross PnL 미측정 · 총 거래비용 분해 없음" 이라고 적어 왔다.

## 항등식 (근사가 아니라 항등식이다)

현금이 0% 수익이라고 보면 세션 t 의 장부 수익은 ``r_p = w·r_h − 명시비용`` 이다
(``w`` 는 **직전 세션 종가 기준** 주식 비중, ``r_h`` 는 주식 슬리브 총수익).
여기에 유니버스 수익 ``r_u`` 와 벤치마크 ``r_b`` 를 끼워 넣으면

    r_p − r_b = w(r_h − r_u)  +  w(r_u − r_b)  +  (w − 1)·r_b  −  명시비용
                 선택            유니버스          노출

이고 **딱 맞는다** — 잔차 항이 없다. 선택에서 슬리피지를 따로 떼어 다섯 항으로 낸다.

| 항 | 뜻 |
|---|---|
| 선택 | 고른 종목이 **살 수 있었던 세계**를 이겼나 |
| 유니버스 | 그 세계(동일가중)가 벤치마크(시총가중 ETF)를 이겼나 — 우리가 못 고르는 부분 |
| 노출 | 덜 투자한 몫. 시장이 내리면 +, 오르면 − |
| 야간갭 | 결정가(직전 종가) → 시가. **우리가 한 일이 아니다** |
| 집행 | 시가 → 체결가. 우리 실행 품질 |
| 명시비용 | 수수료 + 세금 |

## 무엇을 못 가르나 — 정직하게

**집행이 +로 나오는 것은 실력이 아닐 수 있다.** 아침 세션은 지정가로 낸다 — 값이 우리 쪽으로
와야 체결되므로 체결된 것만 보면 시가보다 유리하다(실측 2026-09 +0.97%p). 그 대가는 **미체결**
이고(9/17 주문 110 중 체결 68), 못 산 종목의 기회비용은 여기 안 잡히고 **선택 항에 숨는다.**
집행 항을 실행 품질의 증거로 읽지 말 것.

``r_h`` 는 장부에서 역산한다(``(r_p + 명시비용)/w``). 그래서 **선택 항이 잔차를 흡수한다** —
배당 반영 시점, 일중 타이밍, 회계 정정이 거기 섞인다. 야간갭·집행은 체결 기록에서 따로 재서
빼 두었으므로 그만큼은 갈라져 있다. 벤치마크는 ETF **가격**이라 분배금이 빠져 있고, 그만큼
우리가 유리하게 보인다(`verify_exit_criterion.py` 와 같은 한계).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from quant_rl_trading.collectors.benchmark_etf import BENCHMARK_ETF  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402

START = date(2026, 8, 28)

#: **운용을 바꾼 날.** 각 변경의 "전·후" 를 따로 분해하려고 둔다. 날짜는 그 변경이 **처음 영향을
#: 준 장부 세션**이다(설정은 장 마감 뒤에 넣으므로 보통 다음 거래일). 변경을 하면 여기 한 줄 넣는다.
#: `--changes` 를 주면 이 지점들로 창을 자른다.
CHANGE_POINTS: tuple[tuple[date, str], ...] = (
    (date(2026, 9, 21), "V6 — 노출 축을 국면만 남김(추세·압축 끔)"),
)
TERMS = ("선택", "유니버스", "노출", "야간갭", "집행", "명시비용")


def _by_day(frame: pd.DataFrame, column: str) -> pd.Series:
    out = frame.copy()
    out["day"] = out["valid_from"].dt.tz_convert("Asia/Seoul").dt.date
    out = out.sort_values("valid_from").groupby("day", as_index=True).tail(1)
    return out.set_index("day")[column].astype(float)


def universe_frame(store: Store, days: list[date]) -> pd.DataFrame:
    """**살 수 있었던 세계**의 종가 표(세션 × 종목). 상장·거래가능 종목 전부.

    수익은 호출부가 **우리 장부의 이웃한 두 세션 사이**로 낸다 — 기계 정지로 장부에 빈
    거래일이 생기면(9/14·9/15) 그 구간의 장부 수익은 며칠치인데 벤치마크만 하루치를 세면
    분해가 통째로 어긋난다. 실제로 첫 실행에서 벤치마크가 +4.32% 로 나왔다(참값 −0.84%).

    시총가중이 아니다 — 우리 전략이 동일가중 24종목이라, 시총가중과 견주면 그 차이가
    선택 항으로 흘러든다. 그것을 따로 세우는 것이 이 계열의 목적이다.
    """
    if not days:
        return pd.DataFrame()
    end = datetime.combine(days[-1], time(23, 0), tzinfo=UTC)
    span = (days[-1] - days[0]).days + 12
    # **`read_prices` 를 경유한다** — 휴장일 종가 0 이 수익률을 통째로 뒤집는다(불변식 검사).
    # `adjusted=True`: 수익률을 만드는 자리라 기업행위 보정이 필요하다. 액면분할 하나가
    # 그 종목의 하루 수익을 −50% 로 만들고, 동일가중 평균이면 유니버스 전체가 휜다.
    prices = read_prices(
        store, as_of=end, lookback=span, market="KR",
        columns=["entity_id", "valid_from", "close"], adjusted=True,
    )
    if prices.empty:
        return pd.DataFrame()
    prices = prices.assign(day=prices["valid_from"].dt.tz_convert("Asia/Seoul").dt.date)
    wide = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last")
    return wide.sort_index()


def universe_members(store: Store, days: list[date]) -> dict[date, set[str]]:
    """세션마다 **그날 기준** 상장·거래가능 종목. 미래를 보지 않는다.

    처음엔 창의 **마지막 날** 소속을 전 구간에 썼다. 그러면 마지막 날 상장폐지·거래정지된
    종목이 과거에서도 지워져 "살 수 있었던 세계" 가 생존편향으로 걸러진다 — 그 편향이
    곧장 `선택` 항으로 흘러드는데, 그게 이 도구가 재려는 바로 그 숫자다.
    """
    if not days:
        return {}
    end = datetime.combine(days[-1], time(23, 0), tzinfo=UTC)
    span = (days[-1] - days[0]).days + 40
    frame = store.get(
        "universe", as_of=end, lookback=span, market="KR",
        columns=["entity_id", "valid_from", "is_listed", "is_tradable"],
    )
    if frame.empty:
        return {}
    frame = frame.assign(day=frame["valid_from"].dt.tz_convert("Asia/Seoul").dt.date)
    frame = frame[frame["is_listed"].astype(bool) & frame["is_tradable"].astype(bool)]
    out: dict[date, set[str]] = {}
    for day, chunk in frame.groupby("day"):
        out[day] = set(chunk["entity_id"].astype(str))
    return out


def _span_return(series: pd.Series, start: date, end: date) -> float:
    """``start`` 종가 대비 ``end`` 종가. 둘 중 하나가 없으면 NaN."""
    try:
        first, last = float(series.loc[start]), float(series.loc[end])
    except KeyError:
        return float("nan")
    if not first or first != first or last != last:
        return float("nan")
    return last / first - 1.0


def _universe_span(
    wide: pd.DataFrame, start: date, end: date, members: set[str] | None = None
) -> float:
    """구간 동일가중 수익. **양쪽 종가가 다 있는 종목만** 평균한다 — 결측을 0 으로
    메우면 상장·거래정지 종목이 "변동 없음" 으로 평균을 끌어당긴다.

    ``members`` 는 **구간 시작 시점의** 상장·거래가능 명단이다. 끝 시점 명단을 쓰면
    생존편향이 선택 항으로 흘러든다.
    """
    if wide.empty or start not in wide.index or end not in wide.index:
        return float("nan")
    first, last = wide.loc[start], wide.loc[end]
    both = first.notna() & last.notna() & (first > 0)
    if members is not None:
        both &= pd.Series(
            [str(name) in members for name in wide.columns], index=wide.columns
        )
    if not bool(both.any()):
        return float("nan")
    return float((last[both] / first[both] - 1.0).mean())


def execution_won(store: Store, days: list[date]) -> tuple[pd.Series, pd.Series]:
    """결정가에서 체결가까지의 차이를 **둘로 갈라** 원으로 낸다. 불리하면 음수.

        야간갭 = 시가 − 직전 종가     ← 우리가 한 일이 아니다. 밤사이 시장이 움직인 것
        집행   = 체결가 − 시가        ← 우리 실행 품질

    처음엔 둘을 합쳐 "슬리피지" 라고 불렀는데 +1.78%p 가 나왔다. 유리한 슬리피지가 그 크기일
    리 없다 — 오른 날 많이 팔아서 생긴 **갭**이 실행 품질로 둔갑한 것이었다. 결정은 직전
    종가를 보고 하고(세션 08:40) 체결은 개장 뒤에 나므로, 둘은 원인도 처방도 다르다.

    매수는 비싸게 사면 손해, 매도는 싸게 팔면 손해 — 부호를 그렇게 맞춘다.
    비율로 나누는 것은 호출부가 한다(구간마다 기준 NAV 가 다르다).
    """
    if not days:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    end = datetime.combine(days[-1], time(23, 0), tzinfo=UTC)
    span = (days[-1] - days[0]).days + 12
    trades = store.get("trades", as_of=end, lookback=span, market="KR")
    if trades.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    trades = trades.assign(day=trades["valid_from"].dt.tz_convert("Asia/Seoul").dt.date)
    # 체결가와 견주는 자리라 **원주가**다(adjusted 안 켠다) — 그날 실제로 오간 값이어야 한다.
    prices = read_prices(
        store, as_of=end, lookback=span, market="KR",
        columns=["entity_id", "valid_from", "close", "open"],
    )
    prices = prices.assign(day=prices["valid_from"].dt.tz_convert("Asia/Seoul").dt.date)
    indexed = prices.set_index(["day", "entity_id"])
    closes = indexed["close"].astype(float)
    opens = indexed["open"].astype(float)
    ordered = sorted(set(prices["day"]))
    previous = {day: ordered[i - 1] for i, day in enumerate(ordered) if i}
    gaps: dict[date, float] = {}
    fills: dict[date, float] = {}
    for day, chunk in trades.groupby("day"):
        base = previous.get(day)
        if base is None:
            continue
        gap_total = fill_total = 0.0
        for row in chunk.to_dict("records"):
            entity = str(row["entity_id"])
            prior = closes.get((base, entity))
            open_price = opens.get((day, entity))
            if prior is None or not prior or open_price is None or not open_price:
                continue
            sign = -1.0 if str(row["side"]) == "buy" else 1.0
            quantity = float(row["quantity"])
            gap_total += sign * quantity * (float(open_price) - float(prior))
            fill_total += sign * quantity * (float(row["price"]) - float(open_price))
        gaps[day] = gap_total
        fills[day] = fill_total
    return pd.Series(gaps, dtype=float), pd.Series(fills, dtype=float)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", default="data/_paper")
    parser.add_argument("--from", dest="start", default=START.isoformat())
    parser.add_argument("--to", dest="end")
    parser.add_argument("--daily", action="store_true", help="세션별 표도 찍는다")
    parser.add_argument("--split", action="append", default=[], help="이 날부터 새 구간 (여러 번)")
    parser.add_argument("--changes", action="store_true", help="등록된 운용 변경 지점으로 자른다")
    args = parser.parse_args(argv)

    book = Store(root=Path(args.sandbox))
    source = Store(root=Path("data"))
    now = datetime.now(UTC)  # invariant-allow: wallclock — 조회 시점
    nav = book.get("nav_daily", as_of=now, lookback=300)
    if nav.empty:
        print("nav_daily 가 비었다 — 분해할 것이 없다", file=sys.stderr)
        return 2
    navs = _by_day(nav, "nav")
    equity = _by_day(nav, "equity_kr")
    twr = _by_day(nav, "twr_return")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else max(navs.index)
    days = [d for d in sorted(navs.index) if start <= d <= end]
    if len(days) < 2:
        print(f"세션이 {len(days)}개 — 분해할 것이 없다", file=sys.stderr)
        return 2

    bench = source.get("indices", as_of=now, lookback=300, entity=BENCHMARK_ETF,
                       columns=["valid_from", "close"])
    if bench.empty:
        print(f"{BENCHMARK_ETF} 가 없다 — 벤치마크 없이는 분해할 수 없다", file=sys.stderr)
        return 2
    bench_closes = _by_day(bench, "close").sort_index()
    wide = universe_frame(source, days)
    members = universe_members(source, days)
    gaps, fills = execution_won(book, days)
    costs = cost_won(book, now)

    # **앵커는 벤치마크·유니버스가 둘 다 있는 날이다.** 장부는 주말에도 행을 남기고
    # (회계 갱신), 기계가 멈춘 날은 행이 아예 없다. 앵커 사이의 장부 수익은 복리로 잇고
    # 비용·슬리피지도 같은 구간을 합친다 — 한 행이라도 버리면 합계가 장부와 안 맞는다.
    anchors = [d for d in days if d in bench_closes.index and (wide.empty or d in wide.index)]
    rows = []
    previous = None
    for day in anchors:
        if previous is None:
            previous = day
            continue
        span_days = [d for d in days if previous < d <= day]
        r_p = 1.0
        for d in span_days:
            r_p *= 1.0 + float(twr.get(d, 0.0) or 0.0)
        r_p -= 1.0
        # **직전 장부 세션부터 잰다.** 장부에 빈 거래일이 있으면 그 구간의 장부 수익은
        # 며칠치이고, 벤치마크·유니버스도 같은 구간이어야 한다.
        r_b = _span_return(bench_closes, previous, day)
        # 소속은 **구간 시작 시점** 기준. 그날 명단이 없으면 그 이전 마지막 명단.
        known = [d for d in members if d <= previous]
        r_u = _universe_span(wide, previous, day, members.get(max(known)) if known else None)
        base_nav = float(navs.get(previous, float("nan")))
        weight = float(equity.get(previous, float("nan"))) / base_nav if base_nav else float("nan")
        cost = sum(float(costs.get(d, 0.0) or 0.0) for d in span_days) / base_nav if base_nav else 0.0
        gap = sum(float(gaps.get(d, 0.0) or 0.0) for d in span_days) / base_nav if base_nav else 0.0
        fill = sum(float(fills.get(d, 0.0) or 0.0) for d in span_days) / base_nav if base_nav else 0.0
        if any(v != v for v in (r_p, r_b, r_u, weight)):
            rows.append({"day": day, "r_p": r_p, "r_b": r_b, "미측정": True})
            previous = day
            continue
        # **나누지 않는다.** 예전에는 `r_h = (r_p + 비용)/w` 를 구해 `w(r_h − r_u)` 를 냈는데,
        # w 가 0 에 가까우면 터져서 "비중이 작으면 선택 항을 0 으로" 라는 예외를 뒀었다. 그러면
        # 그 구간만 항등식이 깨지고(나머지 다섯 항은 그대로 나간다) 도구가 조용히 거짓말을 한다.
        # 정의상 `w·r_h = r_p + 비용` 이므로 곱을 직접 쓰면 나눗셈이 아예 없다.
        selection = (r_p + cost) - weight * r_u - gap - fill
        rows.append({
            "day": day, "r_p": r_p, "r_b": r_b, "w": weight, "미측정": False,
            "선택": selection,
            "유니버스": weight * (r_u - r_b),
            "노출": (weight - 1.0) * r_b,
            "야간갭": gap,
            "집행": fill,
            "명시비용": -cost,
        })
        previous = day

    frame = pd.DataFrame(rows)
    good = frame[~frame["미측정"]]
    if good.empty:
        print("분해 가능한 세션이 없다 — 벤치마크·유니버스 결손", file=sys.stderr)
        return 2
    print(f"분해 창 {anchors[0]} ~ {anchors[-1]} · 구간 {len(good)}"
          + (f" (미측정 {int(frame['미측정'].sum())})" if frame["미측정"].any() else ""))

    splits = sorted({date.fromisoformat(d) for d in args.split})
    if args.changes:
        splits = sorted(set(splits) | {d for d, _ in CHANGE_POINTS})
    labels = {d: label for d, label in CHANGE_POINTS}
    edges = [d for d in splits if good["day"].min() < d <= good["day"].max()]
    bounds = [good["day"].min(), *edges]
    mechanics = _mechanics(book, now)
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] if i + 1 < len(bounds) else None
        part = good[(good["day"] >= lo) & ((good["day"] < hi) if hi else True)]
        if part.empty:
            continue
        title = "전체" if len(bounds) == 1 else (
            f"{lo} 부터" + (f" — {labels[lo]}" if lo in labels else "") if i else f"{lo} ~ 변경 전"
        )
        _report(part, title, mechanics)

    if args.daily:
        show = good[["day", "w", "r_p", "r_b", *TERMS]].copy()
        for column in ["w", "r_p", "r_b", *TERMS]:
            show[column] = (show[column] * 100).round(3)
        print("\n" + show.to_string(index=False))
    return 0


def _report(part: pd.DataFrame, title: str, mechanics: pd.DataFrame) -> None:
    """한 구간의 분해와 **메커니즘 점검**.

    둘을 가르는 이유: 변경이 돈이 됐는지는 표본이 쌓여야 말할 수 있지만(판정은 11-25),
    변경이 **뜻대로 작동했는지**는 며칠이면 보인다 — 노출을 올리려 했으면 비중이 올랐나,
    회전을 줄이려 했으면 노출 왕복이 줄었나. 작동조차 안 했다면 수익을 기다릴 이유가 없다.
    """
    print(f"\n=== {title} · 구간 {len(part)} ===")
    print(f"  우리 {part['r_p'].sum() * 100:+.2f}%  ·  벤치마크 {part['r_b'].sum() * 100:+.2f}%"
          f"  ·  차이 {(part['r_p'] - part['r_b']).sum() * 100:+.2f}%p   (일별 합, 복리 아님)")
    for term in TERMS:
        print(f"    {term:6s} {float(part[term].sum()) * 100:+7.2f}%p")
    total = float(sum(part[t].sum() for t in TERMS))
    gap = float((part["r_p"] - part["r_b"]).sum())
    print(f"    {'합계':6s} {total * 100:+7.2f}%p   (차이와 어긋남 {abs(total - gap) * 100:.4f}%p)")

    days = set(part["day"])
    m = mechanics[mechanics["day"].isin(days)] if not mechanics.empty else mechanics
    print("  메커니즘 — 뜻대로 작동했나")
    print(f"    주식 비중     평균 {part['w'].mean():.0%} · 최저 {part['w'].min():.0%} · 최고 {part['w'].max():.0%}")
    if not m.empty and float(m["gross"].sum()) > 0:
        share = float(m["net"].abs().sum()) / float(m["gross"].sum())
        print(f"    거래 구성     노출 변경 {share:.0%} · 종목 교체·리밸런스 {1 - share:.0%}"
              f"  (거래대금 {float(m['gross'].sum()) / 1e8:,.1f}억)")
    scales = m.dropna(subset=["scale"])["scale"] if not m.empty and "scale" in m else pd.Series(dtype=float)
    if len(scales):
        switches = int((scales.diff().abs() > 1e-9).sum())
        print(f"    노출 배수     전환 {switches}회 · 값 {sorted(set(round(v, 2) for v in scales))}")


def _mechanics(book: Store, now: datetime) -> pd.DataFrame:
    """세션마다 순매수·총거래(원)와 적용 노출 배수. 분해가 아니라 **작동 여부**를 본다."""
    trades = book.get("trades", as_of=now, lookback=300, market="KR",
                      columns=["valid_from", "side", "quantity", "price"])
    out = pd.DataFrame(columns=["day", "net", "gross", "scale"])
    if not trades.empty:
        t = trades.assign(
            day=trades["valid_from"].dt.tz_convert("Asia/Seoul").dt.date,
            gross=trades["quantity"].astype(float) * trades["price"].astype(float),
        )
        t["net"] = t["gross"].where(t["side"].astype(str) == "buy", -t["gross"])
        out = t.groupby("day")[["net", "gross"]].sum().reset_index()
    events = book.get("events", as_of=now, lookback=300)
    if not events.empty:
        e = events[(events["stage"] == "exposure")
                   & events["entity_id"].astype(str).str.startswith("session-KR-")].copy()
        if not e.empty:
            import json

            e["scale"] = [float(json.loads(p)["scale"]) if isinstance(p, str) else float(p["scale"])
                          for p in e["payload"]]
            # 세션 run 이름의 날짜 = 그 세션이 **결정한 기준일**(직전 마감). 그 배수는 다음
            # 거래일부터 효력이 있고, 다음 결정이 나올 때까지 유지된다(차단된 세션은 이벤트가
            # 없다 — 적용된 배수가 그대로다). 그래서 **시점 조인**이다: 장부의 각 날에 대해
            # "그날보다 앞선 마지막 결정". 처음엔 '다음 이벤트 날짜' 에 붙여 9/16 의 0.5 가
            # 통째로 빠졌다(9/10 차단 세션처럼 이벤트가 듬성하면 날짜가 밀린다).
            e["decided"] = pd.to_datetime(e["entity_id"].str.slice(11)).dt.date
            decisions = e.sort_values("valid_from").groupby("decided")["scale"].last().sort_index()
            days = sorted(set(out["day"])) if not out.empty else []
            applied = []
            for day in days:
                prior = decisions[decisions.index < day]
                applied.append(float(prior.iloc[-1]) if len(prior) else float("nan"))
            if days:
                out = out.assign(scale=applied)
    return out.sort_values("day") if not out.empty else out


def cost_won(book: Store, now: datetime) -> pd.Series:
    """세션마다 수수료 + 세금(원)."""
    frame = book.get("trades", as_of=now, lookback=300, market="KR",
                     columns=["valid_from", "fee", "tax"])
    if frame.empty:
        return pd.Series(dtype=float)
    frame = frame.assign(day=frame["valid_from"].dt.tz_convert("Asia/Seoul").dt.date)
    return (frame["fee"].fillna(0) + frame["tax"].fillna(0)).groupby(frame["day"]).sum()


if __name__ == "__main__":
    raise SystemExit(main())
