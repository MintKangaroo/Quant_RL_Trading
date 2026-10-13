"""내부자 재료 심화(AR2 계열 후보) — 국장·미장 **같은 정의**의 피처 여섯 개. **탐색 진단용, 등록 전.**

AR(G4 국장 elestock 증감 + G7 미장 Form 4)이 2026-10-13 금고 early 에서 두 시장 다 통과했다. 여기서는 같은 원천을
더 잘게 쓴다. 문헌 근거:

| 피처 | 정의(창은 **접수일 다음 세션**부터 센다) | 근거 |
|---|---|---|
| `opp_buy_60`    | 비일상 내부자의 **장내매수** 금액 합 ÷ ADV20 (60세션) | Cohen·Malloy·Pomorski 2012(기회적 거래만 정보) · Lakonishok·Lee 2001(매수가 정보) |
| `opp_sell_60`   | 비일상 내부자의 **장내매도** 금액 합 ÷ ADV20 (60세션). 미장은 10b5-1 표지 매도 제외 | CMP 2012 · Park·Lim 2023(한국 매도 군집 → 폭락) |
| `buyers_20`     | 20세션 안 장내매수한 **서로 다른** 내부자 수 | Alldredge·Blank 2019(군집 매수) |
| `sellers_20`    | 20세션 안 장내매도한 서로 다른 **비일상** 내부자 수 | Park·Lim 2023 · Alldredge·Blank 2019 |
| `exec_net_60`   | 최고경영진(대표이사·CEO·CFO·회장·사장) 장내 순매수 금액 ÷ ADV20 | Mazza·Ruh 2022(한국, 직급↑ 성과↑) · Wang·Shin·Francis 2012(CFO) |
| `mkt_net_60`    | 모든 내부자의 장내 순매수 금액 ÷ ADV20 — 기계적 변동(증여·선임·스톡옵션·전환…) 제외 | Jeng·Metrick·Zeckhauser 2003 · G4 의 정제판 |

**일상적(routine) 거래** — CMP 2012 의 변형: 그 내부자가 **직전 3년 각각 같은 달에** 장내매매(접수 기준)를 했으면
그 거래는 일상적이다. 3년 이력이 없으면 비일상으로 둔다(CMP 는 표본에서 뺐다 — 우리는 커버리지를 지키려고 남긴다).
국장은 원문이 2021-08~ 이라 **2024-08 전에는 일상 판정이 불가능**하다(전부 비일상).

입력은 `normalize_us`(form4_trades)·`normalize_kr`(insider_reports) 가 같은 모양으로 맞춘다:
``entity_id, filed(date), owner, side(+1 매수/-1 매도), value(금액), exec(bool), planned(bool)``.
피처 계산(`features`)은 시장을 모른다 — 두 시장이 같은 함수를 지난다.
"""
from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

FEATURES = ("opp_buy_60", "opp_sell_60", "buyers_20", "sellers_20", "exec_net_60", "mkt_net_60")
LONG, SHORT, ADV_WINDOW = 60, 20, 20
ROUTINE_YEARS = 3

_US_EXEC = re.compile(r"\b(CEO|CFO|CHIEF\s+EXECUTIVE|CHIEF\s+FINANCIAL|PRESIDENT|CHAIRMAN|CHAIR|PRINCIPAL\s+EXECUTIVE|PRINCIPAL\s+FINANCIAL)\b", re.I)
_US_NOT_EXEC = re.compile(r"\bVICE\s+PRESIDENT\b|\bVP\b|\bVICE\s+CHAIR", re.I)
_KR_EXEC = re.compile(r"대표|CEO|CFO|(?<!부)사장|(?<!부)회장|재무책임|최고재무|최고경영", re.I)
#: 국장 보고자 구분 중 **내부자가 아닌** 것 — 금융기관(K)·연기금 등 전문투자자(Y). 10% 이상 보유라 보고 의무가 생긴
#: 기관이다(국민연금·자산운용사). 이들의 매매는 운용이지 내부 정보가 아니다.
KR_NON_INSIDER_TYPES = frozenset({"K", "Y"})


def normalize_us(trades: pd.DataFrame) -> pd.DataFrame:
    """form4_trades → 공통 모양. 장내 거래(P·S)만, 금액 0 이하는 버린다(G7 과 같은 규칙)."""
    t = trades[trades["trans_code"].isin(["P", "S"])].copy()
    value = t["shares"].astype(float) * t["price"].astype(float)
    title = t["title"].fillna("").astype(str)
    out = pd.DataFrame({
        "entity_id": t["entity_id"].astype(str),
        "filed": pd.to_datetime(t["valid_from"]).dt.tz_convert("America/New_York").dt.date,
        "owner": t["owner_cik"].fillna("").astype(str),
        "side": np.where(t["trans_code"] == "P", 1, -1),
        "value": value,
        "exec": title.str.contains(_US_EXEC) & ~title.str.contains(_US_NOT_EXEC),
        "planned": t["plan_10b5_1"].fillna(0.0).astype(float) == 1.0,
    })
    return out[(out["value"] > 0) & (out["owner"] != "")].reset_index(drop=True)


def normalize_kr(reports: pd.DataFrame, close_on: pd.Series | None = None) -> pd.DataFrame:
    """insider_reports → 공통 모양. 보고사유 범주 ``market``(장내매수·장내매도·시간외)만, 보통주(1)만,
    금융기관·연기금 보고자는 뺀다. 단가가 없으면 ``close_on``((entity_id, filed) → 종가)으로 메운다."""
    r = reports[(reports["category"] == "market") & (reports["security_code"].astype(str) == "1")].copy()
    r = r[~r["reporter_type"].astype(str).isin(KR_NON_INSIDER_TYPES)]
    r = r[r["change"].fillna(0.0) != 0.0]
    filed = pd.to_datetime(r["valid_from"]).dt.tz_convert("Asia/Seoul").dt.date
    price = r["price"].astype(float)
    if close_on is not None and price.isna().any():
        key = pd.MultiIndex.from_arrays([r["entity_id"].astype(str), filed])
        price = price.fillna(pd.Series(close_on.reindex(key).to_numpy(), index=r.index))
    out = pd.DataFrame({
        "entity_id": r["entity_id"].astype(str),
        "filed": filed,
        "owner": r["owner_key"].astype(str),
        "side": np.sign(r["change"].astype(float)).astype(int),
        "value": r["change"].abs().astype(float) * price,
        "exec": r["position"].fillna("").astype(str).str.contains(_KR_EXEC),
        "planned": r["plan_report_date"].fillna("").astype(str).str.match(r"^\d{4}-\d{2}-\d{2}$"),
    })
    return out[out["value"] > 0].reset_index(drop=True)


def routine_flags(trades: pd.DataFrame, *, years: int = ROUTINE_YEARS) -> pd.Series:
    """거래마다 일상적인가. 그 내부자(종목·보고자)가 직전 ``years`` 년 **각각** 같은 달에 장내매매를 했으면 True.
    접수 연·월만 본다 — 그 해 그 달보다 앞선 사실이라 시점 문제가 없다."""
    if trades.empty:
        return pd.Series(dtype=bool)
    ym = pd.DataFrame({
        "k": trades["entity_id"] + "|" + trades["owner"],
        "y": [d.year for d in trades["filed"]],
        "m": [d.month for d in trades["filed"]],
    })
    seen = set(zip(ym["k"], ym["y"], ym["m"], strict=True))
    flags = [all((k, y - j, m) in seen for j in range(1, years + 1)) for k, y, m in zip(ym["k"], ym["y"], ym["m"], strict=True)]
    return pd.Series(flags, index=trades.index, dtype=bool)


def _known_index(filed: pd.Series, sessions: np.ndarray) -> np.ndarray:
    """접수일 → 처음 알 수 있는 세션의 번호(접수일보다 **뒤**의 첫 세션). 접수 시각이 장 마감 뒤(18:00)라서다."""
    days = np.array([np.datetime64(d) for d in filed], dtype="datetime64[D]")
    return np.searchsorted(sessions, days, side="right")


def _window_sum(entities: pd.Index, n: int, ent: np.ndarray, idx: np.ndarray, val: np.ndarray, width: int) -> np.ndarray:
    """(종목 × 세션) 행렬: 세션 i 에 알 수 있게 된 값의 [i-width+1, i] 합."""
    grid = np.zeros((len(entities), n + 1))
    ok = idx < n
    np.add.at(grid, (ent[ok], idx[ok]), val[ok])
    csum = np.cumsum(grid[:, :n], axis=1)
    out = csum.copy()
    out[:, width:] -= csum[:, :-width]
    return out


def _window_distinct(entities: pd.Index, n: int, ent: np.ndarray, own: np.ndarray, idx: np.ndarray, width: int) -> np.ndarray:
    """세션 i 의 [i-width+1, i] 안에 한 번이라도 나온 서로 다른 보고자 수."""
    diff = np.zeros((len(entities), n + 1))
    frame = pd.DataFrame({"e": ent, "o": own, "k": idx}).query("k < @n").drop_duplicates().sort_values(["e", "o", "k"])
    if frame.empty:
        return diff[:, :n]
    for (e, _o), part in frame.groupby(["e", "o"], sort=False):
        start = end = None
        for k in part["k"].to_numpy():
            if end is not None and k <= end:
                end = k + width - 1
                continue
            if start is not None:
                diff[e, start] += 1
                diff[e, min(end + 1, n)] -= 1
            start, end = k, k + width - 1
        diff[e, start] += 1
        diff[e, min(end + 1, n)] -= 1
    return np.cumsum(diff[:, :n], axis=1)


def adv(values: pd.DataFrame) -> pd.DataFrame:
    """세션 × 종목 거래대금 → 세션 i **개장 전** ADV20(직전 20세션 평균, 0 은 결측)."""
    return values.rolling(ADV_WINDOW, min_periods=1).mean().shift(1).replace(0.0, np.nan)


def features(trades: pd.DataFrame, sessions: list[date], adv20: pd.DataFrame) -> pd.DataFrame:
    """공통 모양의 거래 → (entity_id, session, 피처 여섯) 긴 표. ``adv20`` 은 세션 × 종목(개장 전 값).

    값이 전부 0 인 (종목, 세션) 은 싣지 않는다 — 호출자가 0 으로 채운다(6차 규칙: 없는 종목은 순위 중앙)."""
    if trades.empty:
        return pd.DataFrame(columns=["entity_id", "session", *FEATURES])
    t = trades.copy()
    t["routine"] = routine_flags(t)  # 일상 판정은 **전체 이력**으로(세션 창 앞의 거래도 이력이다)
    sess = np.array(sessions, dtype="datetime64[D]")
    n = len(sess)
    t["k"] = _known_index(t["filed"], sess)
    # 첫 세션보다 앞서 접수된 거래는 창에 넣지 않는다 — 넣으면 전부 세션 0 으로 몰린다. 호출자는 워밍업
    # (창 길이 이상)을 앞에 두고 세션을 준다.
    t = t[t["k"] >= 1]
    if t.empty:
        return pd.DataFrame(columns=["entity_id", "session", *FEATURES])
    entities = pd.Index(sorted(t["entity_id"].unique()))
    t["e"] = entities.get_indexer(t["entity_id"])
    owners = pd.Index(sorted(t["owner"].unique()))
    t["o"] = owners.get_indexer(t["owner"])
    buy, sell = t["side"] > 0, t["side"] < 0
    opp = ~t["routine"]
    unplanned = ~t["planned"]

    def wsum(mask: pd.Series, signed: bool = False, width: int = LONG) -> np.ndarray:
        part = t[mask]
        v = (part["value"] * (part["side"] if signed else 1)).to_numpy(float)
        return _window_sum(entities, n, part["e"].to_numpy(), part["k"].to_numpy(), v, width)

    def wdist(mask: pd.Series) -> np.ndarray:
        part = t[mask]
        return _window_distinct(entities, n, part["e"].to_numpy(), part["o"].to_numpy(), part["k"].to_numpy(), SHORT)

    a = adv20.reindex(index=pd.Index(sessions), columns=entities).to_numpy().T  # 종목 × 세션
    with np.errstate(divide="ignore", invalid="ignore"):
        mats = {
            "opp_buy_60": wsum(buy & opp) / a,
            "opp_sell_60": wsum(sell & opp & unplanned) / a,
            "buyers_20": wdist(buy),
            "sellers_20": wdist(sell & opp & unplanned),
            "exec_net_60": wsum(t["exec"], signed=True) / a,
            "mkt_net_60": wsum(pd.Series(True, index=t.index), signed=True) / a,
        }
    for name in mats:
        mats[name] = np.where(np.isfinite(mats[name]), mats[name], 0.0)
    nonzero = np.zeros((len(entities), n), dtype=bool)
    for m in mats.values():
        nonzero |= m != 0
    ei, si = np.nonzero(nonzero)
    out = pd.DataFrame({name: m[ei, si] for name, m in mats.items()})
    out.insert(0, "session", np.array(sessions, dtype=object)[si])
    out.insert(0, "entity_id", entities.to_numpy()[ei])
    return out[["entity_id", "session", *FEATURES]].reset_index(drop=True)
