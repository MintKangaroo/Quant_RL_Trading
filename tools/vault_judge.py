"""홀드아웃 금고 개봉 판정부 — 사전등록 시행 AQ·AR·AS·BD 를 **한 번의 개봉**으로 심사한다.

    .venv/bin/python tools/vault_judge.py --plan                          # 개봉 당일 실행 순서만 인쇄(자료를 읽지 않는다)
    .venv/bin/python tools/vault_judge.py --bake                          # ① 금고 창 패널 굽기
    .venv/bin/python tools/vault_judge.py --judge --trials AQ,AR,AS,BD    # ② 판정 표·기준별 ○×
    .venv/bin/python tools/vault_judge.py --judge --trials AQ,AR,AS,BD --save   # + research_trials·holdout_access 기록

## 금고

- 창 **2026-07-01 ~ 2026-11-13**, 개봉은 **2026-11-23 이후 한 번**(self-improvement.md §1① · modelops-ranker.md ③).
- `--bake` 와 `--judge` 는 그 전에는 **돌지 않는다**(날짜 잠금, `OPEN_FROM`). 6차 측정 도구의 `MEASURE_FROM` 과 같은 잠금이다.
  잠금이 걸린 동안 허용되는 것은 `--plan` 뿐이고, `--plan` 은 창고도 캐시도 읽지 않는다.
- 판정 대상 넷의 등록 문서:
  AQ `docs/protocols/breadth72-forward-2026-09.md` · AR `docs/protocols/insider-forward-2026-09.md` ·
  AS `docs/protocols/raw-feature-vault-2026-09.md` · BD `docs/protocols/us-regime-switch-vault-2026-11.md`.

## 모델은 다시 학습하지 않는다

`tools/trial_insider_forward.py --freeze` 가 2026-06-30 까지로 학습해 `data/_diag/vault-reviews/<이름>.txt`
(lightgbm `model_to_string`)로 얼렸고, 해시 16자리를 등록 문서 "모델 해시" 절에 적었다. 판정부는 그 파일의 sha256 을
**다시 계산해 문서와 대조**하고, 다르면 거부한다(얼린 뒤 모델이 바뀌었다는 뜻이다). AS 의 대조는 AR 의 대조 모델
(`AR-control-s0~2`)이다 — 등록대로 다시 굽지 않는다.

## 금고 창 패널은 어디서 무엇을 읽나 (`--bake`)

| 무엇 | 쓰는 시행 | 어디서 | 어디로 |
|---|---|---|---|
| 국장 Analyst 점수 6종 + 실전 `ranker` · 타깃 h5 · 거래가능 명단 | AQ·AR·AS | 창고 `signals`·가격(`bake_long_panel`) | `data/_diag/vault-window/` |
| 미장 Analyst 점수 6종 · 타깃 | AR·AS·BD | 창고(`backfill_ic_history --market US`) | `data/_diag/vault-window/ic-history-us/` |
| 원피처 35개(국장·미장) | AS | Analyst 내부 피처(`diagnose_ic cache-extra`) | `data/_diag/vault-window/raw-{KR,US}/` |
| 내부자 묶음 G4·G7 | AR | 창고 DART·Form 4(`ranker_sources.build`) | `data/_diag/ranker-sources/` (월 조각) |
| 미장 시총·가격·S&P500 종가 | BD | 창고 `market_stats`·`prices`·`indices` | 판정 때 직접 읽는다(굽지 않음) |

무거운 둘(미장 점수·원피처)은 `--bake` 가 직접 돌리지 않고 **선행 명령을 인쇄하고 멈춘다** — 몇 시간짜리 작업이라
로그를 남기며 따로 돌려야 한다(memory `training-shares-no-machine`).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from quant_rl_trading.store.prices import read_prices  # noqa: E402
from tools.trial_insider_forward import INSIDER, PROTOCOLS, SEEDS  # noqa: E402
from tools.trial_insider_forward import OUT as MODELS  # noqa: E402
from tools.trial_overlay import ANN, MAX_MOVE, ONE_WAY_COST, metrics  # noqa: E402
from tools.trial_pooled import FEATS, daily_ic  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_kit import FEATS as LOOP_FEATS  # noqa: E402
from tools.trial_ranker_kit import judge_panel, mark, market_data, portfolio, record  # noqa: E402
from tools.trial_ranker_sources import GROUPS, attach, build_panel  # noqa: E402
from tools.trial_selection_ranker import capped_cap_weights  # noqa: E402
from tools.trial_selection_smoothing import pick_mult  # noqa: E402
from tools.trial_us_index_minus_losers import top_caps  # noqa: E402
from tools.trial_us_index_tilt import run as tilt_run  # noqa: E402
from tools.trial_us_kit import book as us_book  # noqa: E402
from tools.trial_us_kit import m1_scores, spx_regime, us_panel  # noqa: E402

#: 금고 창과 개봉일 — 등록 문서 넷이 같은 값을 쓴다. 코드에서 고치는 것은 사후 기준 변경이다.
VAULT_START, VAULT_END = date(2026, 7, 1), date(2026, 11, 13)
OPEN_FROM = date(2026, 11, 23)
#: 금고 창 패널 캐시. `data/_diag` 는 개봉 전 시행들의 입력이라 덮지 않는다(bake_long_panel 의 같은 판단).
VAULT = Path("data/_diag/vault-window")
US_WORK = VAULT / "ic-history-us"
RAW_DIRS = {"KR": VAULT / "raw-KR", "US": VAULT / "raw-US"}
TRIALS = ("AQ", "AR", "AS", "BD")
FAMILY = {"AQ": "selection", "AR": "ranker", "AS": "ranker", "BD": "selection"}
ENTITY = {"AQ": "breadth72-forward-2026-09:AQ", "AR": "insider-forward-2026-09:AR",
          "AS": "raw-feature-vault-2026-09:AS", "BD": "us-regime-switch-vault-2026-11:BD"}
#: 금고 창에 구울 국장 Analyst — 여섯에 **실전 랭커**(AQ 원천 ①)를 더한다.
KR_ANALYSTS = ("chart", "event", "flow_kr", "fundamental", "regime", "risk", "ranker")
#: 원피처(AS)를 굽는 Analyst — 시장마다 flow 가 다르다.
RAW_ANALYSTS = {"KR": ("chart", "event", "flow_kr", "fundamental", "regime", "risk"),
                "US": ("chart", "event", "flow_us", "fundamental", "regime", "risk")}
#: 등록 문서의 포트 규칙 — AQ·AR·AS 는 편도 0.41%(`ONE_WAY_COST`), BD 는 0.25%(`accounting.fee_us`).
EVERY = 10
N_BASE, N_WIDE = 24, 72
US_EXIT_MULT = 3            # AR·AS 등록: 완충 3N
BD_EXIT_MULT, BD_WIDE, BD_CAP = 2, 500, 0.10   # BD 등록: 완충 48 · 시총 상위 500 · 종목 상한 10%
BD_CRISIS_FLOOR = -0.03
BD_MIN_BEAR = 10
HASH_LINE = re.compile(r"^- `([A-Za-z0-9-]+)` ([0-9a-f]{16})\s*$", re.M)


def _today() -> date:
    """오늘(UTC). 날짜 잠금이 여기 하나만 본다 — 테스트가 이 함수를 바꿔 끼운다."""
    return datetime.now(UTC).date()  # invariant-allow: wallclock — 사전등록 시점 잠금


def locked(what: str) -> bool:
    """금고 개봉 전이면 True 를 돌려주고 이유를 인쇄한다."""
    today = _today()
    if today >= OPEN_FROM:
        return False
    print(f"{what} 는 금고 개봉({OPEN_FROM}) 이후에만 돈다 — 오늘은 {today}. 사전등록 '중간 들여다보기 금지'"
          f"(self-improvement.md §1①). 지금 허용되는 것은 --plan 뿐이다.", flush=True)
    return True


# --------------------------------------------------------------------------- 얼린 모델


def frozen_hashes(trial: str) -> dict[str, str]:
    """등록 문서 "모델 해시" 절의 {이름: 해시 16자리}."""
    body = PROTOCOLS[trial].read_text()
    head = body.index("## 모델 해시")
    found = dict(HASH_LINE.findall(body[head:]))
    if not found:
        raise SystemExit(f"{PROTOCOLS[trial]} 에 모델 해시가 없다 — --freeze 가 먼저다")
    return found


def booster(name: str, expected: dict[str, str]) -> Any:
    """얼린 모델을 읽고 **해시를 다시 계산해 문서와 대조**한다. 다르면 판정을 거부한다."""
    if name not in expected:
        raise SystemExit(f"등록 문서에 {name} 의 해시가 없다 — 판정 거부")
    path = MODELS / f"{name}.txt"
    if not path.exists():
        raise SystemExit(f"{path} 가 없다 — 얼린 모델을 다시 학습하지 않는다(등록 위반). 파일을 복구해야 한다")
    text = path.read_text()
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    if digest != expected[name]:
        raise SystemExit(f"모델 해시 불일치 {name}: 문서 {expected[name]} ≠ 파일 {digest} — 판정 거부"
                         " (얼린 뒤 모델이 바뀌었다)")
    import lightgbm as lgb
    print(f"  {name} 해시 {digest} 대조 ○", flush=True)
    return lgb.Booster(model_str=text)


def predict(model: Any, frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """얼린 모델의 예측(entity_id·session·pred). 피처 **개수**가 학습과 다르면 거부한다 —
    numpy 로 학습했으므로 모델 안에 열 이름이 없다(순서는 등록 문서가 정한 순서 그대로 만든다)."""
    if model.num_feature() != len(cols):
        raise SystemExit(f"피처 수 불일치: 모델 {model.num_feature()} ≠ 패널 {len(cols)} — 판정 거부")
    out = frame[["entity_id", "session"]].copy()
    out["pred"] = model.predict(frame[cols].to_numpy(np.float32))
    return out


# --------------------------------------------------------------------------- 금고 창 패널


def kr_loop_panel() -> tuple[pd.DataFrame, list[date]]:
    """AQ 의 국장 확장 패널(금고 창) — 규칙은 `trial_ranker_kit.judge_panel` 그대로, 캐시와 창만 금고 것이다."""
    return judge_panel(cache=VAULT, start=VAULT_START, end=VAULT_END)


def ranker_signals() -> pd.DataFrame:
    """AQ 원천 ① — 실전 랭커가 금고 창에 매일 적은 점수(창고 `signals.ranker`, bake 단계가 옮겨 둔 것)."""
    frame = pd.read_pickle(VAULT / "scores-ranker-KR.pkl")  # invariant-allow: data-access — 작업 캐시
    frame["session"] = pd.to_datetime(frame["session"]).dt.date
    frame = frame[(frame["session"] >= VAULT_START) & (frame["session"] <= VAULT_END)]
    return frame.rename(columns={"score": "pred"})[["entity_id", "session", "pred"]]


def pooled_panels(store: Store, *, insider: bool = False,
                  raw: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """AR·AS 의 국장·미장 점수 패널(금고 창) — `trial_insider_forward.freeze_ar/freeze_as` 와 **같은 순서**로 만든다.

    반환: (국장, 미장, 원피처 열). 원피처 열 순서는 freeze_as 의 순서(국장 캐시 먼저, 미장에만 있는 열이 뒤)다 —
    얼린 모델은 numpy 로 학습해 열 이름을 모르므로 이 순서가 계약이다.
    """
    from tools.trial_pooled import load_kr, load_us

    _, kr = load_kr(cache=VAULT)
    kr["session"] = pd.to_datetime(kr["session"]).dt.date
    kr = kr[(kr["session"] >= VAULT_START) & (kr["session"] <= VAULT_END)]
    us = load_us(work=US_WORK, holdout=None)
    us = us[(us["session"] >= VAULT_START) & (us["session"] <= VAULT_END)]
    kr = rank_gauss(kr, FEATS + ["target"])
    us = rank_gauss(us, FEATS + ["target"])
    kr["is_us"], us["is_us"] = 0.0, 1.0
    if insider:
        for group in INSIDER:
            cols = list(GROUPS[group])
            kr = attach(kr, build_panel(store, group, "KR", sorted(kr["session"].unique())), cols)
            us = attach(us, build_panel(store, group, "US", sorted(us["session"].unique())), cols)
    raw_cols: list[str] = []
    if raw:
        from tools.trial_raw_feature_ranker import raw_features
        frames = []
        for market, frame in (("KR", kr), ("US", us)):
            feats, cols = raw_features(market, keys=frame[["entity_id", "session"]].drop_duplicates(),
                                       base=RAW_DIRS[market])
            raw_cols += [c for c in cols if c not in raw_cols]
            frames.append(attach(frame, feats, cols))
            del feats
        for frame in frames:  # 그 시장에 없는 원피처는 0(순위 중앙) — W 등록 규칙
            for col in raw_cols:
                if col not in frame.columns:
                    frame[col] = 0.0
        kr, us = frames
    return kr, us, raw_cols


def us_returns(close: pd.DataFrame) -> pd.DataFrame:
    """t+1→t+2 미장 수익(|일수익|>50% 제외) — `trial_us_kit._walk_and_cache` 와 같은 정의.

    (`trial_overlay._prices` 는 국장 전용이라 쓰지 않는다 — 가격은 `us_panel` 이 이미 넓은 표로 돌려준다.)
    """
    ret = close.shift(-2) / close.shift(-1) - 1.0
    return ret.where(ret.abs() <= MAX_MOVE)


def us_filtered_caps(store: Store) -> tuple[pd.DataFrame, pd.DataFrame]:
    """BD 의 시총 표와 순위 — 증권 종류(`universe.instrument_types_us`)·동전주 하한(`min_price_us`·`min_market_cap_us`)을
    지난 뒤의 세션×종목 시총. 실전 필터와 같은 config 를 읽는다(불변식 10). 시행 AT·`trial_largecap_ranker` 와 같은 경로다."""
    now = datetime.combine(VAULT_END, time(23), tzinfo=UTC)
    span = (VAULT_END - VAULT_START).days + 60
    cfg_at = datetime.now(UTC)  # invariant-allow: wallclock — 설정 현행값·최신 증권 분류(AT 와 같은 한계)
    caps = top_caps(store, now, span)
    kinds = store.get("instrument_types", as_of=cfg_at, lookback=10, market="US",
                      columns=["entity_id", "instrument", "test_issue"])
    types = list(store.config("universe.instrument_types_us", as_of=cfg_at))
    ok = set(kinds[kinds["instrument"].isin(types) & ~kinds["test_issue"].astype(bool)]["entity_id"])
    caps = caps[[c for c in caps.columns if c in ok]]
    caps = caps[(caps.index >= VAULT_START) & (caps.index <= VAULT_END)]
    min_price = float(store.config("universe.min_price_us", as_of=cfg_at))
    min_cap = float(store.config("universe.min_market_cap_us", as_of=cfg_at))
    prices = read_prices(store, as_of=now, lookback=span + 40, columns=["close"], adjusted=False, market="US",
                         entity=list(caps.columns))
    prices["day"] = pd.to_datetime(prices["valid_from"]).dt.date
    px = prices.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    px = px.ffill(limit=5).reindex(index=caps.index, columns=caps.columns)
    del prices
    caps = caps.where((px >= min_price) & (caps >= min_cap))
    return caps, caps.rank(axis=1, ascending=False)


# --------------------------------------------------------------------------- 지표


def stats(daily: pd.Series, bench: pd.Series, extra: dict[str, float] | None = None) -> dict[str, float]:
    """등록 문서가 쓰는 지표 — 연수익·MDD·회전·β·IR 과 **창을 세션 수 절반으로 가른 두 구간**(AS 기준 ③).

    `trial_ranker_kit.summarize` 를 쓰지 않는 이유: 그쪽은 박스/급등을 2024-12-31 로 가르는데 금고 창은
    전부 그 뒤라 한쪽이 빈다.
    """
    daily = daily.dropna()
    m = metrics(daily, bench.reindex(daily.index).fillna(0.0))
    half = len(daily) // 2
    m["h1"] = float(daily.iloc[:half].mean() * ANN) if half else float("nan")
    m["h2"] = float(daily.iloc[half:].mean() * ANN) if half else float("nan")
    return {**m, **(extra or {})}


def _ic(pred: pd.DataFrame, panel: pd.DataFrame) -> float:
    """h5 IC(일별 Spearman 평균) — 패널의 `target`(rank-gauss h5) 대비."""
    merged = pred.merge(panel[["entity_id", "session", "target"]], on=["entity_id", "session"], how="inner")
    series = daily_ic(merged, "pred")
    return float(series.mean())


def _mean(rows: list[dict[str, float]], key: str) -> float:
    return float(np.mean([r[key] for r in rows]))


# --------------------------------------------------------------------------- 판정 (순수 함수 — 테스트가 여기를 잡는다)


def judge_aq(res: dict[str, dict[str, dict[str, float]]]) -> tuple[list[str], str]:
    """AQ 기준 넷 (`breadth72-forward-2026-09.md`). res[원천]["V0"|"V1"] = 지표."""
    srcs = list(res)
    a0 = [res[s]["V0"]["ann"] for s in srcs]
    a1 = [res[s]["V1"]["ann"] for s in srcs]
    m0, m1 = float(np.mean(a0)), float(np.mean(a1))
    s0, s1 = float(np.std(a0, ddof=1)), float(np.std(a1, ddof=1))
    wins = sum(x1 > x0 for x0, x1 in zip(a0, a1, strict=True))
    mdd0, mdd1 = _mean([res[s]["V0"] for s in srcs], "mdd"), _mean([res[s]["V1"] for s in srcs], "mdd")
    t0, t1 = _mean([res[s]["V0"] for s in srcs], "turn"), _mean([res[s]["V1"] for s in srcs], "turn")
    c = (m1 >= m0 + 0.01, wins >= 3, s1 <= s0 / 2, mdd1 >= mdd0 - 0.02 and t1 <= t0)
    lines = ["| 원천 | V0′ 상위24 | V1′ 상위72 | 차이 |", "|---|---|---|---|"]
    lines += [f"| {s} | {res[s]['V0']['ann']:+.1%} | {res[s]['V1']['ann']:+.1%} | "
              f"{res[s]['V1']['ann'] - res[s]['V0']['ann']:+.1%}p |" for s in srcs]
    lines += [
        f"| 평균 | {m0:+.1%} | {m1:+.1%} | {m1 - m0:+.1%}p |",
        f"| 흩어짐 | {s0:.1%}p | {s1:.1%}p | |",
        f"①평균 {m1 - m0:+.1%}p (≥ +1%p) {mark(c[0])} · ②원천 {wins}/4 {mark(c[1])} · "
        f"③흩어짐 {s1:.1%}p vs V0′ 절반 {s0 / 2:.1%}p {mark(c[2])} · "
        f"④MDD {mdd1:.1%} vs {mdd0:.1%} · 회전 {t1:.1f} vs {t0:.1f} {mark(c[3])}",
    ]
    verdict = "채택 V1′(상위 72 · 완충 216 · C10)" if all(c) else "기각 — V0′(상위 24) 유지"
    return lines, verdict


def judge_ar(res: dict[str, dict[str, Any]]) -> tuple[list[str], str]:
    """AR 기준 셋, **두 시장 각각** (`insider-forward-2026-09.md`). res[시장][arm] = {"seeds": [지표…], "ic": ΔIC 용 IC}."""
    lines = ["| 시장 | arm | 연수익(시드 평균) | 시드별 | MDD | 회전 | IC |", "|---|---|---|---|---|---|---|"]
    passed = {}
    for market in res:
        arms = res[market]
        for arm in ("control", "treat"):
            seeds: list[dict[str, float]] = arms[arm]["seeds"]
            lines.append(f"| {market} | {arm} | {_mean(seeds, 'ann'):+.1%} | "
                         + " / ".join(f"{r['ann']:+.1%}" for r in seeds)
                         + f" | {_mean(seeds, 'mdd'):.1%} | {_mean(seeds, 'turn'):.1f} | {arms[arm]['ic']:+.4f} |")
        ct: list[dict[str, float]] = arms["control"]["seeds"]
        tr: list[dict[str, float]] = arms["treat"]["seeds"]
        wins = sum(t["ann"] > c["ann"] for t, c in zip(tr, ct, strict=True))
        dic = float(arms["treat"]["ic"]) - float(arms["control"]["ic"])
        c = (_mean(tr, "ann") > _mean(ct, "ann"), wins == len(tr), dic >= -0.01)
        passed[market] = all(c)
        lines.append(f"{market}: ①평균 {_mean(tr, 'ann') - _mean(ct, 'ann'):+.1%}p (> 0) {mark(c[0])} · "
                     f"②시드 {wins}/{len(tr)} {mark(c[1])} · ③ΔIC {dic:+.4f} (≥ −0.01) {mark(c[2])} → "
                     f"{'통과' if all(c) else '탈락'}")
    good = [m for m, ok in passed.items() if ok]
    if len(good) == len(passed) and good:
        verdict = "채택 후보 — 두 시장 다 통과(랭커 피처 추가 → 재학습 게이트 → shadow)"
    elif good:
        verdict = f"한 시장만 통과({','.join(good)}) — 그 시장만 후보, 사용자 결정"
    else:
        verdict = "기각 — 내부자 재료를 랭커에 넣지 않는다"
    return lines, verdict


def judge_as(kr: dict[str, Any], us_dic: float) -> tuple[list[str], str]:
    """AS 기준 다섯 (`raw-feature-vault-2026-09.md`, 랭커 계열 판정 기준 틀). kr[arm] = {"seeds": […], "ic": IC}."""
    ct: list[dict[str, float]] = kr["control"]["seeds"]
    tr: list[dict[str, float]] = kr["treat"]["seeds"]
    wins = sum(t["ann"] > c["ann"] for t, c in zip(tr, ct, strict=True))
    dic = float(kr["treat"]["ic"]) - float(kr["control"]["ic"])
    halves = [(_mean(tr, k), _mean(ct, k)) for k in ("h1", "h2")]
    c = (
        _mean(tr, "ann") >= _mean(ct, "ann") + 0.02,
        wins == len(tr),
        all(t >= b - 0.01 for t, b in halves),
        dic >= -0.01 and us_dic >= -0.005,
        _mean(tr, "mdd") >= _mean(ct, "mdd") - 0.02 and _mean(tr, "turn") <= _mean(ct, "turn") * 1.2,
    )
    lines = ["| arm | 연수익(시드 평균) | 시드별 | 전반 | 후반 | MDD | 회전 | IC |", "|---|---|---|---|---|---|---|---|"]
    for arm, rows in (("control(Analyst 점수)", ct), ("treat(원피처 35)", tr)):
        ic = kr["control"]["ic"] if rows is ct else kr["treat"]["ic"]
        lines.append(f"| {arm} | {_mean(rows, 'ann'):+.1%} | " + " / ".join(f"{r['ann']:+.1%}" for r in rows)
                     + f" | {_mean(rows, 'h1'):+.1%} | {_mean(rows, 'h2'):+.1%} | {_mean(rows, 'mdd'):.1%} | "
                       f"{_mean(rows, 'turn'):.1f} | {float(ic):+.4f} |")
    lines += [
        f"①국장 평균 {_mean(tr, 'ann') - _mean(ct, 'ann'):+.1%}p (≥ +2%p) {mark(c[0])} · ②시드 {wins}/{len(tr)} {mark(c[1])} · "
        f"③두 구간 {halves[0][0] - halves[0][1]:+.1%}p / {halves[1][0] - halves[1][1]:+.1%}p (≥ −1%p) {mark(c[2])}",
        f"④ΔIC 국장 {dic:+.4f} (≥ −0.01) · 미장 {us_dic:+.4f} (≥ −0.005) {mark(c[3])} · "
        f"⑤MDD {_mean(tr, 'mdd'):.1%} vs {_mean(ct, 'mdd'):.1%} · 회전 {_mean(tr, 'turn'):.1f} vs "
        f"{_mean(ct, 'turn') * 1.2:.1f} {mark(c[4])}",
    ]
    verdict = "채택 후보 — 원피처 랭커(랭커 입력 교체 → 재학습 게이트 → shadow)" if all(c) else "기각 — Analyst 점수 입력 유지"
    return lines, verdict


def judge_bd(res: dict[str, Any], bear_sessions: int) -> tuple[list[str], str]:
    """BD 기준 셋 + 보류 조건 (`us-regime-switch-vault-2026-11.md`). res = {"X0": 지표, "switch": {"seeds": […]}}."""
    lines = [f"금고 창 bear 세션 {bear_sessions}개 (보류 문턱 {BD_MIN_BEAR})"]
    if bear_sessions < BD_MIN_BEAR:
        lines.append(f"판정: 보류 — bear 세션 {bear_sessions} < {BD_MIN_BEAR}, 가설을 잴 거리가 없다(시행 미소진)")
        return lines, f"보류 — bear 세션 {bear_sessions} < {BD_MIN_BEAR}"
    x0 = res["X0"]
    seeds: list[dict[str, float]] = res["switch"]["seeds"]
    wins = sum(r["ann"] > x0["ann"] for r in seeds)
    c = (_mean(seeds, "ann") >= x0["ann"] + 0.01, wins == len(seeds), _mean(seeds, "mdd") >= x0["mdd"] - 0.02)
    lines += ["| 구성 | 연수익 | 시드별 | MDD | 회전 | IR(SPY) |", "|---|---|---|---|---|---|",
              f"| X0(지수 대용) | {x0['ann']:+.1%} | | {x0['mdd']:.1%} | {x0.get('turn', float('nan')):.1f} | "
              f"{x0.get('ir', float('nan')):+.2f} |",
              f"| 전환(bear→상위24) | {_mean(seeds, 'ann'):+.1%} | " + " / ".join(f"{r['ann']:+.1%}" for r in seeds)
              + f" | {_mean(seeds, 'mdd'):.1%} | {_mean(seeds, 'turn'):.1f} | {_mean(seeds, 'ir'):+.2f} |",
              f"①평균 {_mean(seeds, 'ann') - x0['ann']:+.1%}p (≥ +1%p) {mark(c[0])} · ②시드 {wins}/{len(seeds)} {mark(c[1])} · "
              f"③MDD {_mean(seeds, 'mdd'):.1%} vs {x0['mdd']:.1%} (−2%p 까지) {mark(c[2])}"]
    verdict = "채택 — 미장 국면 전환(bear 면 상위 24)" if all(c) else "기각 — X0(지수 대용) 유지"
    return lines, verdict


# --------------------------------------------------------------------------- BD 의 전환 장부


def switch_book(states: pd.Series, score: pd.DataFrame, caps: pd.DataFrame, ranks: pd.DataFrame,
                ret: pd.DataFrame, cost: float, *, every: int = EVERY) -> tuple[pd.Series, dict[str, float]]:
    """BD — bear 면 상위 24 동일가중(완충 48), 그 밖이면 시총가중 상위 500(상한 10%). 국면이 바뀌는 날 전환(비용 포함).

    비중 규칙은 기존 부품을 그대로 쓴다(`pick_mult` · `capped_cap_weights`). 루프를 새로 쓰는 이유는 하나뿐이다 —
    `trial_us_kit.book` 과 `trial_us_index_tilt.run` 은 **각자 한 가지 비중 규칙만** 들고 있어서, 한 장부가
    둘 사이를 오가는 모양을 표현할 수 없다.
    """
    held: list[str] = []
    prev: pd.Series | None = None
    last: str | None = None
    out: dict[date, float] = {}
    turns, switches, bear_days = [], 0, 0
    days = [d for d in score.index if d in ret.index and d in caps.index]
    for i, day in enumerate(days):
        state = str(states.get(day, "unknown"))
        bear_days += state == "bear"
        if prev is None or i % every == 0 or state != last:
            alive = caps.loc[day].dropna()
            if state == "bear":
                row = score.loc[day].dropna()
                row = row[row.index.isin(alive.index)]     # 동전주·증권 종류 필터를 지난 종목만
                if row.empty:
                    continue
                held = pick_mult(held, row.sort_values(ascending=False).index, N_BASE, BD_EXIT_MULT)
                w = pd.Series(1.0 / len(held), index=held)
            else:
                wide = ranks.loc[day][ranks.loc[day] <= BD_WIDE].index
                cap = alive.reindex(wide).dropna()
                if cap.empty:
                    continue
                w = capped_cap_weights(cap, BD_CAP)
                held = []
            if last is not None and state != last:
                switches += 1
            last = state
        else:
            w = prev
        dr = ret.loc[day].reindex(w.index).fillna(0.0)
        turn = float(w.subtract(prev, fill_value=0.0).abs().sum()) if prev is not None else float(w.sum())
        out[day] = float((w * dr).sum() - cost * turn)
        turns.append(turn)
        drifted = w * (1 + dr)
        total = 1.0 - float(w.sum()) + float(drifted.sum())
        prev = drifted / total if total > 0 else w
    return pd.Series(out).sort_index(), {"turn": float(np.mean(turns) * ANN), "switches": float(switches),
                                         "bear": float(bear_days)}


# --------------------------------------------------------------------------- 시행별 판정


def run_aq(store: Store) -> tuple[list[str], str]:
    expected = frozen_hashes("AQ")
    panel, sessions = kr_loop_panel()
    ret, bench, trad = market_data(store, sessions, cache=VAULT)
    sources = {"실전 랭커": ranker_signals()}
    for seed in SEEDS:
        sources[f"loop s{seed}"] = predict(booster(f"AQ-loop-s{seed}", expected), panel, list(LOOP_FEATS))
    res: dict[str, dict[str, dict[str, float]]] = {}
    for name, pred in sources.items():
        res[name] = {}
        for variant, n in (("V0", N_BASE), ("V1", N_WIDE)):
            def fixed_n(_day: date, _row: pd.Series, n: int = n) -> int:
                return n
            daily, extra = portfolio(pred, ret, trad, n_of=fixed_n, every=EVERY)
            res[name][variant] = stats(daily, bench, extra)
        print(f"  {name}: V0 {res[name]['V0']['ann']:+.1%} · V1 {res[name]['V1']['ann']:+.1%}", flush=True)
    return judge_aq(res)


def _ar_books(store: Store, kr: pd.DataFrame, us: pd.DataFrame, arms: dict[str, list[str]],
              models: dict[str, str], expected: dict[str, str]) -> dict[str, dict[str, Any]]:
    """arm × 시드로 두 시장 장부·IC. `models[arm]` = 모델 이름 틀(`{arm}-s{seed}` 를 만드는 접두어)."""
    sessions_kr = sorted(kr["session"].unique())
    kr_ret, kr_bench, trad = market_data(store, sessions_kr, cache=VAULT)
    _, _, us_close, us_bench_close = us_panel(store, start=VAULT_START, end=VAULT_END, work_dirs=(US_WORK,))
    us_ret = us_returns(us_close)
    us_bench = us_bench_close.shift(-2) / us_bench_close.shift(-1) - 1.0
    out: dict[str, dict[str, Any]] = {"KR": {}, "US": {}}
    for arm, cols in arms.items():
        rows: dict[str, list[dict[str, float]]] = {"KR": [], "US": []}
        ics: dict[str, list[float]] = {"KR": [], "US": []}
        for seed in SEEDS:
            model = booster(f"{models[arm]}-s{seed}", expected)
            for market, panel, ret, bench in (("KR", kr, kr_ret, kr_bench), ("US", us, us_ret, us_bench)):
                pred = predict(model, panel, cols)
                if market == "KR":
                    daily, extra = portfolio(pred, ret, trad, every=EVERY)
                else:
                    wide = pred.pivot_table(index="session", columns="entity_id", values="pred").sort_index()
                    daily, extra = us_book(wide.ewm(span=5).mean(), ret, ONE_WAY_COST, n=N_BASE,
                                           exit_mult=US_EXIT_MULT, every=EVERY)
                rows[market].append(stats(daily, bench, extra))
                ics[market].append(_ic(pred, panel))
        for market in ("KR", "US"):
            out[market][arm] = {"seeds": rows[market], "ic": float(np.mean(ics[market]))}
            print(f"  {arm} {market}: 연 {_mean(rows[market], 'ann'):+.1%} · IC {np.mean(ics[market]):+.4f}", flush=True)
    return out


def run_ar(store: Store) -> tuple[list[str], str]:
    expected = frozen_hashes("AR")
    kr, us, _ = pooled_panels(store, insider=True)
    control = FEATS + ["is_us"]
    treat = control + [c for g in INSIDER for c in GROUPS[g]]
    res = _ar_books(store, kr, us, {"control": control, "treat": treat},
                    {"control": "AR-control", "treat": "AR-treat"}, expected)
    return judge_ar(res)


def run_as(store: Store) -> tuple[list[str], str]:
    """AS — 대조는 AR 의 대조 모델(같은 규격, 등록대로 다시 굽지 않는다), 처리는 원피처 35 + is_us."""
    expected = {**frozen_hashes("AR"), **frozen_hashes("AS")}
    kr, us, raw_cols = pooled_panels(store, raw=True)
    arms = {"control": FEATS + ["is_us"], "treat": raw_cols + ["is_us"]}
    res = _ar_books(store, kr, us, arms, {"control": "AR-control", "treat": "AS-treat"}, expected)
    us_dic = float(res["US"]["treat"]["ic"]) - float(res["US"]["control"]["ic"])
    lines, verdict = judge_as(res["KR"], us_dic)
    lines.append(f"기록(기준 아님) 미장 연수익 처리 {_mean(res['US']['treat']['seeds'], 'ann'):+.1%} 대 "
                 f"대조 {_mean(res['US']['control']['seeds'], 'ann'):+.1%}")
    return lines, verdict


def run_bd(store: Store) -> tuple[list[str], str]:
    expected = frozen_hashes("BD")
    panel, sessions, close, bench_close = us_panel(store, start=VAULT_START, end=VAULT_END, work_dirs=(US_WORK,))
    ret = us_returns(close)
    bench = bench_close.shift(-2) / bench_close.shift(-1) - 1.0
    caps, ranks = us_filtered_caps(store)
    cost = float(store.config("accounting.fee_us", as_of=datetime.now(UTC)))  # invariant-allow: wallclock — 설정 현행값
    states = spx_regime(store, sessions, BD_CRISIS_FLOOR, before=True)   # 개장 전 국면(등록)
    from tools.trial_us_index_minus_losers import FEATS as US_FEATS
    res: dict[str, Any] = {"switch": {"seeds": []}}
    x0_done = False
    counts = states.reindex(sessions).value_counts().to_dict()
    for seed in SEEDS:
        pred = predict(booster(f"BD-loop-s{seed}", expected), panel, list(US_FEATS))
        frame = panel[["entity_id", "session", "fund_raw", "has_fund"]].merge(pred, on=["entity_id", "session"])
        score = m1_scores(frame)                                     # AT M1 합성
        daily, extra = switch_book(states, score, caps, ranks, ret, cost)
        res["switch"]["seeds"].append(stats(daily, bench, extra))
        if not x0_done:                                              # X0 는 점수를 안 쓰므로 한 번만
            x0_daily, x0_extra = tilt_run("X0", caps, ranks, score, ret, cost)
            res["X0"] = stats(x0_daily, bench, x0_extra)
            x0_done = True
        print(f"  s{seed}: 전환 연 {res['switch']['seeds'][-1]['ann']:+.1%} · 전환 횟수 {extra['switches']:.0f}", flush=True)
    # 보류 조건은 **실제로 거래된** bear 세션으로 센다(장부가 도는 날이 패널 세션보다 적을 수 있다).
    bear = int(res["switch"]["seeds"][0]["bear"])
    lines, verdict = judge_bd(res, bear)
    lines.append("기록(기준 아님) 패널 국면별 세션: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if "X0" in res:
        lines.append(f"기록 SPY 대비 IR: 전환 {_mean(res['switch']['seeds'], 'ir'):+.2f} · X0 {res['X0']['ir']:+.2f}")
    return lines, verdict


RUNNERS = {"AQ": run_aq, "AR": run_ar, "AS": run_as, "BD": run_bd}


# --------------------------------------------------------------------------- 기록


def record_verdicts(store: Store, results: list[tuple[str, str, list[str]]], *, save: bool) -> int:
    """판정을 `research_trials` 에 1행씩, 금고 개봉을 `holdout_access` 에 1행 적는다. ``save`` 가 아니면 아무것도 안 적는다."""
    if not save:
        print("\n--save 가 없다 — 창고에 아무것도 적지 않았다(시행 미소진).", flush=True)
        return 0
    for trial, verdict, lines in results:
        digest = hashlib.sha256(PROTOCOLS[trial].read_bytes()).hexdigest()[:16]
        record(store, entity=ENTITY[trial], source="vault_judge", family=FAMILY[trial], digest=digest,
               verdict=verdict, lines=lines[-3:], market="US" if trial == "BD" else "KR", run_tag=trial)
    now = datetime.now(UTC)  # invariant-allow: wallclock — 개봉 시각
    store.append("holdout_access", [{
        "entity_id": f"promotion-review-{VAULT_END:%Y-%m}", "valid_from": now, "observed_at": now,
        "source": "vault_judge", "market": "KR", "reason": "promotion-review",
        "window_start": VAULT_START.isoformat(),
        "window_end": VAULT_END.isoformat(),
        "detail": "vault_judge — " + " / ".join(f"{t} {v}" for t, v, _ in results),
    }], ingest_run_id=f"vault-judge-{now:%Y%m%dT%H%M%S}")
    print(f"\n기록 완료 — research_trials {len(results)}행 · holdout_access 1행(금고는 이제 소진이다).", flush=True)
    return len(results)


# --------------------------------------------------------------------------- 굽기


def bake_plan() -> list[tuple[str, Path, str]]:
    """(설명, 있어야 하는 파일, 그것을 만드는 명령). 인쇄만 해도 순서를 알 수 있게 둔다."""
    return [
        ("① 국장 점수·타깃·거래가능 명단(AQ·AR·AS)", VAULT / "scores-ranker-KR.pkl",
         ".venv/bin/python tools/vault_judge.py --bake   # bake_long_panel 을 금고 창으로 직접 부른다"),
        ("② 미장 Analyst 점수·타깃(AR·AS·BD)", US_WORK,
         f".venv/bin/python tools/backfill_ic_history.py --market US --start {VAULT_START:%Y-%m} "
         f"--end {VAULT_END:%Y-%m} --work {US_WORK} --sessions 120"),
        ("③ 국장 원피처(AS)", RAW_DIRS["KR"] / "features-chart-KR.pkl",
         f".venv/bin/python tools/diagnose_ic.py cache-extra --market KR --cache-dir {RAW_DIRS['KR']} "
         f"--analyst {' '.join(RAW_ANALYSTS['KR'])}"),
        ("④ 미장 원피처(AS)", RAW_DIRS["US"] / "features-chart-US.pkl",
         f".venv/bin/python tools/diagnose_ic.py cache-extra --market US --cache-dir {RAW_DIRS['US']} "
         f"--analyst {' '.join(RAW_ANALYSTS['US'])}"),
        ("⑤ 내부자 묶음 G4·G7 월 조각(AR)",
         Path(f"data/_diag/ranker-sources/G7-US-{VAULT_END:%Y%m}.parquet"),  # invariant-allow: data-access — 존재 확인할 작업 파일 경로
         ".venv/bin/python tools/vault_judge.py --bake   # ranker_sources.build 를 금고 창 세션으로 부른다"),
    ]


def bake(store: Store) -> int:
    """금고 창 패널을 굽는다 — 가벼운 것은 직접, 무거운 둘(②③④)은 선행 명령을 인쇄하고 멈춘다."""
    from quant_rl_trading.collectors.market_hours import Market
    from quant_rl_trading.settings import load_env
    from tools.bake_long_panel import bake_scores, bake_targets, bake_tradable

    load_env()
    VAULT.mkdir(parents=True, exist_ok=True)
    print(f"=== 금고 창 패널 굽기 · {VAULT_START} ~ {VAULT_END} · {VAULT} ===", flush=True)
    bake_targets(store, Market("KR"), VAULT_START, VAULT_END, VAULT)
    bake_scores(store, Market("KR"), VAULT_START, VAULT_END, VAULT, KR_ANALYSTS)
    bake_tradable(store, Market("KR"), VAULT_START, VAULT_END, VAULT)
    sessions = {"KR": [d.date() if hasattr(d, "date") else d
                       for d in pd.read_pickle(VAULT / "calendar-KR.pkl")["session"]]}  # invariant-allow: data-access — 작업 캐시
    if list(US_WORK.glob("scores-*.parquet")):  # invariant-allow: data-access — 창고가 아닌 작업 조각
        from tools.trial_pooled import load_us
        us = load_us(work=US_WORK, holdout=None)
        sessions["US"] = sorted(d for d in us["session"].unique() if VAULT_START <= d <= VAULT_END)
        del us
    else:
        print(f"미장 점수 조각이 아직 없다({US_WORK}) — 미장 달력·G7 조각은 ② 뒤에 다시 --bake.", flush=True)
    # 원피처 달력 — `diagnose_ic cache-extra` 가 이 파일의 세션만 굽는다(그 도구가 달력을 만들지 않는다).
    for market, days in sessions.items():
        out = RAW_DIRS[market]
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"calendar-{market}.pkl"
        if path.exists():
            continue
        pd.DataFrame({"session": days}).to_pickle(path)  # invariant-allow: data-access — 작업 캐시
        print(f"원피처 달력 {market}: {len(days)}세션 → {path}", flush=True)
    # 내부자 묶음 — freeze 와 같게 **두 묶음 × 두 시장** 을 굽는다(없는 시장은 0, 6차 규칙).
    for group in INSIDER:
        for market, days in sessions.items():
            build_panel(store, group, market, days, collect=False)
    missing = [(label, cmd) for label, ready, cmd in bake_plan() if not ready.exists()]
    if missing:
        print("\n남은 선행 작업 — 로그를 남기며 따로 돌린다(무겁다):", flush=True)
        for label, cmd in missing:
            print(f"  {label}\n    {cmd}", flush=True)
        return 3
    print("\n금고 창 패널 준비 완료 — 이제 --judge.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--plan", action="store_true", help="개봉 당일 실행 순서만 인쇄(자료를 읽지 않는다)")
    parser.add_argument("--bake", action="store_true", help="금고 창 패널 굽기 (개봉 이후만)")
    parser.add_argument("--judge", action="store_true", help="판정 (개봉 이후만)")
    parser.add_argument("--trials", default=",".join(TRIALS))
    parser.add_argument("--save", action="store_true", help="research_trials·holdout_access 에 기록")
    args = parser.parse_args(argv)
    if args.plan:
        print(f"=== 금고 개봉({OPEN_FROM}) 당일 실행 순서 · 창 {VAULT_START}~{VAULT_END} ===", flush=True)
        for i, (label, ready, cmd) in enumerate(bake_plan(), 1):
            print(f"{i}. {label}\n   있어야 하는 것: {ready}\n   {cmd}", flush=True)
        print(f"{len(bake_plan()) + 1}. 판정\n   .venv/bin/python tools/vault_judge.py --judge "
              f"--trials {','.join(TRIALS)} --save", flush=True)
        return 0
    if not (args.bake or args.judge):
        parser.error("--plan · --bake · --judge 중 하나")
    if locked("--bake" if args.bake else "--judge"):
        return 2
    store = Store(root=Path(args.root))
    if args.bake:
        return bake(store)
    trials = [t for t in args.trials.split(",") if t]
    unknown = [t for t in trials if t not in RUNNERS]
    if unknown:
        parser.error(f"모르는 시행: {unknown}")
    results: list[tuple[str, str, list[str]]] = []
    for trial in trials:
        digest = hashlib.sha256(PROTOCOLS[trial].read_bytes()).hexdigest()[:16]
        print(f"\n=== 시행 {trial} — {PROTOCOLS[trial]} (해시 {digest}) · 금고 창 {VAULT_START}~{VAULT_END} ===", flush=True)
        lines, verdict = RUNNERS[trial](store)
        print("\n" + "\n".join(lines) + f"\n판정: {verdict}", flush=True)
        results.append((trial, verdict, [*lines, f"판정: {verdict}"]))
    print("\n=== 요약 ===", flush=True)
    for trial, verdict, _ in results:
        print(f"{trial}: {verdict}", flush=True)
    record_verdicts(store, results, save=args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
