"""홀드아웃 금고 개봉 판정부 — 사전등록 시행 AQ·AR·AS·BD(+ BE2)를 **한 번의 개봉**으로 심사한다.

    .venv/bin/python tools/vault_judge.py --plan [--window early]                 # 실행 순서만 인쇄(자료를 읽지 않는다)
    .venv/bin/python tools/vault_judge.py --bake --window early                   # ① 금고 창 패널 굽기
    .venv/bin/python tools/vault_judge.py --judge --window early                  # ② 판정 표·기준별 ○×
    .venv/bin/python tools/vault_judge.py --judge --window early --save           # + research_trials·holdout_access 기록

## 창 셋 (`--window`, 기본 early)

| 창 | 구간 | 굽기 / 판정 가능일 | 시행 | 창을 정한 문서 |
|---|---|---|---|---|
| `registered` | 2026-07-01~11-13 | 11-23 / 11-23 | AQ·AR·AS·BD | 각 시행의 등록 문서(원래 설계) |
| `early` | 2026-07-01~09-30 | 문서가 정한다 | AQ·AR·AS·BD·BE2 | `docs/protocols/vault-early-open-2026-10.md` |
| `second` | 2026-10-01~11-13 | 문서가 정한다 | BE2(확인)·BD(보류였으면) + 따로 등록 DF2·P1B | 같은 문서 + 각자 등록 문서 |

`early`·`second` 의 날짜·시행은 **코드가 아니라 등록 문서의 `창` 줄**에서 읽는다(`windows()`). 날짜를 인자로 받지 않는
것은 일부러다 — 인자로 창을 옮길 수 있으면 해시 잠금이 뜻을 잃는다. 대신 두 겹으로 잠근다:
① 문서의 sha256 앞 16자가 이 파일의 `EARLY_PROTOCOL_HASH` 와 같아야 한다(초안이면 None → 전부 거부),
② 오늘이 문서가 정한 굽기/판정 가능일 이후여야 한다. 또 개봉 이력(`holdout_access`)에 겹치는 창이 있으면
거부한다 — 한 번 연 금고를 다른 창 이름으로 다시 여는 길을 막는다.

## 금고 (registered — 원래 설계)

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

타깃(h5 라벨)은 **판정 가능일 전에는 굽지 않는다** — 굽기 도구는 파일이 있으면 다시 굽지 않으므로, 라벨이 닫히기 전에
구우면 창 끝 세션의 라벨이 빠진 채 굳는다.

## BE2 (early·second) — 실전 경로로 잰다

얼린 모델(`data/models/be2/be2-v1.0.0-20260630.*`)과 얼린 C0(`c0-v1.0.0-20260630.*`, `tools/freeze_be2.py --arm C0`)의
**사이드카 sha256 을 등록 문서와 대조**하고, 사이드카 안의 파일 지문을 다시 계산한다(`Be2Model.problems`).
입력은 창고 `fa_features`(실전 매일 경로 `analysts/fa_features.build_session` 이 적은 것)이고, 창·채점은
be2 Analyst 와 **같은 함수**(`be2.session_batch`·`Be2Model.seed_predictions`)다. be2 **신호**(`signals.be2`)는 읽지 않는다 —
그것은 시드 평균·EMA·z 를 거친 값이라 시드별 판정을 못 한다. 시드마다 BE2 = (BE1 백분위 + C1 백분위)/2, C1 = 얼린
GBM, C0 = 얼린 6점수 GBM → 국장 포트(`trial_ranker_kit.portfolio`, `final_round_kit.evaluate` 국장 가지와 같다) →
기준 ①~⑥(③ 은 창을 세션 수 절반으로 가른 두 구간 — 박스 국면이 창에 없다).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.analysts import be2 as be2_module  # noqa: E402
from quant_rl_trading.schemas.fa import SCORE_COLUMNS  # noqa: E402
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

#: 금고 창과 개봉일 — **원래 등록(registered)** 값. 등록 문서 넷이 같은 값을 쓴다. 코드에서 고치는 것은 사후 기준 변경이다.
#: 아래 모듈 변수 VAULT_START·VAULT_END·OPEN_FROM·VAULT·US_WORK·RAW_DIRS 는 `use_window` 가 고른 창으로 다시 묶는다.
REGISTERED_START, REGISTERED_END = date(2026, 7, 1), date(2026, 11, 13)
REGISTERED_OPEN = date(2026, 11, 23)
VAULT_START, VAULT_END = REGISTERED_START, REGISTERED_END
OPEN_FROM = REGISTERED_OPEN
#: 금고 창 패널 캐시. `data/_diag` 는 개봉 전 시행들의 입력이라 덮지 않는다(bake_long_panel 의 같은 판단).
VAULT = Path("data/_diag/vault-window")
US_WORK = VAULT / "ic-history-us"
RAW_DIRS = {"KR": VAULT / "raw-KR", "US": VAULT / "raw-US"}
TRIALS = ("AQ", "AR", "AS", "BD")
FAMILY = {"AQ": "selection", "AR": "ranker", "AS": "ranker", "BD": "selection", "BE2": "ranker"}
ENTITY = {"AQ": "breadth72-forward-2026-09:AQ", "AR": "insider-forward-2026-09:AR",
          "AS": "raw-feature-vault-2026-09:AS", "BD": "us-regime-switch-vault-2026-11:BD",
          "BE2": "final-model-round-2026-10:BE2"}

#: 금고 앞당김 등록 문서(창 early·second 를 선언한다). 시행별 기준은 각 시행 문서 그대로다.
EARLY_PROTOCOL = Path("docs/protocols/vault-early-open-2026-10.md")
#: 그 문서의 sha256 앞 16자 — **사용자 승인 뒤 해시를 고정할 때** 적는다. None 이면 초안이고, early·second 는 전부 거부한다.
EARLY_PROTOCOL_HASH: str | None = "2e9c645a62dc7bbc"  # 2026-09-30 사용자 승인 고정
#: 판정 기준을 담은 문서 — 시행 넷은 각자 등록 문서, BE2 는 마지막 모델 회차 문서(해시 34abffde1d5e6bed).
PROTOCOL_OF: dict[str, Path] = {**PROTOCOLS, "BE2": Path("docs/protocols/final-model-round-2026-10.md")}
#: 얼린 BE2·C0 — 사이드카 줄기(`<줄기>.json`). 해시는 앞당김 등록 문서 "모델 해시" 절에 적는다.
BE2_MODELS = Path("data/models/be2")
BE2_STEM, C0_STEM = "be2-v1.0.0-20260630", "c0-v1.0.0-20260630"
C0_FEATURES = (*SCORE_COLUMNS, "is_us")
#: BE2 채택 기준 ①~⑥ — 마지막 모델 회차 등록 그대로(`final_round_kit.GATE_*` 와 같은 값, 테스트가 맞춘다).
BE2_GATE_MEAN, BE2_GATE_SHARE, BE2_GATE_HALF = 0.02, 0.80, -0.01
BE2_GATE_IC, BE2_GATE_MDD, BE2_GATE_TURN, BE2_GATE_MODEL = 0.0, 0.02, 1.2, 0.01
#: **따로 등록된 시행**이 창에 붙는 자리 — 앞당김 문서(해시 고정)의 `창` 줄은 못 고치므로, 제 등록 문서·제 해시로 붙는다.
#: DF2(docs/protocols/df2-2026-10.md, 2026-09-30 사용자 "C로 하자") 는 두 번째 금고(second)에서 BE2·BD 와 **한 번에** 연다.
DF2_PROTOCOL = Path("docs/protocols/df2-2026-10.md")
DF2_PROTOCOL_HASH: str | None = "dbac9e2be1db35ef"   # 2026-09-30 고정
#: P1-b′(docs/protocols/p1b-prime-2026-10.md, 2026-10-08 사용자 설문 "등록 + shadow") 도 second 에서 같은 개봉으로 판정한다.
P1B_PROTOCOL = Path("docs/protocols/p1b-prime-2026-10.md")
P1B_PROTOCOL_HASH: str | None = "12f053518c05ae3c"   # 2026-10-08 고정(e483c7f)
EXTRA_TRIALS: dict[str, tuple[str, ...]] = {"second": ("DF2", "P1B")}
EXTRA_PROTOCOLS: dict[str, tuple[Path, str | None]] = {"DF2": (DF2_PROTOCOL, DF2_PROTOCOL_HASH),
                                                       "P1B": (P1B_PROTOCOL, P1B_PROTOCOL_HASH)}
FAMILY["DF2"] = "selection"
ENTITY["DF2"] = "df2-2026-10:DF2"
FAMILY["P1B"] = "selection"
ENTITY["P1B"] = "p1b-prime-2026-10:P1B"
#: P1-b′ 변형 — 등록 표 그대로 (N, 재조정 R, 완충 배수, 비용 인지 θ). B0 = 대조(현행).
P1B_ARMS: dict[str, tuple[int, int, int, float | None]] = {"B0": (24, 10, 3, None), "B1′": (100, 20, 5, 1.0),
                                                          "B2′": (24, 20, 3, None)}
#: EMA5 를 데우는 창 앞 세션 수 — 등록은 "창 시작 전 세션 예측으로 데운다" 만 정했다. span 5 면 20세션 앞 무게는
#: (2/3)^20 ≈ 3e-4 라 값에 영향이 없다. 데운 세션의 수익은 쓰지 않는다(장부는 창 첫 세션에 빈 손으로 시작).
P1B_WARM = 20
#: 기준 ①~④ — 등록(초안 P1-b 와 같다): ① 연 ≥ B0 + 1%p · NW t(lag 4) ≥ 2.0 ② 시드 5 중 4 ③ 국면(= 창 전체) ≥ B0 − 1%p
#: ④ 연회전 ≤ B0 × 0.6 · MDD 가 B0 보다 2%p 넘게 깊지 않다.
P1B_GATE_ANN, P1B_GATE_T, P1B_GATE_SHARE, P1B_GATE_REGIME = 0.01, 2.0, 4, -0.01
P1B_GATE_TURN, P1B_GATE_MDD = 0.6, 0.02
P1B_KEYS = ("ann", "turn", "mdd", "sel_ir")
#: 얼린 BF1 사이드카(`tools/freeze_be2.py --arm BF1`) — 해시는 **얼린 뒤** 여기에 적는다(DF2 문서는 고정돼 못 고친다).
#: None 이면 DF2 판정을 거부한다(얼리기가 먼저다).
BF1_MODELS = Path("data/models/bf1")   # BE2 폴더와 따로 — be2 Analyst 가 보는 폴더에 섞지 않는다. manifest.json 도 여기
BF1_STEM = "bf1-v1.0.0-20260630"
BF1_SIDECAR_HASH: str | None = None
#: DF2 판정 불가(보류) 규칙 — 등록 문서 값 그대로: 확인 발동 세션 < 5 · 발동 든 재조정일 < 1 · C1′ 창 MDD 가 −5% 보다 얕다.
DF2_MIN_FIRE, DF2_MIN_FIRE_REBAL, DF2_MIN_DEPTH = 5, 1, -0.05
#: DF2 기준 — DF 와 같다(C1′ 대비, 시드 평균): MDD 1%p 얕게 · 연 ≥ −0.5%p · 두 구간 ≥ −1%p · 회전 ≤ ×1.2.
DF2_GATE_MDD, DF2_GATE_ANN, DF2_GATE_HALF, DF2_GATE_TURN = 0.01, -0.005, -0.01, 1.2
DF2_KEYS = ("ann", "h1", "h2", "mdd", "turn")
WINDOW_LINE = re.compile(r"^- `창 (\w+)` (\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2}) · 굽기 (\d{4}-\d{2}-\d{2}) · "
                         r"판정 (\d{4}-\d{2}-\d{2}) · 시행 ([A-Z0-9,]+)\s*$", re.M)


@dataclass(frozen=True)
class Window:
    """금고 창 하나. ``protocol`` 이 None 이면 원래 등록(시행 문서들이 창을 정했다)."""

    name: str
    start: date
    end: date
    bake_from: date
    judge_from: date
    trials: tuple[str, ...]
    cache: Path
    protocol: Path | None = None


def windows(doc: Path | None = None) -> dict[str, Window]:
    """창 셋 — registered 는 상수, early·second 는 앞당김 등록 문서의 `창` 줄에서 읽는다(문서가 없으면 registered 만)."""
    out = {"registered": Window("registered", REGISTERED_START, REGISTERED_END, REGISTERED_OPEN, REGISTERED_OPEN,
                                TRIALS, Path("data/_diag/vault-window"))}
    doc = doc or EARLY_PROTOCOL
    if not doc.exists():
        return out
    for name, start, end, bake_from, judge_from, trials in WINDOW_LINE.findall(doc.read_text()):
        out[name] = Window(name, date.fromisoformat(start), date.fromisoformat(end), date.fromisoformat(bake_from),
                           date.fromisoformat(judge_from), tuple(t for t in trials.split(",") if t),
                           Path(f"data/_diag/vault-{name}"), protocol=doc)
    return out


def use_window(win: Window) -> None:
    """모듈의 창 변수를 이 창으로 다시 묶는다 — 시행 러너들은 모듈 변수를 읽는다(원래 코드 그대로)."""
    global VAULT_START, VAULT_END, OPEN_FROM, VAULT, US_WORK, RAW_DIRS
    VAULT_START, VAULT_END, OPEN_FROM = win.start, win.end, win.judge_from
    VAULT = win.cache
    US_WORK = VAULT / "ic-history-us"
    RAW_DIRS = {"KR": VAULT / "raw-KR", "US": VAULT / "raw-US"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def registration_problems(win: Window | None = None) -> list[str]:
    """앞당김 등록이 **고정됐는지** — 빈 목록이어야 early·second 의 자료를 읽는다(날짜와 무관한 잠금).

    ① `EARLY_PROTOCOL_HASH` 가 있다(초안이 아니다) ② 문서 해시가 그 값과 같다 ③ 문서가 고정한 시행 문서 해시
    (`doc:<시행>` 줄)가 지금 파일과 같다 — 시행 문서가 개봉 전에 바뀌었으면 거부. 원래 등록 창(registered)은 해당 없음.
    """
    if win is not None and win.protocol is None:
        return []
    doc = win.protocol if win is not None and win.protocol is not None else EARLY_PROTOCOL
    if not doc.exists():
        return [f"{doc} 가 없다"]
    if EARLY_PROTOCOL_HASH is None:
        return [f"{doc} 는 초안이다 — 등록 해시가 고정되지 않았다(EARLY_PROTOCOL_HASH = None). 사용자 승인 뒤 고정한다"]
    digest = _digest(doc)
    if digest != EARLY_PROTOCOL_HASH:
        return [f"{doc} 해시 {digest} ≠ 고정값 {EARLY_PROTOCOL_HASH} — 고정 뒤 문서가 바뀌었다"]
    problems = extra_problems(win)
    pinned = pinned_hashes(doc)
    for trial, path in PROTOCOL_OF.items():
        want = pinned.get(f"doc:{trial}")
        if want is None:
            problems.append(f"{doc} 에 doc:{trial} 해시 줄이 없다")
        elif _digest(path) != want:
            problems.append(f"{path} 해시 {_digest(path)} ≠ 앞당김 문서가 고정한 {want} — 시행 문서가 바뀌었다")
    return problems


def trials_of(win: Window) -> tuple[str, ...]:
    """창에서 심사하는 시행 — 앞당김 문서 `창` 줄의 시행 + 따로 등록돼 붙은 시행(`EXTRA_TRIALS`)."""
    return (*win.trials, *EXTRA_TRIALS.get(win.name, ()))


def protocol_path(trial: str) -> Path:
    return EXTRA_PROTOCOLS[trial][0] if trial in EXTRA_PROTOCOLS else PROTOCOL_OF[trial]


def extra_problems(win: Window | None) -> list[str]:
    """창에 붙은 따로 등록 시행의 문서가 **고정됐고 그대로인지**. 어긋나면 그 창 전체를 거부한다(개봉은 한 번이라서)."""
    if win is None:
        return []
    out = []
    for trial in EXTRA_TRIALS.get(win.name, ()):
        path, want = EXTRA_PROTOCOLS[trial]
        if want is None:
            out.append(f"{trial} 등록 {path} 해시가 고정되지 않았다(None)")
        elif not path.exists():
            out.append(f"{trial} 등록 {path} 가 없다")
        elif _digest(path) != want:
            out.append(f"{trial} 등록 {path} 해시 {_digest(path)} ≠ 고정값 {want} — 고정 뒤 문서가 바뀌었다")
    return out


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
#: 해시 뒤의 같은 줄 덧붙임(괄호 메모)은 허용한다 — 앞당김 문서의 C0 줄이 "2f85… (9/30 11:1x 얼림, …)" 로 고정돼
#: 줄끝 `$` 만 받던 식이 C0 해시를 못 읽어 BE2 판정이 거부될 뻔했다(2026-10-04 리허설). 줄바꿈은 넘지 않는다(`[ \t]`).
HASH_LINE = re.compile(r"^- `([A-Za-z0-9.:-]+)` ([0-9a-f]{16})(?:[ \t][^\n]*)?$", re.M)


def _today() -> date:
    """오늘(UTC). 날짜 잠금이 여기 하나만 본다 — 테스트가 이 함수를 바꿔 끼운다."""
    return datetime.now(UTC).date()  # invariant-allow: wallclock — 사전등록 시점 잠금


def locked(what: str, win: Window | None = None) -> bool:
    """잠겨 있으면 True 를 돌려주고 이유를 인쇄한다. ``win`` 이 없으면 원래 등록 창(11/23)이다.

    early·second 는 **해시 잠금이 먼저**다 — 초안이면 날짜가 지나도 돌지 않는다. 그다음 날짜: ``--bake`` 는 창의 굽기
    가능일, ``--judge`` 는 판정 가능일.
    """
    win = win or windows()["registered"]
    problems = registration_problems(win)
    if problems:
        print(f"{what} ({win.name} 창) 거부 — 등록이 고정되지 않았다:\n  " + "\n  ".join(problems)
              + "\n지금 허용되는 것은 --plan 뿐이다.", flush=True)
        return True
    today = _today()
    opens = win.bake_from if what == "--bake" else win.judge_from
    if today >= opens:
        return False
    print(f"{what} 는 금고 개봉({opens}) 이후에만 돈다 — 오늘은 {today}. 사전등록 '중간 들여다보기 금지'"
          f"(self-improvement.md §1①). 지금 허용되는 것은 --plan 뿐이다.", flush=True)
    return True


#: 두 번째 금고에서 심사하는 조건 — 앞 개봉(early)의 판정이 이 말로 시작할 때만. BE2 는 채택(또는 ①~⑤ 통과)의 확인,
#: BD 는 early 에서 bear 세션 부족으로 **보류**(시행 미소진)였을 때만 같은 기준으로 한 번.
SECOND_NEEDS: dict[str, dict[str, tuple[str, ...]]] = {"second": {"BE2": ("채택", "①~⑤"), "BD": ("보류",)}}


def prior_verdict(store: Store, trial: str) -> str | None:
    """이 판정부가 그 시행에 적은 가장 최근 판정(research_trials detail 의 첫 칸). 없으면 None."""
    now = datetime.now(UTC)  # invariant-allow: wallclock — 지금까지 적힌 판정 전부
    rows = store.get("research_trials", as_of=now, lookback=3650)
    if rows.empty:
        return None
    rows = rows[(rows["entity_id"] == ENTITY[trial]) & (rows["source"].astype(str) == "vault_judge")]
    if rows.empty:
        return None
    return str(rows.sort_values("observed_at").iloc[-1]["detail"]).split(" | ")[0]


def consumed(store: Store, win: Window) -> list[str]:
    """이 창과 겹치는 **이미 연** 금고(`holdout_access`, reason promotion-review). 있으면 판정·기록을 거부한다."""
    now = datetime.now(UTC)  # invariant-allow: wallclock — 개봉 이력은 지금까지 적힌 전부
    rows = store.get("holdout_access", as_of=now, lookback=3650)
    if rows.empty:
        return []
    rows = rows[rows["reason"].astype(str) == "promotion-review"]
    out = []
    for _, row in rows.iterrows():
        a, b = date.fromisoformat(str(row["window_start"])), date.fromisoformat(str(row["window_end"]))
        if a <= win.end and win.start <= b:
            out.append(f"{a}~{b} ({row.get('entity_id', '')})")
    return out


# --------------------------------------------------------------------------- 얼린 모델


def frozen_hashes(trial: str) -> dict[str, str]:
    """등록 문서 "모델 해시" 절의 {이름: 해시 16자리}. BE2 는 앞당김 등록 문서에 적는다(마지막 회차 문서는 고정돼 못 고친다)."""
    doc = EARLY_PROTOCOL if trial == "BE2" else PROTOCOLS[trial]
    body = doc.read_text()
    head = body.index("## 모델 해시")
    found = {k: v for k, v in HASH_LINE.findall(body[head:]) if not k.startswith("doc:")}
    if not found:
        raise SystemExit(f"{doc} 에 모델 해시가 없다 — --freeze 가 먼저다")
    return found


def pinned_hashes(doc: Path) -> dict[str, str]:
    """문서 전체의 "- `이름` 해시16" 줄 — 시행 문서 고정(`doc:AQ` …)과 모델 해시가 같이 들어 있다."""
    return dict(HASH_LINE.findall(doc.read_text()))


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


BE2_KEYS = ("ann", "h1", "h2", "mdd", "turn", "ic")


def judge_be2(res: dict[str, list[dict[str, float]]], *, confirm: bool = False) -> tuple[list[str], str]:
    """BE2 기준 ①~⑥ (`final-model-round-2026-10.md`, 창만 앞당김 문서). res[군] = 시드 순서대로 지표 목록, 군 = BE2·C0·C1.

    ① 시드 평균 연수익 ≥ C0 + 2%p ② 시드의 80% 이상이 같은 시드 C0 보다 높다 ③ 창을 세션 수 절반으로 가른 두 구간
    모두 ≥ C0 − 1%p(박스 국면이 창에 없어 등록 틀 ③ 의 "판정 창을 반으로 나눈 두 구간" 을 쓴다) ④ ΔIC(h5) ≥ 0
    ⑤ MDD 가 2%p 넘게 깊지 않고 회전 ≤ C0 × 1.2 · ⑥ 모델 주장 ≥ C1 + 1%p.

    ``confirm`` — 두 번째 금고(10/1~11/13)의 **확인**: 연수익 ≥ C0(여백 0) 이고 ⑤. ①~⑥ 은 기록만 한다.
    지표 키가 빠지거나 nan 이면 크게 멈춘다 — 조용히 "기각" 으로 끝나지 않게(kit.judge 와 같은 이유).
    """
    for arm in ("BE2", "C0", "C1"):
        for i, row in enumerate(res[arm]):
            bad = [k for k in BE2_KEYS if k not in row or not np.isfinite(row[k])]
            if bad:
                raise ValueError(f"judge_be2: {arm} 시드 {i} 에 지표 {bad} 가 없다 — 조용히 기각하지 않는다")
    be, c0, c1 = res["BE2"], res["C0"], res["C1"]
    wins = sum(b["ann"] > c["ann"] for b, c in zip(be, c0, strict=True))
    share = wins / len(be)
    halves = [(_mean(be, k), _mean(c0, k)) for k in ("h1", "h2")]
    dic = _mean(be, "ic") - _mean(c0, "ic")
    c = (
        _mean(be, "ann") >= _mean(c0, "ann") + BE2_GATE_MEAN,
        share >= BE2_GATE_SHARE,
        all(b >= z + BE2_GATE_HALF for b, z in halves),
        dic >= BE2_GATE_IC,
        _mean(be, "mdd") >= _mean(c0, "mdd") - BE2_GATE_MDD and _mean(be, "turn") <= _mean(c0, "turn") * BE2_GATE_TURN,
    )
    model = _mean(be, "ann") >= _mean(c1, "ann") + BE2_GATE_MODEL
    lines = ["| 군 | 연수익(시드 평균) | 시드별 | 전반 | 후반 | MDD | 회전 | IC |", "|---|---|---|---|---|---|---|---|"]
    for arm, rows in (("BE2", be), ("C1 GBM·FA", c1), ("C0 6점수", c0)):
        lines.append(f"| {arm} | {_mean(rows, 'ann'):+.1%} | " + " / ".join(f"{r['ann']:+.1%}" for r in rows)
                     + f" | {_mean(rows, 'h1'):+.1%} | {_mean(rows, 'h2'):+.1%} | {_mean(rows, 'mdd'):.1%} | "
                       f"{_mean(rows, 'turn'):.1f} | {_mean(rows, 'ic'):+.4f} |")
    lines += [
        f"①평균 {_mean(be, 'ann') - _mean(c0, 'ann'):+.1%}p (≥ +2%p) {mark(c[0])} · ②{wins}/{len(be)} ({share:.0%}) {mark(c[1])} · "
        f"③두 구간 {halves[0][0] - halves[0][1]:+.1%}p / {halves[1][0] - halves[1][1]:+.1%}p (≥ −1%p) {mark(c[2])}",
        f"④ΔIC {dic:+.4f} (≥ 0) {mark(c[3])} · ⑤MDD {_mean(be, 'mdd'):.1%} 대 {_mean(c0, 'mdd'):.1%} · 회전 "
        f"{_mean(be, 'turn'):.1f} 대 {_mean(c0, 'turn'):.1f} {mark(c[4])} · ⑥대 C1 {_mean(be, 'ann') - _mean(c1, 'ann'):+.1%}p "
        f"(≥ +1%p) {mark(model)}",
    ]
    if confirm:
        ok = _mean(be, "ann") >= _mean(c0, "ann") and c[4]
        verdict = ("확인 — 두 번째 금고에서도 C0 이상·해 없음" if ok
                   else "확인 실패 — 두 번째 금고에서 C0 미만이거나 ⑤ 위반(승격 보류, 사용자 결정)")
    elif all(c) and model:
        verdict = "채택 — BE2 금고 통과(①~⑥, 모델이 나아서)"
    elif all(c):
        verdict = "①~⑤ 통과·⑥ 미통과 — 정보 효과: BE2 모델 주장 기각, C1(GBM·FA) 후보 여부는 사용자 결정"
    else:
        verdict = "기각 — BE2 를 실전에 넣지 않는다(shadow 는 기록으로만)"
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


def frozen_be2(expected: dict[str, str], *, folder: Path | None = None) -> tuple[Any, dict[int, Any]]:
    """얼린 BE2·C0 을 싣는다 — **사이드카 sha256 을 등록 문서와 대조**하고 사이드카 안 파일 지문을 다시 잰다.

    창고는 읽지 않는다(파일만). 어긋나면 판정 거부 — 다시 학습하지 않는다.
    """
    folder = folder or BE2_MODELS
    for stem in (BE2_STEM, C0_STEM):
        if stem not in expected:
            raise SystemExit(f"앞당김 등록 문서에 {stem} 의 해시가 없다 — 판정 거부(C0 는 freeze_be2 --arm C0 가 먼저다)")
        path = folder / f"{stem}.json"
        if not path.exists():
            raise SystemExit(f"{path} 가 없다 — 얼린 모델을 다시 학습하지 않는다(등록 위반). 파일을 복구해야 한다")
        digest = _digest(path)
        if digest != expected[stem]:
            raise SystemExit(f"사이드카 해시 불일치 {stem}: 문서 {expected[stem]} ≠ 파일 {digest} — 판정 거부")
    model = be2_module.Be2Model.load(folder / f"{BE2_STEM}.json")
    problems = model.problems()
    if problems:
        raise SystemExit("얼린 BE2 를 쓸 수 없다 — 판정 거부: " + "; ".join(problems))
    meta = json.loads((folder / f"{C0_STEM}.json").read_text(encoding="utf-8"))
    if tuple(meta.get("features", ())) != C0_FEATURES:
        raise SystemExit(f"C0 열 {meta.get('features')} ≠ {list(C0_FEATURES)} — 판정 거부")
    if meta.get("trained_through") != model.trained_through.isoformat():
        raise SystemExit(f"C0 자르는 날 {meta.get('trained_through')} ≠ BE2 {model.trained_through} — 같은 규칙으로 얼려야 한다")
    if sorted(int(x) for x in meta.get("seeds", [])) != sorted(model.seeds):
        raise SystemExit(f"C0 시드 {meta.get('seeds')} ≠ BE2 시드 {list(model.seeds)} — 판정 거부")
    import lightgbm as lgb
    c0: dict[int, Any] = {}
    for seed in model.seeds:
        name = meta["files"]["gbm"][str(seed)]
        path = folder / name
        if not path.exists() or be2_module.file_digest(path) != meta["sha256"].get(name):
            raise SystemExit(f"C0 파일 {name} 이 없거나 지문이 사이드카와 다르다 — 판정 거부")
        c0[int(seed)] = lgb.Booster(model_file=str(path))
    print(f"  BE2 {BE2_STEM} · C0 {C0_STEM} 사이드카 해시·파일 지문 대조 ○ (시드 {list(model.seeds)})", flush=True)
    return model, c0


def be2_predictions(store: Store, sessions: list[date], model: Any, c0: dict[int, Any], *,
                    as_of_of: Any) -> dict[str, dict[int, pd.DataFrame]]:
    """세션마다 창고 `fa_features`(그 세션 as_of 까지 관측된 것만)로 BE2·C1·C0 시드별 예측. 실전 be2 Analyst 와 같은 함수.

    한 세션이라도 채점하지 못하면 **멈춘다** — 창을 조용히 줄이면 판정 창이 등록과 달라진다.
    """
    arms: dict[str, dict[int, list[pd.DataFrame]]] = {a: {int(s): [] for s in model.seeds} for a in ("BE2", "C1", "C0")}
    failed: list[str] = []
    for day in sessions:
        batch = be2_module.session_batch(store, "KR", as_of_of(day))
        if isinstance(batch, str):
            failed.append(f"{day}: {batch}")
            continue
        if batch.session != day:
            failed.append(f"{day}: as_of 가 다른 세션({batch.session})을 가리킨다")
            continue
        x0 = batch.today.set_index("entity_id").loc[batch.entities, list(C0_FEATURES)].to_numpy(np.float32)
        base = pd.DataFrame({"entity_id": batch.entities, "session": day})
        for seed, (be1, c1) in model.seed_predictions(batch.x, batch.flat).items():
            arms["BE2"][int(seed)].append(base.assign(pred=(be2_module.percentile(be1) + be2_module.percentile(c1)) / 2.0))
            arms["C1"][int(seed)].append(base.assign(pred=np.asarray(c1, dtype=float)))
            arms["C0"][int(seed)].append(base.assign(pred=np.asarray(c0[int(seed)].predict(x0), dtype=float)))
    if failed:
        raise SystemExit(f"금고 창 {len(failed)}세션을 채점하지 못했다 — 창을 조용히 줄이지 않는다(score_be2 --features-only 로 채운다):\n  "
                         + "\n  ".join(failed[:10]))
    return {a: {s: pd.concat(v, ignore_index=True) for s, v in by.items()} for a, by in arms.items()}


def run_be2(store: Store, *, confirm: bool = False) -> tuple[list[str], str]:
    """BE2 — 얼린 모델 × 실전 경로 FA × 국장 포트(등록 채점 규칙). 대조 C1(BE2 파일 안 GBM)·C0(얼린 6점수 GBM)."""
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock
    from tools.trial_lambdarank import top_overlap

    model, c0 = frozen_be2(frozen_hashes("BE2"))
    sessions = list(trading_days(Market.KR, VAULT_START, VAULT_END))
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    preds = be2_predictions(store, sessions, model, c0, as_of_of=policy.for_session)
    panel, _ = kr_loop_panel()
    y = panel[["entity_id", "session", "y5"]].rename(columns={"y5": "target"})
    del panel
    ret, bench, trad = market_data(store, sessions, cache=VAULT)
    res: dict[str, list[dict[str, float]]] = {}
    for arm, by_seed in preds.items():
        res[arm] = []
        for seed in model.seeds:
            daily, extra = portfolio(by_seed[int(seed)], ret, trad, every=EVERY)
            row = stats(daily, bench, extra)
            row["ic"] = _ic(by_seed[int(seed)], y)
            res[arm].append(row)
        print(f"  {arm}: 연 {_mean(res[arm], 'ann'):+.1%} · IC {_mean(res[arm], 'ic'):+.4f}", flush=True)
    lines, verdict = judge_be2(res, confirm=confirm)
    overlap = np.mean([top_overlap(preds["BE2"][int(s)], preds["C0"][int(s)]) for s in model.seeds])
    lines.append(f"기록(기준 아님) 채점 세션 {len(sessions)} · 상위 24 겹침 BE2 대 C0 {overlap:.0%} · "
                 f"β BE2 {_mean(res['BE2'], 'beta'):+.2f} 대 C0 {_mean(res['C0'], 'beta'):+.2f} · 입력 = 실전 경로 fa_features(miss_ba as_of)")
    return lines, verdict


# --------------------------------------------------------------------------- DF2 (df2-2026-10.md — 두 번째 금고, 국장)


def df2_fire(now: pd.DataFrame, prev: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """확인 발동 — 그 세션과 **바로 앞 세션** 모두 p_위기 > 문턱. 해제는 즉시(둘 중 하나라도 아니면 끔).

    ``now``·``prev`` = `trial_next_four.attach_probs` 의 결과(session·market·p0…) — prev 는 한 세션 밀린 확률을 붙인 것.
    반환: session·market·fire(bool). 확률이 없는 행은 발동 아님이 아니라 **멈춘다**(require_probs 가 먼저 거른다).
    """
    m = now[["session", "market", "p0"]].merge(prev[["session", "market", "p0"]], on=["session", "market"],
                                               suffixes=("", "_prev"), how="left")
    if m[["p0", "p0_prev"]].isna().any().any():
        bad = m.loc[m[["p0", "p0_prev"]].isna().any(axis=1), "session"].head(3).tolist()
        raise ValueError(f"DF2: 확률이 없는 세션이 있다(예: {bad}) — 발동 아님으로 치지 않는다")
    m["fire"] = (m["p0"].to_numpy(np.float64) > threshold) & (m["p0_prev"].to_numpy(np.float64) > threshold)
    return m[["session", "market", "fire"]].reset_index(drop=True)


def df2_weights(fire: pd.DataFrame, blend: float) -> pd.DataFrame:
    """확인 발동 세션만 C1 1−blend · BF2 blend, 아니면 C1 1 — `trial_next_four.df_weights` 와 같은 모양(발동 규칙만 다르다)."""
    out = fire[["session", "market"]].copy()
    on = fire["fire"].to_numpy(bool)
    out["C1"] = np.where(on, 1.0 - blend, 1.0)
    out["BF2"] = np.where(on, blend, 0.0)
    return out.reset_index(drop=True)


def rebalance_days(pred: pd.DataFrame, ret: pd.DataFrame, every: int = EVERY) -> list[date]:
    """`trial_ranker_kit.portfolio` 가 명단을 바꾸는 날 — 예측 세션 중 수익이 있는 날의 0·every·2every… 번째."""
    days = [d for d in sorted(pd.unique(pred["session"])) if d in ret.index]
    return days[::every]


def judge_df2(res: dict[str, list[dict[str, float]]], *, fire_sessions: int, fire_rebalances: int,
              held_before: bool = False) -> tuple[list[str], str]:
    """DF2 — 판정 불가 셋을 먼저, 그다음 기준 넷(C1′ 대비, 시드 평균). res[군] = 시드 순서 지표, 군 = DF2·C1′·DF.

    판정 불가(보류, 시행 미소진): (가) 확인 발동 세션 < 5 (나) 발동 든 재조정일 < 1 (다) C1′ 창 MDD 가 −5% 보다 얕다.
    ``held_before`` — 앞 판정이 이미 보류였다. 그러면 이번 보류는 두 번째이고 DF2 를 **닫는다**(등록: 보류 두 번이면 닫음).
    기준: ① MDD 가 1%p 이상 얕다 ② 연 ≥ C1′ − 0.5%p ③ 창을 세션 수 절반으로 가른 두 구간 모두 ≥ C1′ − 1%p ④ 회전 ≤ C1′ × 1.2.
    DF(확인 없는 원래 규칙)는 기록만. 지표 키가 빠지거나 nan 이면 크게 멈춘다.
    """
    for arm in ("DF2", "C1′", "DF"):
        for i, row in enumerate(res[arm]):
            bad = [k for k in DF2_KEYS if k not in row or not np.isfinite(row[k])]
            if bad:
                raise ValueError(f"judge_df2: {arm} 시드 {i} 에 지표 {bad} 가 없다 — 조용히 기각하지 않는다")
    d2, c1, df = res["DF2"], res["C1′"], res["DF"]
    lines = ["| 군 | 연수익(시드 평균) | 시드별 | 전반 | 후반 | MDD | 회전 |", "|---|---|---|---|---|---|---|"]
    for arm, rows in (("DF2 확인 발동", d2), ("C1′ = pct(C1)", c1), ("DF 원래 규칙(기록)", df)):
        lines.append(f"| {arm} | {_mean(rows, 'ann'):+.1%} | " + " / ".join(f"{r['ann']:+.1%}" for r in rows)
                     + f" | {_mean(rows, 'h1'):+.1%} | {_mean(rows, 'h2'):+.1%} | {_mean(rows, 'mdd'):.1%} | {_mean(rows, 'turn'):.1f} |")
    depth = _mean(c1, "mdd")
    lines.append(f"판정 가능 여부: 확인 발동 세션 {fire_sessions} (≥ {DF2_MIN_FIRE}) · 발동 든 재조정일 {fire_rebalances} "
                 f"(≥ {DF2_MIN_FIRE_REBAL}) · C1′ 창 MDD {depth:.1%} (≤ {DF2_MIN_DEPTH:.0%})")
    why = [w for ok, w in ((fire_sessions >= DF2_MIN_FIRE, f"확인 발동 {fire_sessions}세션 < {DF2_MIN_FIRE}"),
                           (fire_rebalances >= DF2_MIN_FIRE_REBAL, f"발동 든 재조정일 {fire_rebalances} < {DF2_MIN_FIRE_REBAL}"),
                           (depth <= DF2_MIN_DEPTH, f"C1′ 창 MDD {depth:.1%} 가 {DF2_MIN_DEPTH:.0%} 보다 얕다")) if not ok]
    if why and held_before:
        return lines, "닫음 — 보류 두 번째(" + " · ".join(why) + ") · 위기를 기다리며 창을 더 쓰지 않는다(등록 규칙)"
    if why:
        return lines, ("보류 — 판정 불가(" + " · ".join(why) + ") · 시행 미소진, 다음 안 본 창에서 같은 기준으로 한 번 더"
                       " (보류가 두 번이면 DF2 를 닫는다)")
    d = {k: _mean(d2, k) - _mean(c1, k) for k in ("ann", "h1", "h2", "mdd")}
    c = (d["mdd"] >= DF2_GATE_MDD, d["ann"] >= DF2_GATE_ANN,
         d["h1"] >= DF2_GATE_HALF and d["h2"] >= DF2_GATE_HALF,
         _mean(d2, "turn") <= _mean(c1, "turn") * DF2_GATE_TURN)
    lines.append(f"①MDD {_mean(d2, 'mdd'):.1%} 대 {_mean(c1, 'mdd'):.1%} ({d['mdd']:+.1%}p, ≥ +1%p) {mark(c[0])} · "
                 f"②연 {d['ann']:+.1%}p (≥ −0.5%p) {mark(c[1])} · ③두 구간 {d['h1']:+.1%}p / {d['h2']:+.1%}p (≥ −1%p) {mark(c[2])} · "
                 f"④회전 {_mean(d2, 'turn'):.1f} 대 {_mean(c1, 'turn'):.1f} (≤ ×1.2) {mark(c[3])}")
    lines.append(f"기록(기준 아님) DF2 − DF 연 {_mean(d2, 'ann') - _mean(df, 'ann'):+.1%}p · MDD {_mean(d2, 'mdd') - _mean(df, 'mdd'):+.1%}p")
    return lines, ("채택 후보 — 방어(①~④), shadow 20세션 뒤 사용자 결정" if all(c) else "기각")


def vault_index_closes(store: Store, market: str, until: date) -> pd.Series:
    """금고 창 끝까지의 지수 종가 — `trial_next_four.index_closes` 와 **같은 읽기**, 금고 거부만 없다(판정부만 부른다).

    HMM 은 `v2_regime_hmm.run`(달마다 그 달 첫 세션 전까지 적합 · 전방 필터)이라 끝을 늘려도 앞 세션의 확률은 바뀌지 않는다.
    """
    from tools import v2_regime_hmm as hmm
    if until > VAULT_END:
        raise ValueError(f"until {until} 이 금고 창 끝({VAULT_END}) 뒤다")
    end = datetime.combine(until, time(23), tzinfo=UTC)
    idx = store.get("indices", as_of=end, lookback=(until - date(2020, 1, 1)).days,
                    market=market, columns=["entity_id", "valid_from", "close"])
    idx = idx[idx["entity_id"] == hmm.INDEX[market]].assign(day=lambda f: pd.to_datetime(f["valid_from"]).dt.date)
    closes = idx.groupby("day")["close"].last().sort_index()
    return closes[(closes > 0) & (closes.index <= until)]


def frozen_df2(*, folder: Path | None = None, bf1_folder: Path | None = None,
               bf1_hash: str | None = None) -> tuple[Any, dict[int, Any]]:
    """DF2 의 얼린 모델 — C1 = BE2 파일 안 GBM(해시는 앞당김 문서 '모델 해시'), BF1 = `freeze_be2 --arm BF1` 사이드카."""
    folder = folder or BE2_MODELS
    bf1_folder = bf1_folder or BF1_MODELS
    bf1_hash = bf1_hash if bf1_hash is not None else BF1_SIDECAR_HASH
    expected = frozen_hashes("BE2")
    if BE2_STEM not in expected:
        raise SystemExit(f"앞당김 등록 문서에 {BE2_STEM} 해시가 없다 — 판정 거부")
    be2_path = folder / f"{BE2_STEM}.json"
    if not be2_path.exists() or _digest(be2_path) != expected[BE2_STEM]:
        raise SystemExit(f"{be2_path} 가 없거나 해시가 문서({expected[BE2_STEM]})와 다르다 — 판정 거부")
    model = be2_module.Be2Model.load(be2_path)
    problems = model.problems()
    if problems:
        raise SystemExit("얼린 BE2(C1 GBM) 를 쓸 수 없다 — 판정 거부: " + "; ".join(problems))
    if bf1_hash is None:
        raise SystemExit("BF1_SIDECAR_HASH 가 없다 — tools/freeze_be2.py --arm BF1 --verify 뒤 해시를 적어야 한다(판정 거부)")
    path = bf1_folder / f"{BF1_STEM}.json"
    if not path.exists() or _digest(path) != bf1_hash:
        raise SystemExit(f"{path} 가 없거나 해시가 고정값({bf1_hash})과 다르다 — 얼린 모델을 다시 학습하지 않는다(판정 거부)")
    meta = json.loads(path.read_text(encoding="utf-8"))
    if tuple(meta.get("features", ())) != tuple(model.features):
        raise SystemExit("BF1 열이 C1(FA 76) 과 다르다 — 판정 거부")
    if meta.get("trained_through") != model.trained_through.isoformat():
        raise SystemExit(f"BF1 자르는 날 {meta.get('trained_through')} ≠ C1 {model.trained_through} — 판정 거부")
    if sorted(int(x) for x in meta.get("seeds", [])) != sorted(model.seeds):
        raise SystemExit(f"BF1 시드 {meta.get('seeds')} ≠ C1 시드 {list(model.seeds)} — 판정 거부")
    import lightgbm as lgb
    bf1: dict[int, Any] = {}
    for seed in model.seeds:
        name = meta["files"]["rank"][str(seed)]
        fp = bf1_folder / name
        if not fp.exists() or be2_module.file_digest(fp) != meta["sha256"].get(name):
            raise SystemExit(f"BF1 파일 {name} 이 없거나 지문이 사이드카와 다르다 — 판정 거부")
        bf1[int(seed)] = lgb.Booster(model_file=str(fp))
    print(f"  C1 {BE2_STEM} · BF1 {BF1_STEM} 사이드카 해시·파일 지문 대조 ○ (시드 {list(model.seeds)})", flush=True)
    return model, bf1


def df2_predictions(store: Store, sessions: list[date], model: Any, bf1: dict[int, Any], *,
                    as_of_of: Any) -> dict[str, dict[int, pd.DataFrame]]:
    """세션마다 창고 `fa_features`(그 세션 as_of 까지)로 C1·BF1 시드별 원점수 — BE2 금고와 **같은** `session_batch`.

    한 세션이라도 채점 못 하면 멈춘다(창을 조용히 줄이지 않는다). 반환 {C1·BF1: {시드: entity_id·session·market·pred}}.
    """
    arms: dict[str, dict[int, list[pd.DataFrame]]] = {a: {int(s): [] for s in model.seeds} for a in ("C1", "BF1")}
    failed: list[str] = []
    for day in sessions:
        batch = be2_module.session_batch(store, "KR", as_of_of(day))
        if isinstance(batch, str):
            failed.append(f"{day}: {batch}")
            continue
        if batch.session != day:
            failed.append(f"{day}: as_of 가 다른 세션({batch.session})을 가리킨다")
            continue
        base = pd.DataFrame({"entity_id": batch.entities, "session": day, "market": "KR"})
        for seed in model.seeds:
            arms["C1"][int(seed)].append(base.assign(pred=model.predict_gbm(int(seed), batch.flat)))
            arms["BF1"][int(seed)].append(base.assign(pred=np.asarray(bf1[int(seed)].predict(batch.flat), dtype=float)))
    if failed:
        raise SystemExit(f"금고 창 {len(failed)}세션을 채점하지 못했다 — 창을 조용히 줄이지 않는다:\n  " + "\n  ".join(failed[:10]))
    return {a: {s: pd.concat(v, ignore_index=True) for s, v in by.items()} for a, by in arms.items()}


def df2_prev(probs: pd.DataFrame) -> pd.DataFrame:
    """바로 앞 세션의 확률 — HMM 표(지수 세션 축)를 한 행 민다. 국장 지수 세션 = 국장 세션이라 '앞 세션' 이 된다."""
    return probs.assign(p0=probs["p0"].shift(1))


def df2_results(preds: dict[str, dict[int, pd.DataFrame]], probs: pd.DataFrame, ret: pd.DataFrame,
                bench: pd.Series, trad: dict[date, set[str]], seeds: list[int]) -> tuple[list[str], dict[str, Any]]:
    """채점된 C1·BF1 원점수 → DF2·C1′·DF 국장 포트 지표(시드별). 창고를 안 읽는다(합성 스모크가 이 함수를 그대로 돈다).

    반환: (기록 줄, {res, fire_sessions, fire_rebalances, overlap}). 발동 세는 값은 첫 시드에서 — 발동은 HMM 만 보므로 시드와 무관하다.
    """
    from tools import trial_next_four as nf
    from tools.trial_lambdarank import top_overlap

    prev = df2_prev(probs)
    res: dict[str, list[dict[str, float]]] = {"DF2": [], "C1′": [], "DF": []}
    lines: list[str] = []
    fire_sessions = fire_rebal = 0
    overlaps = []
    for seed in seeds:
        c1 = preds["C1"][int(seed)]
        pcts = nf.pct_frame({"C1": c1, "BF2": nf.bf2_scores(preds["BF1"][int(seed)], c1)})
        now = nf.attach_probs(pcts, {"KR": probs})
        before = nf.attach_probs(pcts, {"KR": prev})
        if not lines:
            lines = nf.require_probs(now)                 # rc 8 — 그 날짜 확률 < 95% 또는 빈 세션
        fire = df2_fire(now, before, nf.DF_THRESHOLD)
        arms = {"DF2": nf.weighted(pcts, df2_weights(fire, nf.DF_BLEND)),
                "C1′": nf.weighted(pcts, nf.constant_weights(pcts, C1=1.0)),
                "DF": nf.weighted(pcts, nf.df_weights(now.dropna(subset=["p0"])))}
        if seed == seeds[0]:
            on = set(fire.loc[fire["fire"], "session"])
            fire_sessions = len(on)
            fire_rebal = sum(d in on for d in rebalance_days(arms["DF2"], ret))
            lines.append(f"기록: 확인 발동 {fire_sessions}/{len(fire)}세션 · 원래 규칙(DF) 발동 "
                         f"{int((now['p0'] > nf.DF_THRESHOLD).sum())}세션 · 발동 든 재조정일 {fire_rebal}")
        for arm, pred in arms.items():
            daily, extra = portfolio(pred[["entity_id", "session", "pred"]], ret, trad, every=EVERY)
            res[arm].append(stats(daily, bench, extra))
        overlaps.append(top_overlap(arms["DF2"][["entity_id", "session", "pred"]], arms["C1′"][["entity_id", "session", "pred"]]))
        del pcts, arms
    return lines, {"res": res, "fire_sessions": fire_sessions, "fire_rebalances": fire_rebal,
                   "overlap": float(np.mean(overlaps))}


def run_df2(store: Store) -> tuple[list[str], str]:
    """DF2 — 얼린 C1·BF1 × 실전 경로 FA × 금고 HMM(같은 규칙) × 국장 포트. 대조 C1′ = pct(C1), DF 는 기록.

    앞 DF2 판정이 보류가 아닌 무엇이면(채택 후보·기각·닫음) 다시 돌지 않는다 — 시행은 1회다.
    """
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock
    from tools import trial_next_four as nf

    prior = prior_verdict(store, "DF2")
    if prior is not None and not prior.startswith("보류"):
        raise SystemExit(f"DF2 는 이미 판정됐다({prior!r}) — 1회 시행이라 다시 돌지 않는다")
    model, bf1 = frozen_df2()
    sessions = list(trading_days(Market.KR, VAULT_START, VAULT_END))
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    preds = df2_predictions(store, sessions, model, bf1, as_of_of=policy.for_session)
    probs = nf.regime_probs(vault_index_closes(store, "KR", VAULT_END))
    ret, bench, trad = market_data(store, sessions, cache=VAULT)
    fire_lines, out = df2_results(preds, probs, ret, bench, trad, [int(s) for s in model.seeds])
    res = out["res"]
    lines, verdict = judge_df2(res, fire_sessions=out["fire_sessions"], fire_rebalances=out["fire_rebalances"],
                               held_before=prior is not None)
    lines = fire_lines + lines + [
        f"기록(기준 아님) 채점 세션 {len(sessions)} · 상위 24 겹침 DF2 대 C1′ {out['overlap']:.0%} · β DF2 "
        f"{_mean(res['DF2'], 'beta'):+.2f} 대 C1′ {_mean(res['C1′'], 'beta'):+.2f} · 입력 = 실전 경로 fa_features · "
        f"HMM = 금고 창 끝까지 같은 규칙 · 앞 판정 {prior or '없음'}"]
    return lines, verdict


# --------------------------------------------------------------------------- P1-b′ (p1b-prime-2026-10.md — 두 번째 금고, 국장)


def p1b_stats(daily: pd.Series, turn: pd.Series, uni: pd.Series, days: list[date]) -> dict[str, float]:
    """변형 하나·시드 하나의 지표 — 비용 후 연수익·연회전·MDD 와 **비용 후 선택 IR**(유니버스 EW 대비, 진단 ② 와 같은 정의)."""
    r = daily.reindex(days).fillna(0.0)
    sel = r - uni.reindex(days).fillna(0.0)
    vol = float(sel.std() * np.sqrt(ANN))
    nav = (1.0 + r).cumprod()
    return {"ann": float(r.mean() * ANN), "turn": float(turn.reindex(days).fillna(0.0).sum() * ANN / len(days)),
            "mdd": float((nav / nav.cummax() - 1.0).min()),
            "sel_ann": float(sel.mean() * ANN), "sel_ir": float(sel.mean() * ANN / vol) if vol > 0 else float("nan")}


def p1b_results(preds: dict[int, pd.DataFrame], ret: pd.DataFrame, bench: pd.Series, trad: dict[date, set[str]],
                sessions: list[date], seeds: list[int]) -> dict[str, Any]:
    """얼린 C0 시드별 원점수(데운 세션 포함) → EMA5 → 변형 셋의 롱 다리(`diag_style_hedge_cost.long_leg`, 등록 그대로).

    창고를 안 읽는다(합성 스모크가 이 함수를 그대로 돈다). 반환 {res: {변형: [시드별 지표]}, daily: {변형: 시드 평균 일수익},
    days, uni, style}. 데운 세션(창 첫 세션 앞)은 EMA 만 데우고 장부에 들이지 않는다.
    """
    from tools.diag_style_hedge_cost import long_leg
    from tools.trial_ranker_kit import SPAN

    first = sessions[0]
    legs: dict[str, dict[int, pd.Series]] = {a: {} for a in P1B_ARMS}
    turns: dict[str, dict[int, pd.Series]] = {a: {} for a in P1B_ARMS}
    for seed in seeds:
        wide = preds[int(seed)].pivot_table(index="session", columns="entity_id", values="pred").sort_index()
        wide = wide.ewm(span=SPAN).mean()
        wide = wide[wide.index >= first]
        for arm, (n, every, mult, theta) in P1B_ARMS.items():
            daily, turn, _ = long_leg(wide, ret, trad, n, every=every, mult=mult, theta=theta)
            legs[arm][int(seed)], turns[arm][int(seed)] = daily, turn
        del wide
    days = sorted(set().union(*(s.index for by in legs.values() for s in by.values())))
    if not days:
        raise ValueError("P1-b′: 장부가 한 세션도 돌지 않았다 — 조용히 기각하지 않는다")
    uni = pd.Series({d: float(ret.loc[d].reindex(list(trad.get(d, ()))).mean()) for d in days if d in ret.index}).reindex(days)
    res = {a: [p1b_stats(legs[a][int(s)], turns[a][int(s)], uni, days) for s in seeds] for a in P1B_ARMS}
    daily = {a: pd.DataFrame(by).reindex(days).fillna(0.0).mean(axis=1) for a, by in legs.items()}
    style = float((uni - bench.reindex(days)).fillna(0.0).mean() * ANN)
    return {"res": res, "daily": daily, "days": days, "uni": uni, "style": style}


def judge_p1b(res: dict[str, list[dict[str, float]]], daily: dict[str, pd.Series]) -> tuple[list[str], str]:
    """P1-b′ — 변형(B1′·B2′)마다 B0 대비 기준 넷. 둘 다 통과하면 비용 후 선택 IR 이 높은 쪽 하나가 후보(사용자 결정).

    ③ 은 등록대로 second 창이 급등 국면 하나뿐이라 창 전체 비교다. 지표 키가 빠지거나 nan 이면 크게 멈춘다.
    """
    from quant_rl_trading.analysts import ic as ic_module

    for arm in P1B_ARMS:
        for i, row in enumerate(res[arm]):
            bad = [k for k in P1B_KEYS if k not in row or not np.isfinite(row[k])]
            if bad:
                raise ValueError(f"judge_p1b: {arm} 시드 {i} 에 지표 {bad} 가 없다 — 조용히 기각하지 않는다")
    b0 = res["B0"]
    lines = ["| 변형 | 비용 후 연(시드 평균) | 시드별 | 선택 IR | 연회전 | MDD |", "|---|---|---|---|---|---|"]
    for arm, rows in res.items():
        lines.append(f"| {arm} | {_mean(rows, 'ann'):+.1%} | " + " / ".join(f"{r['ann']:+.1%}" for r in rows)
                     + f" | {_mean(rows, 'sel_ir'):+.2f} | {_mean(rows, 'turn'):.1f} | {_mean(rows, 'mdd'):.1%} |")
    passed = []
    for arm in (a for a in P1B_ARMS if a != "B0"):
        rows = res[arm]
        d = _mean(rows, "ann") - _mean(b0, "ann")
        t = float(ic_module.newey_west_t(daily[arm] - daily["B0"], lag=4))
        wins = sum(r["ann"] > c["ann"] for r, c in zip(rows, b0, strict=True))
        turn_ratio = _mean(rows, "turn") / _mean(b0, "turn")
        deeper = _mean(b0, "mdd") - _mean(rows, "mdd")
        c = (d >= P1B_GATE_ANN and t >= P1B_GATE_T, wins >= P1B_GATE_SHARE, d >= P1B_GATE_REGIME,
             turn_ratio <= P1B_GATE_TURN and deeper <= P1B_GATE_MDD)
        lines.append(f"{arm}: ①연 {d:+.1%}p (≥ +1%p) · NW t {t:+.2f} (≥ 2.0) {mark(c[0])} · ②시드 {wins}/{len(rows)} (≥ 4) {mark(c[1])} · "
                     f"③창 전체 {d:+.1%}p (≥ −1%p) {mark(c[2])} · ④회전비 {turn_ratio:.2f} (≤ 0.6) · MDD {deeper:+.1%}p 깊음 "
                     f"(≤ 2%p) {mark(c[3])}")
        if all(c):
            passed.append(arm)
    if not passed:
        return lines, "기각"
    best = max(passed, key=lambda a: _mean(res[a], "sel_ir"))
    return lines, f"채택 후보 {best} — ①~④ 통과({', '.join(passed)}), 후보이지 실전 전환이 아니다(사용자 결정)"


def p1b_predictions(store: Store, sessions: list[date], c0: dict[int, Any], *, as_of_of: Any) -> dict[int, pd.DataFrame]:
    """세션마다 창고 `fa_features` 로 얼린 C0 시드별 원점수 — BE2 금고와 같은 `session_batch`·같은 열. 구멍이면 멈춘다."""
    parts: dict[int, list[pd.DataFrame]] = {int(s): [] for s in c0}
    failed: list[str] = []
    for day in sessions:
        batch = be2_module.session_batch(store, "KR", as_of_of(day))
        if isinstance(batch, str):
            failed.append(f"{day}: {batch}")
            continue
        if batch.session != day:
            failed.append(f"{day}: as_of 가 다른 세션({batch.session})을 가리킨다")
            continue
        x0 = batch.today.set_index("entity_id").loc[batch.entities, list(C0_FEATURES)].to_numpy(np.float32)
        base = pd.DataFrame({"entity_id": batch.entities, "session": day})
        for seed, booster in c0.items():
            parts[int(seed)].append(base.assign(pred=np.asarray(booster.predict(x0), dtype=float)))
    if failed:
        raise SystemExit(f"P1-b′ {len(failed)}세션을 채점하지 못했다 — 창을 조용히 줄이지 않는다:\n  " + "\n  ".join(failed[:10]))
    return {s: pd.concat(v, ignore_index=True) for s, v in parts.items()}


def run_p1b(store: Store) -> tuple[list[str], str]:
    """P1-b′ — 얼린 C0(시드 0~4) × 실전 경로 FA × 진단 ② 롱 다리 × 변형 셋(B0 대조). 시행은 1회 — 앞 판정이 있으면 멈춘다."""
    from datetime import timedelta

    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.collectors.publication import publication_policy
    from quant_rl_trading.replay.clock import LiveClock

    prior = prior_verdict(store, "P1B")
    if prior is not None:
        raise SystemExit(f"P1-b′ 는 이미 판정됐다({prior!r}) — 1회 시행이라 다시 돌지 않는다")
    _, c0 = frozen_be2(frozen_hashes("BE2"))          # C0 사이드카 해시는 앞당김 문서 '모델 해시'(2f856986d7e668a8)
    sessions = list(trading_days(Market.KR, VAULT_START, VAULT_END))
    warm = list(trading_days(Market.KR, VAULT_START - timedelta(days=60), VAULT_START - timedelta(days=1)))[-P1B_WARM:]
    policy = publication_policy(store, Market.KR, clock=LiveClock())
    preds = p1b_predictions(store, [*warm, *sessions], c0, as_of_of=policy.for_session)
    ret, bench, trad = market_data(store, sessions, cache=VAULT)
    out = p1b_results(preds, ret, bench, trad, sessions, sorted(c0))
    lines, verdict = judge_p1b(out["res"], out["daily"])
    lines.append(f"기록(기준 아님) 장부 세션 {len(out['days'])} · 데운 세션 {len(warm)} · 선택 항(B0) "
                 f"{_mean(out['res']['B0'], 'sel_ann'):+.1%} · 스타일 항(유니버스 EW − K200) {out['style']:+.1%} · "
                 f"점수 = 얼린 C0 원점수 · 수익 = trial_ranker_kit.market_data(편도 {ONE_WAY_COST:.2%})")
    return lines, verdict


RUNNERS = {"AQ": run_aq, "AR": run_ar, "AS": run_as, "BD": run_bd, "BE2": run_be2, "DF2": run_df2, "P1B": run_p1b}


# --------------------------------------------------------------------------- 기록


def record_verdicts(store: Store, results: list[tuple[str, str, list[str]]], *, save: bool,
                    win: Window | None = None) -> int:
    """판정을 `research_trials` 에 1행씩, 금고 개봉을 `holdout_access` 에 1행 적는다. ``save`` 가 아니면 아무것도 안 적는다.

    원래 등록 창이면 protocol_hash = 시행 문서 해시(그대로). 앞당김 창이면 protocol_hash = **앞당김 등록 문서** 해시이고
    시행 문서 해시는 detail 에 적는다 — 이번 개봉을 지배한 것은 창을 옮긴 문서이고, 그 문서가 시행 문서 해시를 고정한다.
    다중검정 기록: 각 행과 개봉 행에 "한 번에 연 시행 수" 를 적는다(self-improvement §1③ 누적 카운터는 시행마다 1).
    """
    if not save:
        print("\n--save 가 없다 — 창고에 아무것도 적지 않았다(시행 미소진).", flush=True)
        return 0
    early = win is not None and win.protocol is not None
    batch = f"금고 {win.name if win else 'registered'} {VAULT_START}~{VAULT_END} · 동시 개봉 {len(results)}시행"
    for trial, verdict, lines in results:
        own = hashlib.sha256(protocol_path(trial).read_bytes()).hexdigest()[:16]
        #: 따로 등록돼 붙은 시행(DF2)은 **제 문서**가 판정을 지배한다 — 앞당김 문서는 그 시행을 모른다.
        governed = early and win is not None and win.protocol is not None and trial not in EXTRA_PROTOCOLS
        digest = _digest(win.protocol) if governed and win is not None and win.protocol is not None else own
        extra = [batch, f"시행 문서 {protocol_path(trial).name} {own}"] if early else []
        record(store, entity=ENTITY[trial], source="vault_judge", family=FAMILY[trial], digest=digest,
               verdict=verdict, lines=[*extra, *lines[-3:]], market="US" if trial == "BD" else "KR", run_tag=trial)
    now = datetime.now(UTC)  # invariant-allow: wallclock — 개봉 시각
    adopted = sum(v.startswith(("채택", "확인 —")) for _, v, _ in results)
    tag = "" if not early or win is None else f"{win.name}-"
    store.append("holdout_access", [{
        "entity_id": f"promotion-review-{tag}{VAULT_END:%Y-%m}", "valid_from": now, "observed_at": now,
        "source": "vault_judge", "market": "KR", "reason": "promotion-review",
        "window_start": VAULT_START.isoformat(),
        "window_end": VAULT_END.isoformat(),
        "detail": (f"vault_judge — {batch} · 채택 {adopted} — " if early else "vault_judge — ")
                  + " / ".join(f"{t} {v}" for t, v, _ in results),
    }], ingest_run_id=f"vault-judge-{now:%Y%m%dT%H%M%S}")
    print(f"\n기록 완료 — research_trials {len(results)}행 · holdout_access 1행(금고는 이제 소진이다).", flush=True)
    return len(results)


# --------------------------------------------------------------------------- 굽기


def bake_plan(win: Window | None = None) -> list[tuple[str, Path, str]]:
    """(설명, 있어야 하는 파일, 그것을 만드는 명령). 인쇄만 해도 순서를 알 수 있게 둔다. 창은 모듈 변수(use_window)."""
    flag = f" --window {win.name}" if win is not None else ""
    trials = trials_of(win) if win is not None else TRIALS
    extra: list[tuple[str, Path, str]] = []
    if "BE2" in trials:
        extra = [
            ("⑥ 얼린 C0(BE2 대조, 6점수 GBM) — 해시를 앞당김 문서 '모델 해시' 에 적은 뒤 해시 고정",
             BE2_MODELS / f"{C0_STEM}.json",
             "setsid nohup .venv/bin/python -u tools/freeze_be2.py --arm C0 --verify >> logs/freeze-c0.log 2>&1 &"),
        ]
    if "DF2" in trials:
        extra.append(("⑦ 얼린 BF1(DF2, LambdaRank) — 사이드카 해시를 vault_judge.BF1_SIDECAR_HASH 에 적는다",
                      BF1_MODELS / f"{BF1_STEM}.json",
                      "setsid nohup scripts/freeze_bf1.sh > /dev/null 2>&1 &   # 조건 대기 · logs/freeze-bf1-*.log"))
    return [
        ("① 국장 점수·거래가능 명단(AQ·AR·AS·BE2)", VAULT / "scores-ranker-KR.pkl",
         f".venv/bin/python tools/vault_judge.py --bake{flag}   # bake_long_panel 을 금고 창으로 직접 부른다"),
        ("①′ 국장 타깃 h5(판정 가능일 이후 — 라벨이 닫힌 뒤)", VAULT / "targets-KR-h5.pkl",
         f".venv/bin/python tools/vault_judge.py --bake{flag}   # 판정 가능일 이후에 다시"),
        # 측정 시점이 달 말일이면 창 끝 h5 라벨이 그 시점에 없어 창 끝 세션이 **채점에서도** 빠진다(10/4 리허설: 미장 57/64).
        # 판정 가능일 달까지 시점을 늘리고(공표된 마지막 세션으로 물러난다) 창 밖 라벨은 버린다. 점검: tools/vault_coverage.py
        ("② 미장 Analyst 점수·타깃(AR·AS·BD) — 판정 가능일 이후", US_WORK,
         f".venv/bin/python tools/backfill_ic_history.py --market US --start {VAULT_START:%Y-%m} "
         f"--end {OPEN_FROM:%Y-%m} --last-session {VAULT_END} --work {US_WORK} --sessions 120"),
        ("③ 국장 원피처(AS)", RAW_DIRS["KR"] / "features-chart-KR.pkl",
         f".venv/bin/python tools/diagnose_ic.py cache-extra --market KR --cache-dir {RAW_DIRS['KR']} "
         f"--analyst {' '.join(RAW_ANALYSTS['KR'])}"),
        ("④ 미장 원피처(AS)", RAW_DIRS["US"] / "features-chart-US.pkl",
         f".venv/bin/python tools/diagnose_ic.py cache-extra --market US --cache-dir {RAW_DIRS['US']} "
         f"--analyst {' '.join(RAW_ANALYSTS['US'])}"),
        ("⑤ 내부자 묶음 G4·G7 월 조각(AR)",
         Path(f"data/_diag/ranker-sources/G7-US-{VAULT_END:%Y%m}.parquet"),  # invariant-allow: data-access — 존재 확인할 작업 파일 경로
         f".venv/bin/python tools/vault_judge.py --bake{flag}   # ranker_sources.build 를 금고 창 세션으로 부른다"),
        *extra,
    ]


def bake(store: Store, win: Window | None = None) -> int:
    """금고 창 패널을 굽는다 — 가벼운 것은 직접, 무거운 둘(②③④)은 선행 명령을 인쇄하고 멈춘다."""
    from quant_rl_trading.collectors.market_hours import Market, trading_days
    from quant_rl_trading.settings import load_env
    from tools.bake_long_panel import bake_scores, bake_targets, bake_tradable

    load_env()
    VAULT.mkdir(parents=True, exist_ok=True)
    judge_from = win.judge_from if win is not None else OPEN_FROM
    print(f"=== 금고 창 패널 굽기 · {VAULT_START} ~ {VAULT_END} · {VAULT} ===", flush=True)
    if _today() >= judge_from:
        bake_targets(store, Market("KR"), VAULT_START, VAULT_END, VAULT)
    else:
        # 타깃 파일은 한 번 구우면 다시 안 굽는다 — 라벨이 닫히기 전에 구우면 창 끝 세션이 빠진 채 굳는다.
        calendar = VAULT / "calendar-KR.pkl"
        if not calendar.exists():
            pd.DataFrame({"session": list(trading_days(Market.KR, VAULT_START, VAULT_END))}).to_pickle(calendar)  # invariant-allow: data-access — 작업 캐시
        print(f"  타깃 h5 는 판정 가능일({judge_from}) 이후에 굽는다 — 지금은 건너뛴다", flush=True)
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
    if win is not None and {"BE2", "DF2", "P1B"} & set(trials_of(win)):
        # BE2·DF2 입력 = 창고 fa_features. 창(60 국장∪미장 세션)이 첫 채점일 앞 약 3개월을 덮어야 한다 — 적재 기록만 본다(값은 안 읽는다).
        from quant_rl_trading.analysts import fa_features
        first = be2_module.time_axis(VAULT_START)[-be2_module.WINDOW:][0]   # 첫 채점일의 60칸 창 첫날
        need = list(trading_days(Market.KR, first, VAULT_END))
        lack = [d for d in need if not store.ingest_run_recorded(fa_features.TABLE, fa_features.run_id("KR", d))]
        if lack:
            print(f"  BE2 FA 피처가 {len(lack)}세션 없다({lack[0]}~{lack[-1]}) — 선행:\n    .venv/bin/python tools/score_be2.py "
                  f"--start {lack[0]} --end {lack[-1]} --features-only", flush=True)
        else:
            print(f"  BE2 FA 피처 {len(need)}세션 적재 확인 ({need[0]}~{need[-1]})", flush=True)
    missing = [(label, cmd) for label, ready, cmd in bake_plan(win) if not ready.exists()]
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
    parser.add_argument("--window", default="early",
                        help="registered(원래 7/1~11/13) · early(앞당김 7/1~9/30) · second(10/1~11/13) — 날짜는 등록 문서가 정한다")
    parser.add_argument("--trials", default=None, help="기본 = 그 창의 시행 전부")
    parser.add_argument("--save", action="store_true", help="research_trials·holdout_access 에 기록")
    args = parser.parse_args(argv)
    known = windows()
    if args.window not in known:
        parser.error(f"창 {args.window!r} 이 없다 — 있는 창: {sorted(known)} (early·second 는 {EARLY_PROTOCOL} 의 '창' 줄)")
    win = known[args.window]
    use_window(win)
    if args.plan:
        state = "고정" if not registration_problems(win) else "초안(미고정) — --bake·--judge 거부"
        print(f"=== 금고 {win.name} 실행 순서 · 창 {win.start}~{win.end} · 굽기 {win.bake_from}~ · 판정 {win.judge_from}~ · "
              f"시행 {','.join(trials_of(win))} · 등록 {state} ===", flush=True)
        plan = bake_plan(win)
        for i, (label, ready, cmd) in enumerate(plan, 1):
            print(f"{i}. {label}\n   있어야 하는 것: {ready}\n   {cmd}", flush=True)
        print(f"{len(plan) + 1}. 판정\n   .venv/bin/python tools/vault_judge.py --judge --window {win.name} "
              f"--trials {','.join(trials_of(win))} --save", flush=True)
        return 0
    if not (args.bake or args.judge):
        parser.error("--plan · --bake · --judge 중 하나")
    trials = [t for t in (args.trials or ",".join(trials_of(win))).split(",") if t]
    unknown = [t for t in trials if t not in trials_of(win) or t not in RUNNERS]
    if unknown:
        parser.error(f"{win.name} 창에서 심사하지 않는 시행: {unknown} (이 창: {list(trials_of(win))})")
    if locked("--bake" if args.bake else "--judge", win):
        return 2
    store = Store(root=Path(args.root))
    opened = consumed(store, win)
    other = [w for w in opened if not w.startswith(f"{win.start}~{win.end}")]
    if args.judge and (other or (opened and args.save)):
        print(f"금고 {win.name}({win.start}~{win.end}) 판정 거부 — 이미 연 창과 겹친다: {opened}. "
              "금고는 한 번 연다(self-improvement.md §1①).", flush=True)
        return 2
    if args.bake:
        return bake(store, win)
    results: list[tuple[str, str, list[str]]] = []
    for trial in trials:
        if win.name in SECOND_NEEDS and trial in SECOND_NEEDS[win.name]:
            prior = prior_verdict(store, trial)
            if prior is None or not prior.startswith(SECOND_NEEDS[win.name][trial]):
                print(f"\n=== 시행 {trial} — {win.name} 창 건너뜀: 앞 개봉 판정이 {prior!r} "
                      f"(이 창은 {SECOND_NEEDS[win.name][trial]} 인 경우만 — 등록 문서 '두 번째 금고') ===", flush=True)
                continue
        digest = hashlib.sha256(protocol_path(trial).read_bytes()).hexdigest()[:16]
        print(f"\n=== 시행 {trial} — {protocol_path(trial)} (해시 {digest}) · 금고 {win.name} {VAULT_START}~{VAULT_END} ===",
              flush=True)
        if trial == "BE2":
            lines, verdict = run_be2(store, confirm=win.name == "second")
        else:
            lines, verdict = RUNNERS[trial](store)
        print("\n" + "\n".join(lines) + f"\n판정: {verdict}", flush=True)
        results.append((trial, verdict, [*lines, f"판정: {verdict}"]))
    print("\n=== 요약 ===", flush=True)
    for trial, verdict, _ in results:
        print(f"{trial}: {verdict}", flush=True)
    record_verdicts(store, results, save=args.save, win=win)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
