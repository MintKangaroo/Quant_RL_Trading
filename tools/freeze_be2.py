"""BE2 모델 얼리기 — 판정 창 끝(2026-06-30)까지의 자료로 BE1 트랜스포머·C1 GBM 을 시드 5개씩 한 번 학습해 파일로 남긴다.

    .venv/bin/python tools/freeze_be2.py --plan                       # 자르는 날·재학습 지점만 찍는다(세션 열만 읽는다)
    .venv/bin/python tools/freeze_be2.py [--seeds 0,1,2,3,4] [--schedule chain|cold] [--verify]
    .venv/bin/python tools/freeze_be2.py --synthetic --steps 4 --max-epochs 1 --out /tmp/be2   # 합성 스모크
    .venv/bin/python tools/freeze_be2.py --arm C0 [--verify]          # 금고 대조 C0(6점수 GBM)만 얼린다
    .venv/bin/python tools/freeze_be2.py --arm BF1 [--verify]         # DF2 금고의 BF1 LambdaRank 만 얼린다(df2-2026-10.md)

## 왜

마지막 모델 회차(docs/protocols/final-model-round-2026-10.md, 해시 34abffde1d5e6bed)는 BE2 = BE1 + C1 순위 평균을 채택
후보로 적었다. 판정 도구는 **예측만** 남겼다(`data/_diag/final-round/BE/seed*-judge` 파일, C1 은 `pred-C1-seed*`) —
shadow 와 금고 심사는 "얼린 모델" 이 필요하다. 이 도구가 그것을 만든다(docs/design/be2-shadow.md).

## 판정과 같은 설정 (재현성)

- 자료: `final_round_kit.load_full_panel(("KR","US"))` — 판정 패널 캐시 그대로(국장+미장 합동, FA 76열, 창 2022-07-01~2026-06-30).
- 자르는 날: 첫 사용일(2026-07-01)을 블록 시작으로 보고 `kit.train_end` — 퍼지 5 + 엠바고 5, **라벨이 7월 가격을 안 본다**.
- 트랜스포머: `trial_final_transformer` 의 `train_model`·큐브·억제 장치를 그대로 부른다(하이퍼파라미터 등록 고정값, 같은 시드).
  · ``--schedule chain``(기본) — 판정 워크포워드의 재학습 사슬(블록 0·5·…·40, 첫 회 콜드 최대 8에포크, 이후 웜 2에포크)을
    **그대로 다시 밟은 뒤** 첫 사용일 앞에서 웜 재학습 한 번을 더 한다. 판정의 마지막 모델이 이 사슬의 끝이었다.
    ``--verify`` 면 사슬 끝 모델로 판정 마지막 블록을 다시 예측해 저장된 판정 예측과 견준다(같은 스레드면 같아야 한다).
  · ``--schedule cold`` — 자르는 날까지 콜드 한 번(최대 8에포크·조기 종료). 빠르지만 판정 모델의 학습 경로와 다르다.
- GBM: `trial_ranker_kit.fit`(시행 L 규격, 300라운드) 를 같은 시드로 FA 76열에. ``--verify`` 면 판정 마지막 블록 끝점으로
  한 번 더 적합해 `pred-C1-seed*` 캐시와 견준다.

## 계산량·메모리 (추정 — 판정 로그 `logs/trial-final-transformer-BE.log` 실측 기준)

- 패널 조립(캐시 읽기) 최대 RSS ≈ 4.8GB(판정 로그의 load_full_panel) · 큐브 ≈ 310MB(float16).
- 트랜스포머 chain: 시드당 재학습 9회 + 1회 ≈ 판정 시드 시간(90~106분)에서 추론(~7분)을 뺀 **≈ 1.5~1.7시간**,
  시드 5 ≈ **8~8.5시간**. cold: 시드당 최대 8에포크 × (160스텝 × 2집합 + 내부 검증) ≈ **15~40분**, 시드 5 ≈ 1.5~3시간.
- GBM: 학습 행 ≈ 3.6M × 76열(float32 ≈ 1.1GB) + LightGBM 비닝 → 시드당 수 분, 최대 RSS ≈ **5.5~6.5GB**(판정 BE 전체 6.4GB).
- 시드마다 파일을 먼저 남기므로(`partial/`) 중간에 죽어도 끝난 시드는 다시 돌지 않는다. **혼자 돌려야 한다**(D1·BF·BG 뒤).

**금고는 안 연다.** 패널 창은 2026-06-30 까지이고(`kit.check_window`), 학습 라벨도 자르는 날에서 멈춘다.

## `--arm C0` — 금고 앞당김 심사의 대조 C0 (docs/protocols/vault-early-open-2026-10.md)

마지막 회차의 C0(현행 6점수 GBM, `kit.controls` 의 ``[*SCORE_FEATS, "is_us"]``)는 **예측만** 캐시됐다(`pred-C0-seed*`).
금고 심사는 C0 도 얼린 모델이 필요하다. 같은 패널·같은 자르는 날(`kit.train_end`)·같은 `trial_ranker_kit.fit`·같은 시드로
GBM 만 한 번 적합해 `c0-v1.0.0-<data_end>-gbm-seed*.txt` + 사이드카 `c0-v1.0.0-<data_end>.json` 을 쓴다
(파일 이름이 `be2-` 로 시작하지 않으므로 be2 Analyst 는 이것을 못 집는다). 트랜스포머는 돌지 않는다.
계산량: 패널 적재(판정 캐시, 최대 RSS ≈ 4.8GB) + 7열 GBM 시드 5(대조군 굽기 실측 적합 1회 ≈ 8초) → **15~25분**, 최대 RSS ≈ 5GB.
``--verify`` 면 판정 마지막 블록 끝점으로 한 번 더 적합해 `pred-C0-seed*` 캐시와 견준다(비트 동일이어야 한다).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time as time_module
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import be2 as be2_module  # noqa: E402
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.schemas.fa import BLOCK_COLUMNS, BLOCK_ORDER, FA_FEATURES, FLAG_OF  # noqa: E402

PROTOCOL = Path("docs/protocols/final-model-round-2026-10.md")
USABLE_FROM = date(2026, 7, 1)
JUDGE_CACHE = Path("data/_diag/final-round/BE")
DEFAULT_OUT = Path("data/models/be2")
#: 판정 러너(scripts/final_round_BE.sh)와 같은 스레드 — 스레드 수가 다르면 부동소수 합 순서가 달라진다.
DEFAULT_THREADS = 12


# --------------------------------------------------------------------------- 합성 자료


def synthetic_panel(*, end: date = date(2026, 6, 30), n_sessions: int = 200, n_entities: int = 80,
                    seed: int = 0) -> tuple[pd.DataFrame, list[str], dict[str, list[str]], list[date]]:
    """FA 76열 모양의 작은 합성 패널(국장 + 미장) — 배선 확인용, 수치는 뜻이 없다.

    세션은 **실제 국장 거래일**이다 — 얼린 모델을 Analyst 에 물려 창을 읽는 테스트가 같은 달력을 쓰게.
    """
    rng = np.random.default_rng(seed)
    kr_days = [d for d in trading_days(Market.KR, end - timedelta(days=n_sessions * 2), end)][-n_sessions:]
    rows = []
    for s in kr_days:
        x = rng.normal(size=(n_entities, len(FA_FEATURES))).astype(np.float32)
        frame = pd.DataFrame(x, columns=list(FA_FEATURES))
        for flag in FLAG_OF.values():
            frame[flag] = (rng.random(n_entities) < 0.2).astype(np.float32)
        is_us = (np.arange(n_entities) % 4 == 0)
        frame["is_us"] = is_us.astype(np.float32)
        y = 0.4 * frame["chart"].to_numpy() + 0.2 * frame["raw_regime_beta"].to_numpy() + rng.normal(0, 0.9, n_entities)
        frame["entity_id"] = [f"{'US' if u else 'KR'}:S{i:04d}" for i, u in enumerate(is_us)]
        frame["session"] = s
        frame["market"] = np.where(is_us, "US", "KR")
        frame["y5"] = (pd.Series(y).rank(pct=True) - 0.5).to_numpy(np.float32)
        rows.append(frame)
    panel = pd.concat(rows, ignore_index=True)
    groups = {name: [*BLOCK_COLUMNS[name], *([FLAG_OF[name]] if name in FLAG_OF else [])] for name in BLOCK_ORDER}
    return panel, list(FA_FEATURES), groups, list(kr_days)


# --------------------------------------------------------------------------- 계획


def panel_sessions(kit: Any) -> list[date]:
    """판정 패널 국장 조각의 세션(블록 축) — 세션 열 하나만 읽는다. `load_full_panel` 의 네 번째 반환값과 같다."""
    import pyarrow.parquet as pq  # invariant-allow: data-access — 창고가 아닌 판정 패널 캐시(읽기만)

    tag = f"KR+US-{kit.JUDGE_START:%Y%m%d}-{kit.JUDGE_END:%Y%m%d}"
    path = kit.CACHE / f"panel-KR-{tag}.parquet"  # invariant-allow: data-access — 판정 패널 캐시
    days = pq.read_table(path, columns=["session"]).column("session").to_pandas()  # invariant-allow: data-access
    return sorted(set(pd.to_datetime(days).dt.date))


def plan(kit: Any, sessions: list[date], usable_from: date) -> dict[str, Any]:
    """자르는 날·재학습 지점 — 판정 워크포워드와 같은 규칙(`kit.blocks`·`kit.train_end`·RETRAIN_EVERY)."""
    from tools import trial_final_transformer as be

    blocks = list(kit.blocks(sessions))
    if not sessions or usable_from <= sessions[-1]:
        raise ValueError(f"첫 사용일 {usable_from} 은 패널 마지막 세션 {sessions[-1] if sessions else None} 뒤여야 한다")
    cut = kit.train_end([*sessions, usable_from], len(sessions))
    chain = [(number, kit.train_end(sessions, first)) for number, (first, _last) in enumerate(blocks)
             if number % be.RETRAIN_EVERY == 0]
    return {"blocks": blocks, "cut": cut, "chain": chain, "data_end": sessions[-1]}


# --------------------------------------------------------------------------- 트랜스포머


def _state(model: Any) -> dict[str, Any]:
    return {k: v.detach().clone() for k, v in model.state_dict().items()}


def train_transformer(kit: Any, bits: dict[str, Any], sessions: list[date], the_plan: dict[str, Any], seed: int, *,
                      schedule: str, steps: int, max_epochs: int, verify: bool) -> tuple[Any, dict[str, Any]]:
    """시드 하나 — (state_dict, 기록). 판정 `run_seed` 의 재학습 사슬을 예측 없이 다시 밟는다."""
    from tools import trial_final_transformer as be

    cube, observed, market_id = bits["cube"], bits["observed"], bits["market_id"]
    targets, aug, cube_sessions = bits["targets"], bits["aug"], bits["cube_sessions"]
    index_of = {s: i for i, s in enumerate(cube_sessions)}
    began = time_module.monotonic()  # invariant-allow: wallclock — 소요 시간 기록
    retrains: list[dict[str, Any]] = []

    def fit_until(cut: date, init: Any, epochs: int) -> tuple[Any, Any, int, int]:
        fit, val = kit.inner_split([s for s in cube_sessions if s <= cut])
        fit_days = [index_of[s] for s in fit if s in index_of]
        val_days = [index_of[s] for s in val if s in index_of]
        model, log = be.train_model(cube, observed, market_id, targets, fit_days, val_days, seed, aug,
                                    init_state=init, max_epochs=epochs, steps_per_epoch=steps)
        return model, log, len(fit_days), len(val_days)

    def note(label: str, cut: date, log: Any, n_fit: int, n_val: int) -> None:
        retrains.append({"at": label, "cut": cut.isoformat(), "fit_sessions": n_fit, "val_sessions": n_val,
                         "epochs": int(log.epochs), "stopped_early": bool(log.stopped_early),
                         "val_scores": [float(v) for v in log.val_scores],
                         "train_losses": [float(v) for v in log.train_losses]})
        print(f"  seed {seed} · {label} · 적합 {n_fit} / 검증 {n_val} (~{cut}) · epoch {log.epochs}"
              f"{' (조기종료)' if log.stopped_early else ''} · 검증 "
              f"{log.val_scores[-1] if log.val_scores else float('nan'):+.4f} · 누적 "
              f"{(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock

    model = None
    verify_out: dict[str, Any] = {}
    if schedule == "chain":
        for number, cut in the_plan["chain"]:
            warm = be.WARM_START and model is not None
            init = _state(model) if (warm and model is not None) else None
            model, log, n_fit, n_val = fit_until(cut, init, min(max_epochs, be.MAX_EPOCHS_WARM) if warm else max_epochs)
            note(f"사슬 블록 {number}", cut, log, n_fit, n_val)
        if verify and model is not None and the_plan["blocks"]:
            first, last = the_plan["blocks"][-1]
            days = be.block_indices(kit, cube_sessions, sessions, first, last)
            pred = be.predict_days(model, cube, observed, market_id, bits["entities"], cube_sessions, days)
            verify_out = compare_with_judge(pred, seed)
            print(f"  seed {seed} · 판정 마지막 블록 재현: {verify_out}", flush=True)
        init = _state(model) if model is not None else None
        model, log, n_fit, n_val = fit_until(the_plan["cut"], init,
                                             min(max_epochs, be.MAX_EPOCHS_WARM) if init is not None else max_epochs)
        note("첫 사용일 앞 웜 재학습", the_plan["cut"], log, n_fit, n_val)
    elif schedule == "cold":
        model, log, n_fit, n_val = fit_until(the_plan["cut"], None, max_epochs)
        note("콜드 한 번", the_plan["cut"], log, n_fit, n_val)
    else:
        raise ValueError(f"schedule {schedule!r}")
    minutes = (time_module.monotonic() - began) / 60  # invariant-allow: wallclock
    return _state(model), {"retrains": retrains, "minutes": round(minutes, 2), "verify": verify_out}


def compare_with_judge(pred: pd.DataFrame, seed: int, *, cache: Path = JUDGE_CACHE) -> dict[str, Any]:
    """재현 확인 — 판정이 저장한 시드 예측과 같은 (종목, 세션) 에서 견준다. 없으면 빈 dict."""
    path = cache / f"seed{seed}-judge.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일
    if not path.exists() or pred.empty:
        return {}
    saved = pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일
    saved["session"] = pd.to_datetime(saved["session"]).dt.date
    both = pred.merge(saved, on=["entity_id", "session", "market"], suffixes=("", "_judge"))
    if both.empty:
        return {"rows": 0}
    diff = (both["pred"] - both["pred_judge"]).abs()
    rho = both.groupby(["session", "market"]).apply(
        lambda g: g["pred"].corr(g["pred_judge"], method="spearman"), include_groups=False)
    return {"rows": len(both), "max_abs_diff": float(diff.max()), "mean_spearman": float(rho.mean())}


# --------------------------------------------------------------------------- GBM


def train_gbm(panel: pd.DataFrame, feats: list[str], cut: date, seed: int) -> Any:
    """C1 — 시행 L 규격 GBM 을 FA 로. 학습 행 = 자르는 날까지 라벨 있는 행(kit `walk_gbm` 과 같다)."""
    from tools import trial_ranker_kit as rkit

    train = panel[(panel["session"] <= cut) & panel["y5"].notna()]
    X, y = train[feats].to_numpy(np.float32), train["y5"].to_numpy(np.float32)
    return rkit.fit(X, y, seed=int(seed)), len(train)


def verify_gbm(kit: Any, panel: pd.DataFrame, feats: list[str], sessions: list[date], blocks: list[tuple[int, int]],
               seed: int) -> dict[str, Any]:
    """판정 마지막 블록 끝점으로 한 번 더 적합해 `pred-C1-seed*` 캐시와 견준다(같은 스레드·자료면 같아야 한다)."""
    first, last = blocks[-1]
    path = kit.control_path("C1", int(seed), kit.control_tag(panel))
    if not path.exists():
        return {}
    booster, _ = train_gbm(panel, feats, kit.train_end(sessions, first), seed)
    test = kit.block_rows(panel, sessions, first, last)
    pred = test[["entity_id", "session", "market"]].assign(pred=booster.predict(test[feats].to_numpy(np.float32)))
    saved = pd.read_pickle(path)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
    both = pred.merge(saved, on=["entity_id", "session", "market"], suffixes=("", "_judge"))
    if both.empty:
        return {"rows": 0}
    return {"rows": len(both), "max_abs_diff": float((both["pred"] - both["pred_judge"]).abs().max())}


# --------------------------------------------------------------------------- C0 (금고 대조)

C0_VERSION = "c0-v1.0.0"


def c0_features(kit: Any) -> list[str]:
    """C0 의 열 — `kit.controls` 의 ``cols["C0"]`` 와 같은 식(현행 6점수 + is_us). 순서가 계약이다."""
    return [*kit.SCORE_FEATS, "is_us"]


def verify_c0(kit: Any, panel: pd.DataFrame, feats: list[str], sessions: list[date], blocks: list[tuple[int, int]],
              seed: int) -> dict[str, Any]:
    """판정 마지막 블록 끝점으로 적합해 `pred-C0-seed*` 캐시와 견준다(`verify_gbm` 의 C0 판)."""
    first, last = blocks[-1]
    path = kit.control_path("C0", int(seed), kit.control_tag(panel))
    if not path.exists():
        return {}
    booster, _ = train_gbm(panel, feats, kit.train_end(sessions, first), seed)
    test = kit.block_rows(panel, sessions, first, last)
    pred = test[["entity_id", "session", "market"]].assign(pred=booster.predict(test[feats].to_numpy(np.float32)))
    saved = pd.read_pickle(path)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
    both = pred.merge(saved, on=["entity_id", "session", "market"], suffixes=("", "_judge"))
    if both.empty:
        return {"rows": 0}
    return {"rows": len(both), "max_abs_diff": float((both["pred"] - both["pred_judge"]).abs().max())}


def freeze_c0(kit: Any, panel: pd.DataFrame, sessions: list[date], the_plan: dict[str, Any], seeds: list[int],
              out: Path, *, verify: bool, synthetic: bool, usable_from: date) -> Path:
    """C0 GBM 시드별 적합 → 파일·사이드카. 이미 있는 시드는 건너뛴다(이어 돌기)."""
    feats = c0_features(kit)
    missing_cols = [c for c in feats if c not in panel.columns]
    if missing_cols:
        raise SystemExit(f"C0 열 {missing_cols} 이 패널에 없다 — 멈춘다")
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{C0_VERSION}-{the_plan['data_end']:%Y%m%d}"
    info: dict[str, dict[str, Any]] = {}
    files: dict[str, str] = {}
    digests: dict[str, str] = {}
    for seed in seeds:
        txt = out / f"{stem}-gbm-seed{seed}.txt"
        if txt.exists():
            print(f"  C0 seed {seed} · 이미 얼렸다 — 건너뛴다", flush=True)
            info[str(seed)] = {}
        else:
            booster, n_rows = train_gbm(panel, feats, the_plan["cut"], seed)
            booster.save_model(str(txt))
            info[str(seed)] = {"rows": int(n_rows)}
            if verify and not synthetic:
                info[str(seed)]["verify"] = verify_c0(kit, panel, feats, sessions, the_plan["blocks"], seed)
            print(f"  C0 seed {seed} · 학습 {n_rows:,}행 · {info[str(seed)]} · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
        files[str(seed)] = txt.name
        digests[txt.name] = be2_module.file_digest(txt)
    meta = {
        "version": C0_VERSION, "arm": "C0",
        "protocol": "docs/protocols/vault-early-open-2026-10.md",
        "round_protocol_hash": be2_module.PROTOCOL_HASH,
        "trained_through": the_plan["cut"].isoformat(), "data_end": the_plan["data_end"].isoformat(),
        "usable_from": usable_from.isoformat(), "features": feats, "seeds": seeds,
        "gbm": {"trainer": "tools/trial_ranker_kit.fit", "rounds": 300, "num_leaves": 7, "min_data_in_leaf": 2000,
                "learning_rate": 0.03, "bagging_fraction": 0.8, "feature_fraction": 1.0, "lambda_l2": 1.0,
                "num_threads": 6},
        "files": {"gbm": files}, "sha256": digests, "gbm_log": info, "synthetic": bool(synthetic),
        "created_at": LiveClock().now().isoformat(),
    }
    return write_sidecar(out, stem, meta)


# --------------------------------------------------------------------------- BF1 (DF2 금고)

BF1_VERSION = "bf1-v1.0.0"


def train_bf1(panel: pd.DataFrame, feats: list[str], cut: date, seed: int) -> tuple[Any, dict[str, float], int]:
    """BF1 LambdaRank — 판정 `walk_rank` 와 **같은 몸통**(`_fit_rank_rows`)·같은 쿼리(세션×시장)·같은 내부 분할(`kit.inner_split`).

    학습 행 = 자르는 날까지 라벨 있는 행(패널 행 위치, `walk_rank` 와 같은 식). 반환: (booster, 진단, 학습 행 수).
    """
    from tools import final_round_kit as kit
    from tools import trial_final_lambdarank as bf

    keys = bf.query_keys(panel)
    rows = np.flatnonzero(((panel["session"] <= cut) & panel["y5"].notna()).to_numpy())
    booster, diag = bf._fit_rank_rows(panel, rows, feats, keys, int(seed), kit.inner_split)
    return booster, diag, len(rows)


def file_roundtrip(booster: Any, txt: Path, panel: pd.DataFrame, feats: list[str], n: int = 20_000) -> float:
    """적은 파일을 다시 읽은 부스터(금고 판정부가 쓰는 것) 대 메모리 부스터(best_iteration)의 최대 차 — 0 이어야 한다."""
    import lightgbm as lgb

    x = panel[feats].tail(n).to_numpy(np.float32)
    loaded = lgb.Booster(model_file=str(txt))
    return float(np.abs(loaded.predict(x) - booster.predict(x, num_iteration=booster.best_iteration)).max())


def verify_bf1(kit: Any, panel: pd.DataFrame, feats: list[str], sessions: list[date], blocks: list[tuple[int, int]],
               seed: int) -> dict[str, Any]:
    """판정 마지막 블록 끝점으로 적합해 `pred-BF1-seed*` 캐시와 견준다 — `walk_rank` 처럼 best_iteration 으로 예측."""
    first, last = blocks[-1]
    path = kit.CACHE / f"pred-BF1-seed{int(seed)}-{kit.control_tag(panel)}.pkl"  # invariant-allow: data-access — 작업 캐시
    if not path.exists():
        return {}
    booster, _, _ = train_bf1(panel, feats, kit.train_end(sessions, first), seed)
    test = kit.block_rows(panel, sessions, first, last)
    pred = test[["entity_id", "session", "market"]].assign(
        pred=booster.predict(test[feats].to_numpy(np.float32), num_iteration=booster.best_iteration))
    saved = pd.read_pickle(path)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
    saved["session"] = pd.to_datetime(saved["session"]).dt.date
    both = pred.merge(saved[["entity_id", "session", "market", "pred"]], on=["entity_id", "session", "market"],
                      suffixes=("", "_judge"))
    if both.empty:
        return {"rows": 0}
    return {"rows": len(both), "max_abs_diff": float((both["pred"] - both["pred_judge"]).abs().max())}


def freeze_bf1(kit: Any, panel: pd.DataFrame, sessions: list[date], the_plan: dict[str, Any], seeds: list[int],
               out: Path, *, verify: bool, synthetic: bool, usable_from: date, feats: list[str]) -> Path:
    """BF1 시드별 적합 → 파일(best_iteration 까지만 저장)·사이드카. 이미 있는 시드는 건너뛴다(이어 돌기).

    DF2(docs/protocols/df2-2026-10.md) 의 두 번째 금고가 이 파일을 쓴다. 파일 이름이 `be2-` 로 시작하지 않으므로
    be2 Analyst 는 이것을 못 집는다(C0 와 같다).
    """
    from tools import trial_final_lambdarank as bf

    out.mkdir(parents=True, exist_ok=True)
    stem = f"{BF1_VERSION}-{the_plan['data_end']:%Y%m%d}"
    info: dict[str, dict[str, Any]] = {}
    files: dict[str, str] = {}
    digests: dict[str, str] = {}
    for seed in seeds:
        txt = out / f"{stem}-rank-seed{seed}.txt"
        if txt.exists():
            print(f"  BF1 seed {seed} · 이미 얼렸다 — 건너뛴다", flush=True)
            info[str(seed)] = {}
        else:
            booster, diag, n_rows = train_bf1(panel, feats, the_plan["cut"], seed)
            # save_model(num_iteration=None) 은 best_iteration 이 있으면 거기까지만 적는다 — 판정 예측과 같은 트리 수.
            booster.save_model(str(txt))
            info[str(seed)] = {"rows": int(n_rows), "best_iter": int(diag["best_iter"]),
                               "inner_valid_ndcg": float(diag["inner_valid_ndcg"]),
                               "file_roundtrip": file_roundtrip(booster, txt, panel, feats)}
            del booster
            if verify and not synthetic:
                info[str(seed)]["verify"] = verify_bf1(kit, panel, feats, sessions, the_plan["blocks"], seed)
            print(f"  BF1 seed {seed} · 학습 {n_rows:,}행 · {info[str(seed)]} · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)
        files[str(seed)] = txt.name
        digests[txt.name] = be2_module.file_digest(txt)
    meta = {
        "version": BF1_VERSION, "arm": "BF1",
        "protocol": "docs/protocols/df2-2026-10.md",
        "round_protocol_hash": be2_module.PROTOCOL_HASH,
        "trained_through": the_plan["cut"].isoformat(), "data_end": the_plan["data_end"].isoformat(),
        "usable_from": usable_from.isoformat(), "features": list(feats), "seeds": seeds,
        "rank": {"trainer": "tools/trial_final_lambdarank._fit_rank_rows", "params": {k: v for k, v in bf.PARAMS.items()},
                 "max_rounds": bf.MAX_ROUNDS, "early_stop": bf.EARLY_STOP, "query": list(bf.GROUP_KEYS)},
        "files": {"rank": files}, "sha256": digests, "rank_log": info, "synthetic": bool(synthetic),
        "created_at": LiveClock().now().isoformat(),
    }
    return write_sidecar(out, stem, meta)


# --------------------------------------------------------------------------- 파일


def stem_for(data_end: date) -> str:
    return f"{be2_module.VERSION}-{data_end:%Y%m%d}"


def write_sidecar(out: Path, stem: str, meta: dict[str, Any]) -> Path:
    path = out / f"{stem}.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--schedule", choices=("chain", "cold"), default="chain")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--steps", type=int, default=None, help="에포크당 스텝(기본 = 판정 등록값 160). 합성 스모크만 줄인다")
    parser.add_argument("--max-epochs", type=int, default=None, help="콜드 최대 에포크(기본 = 판정 등록값 8)")
    parser.add_argument("--verify", action="store_true", help="판정 마지막 블록 재현 확인(시간이 조금 더 든다)")
    parser.add_argument("--plan", action="store_true", help="자르는 날·재학습 지점만 찍고 끝(학습 없음)")
    parser.add_argument("--synthetic", action="store_true", help="창고·캐시를 안 읽고 합성 패널로 배선만 본다")
    parser.add_argument("--skip-gbm", action="store_true")
    parser.add_argument("--arm", choices=("BE2", "C0", "BF1"), default="BE2",
                        help="C0 = 금고 대조(6점수 GBM)만 · BF1 = DF2 금고의 LambdaRank 만 얼린다 — 트랜스포머는 안 돈다")
    args = parser.parse_args(argv)

    from tools import final_round_kit as kit
    from tools import trial_final_transformer as be

    # 구조 상수가 판정 도구와 같은지 — 다르면 얼린 가중치를 실전 구조가 못 싣거나, 실어도 다른 모델이다.
    for name in ("WINDOW", "D_MODEL", "HEADS", "FFN", "DROPOUT", "TIME_LAYERS", "SET_LAYERS", "TIME_OFFSETS", "MIN_SET"):
        if getattr(be, name) != getattr(be2_module, name):
            print(f"구조 상수 {name} 가 판정 도구({getattr(be, name)})와 실전({getattr(be2_module, name)})에서 다르다", flush=True)
            return 2
    digest = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16] if PROTOCOL.exists() else "없음"
    if not args.synthetic and digest != be2_module.PROTOCOL_HASH:
        print(f"등록 문서 해시 {digest} ≠ {be2_module.PROTOCOL_HASH} — 판정한 등록과 다른 문서다. 멈춘다.", flush=True)
        return 2
    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip() != ""]
    steps = args.steps or be.STEPS_PER_EPOCH
    max_epochs = args.max_epochs or be.MAX_EPOCHS
    began = time_module.monotonic()  # invariant-allow: wallclock — 소요 시간 기록

    if args.synthetic:
        panel, feats, groups, sessions = synthetic_panel()
        usable_from = trading_days(Market.KR, sessions[-1] + timedelta(days=1), sessions[-1] + timedelta(days=10))[0]
    elif args.plan:
        # 계획만 — 패널 전체(4.8GB)를 올리지 않고 국장 조각의 세션 열만 읽는다(블록 축 = 국장 세션).
        sessions, usable_from = panel_sessions(kit), USABLE_FROM
        panel, feats, groups = pd.DataFrame(), list(FA_FEATURES), {}
    else:
        panel, feats, groups, sessions = kit.load_full_panel(("KR", "US"), root=args.root)
        usable_from = USABLE_FROM
        if list(feats) != list(FA_FEATURES):
            print("kit 의 FA 열 목록이 schemas/fa.FA_FEATURES 와 다르다 — 실전이 다른 열을 먹게 된다. 멈춘다.", flush=True)
            print(f"  kit {len(feats)}열 · 실전 {len(FA_FEATURES)}열 · 첫 차이 "
                  f"{next((a, b) for a, b in zip(feats, FA_FEATURES, strict=False) if a != b) if len(feats) == len(FA_FEATURES) else '길이'}",
                  flush=True)
            return 2
    the_plan = plan(kit, sessions, usable_from)
    print(f"=== BE2 얼리기 — 등록 해시 {digest} · 패널 {len(panel):,}행 · 국장 세션 {len(sessions)} "
          f"({sessions[0]}~{sessions[-1]}) · 블록 {len(the_plan['blocks'])} · 자르는 날 {the_plan['cut']} · "
          f"첫 사용일 {usable_from} · 일정 {args.schedule} · 시드 {seeds} ===", flush=True)
    print("  사슬 재학습 지점: " + " · ".join(f"블록{n}~{c}" for n, c in the_plan["chain"]), flush=True)
    if args.plan:
        return 0
    if args.arm == "C0":
        sidecar = freeze_c0(kit, panel, sessions, the_plan, seeds, Path(args.out), verify=args.verify,
                            synthetic=args.synthetic, usable_from=usable_from)
        print(f"C0 사이드카 {sidecar} · sha256[:16] {hashlib.sha256(sidecar.read_bytes()).hexdigest()[:16]} "
              f"· 최대 RSS {kit.rss_mb():.0f}MB · {(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
        return 0

    if args.arm == "BF1":
        from tools import trial_final_lambdarank as bf

        panel = bf.compact_objects(panel)   # 판정 main 과 같다(값·순서 그대로, 객체만 나눈다)
        sidecar = freeze_bf1(kit, panel, sessions, the_plan, seeds, Path(args.out), verify=args.verify,
                             synthetic=args.synthetic, usable_from=usable_from, feats=list(feats))
        print(f"BF1 사이드카 {sidecar} · sha256[:16] {hashlib.sha256(sidecar.read_bytes()).hexdigest()[:16]} "
              f"· 최대 RSS {kit.rss_mb():.0f}MB · {(time_module.monotonic() - began) / 60:.1f}분", flush=True)  # invariant-allow: wallclock
        return 0

    import torch

    torch.set_num_threads(args.threads)
    out = Path(args.out)
    partial = out / "partial"
    partial.mkdir(parents=True, exist_ok=True)
    stem = stem_for(the_plan["data_end"])

    # 큐브 — 판정 main 과 같은 순서·같은 함수.
    entities = sorted(panel["entity_id"].unique())
    columns, index_of = be._feature_layout(list(feats))
    if "is_us" not in panel.columns:
        panel = panel.assign(is_us=(panel["market"] == "US").astype(np.float32))
    cube_sessions = sorted(panel["session"].unique())
    bits: dict[str, Any] = {"entities": entities, "cube_sessions": cube_sessions}
    bits["cube"] = be.build_cube(panel, columns, cube_sessions, entities)
    bits["observed"] = be.observed_mask(panel, cube_sessions, entities)
    market_of = panel.drop_duplicates("entity_id").set_index("entity_id")["market"]
    bits["market_id"] = (market_of.reindex(entities).fillna("KR") == "US").to_numpy(np.int8)
    bits["targets"] = be.target_columns(panel, cube_sessions, entities)
    bits["aug"] = be.Aug(kit.drop_groups, dict(groups), index_of)
    print(f"  큐브 {bits['cube'].nbytes / 1e6:.0f}MB · 종목 {len(entities):,} · 큐브 축 세션 {len(cube_sessions)} · "
          f"최대 RSS {kit.rss_mb():.0f}MB", flush=True)

    records: dict[int, dict[str, Any]] = {}
    for seed in seeds:
        pt = partial / f"{stem}-transformer-seed{seed}.pt"
        info_path = partial / f"{stem}-transformer-seed{seed}.json"
        if pt.exists() and info_path.exists():
            records[seed] = json.loads(info_path.read_text(encoding="utf-8"))
            print(f"  seed {seed} · 이미 얼렸다 — 건너뛴다 ({pt.name})", flush=True)
            continue
        state, info = train_transformer(kit, bits, sessions, the_plan, seed, schedule=args.schedule,
                                        steps=steps, max_epochs=max_epochs, verify=args.verify)
        torch.save(state, pt)
        info_path.write_text(json.dumps(info, ensure_ascii=False, default=str), encoding="utf-8")
        records[seed] = info
    del bits
    kit.release_memory()

    gbm_info: dict[int, dict[str, Any]] = {}
    if not args.skip_gbm:
        for seed in seeds:
            txt = partial / f"{stem}-gbm-seed{seed}.txt"
            if txt.exists():
                print(f"  GBM seed {seed} · 이미 얼렸다 — 건너뛴다", flush=True)
                gbm_info[seed] = {}
                continue
            booster, n_rows = train_gbm(panel, list(feats), the_plan["cut"], seed)
            booster.save_model(str(txt))
            gbm_info[seed] = {"rows": int(n_rows)}
            if args.verify and not args.synthetic:
                gbm_info[seed]["verify"] = verify_gbm(kit, panel, list(feats), sessions, the_plan["blocks"], seed)
            print(f"  GBM seed {seed} · 학습 {n_rows:,}행 · {gbm_info[seed]} · 최대 RSS {kit.rss_mb():.0f}MB", flush=True)

    # 끝난 시드만 사이드카에 올린다 — 요청한 시드가 다 있어야 쓴다.
    missing = [s for s in seeds if not (partial / f"{stem}-transformer-seed{s}.pt").exists()
               or not (partial / f"{stem}-gbm-seed{s}.txt").exists()]
    if missing:
        print(f"시드 {missing} 파일이 아직 없다 — 사이드카를 안 쓴다(다시 돌리면 이어 간다).", flush=True)
        return 1
    files = {"transformer": {}, "gbm": {}}
    digests: dict[str, str] = {}
    for seed in seeds:
        for kind, suffix in (("transformer", "pt"), ("gbm", "txt")):
            src = partial / f"{stem}-{kind}-seed{seed}.{suffix}"
            dst = out / src.name
            dst.write_bytes(src.read_bytes())
            files[kind][str(seed)] = dst.name
            digests[dst.name] = be2_module.file_digest(dst)
    meta = {
        "version": be2_module.VERSION,
        "protocol": str(PROTOCOL),
        "protocol_hash": be2_module.PROTOCOL_HASH,
        "trained_through": the_plan["cut"].isoformat(),
        "data_end": the_plan["data_end"].isoformat(),
        "usable_from": usable_from.isoformat(),
        "features": list(feats),
        "seeds": seeds,
        "arch": be2_module.ARCH,
        "training": {
            "schedule": args.schedule, "threads": args.threads, "steps_per_epoch": steps, "max_epochs": max_epochs,
            "max_epochs_warm": be.MAX_EPOCHS_WARM, "patience": be.PATIENCE, "retrain_every": be.RETRAIN_EVERY,
            "lr": be.LR, "weight_decay": be.WEIGHT_DECAY, "clip": be.CLIP, "group_drop_p": be.GROUP_DROP_P,
            "input_noise": be.INPUT_NOISE, "entity_subsample": be.ENTITY_SUBSAMPLE, "purge": kit.PURGE,
            "embargo": kit.EMBARGO, "inner_val_share": kit.INNER_VAL_SHARE,
            "chain": [{"block": n, "cut": c.isoformat()} for n, c in the_plan["chain"]],
            "synthetic": bool(args.synthetic),
        },
        "gbm": {"trainer": "tools/trial_ranker_kit.fit", "rounds": 300, "num_leaves": 7, "min_data_in_leaf": 2000,
                "learning_rate": 0.03, "bagging_fraction": 0.8, "feature_fraction": 1.0, "lambda_l2": 1.0,
                "num_threads": 6},
        "files": files,
        "sha256": digests,
        "transformer_log": {str(k): v for k, v in records.items()},
        "gbm_log": {str(k): v for k, v in gbm_info.items()},
        "rss_mb_max": round(kit.rss_mb(), 0),
        "minutes": round((time_module.monotonic() - began) / 60, 1),  # invariant-allow: wallclock
        "created_at": LiveClock().now().isoformat(),
    }
    path = write_sidecar(out, stem, meta)
    print(f"사이드카 {path} · 시드 {seeds} · 최대 RSS {meta['rss_mb_max']:.0f}MB · {meta['minutes']}분", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
