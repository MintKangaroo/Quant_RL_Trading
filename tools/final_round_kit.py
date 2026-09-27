"""마지막 모델 회차(시행 BE·BF·BG)가 같이 쓰는 공통 틀 — `docs/protocols/final-model-round-2026-10.md`.

세 시행(트랜스포머 v2 · LambdaRank v2 · 잔차 RL)과 대조군 둘(C0·C1)이 **같은 패널·같은 블록·같은 포트·같은 판정**을
쓰게 하는 것이 이 파일의 전부다. 9/22 복기의 실수 하나가 "도구마다 판정 창·평활 규칙을 따로 써서 같은 산출물이라던
T0·B0·C0 이 1.6~1.7%p 씩 달랐다" 였다 — `tools/trial_ranker_kit.py` 가 랭커 계열에서 그것을 모았고, 이 파일은
**전 피처(FA) 패널**과 **국장+미장 합동 판정**까지 같은 방식으로 모은다.

규칙은 베끼지 않는다. 패널·워크포워드·포트·지표는 전부 기존 부품을 부른다:

- 국장 점수 6 + y5 — 확장 패널(`data/_diag-long`), `trial_ranker_ensemble.SOURCES` 와 같은 파일·같은 인덱스 정렬 concat.
- 미장 점수 6 + y5 — `trial_us_kit.us_panel`(거래대금 상위 1,000 · 보통주·ADR · 시행 AT 와 같은 패널).
- 원피처 — `trial_raw_feature_ranker.raw_features`(시행 W 와 같은 캐시·같은 이름 규칙).
- 묶음 G1~G8 — `tools/trial_ranker_sources.build_panel` 이 구워 둔 월 조각을 **읽기만** 한다(이 파일은 굽지 않는다).
- 밸류업 5 — `trial_kr_valueup.features`(시행 BA 와 같은 공시 규칙).
- 포트·지표 — 국장 `trial_ranker_kit.portfolio`(비용 0.41% 내장) · 미장 `trial_us_kit.book`(비용은 config `accounting.fee_us`),
  요약은 양쪽 다 `trial_ranker_kit.summarize`.

**금고는 열지 않는다.** `window` 끝이 2026-07-01 이상이면 에러로 멈춘다(`check_window`). 금고 창 판정은
`tools/vault_judge.py` 의 일이다.

메모리: 9.7GB 한 대에 FA 패널은 크다. 시장별로 따로 만들어 `data/_diag/final-round/` 아래 `panel-{시장}-{창}`
조각으로 float32 로 굽고, 합치는 것은 마지막에 한 번만 한다. `rss_mb()` 로 최대 RSS 를 재서 보고에 적는다.
"""

from __future__ import annotations

import glob
import os
import sys
from collections.abc import Sequence
from typing import Any
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402
from tools import trial_ranker_kit as rkit  # noqa: E402
from tools import trial_us_kit as ukit  # noqa: E402
from tools.trial_pooled import daily_ic  # noqa: E402
from tools.trial_pooled_rank import rank_gauss  # noqa: E402
from tools.trial_ranker_ensemble import BOX_END, JUDGE_END, JUDGE_START, SOURCES  # noqa: E402
from tools.trial_ranker_ensemble import FEATS as SCORE_FEATS  # noqa: E402
from tools.trial_ranker_sources import GROUPS  # noqa: E402

#: 금고 첫 날. 판정 창은 이 날 **전**에서 끝난다 — 넘기면 에러다.
VAULT_START = date(2026, 7, 1)
#: 확장 패널에서 실제로 쓸 수 있는 첫 날(국장 점수 조각 2021-11-10). 등록 문서의 "2021-08~" 는 굽기 목표였다.
PANEL_FIRST = date(2021, 11, 10)

MIN_TRAIN, BLOCK = rkit.MIN_TRAIN, rkit.BLOCK
#: 퍼지 5(타깃 지평 h5) + 엠바고 5 — 등록 §과적합 억제 1. 학습 끝점은 블록 시작에서 GAP+1 세션 앞이다.
PURGE, EMBARGO = 5, 5
GAP = PURGE + EMBARGO
#: **채점되는 첫 세션의 국장 축 인덱스** = `blocks()[0][0]`. `MIN_TRAIN + PURGE` 다 — **GAP 이 아니다.**
#: GAP(퍼지+엠바고)은 `train_end` 로 학습 끝점만 당기고 블록 시작은 밀지 않는다. 시계열 창 워밍업이 차는지
#: 따질 때 쓰는 수가 이것이고, 2026-09-27 에 이 둘을 헷갈려 155 를 160 으로 적은 자리가 셋 있었다.
FIRST_JUDGED_OFFSET = MIN_TRAIN + PURGE
N, EXIT_MULT, SPAN = rkit.N, rkit.EXIT_MULT, rkit.SPAN
#: 시행 AO(국장)·AU(미장) 가 채택한 10세션 재조정.
REBALANCE_EVERY = 10
#: 학습창의 마지막 이만큼을 조기 종료용 내부 검증으로 뗀다(판정 블록은 건드리지 않는다).
INNER_VAL_SHARE = 0.20
SEEDS = (0, 1, 2, 3, 4)

CACHE = Path("data/_diag/final-round")
LONG_CACHE = rkit.CACHE            # data/_diag-long — 국장 확장 패널
# 국장은 전 창 굽기(scripts/final_round_bake_features.sh, 2026-09-28 04:06 완료 · 2022-04~2026-06 · 1,038세션)로 옮겼다.
# 옛 data/_diag 는 6차 패널 입력(2025-05~)이라 그대로 둔다.
RAW_DIRS = {"KR": Path("data/_diag/kr-long"), "US": Path("data/_diag/w-us")}
SOURCES_CACHE = Path("data/_diag/ranker-sources")
#: 국장 편도 비용은 `trial_overlay.ONE_WAY_COST`(0.41%) 가 포트 함수 안에 박혀 있다. 미장은 config 에서 읽는다.
KR_COST = rkit.ONE_WAY_COST

#: 관문 종료 코드 — 셸이 "무엇이 없어서 안 돌았나" 를 구분해 적을 수 있게 셋을 다르게 둔다.
#: 3 대조군 예측 없음(`require_controls`) · 4 원피처 캐시 미달(`require_full_coverage`) · 5 시계열 창 미달(`require_full_window`).
CONTROLS_EXIT, COVERAGE_EXIT, WINDOW_EXIT = 3, 4, 5

#: 판정 기준 ①~⑥ (등록 §채택 기준).
GATE_MEAN, GATE_SHARE, GATE_REGIME = 0.02, 0.80, -0.01
GATE_IC, GATE_MDD, GATE_TURN, GATE_MODEL = 0.0, 0.02, 1.2, 0.01


# --------------------------------------------------------------------------- 묶음 정의


class _Default:
    """"인자를 안 줬다" 와 "None 을 줬다" 를 가른다 — 기본은 BLOCK_ORDER, None 은 "정의된 전부"."""


DEFAULT = _Default()


@dataclass(frozen=True)
class Group:
    """FA 의 한 묶음. ``cols`` 는 피처, ``flag`` 는 결측 표지 한 칸, ``zero_first`` 는 rank-gauss 전에 0 으로 채울 열."""

    name: str
    cols: tuple[str, ...]
    flag: str | None
    markets: tuple[str, ...]
    zero_first: tuple[str, ...] = ()

    @property
    def feats(self) -> list[str]:
        return [*self.cols, *([self.flag] if self.flag else [])]


def _raw_cols(market: str) -> list[str]:
    """시행 W 와 같은 이름 규칙(raw_{analyst}_{feature}) — 캐시 헤더만 읽는다."""
    out: list[str] = []
    for analyst in ukit_analysts(market):
        path = RAW_DIRS[market] / f"features-{analyst}-{market}.pkl"
        if not path.exists():
            continue
        frame = pd.read_pickle(path)  # invariant-allow: data-access — 진단 캐시(창고 아님)
        out += [f"raw_{analyst}_{c}" for c in frame.columns if c not in ("entity_id", "session")]
        del frame
    return out


def ukit_analysts(market: str) -> tuple[str, ...]:
    from tools.trial_raw_feature_ranker import ANALYSTS
    return ANALYSTS[market]


def blocks_of(markets: Sequence[str] = ("KR", "US"), *,
              include: Sequence[str] | None | _Default = DEFAULT) -> dict[str, Group]:
    """FA 의 묶음 표. 기본은 `BLOCK_ORDER`(G8 제외). `include=None` 이면 정의된 묶음 전부다.

    G10(5% 대량보유)·G11 은 이 회차에 넣지 않는다(리드 결정 2026-09-27) — 별도 판정 대상이고, FA 는 고정이다.
    """
    from tools.trial_kr_valueup import KINDS as VU_KINDS
    from tools.trial_kr_valueup import NEW as VU_NEW

    raw = sorted({c for m in markets for c in _raw_cols(m)})
    table: list[Group] = [
        Group("score", tuple(SCORE_FEATS), None, ("KR", "US")),
        Group("raw", tuple(raw), "miss_raw", ("KR", "US")),
    ]
    for g in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):  # G8 은 BLOCK_ORDER 에서 빠져 기본 include 밖이다
        # 6차 `attach` 규칙 그대로 — 결측은 rank-gauss 뒤 0(순위 중앙). 표지 한 칸이 "자료 자체가 없었다" 를 말한다.
        table.append(Group(g, tuple(GROUPS[g]), f"miss_{g.lower()}", ("KR", "US")))
    table.append(Group("ba", tuple(VU_NEW), "miss_ba", ("KR",), zero_first=tuple(VU_KINDS)))
    # G10(5% 대량보유)·G11 자리: Group("G10", (...), "miss_g10", ("KR", "US")).
    # **이 회차에는 넣지 않는다**(리드 결정 2026-09-27 — 별도 판정 대상이고 FA 는 고정이다). 자리만 비워 둔다.
    keep = set(BLOCK_ORDER) if isinstance(include, _Default) else (set(include) if include else None)
    return {g.name: g for g in table if keep is None or g.name in keep}


#: FA 의 기본 묶음. **G8 은 빠져 있다**(리드 결정 2026-09-27): 월 조각이 아직 없고 6차 G8 판정 자체가 10/4 다.
#: 표지 1(자료 없음)로 조용히 통과하므로, 넣어 둔 채로는 "넣었다" 고 착각한다. 나중에 쓰려면 `include` 에 "G8" 을 더한다.
BLOCK_ORDER = ("score", "raw", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "ba")


# --------------------------------------------------------------------------- 창·메모리


def raw_span(market: str) -> tuple[date, date] | None:
    """`RAW_DIRS[market]` 의 원피처 캐시가 덮는 세션 구간. 없으면 None.

    달력 파일(`calendar-{시장}.pkl`, 몇 KB)만 읽는다 — 피처 pkl 은 수십~수백 MB 라 관문에서 읽을 것이 아니다.
    """
    base = RAW_DIRS.get(market)
    if base is None:
        return None
    cal = base / f"calendar-{market}.pkl"
    probe = base / f"features-{ukit_analysts(market)[0]}-{market}.pkl"
    path = cal if cal.exists() else (probe if probe.exists() else None)
    if path is None:
        return None
    frame = pd.read_pickle(path)  # invariant-allow: data-access — 진단 캐시(창고 아님)
    days = pd.to_datetime(frame["session"]).dt.date
    return (days.min(), days.max()) if len(days) else None


def coverage_ready(window: tuple[date, date] = None, *,  # type: ignore[assignment]
                   markets: Sequence[str] = ("KR", "US")) -> tuple[bool, list[str]]:
    """원피처 캐시가 판정 창을 덮는가 — (통과 여부, 사람이 읽을 줄들). **수익을 보지 않는 관문**이다.

    시행 도구(BE·BF·BG)가 본 측정 앞에 부르는 자리다. 2026-09-27 실측으로 국장 캐시는 2025-05-21~ 뿐이고
    판정 창 976세션 중 271(28%)만 덮었다 — 국장 박스 국면 614세션에는 FA 의 새 재료가 한 칸도 없었다.
    그 상태로 재면 기준 ①~⑤(대 C0)가 사실상 C0 대 C0 이 된다. 전 창 굽기는
    `scripts/final_round_bake_features.sh` 가 하고, 끝나면 `RAW_DIRS["KR"]` 를 그 디렉터리로 바꾼다(리드).
    """
    window = window or (JUDGE_START, JUDGE_END)
    ok, lines = True, []
    for market in markets:
        span = raw_span(market)
        if span is None:
            ok = False
            lines.append(f"{market}: 원피처 캐시가 없다 ({RAW_DIRS.get(market)})")
            continue
        lo, hi = span
        if market == "US":
            # 미장 원피처는 **미장 점수 패널의 달력 그대로** 굽는다(`scripts/bake_w_us_wide.sh`) — 둘이 같은 창이라
            # 견줄 바깥 기준이 없다. 그래서 여기서는 "있다" 까지만 보고, 실제 커버리지는 `--precheck` 의 결측률로 본다.
            lines.append(f"{market}: 캐시 {lo}~{hi} (점수 패널과 같은 창) → 있다 ({RAW_DIRS[market]})")
            continue
        # 국장은 확장 패널(2021-11~)보다 캐시가 훨씬 짧았다 — 이 관문이 잡으려는 구멍이 그것이다.
        covered = lo <= window[0] and hi >= window[1]
        ok = ok and covered
        lines.append(f"{market}: 캐시 {lo}~{hi} · 필요 {window[0]}~{window[1]} → "
                     f"{'덮는다' if covered else '**모자란다** — scripts/final_round_bake_features.sh 가 먼저다'}"
                     f" ({RAW_DIRS[market]})")
    return ok, lines


def require_full_coverage(window: tuple[date, date] = None, *,  # type: ignore[assignment]
                          markets: Sequence[str] = ("KR", "US")) -> None:
    """원피처 캐시가 판정 창을 못 덮으면 **측정을 시작하지 않는다** — `SystemExit(COVERAGE_EXIT)`.

    시행 도구(BE·BF·BG)의 러너가 대조군 관문(`require_controls`, rc=3) 옆에 나란히 부르는 자리다.
    종료 코드를 다르게 둔 이유: 셸이 "대조군이 없다" 와 "재료가 안 구워졌다" 를 구분해 로그에 적어야 한다.
    """
    ok, lines = coverage_ready(window, markets=markets)
    print("굽기 관문 — " + ("통과" if ok else "**미달**"), flush=True)
    for line in lines:
        print(f"  {line}", flush=True)
    if not ok:
        print("판정을 시작하지 않는다. scripts/final_round_bake_features.sh 가 먼저다.", flush=True)
        raise SystemExit(COVERAGE_EXIT)


def require_full_window(axis: Sequence[date], first_judged: date, length: int, *, label: str = "창") -> None:
    """채점되는 첫 세션 앞에 `length` 세션이 있는지 — 없으면 `SystemExit(WINDOW_EXIT)`.

    시계열 창(BE 트랜스포머 60세션)·에피소드를 쓰는 도구가 시드마다 부른다. 지금 규격(`FIRST_JUDGED_OFFSET = 155`)에서는
    넉넉히 남지만, **0 으로 채워지는 창은 아무 경고도 내지 않는다** — rank-gauss 뒤 0 은 "자료 없음" 이 아니라
    "순위 중앙" 으로 보이기 때문이다. `MIN_TRAIN`·`GAP`·등록 창이 바뀌면 조용히 절반이 0 인 창으로 학습하는 대신
    rc 로 나오게 한다(BE 담당 제안, 2026-09-27 — 근거를 주석이 아니라 검사로).

    ``axis`` 는 그 도구가 쓰는 시간 축이다 — 국장 축이 아니라 보통 `all_sessions(panel)`(국장 ∪ 미장)이다.
    """
    before = sum(1 for s in axis if s < first_judged)
    if before < length:
        print(f"{label} 미달 — 채점 첫 세션 {first_judged} 앞에 {before}세션뿐이다(필요 {length}). "
              f"0 으로 채운 창으로 학습하지 않는다.", flush=True)
        raise SystemExit(WINDOW_EXIT)


def check_window(window: tuple[date, date]) -> tuple[date, date]:
    """금고 방벽. 판정 창은 2026-06-30 까지다 — 끝이 2026-07-01 이상이면 멈춘다."""
    start, end = window
    if end >= VAULT_START:
        raise ValueError(f"금고({VAULT_START}~) 행은 넣지 않는다 — window 끝 {end} 는 {VAULT_START} 전이어야 한다")
    if start < PANEL_FIRST:
        raise ValueError(f"확장 패널 첫 날은 {PANEL_FIRST} 다 — window 시작 {start} 은 그보다 뒤여야 한다")
    if start >= end:
        raise ValueError(f"window 가 뒤집혔다: {start} ~ {end}")
    return start, end


def rss_mb() -> float:
    """지금까지의 **최대** RSS(MB). VmHWM 이라 되돌아가지 않는다 — 스모크 보고에 그대로 적는다."""
    try:
        with open("/proc/self/status") as handle:  # invariant-allow: data-access — /proc, 창고 아님
            for line in handle:
                if line.startswith("VmHWM:"):
                    return float(line.split()[1]) / 1024.0
    except OSError:
        pass
    return float("nan")


def _log(message: str) -> None:
    print(f"[final-round] {message} (최대 RSS {rss_mb():.0f}MB)", flush=True)


# --------------------------------------------------------------------------- 학습 진행 기록

#: 진행 기록 표. 이름을 문자열로 흩뿌리지 않는다.
PROGRESS_TABLE = "trial_progress"

#: **적을 수 있는 칸의 전부.** 판정 창의 수익·IC·MDD·회전은 여기에 없다 — 등록 §과적합 억제 5
#: ("학습 구간 성과는 판정에 쓰지 않는다")의 짝이다. 진행 화면이 판정 창 숫자를 비추면
#: 학습이 끝나기 전에 사람이 그것을 읽게 되고, 그 뒤의 판정은 사전등록이 아니다.
#: 판정 결과는 `trial_ranker_kit.record` 가 research_trials 에 한 번만 적는다.
PROGRESS_FIELDS = (
    "market", "seed", "n_seeds", "block", "n_blocks", "fold", "n_folds",
    "step", "epoch", "rounds", "train_loss", "val_loss", "metric", "stopped_early", "elapsed_s", "note",
)
_PROGRESS_INT = ("seed", "n_seeds", "block", "n_blocks", "fold", "n_folds", "epoch", "rounds")


def record_progress(store: Any, clock: Any, trial: str, *,
                    source: str = "final_round_kit", **fields: Any) -> bool:
    """학습 진행 1행 — **블록(또는 폴드) 하나가 끝날 때마다** 부른다. 성공하면 True.

    쓰기 실패가 학습을 죽이지 않는다. 밤새 도는 학습이 창고 잠금이나 디스크 때문에 죽으면
    잃는 것은 진행 표시 한 줄이 아니라 몇 시간이다 — 그래서 예외를 경고로 바꾼다
    (`warnings.warn` — 삼키지는 않는다. 테스트가 그 경고를 본다).

    `store=None` 이면 아무것도 하지 않는다(합성 스모크·테스트에서 기록을 끄는 길).
    시각은 `clock.now()` 로만 얻는다(불변식 2) — 도구는 `LiveClock()` 을 넣는다.

    `train_loss`·`val_loss` 는 **낮을수록 좋은 값**이다. 손실이 아닌 지표로 조기 종료하는 모델은
    부호를 뒤집어 넣고 `metric` 에 원 지표 이름을 적는다(표 주석 참고).

    `PROGRESS_FIELDS` 밖의 이름은 **적지 않고 경고한다.** 판정 창 지표(수익·IC)를 실수로
    넘기는 것을 여기서 막는다 — 표에 칸이 없어 SchemaViolation 이 날 것이지만, 그때는
    이미 "왜 안 적히지" 를 새벽에 뒤지게 된다.
    """
    if store is None:
        return False
    import warnings

    unknown = sorted(set(fields) - set(PROGRESS_FIELDS))
    if unknown:
        warnings.warn(
            f"trial_progress 에 없는 칸 {unknown} — 적지 않는다. 판정 창 지표는 "
            "이 표에 들어가지 않는다(research_trials 가 판정 뒤에 적는다)",
            RuntimeWarning, stacklevel=2,
        )
        return False
    try:
        now = clock.now()
        row: dict[str, object] = {
            "entity_id": str(trial), "valid_from": now, "observed_at": now,
            "source": source,
        }
        for name in PROGRESS_FIELDS:
            value = fields.get(name)
            if value is None:
                row[name] = None
            elif name in _PROGRESS_INT or name == "step":
                row[name] = int(value)
            elif name == "stopped_early":
                row[name] = bool(value)
            elif name in ("train_loss", "val_loss", "elapsed_s"):
                row[name] = float(value)
            else:
                row[name] = str(value)[:200]
        # 한 블록에 파일 하나다. 블록이 몇 분~몇십 분이라 하루 파티션에 수십~수백 개이고
        # rl_updates(업데이트마다 한 행) 와 같은 규모다. **에포크마다 적지 않는다** —
        # 그러면 파일이 수만 개가 되어 창고가 마비된다(us-backfill 파티션 폭발).
        stamp = f"{now:%Y%m%dT%H%M%S%f}"
        run_id = (f"trial-progress-{trial}-s{row.get('seed')}-b{row.get('block')}"
                  f"-f{row.get('fold')}-{stamp}")
        store.append(PROGRESS_TABLE, [row], ingest_run_id=run_id)
        return True
    except Exception as error:  # 진행 기록 한 줄 때문에 밤새 도는 학습이 죽지 않는다
        warnings.warn(f"trial_progress 기록 실패({trial}): {error!r}", RuntimeWarning, stacklevel=2)
        return False


# --------------------------------------------------------------------------- 블록별 자료


def _kr_scores(window: tuple[date, date]) -> pd.DataFrame:
    """국장 점수 6 + y5. `trial_ranker_ensemble.load_panel` 과 **같은 파일·같은 인덱스 정렬 concat** 이되
    h20·h60 을 읽지 않는다 — 275MB 쓰지 않을 타깃을 읽으면 FA 조립 전에 여유가 사라진다."""
    start, end = window
    parts = []
    for name, col in SOURCES:
        f = pd.read_pickle(LONG_CACHE / f"scores-{name}-KR.pkl")  # invariant-allow: data-access — 작업 캐시
        f["session"] = pd.to_datetime(f["session"]).dt.date
        f = f[(f["session"] >= start) & (f["session"] <= end)]
        parts.append(f.set_index(["entity_id", "session"])["score"].astype(np.float32).rename(col))
        del f
    t = pd.read_pickle(LONG_CACHE / "targets-KR-h5.pkl")  # invariant-allow: data-access — 작업 캐시
    t["session"] = pd.to_datetime(t["session"]).dt.date
    t = t[(t["session"] >= start) & (t["session"] <= end)]
    parts.append(t.set_index(["entity_id", "session"])["target"].astype(np.float32).rename("y5"))
    del t
    panel = pd.concat(parts, axis=1, join="outer").reset_index()
    panel["market"] = "KR"
    return panel


def _us_scores(store: Store, window: tuple[date, date]) -> pd.DataFrame:
    """미장 점수 6 + y5 — 시행 AT 와 같은 패널(거래대금 상위 1,000 · 보통주·ADR). `has_fund`·`fund_raw` 는
    피처가 아니라 기록으로 남긴다(미장 합성 M1 을 쓸지는 등록 때 정한다)."""
    start, end = window
    panel, _sessions, _close, _bench = ukit.us_panel(store, start=start, end=end)
    return panel[["entity_id", "session", *SCORE_FEATS, "y5", "has_fund", "fund_raw", "market"]]


def _raw_block(market: str, keys: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """원피처 — 시행 W 의 `raw_features`. `keys` 로 판정 패널 행만 남긴다(W 의 첫 판이 바깥 병합으로 6.7GB OOM 이었다)."""
    from tools.trial_raw_feature_ranker import raw_features
    return raw_features(market, keys=keys, base=RAW_DIRS[market])


def _group_block(group: str, market: str, window: tuple[date, date]) -> pd.DataFrame:
    """묶음 G* 의 월 조각을 **읽기만** 한다. 굽는 것은 `tools/trial_ranker_sources.build_panel` 의 일이다 —
    여기서 굽기 시작하면 국장 2021-11~2025-04 를 Analyst 로 다시 돌려 하룻밤이 날아간다."""
    start, end = window
    cols = list(GROUPS[group])
    paths = sorted(glob.glob(str(SOURCES_CACHE / f"{group}-{market}-*.parquet")))  # invariant-allow: data-access — 창고가 아닌 작업 파일
    parts = []
    for path in paths:
        month = Path(path).stem.rsplit("-", 1)[-1]
        if not (f"{start:%Y%m}" <= month <= f"{end:%Y%m}"):
            continue
        f = pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        if f.empty:
            continue
        f["session"] = pd.to_datetime(f["session"]).dt.date
        f = f[(f["session"] >= start) & (f["session"] <= end)]
        parts.append(f[["entity_id", "session", *[c for c in cols if c in f.columns]]])
        del f
    if not parts:
        return pd.DataFrame(columns=["entity_id", "session", *cols])
    out = pd.concat(parts, ignore_index=True)
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = out[c].astype(np.float32)
    return out.drop_duplicates(["entity_id", "session"], keep="last")


def _ba_block(store: Store, sessions: list[date], entities: set[str]) -> pd.DataFrame:
    """밸류업·주주환원 5 — 시행 BA 의 `features`. 국장 전용(DART 공시)."""
    from tools.trial_kr_valueup import features as vu_features
    return vu_features(store, sessions, entities)


# --------------------------------------------------------------------------- 붙이기


def attach_block(panel: pd.DataFrame, frame: pd.DataFrame, group: Group) -> pd.DataFrame:
    """묶음 하나를 왼쪽 병합으로 붙이고 **결측 표지 한 칸**을 세운다.

    표지는 개별 열의 NaN 이 아니라 **그 (종목, 세션) 키가 묶음 자료에 있었는가** 다. 0 이 "사건이 없었다" 인
    개수 피처(내부자 건수·공시 건수)와 "자료 자체가 없었다" 를 가르는 유일한 방법이다 — 시행 BA 의 교훈이
    "개수 피처는 사건이 아니라 회사 성격을 말한다" 였고, 표지 없이는 그 둘이 한 칸에 섞인다.
    """
    cols = list(group.cols)
    for c in cols:
        if c not in panel.columns:
            panel[c] = np.nan
    if frame is None or frame.empty:
        if group.flag:
            panel[group.flag] = np.float32(1.0)
        return panel
    have = [c for c in cols if c in frame.columns]
    right = frame[["entity_id", "session", *have]].drop_duplicates(["entity_id", "session"], keep="last")
    right["_hit"] = np.float32(1.0)
    merged = panel.merge(right, on=["entity_id", "session"], how="left", suffixes=("", "_new"))
    for c in have:
        new = f"{c}_new" if f"{c}_new" in merged.columns else c
        merged[c] = merged[new].astype(np.float32)
        if new != c:
            merged = merged.drop(columns=[new])
    if group.flag:
        merged[group.flag] = (1.0 - merged["_hit"].fillna(0.0)).astype(np.float32)
    return merged.drop(columns=["_hit"])


def feature_names(groups: dict[str, Group], columns: Sequence[str] | None = None) -> list[str]:
    """모델에 넣는 열 이름 — 묶음 피처 · 결측 표지 · is_us 순. `columns` 를 주면 그 안에 있는 것만."""
    out = [c for g in groups.values() for c in g.cols]
    out += [g.flag for g in groups.values() if g.flag]
    out.append("is_us")
    return [c for c in out if columns is None or c in set(columns)]


def finalize(panel: pd.DataFrame, groups: dict[str, Group]) -> tuple[pd.DataFrame, list[str]]:
    """묶음별 채우기 규칙 → **시장별** rank-gauss → is_us. 결측 표지는 0/1 그대로 둔다(순위로 바꾸면 표지가 아니다)."""
    zero_first = [c for g in groups.values() for c in g.zero_first if c in panel.columns]
    if zero_first:
        panel[zero_first] = panel[zero_first].fillna(0.0)
    scaled = [c for g in groups.values() for c in g.cols if c in panel.columns]
    panel = rank_gauss(panel, [*scaled, "y5"])          # groupby(["market","session"]) — 시장별이다
    flags = [g.flag for g in groups.values() if g.flag]
    for f in flags:
        if f not in panel.columns:
            panel[f] = np.float32(1.0)
        panel[f] = panel[f].fillna(1.0).astype(np.float32)
    panel["is_us"] = (panel["market"] == "US").astype(np.float32)
    feats = [*scaled, *flags, "is_us"]
    return panel, feats


# --------------------------------------------------------------------------- 패널


def _build_market(store: Store, market: str, window: tuple[date, date],
                  groups: dict[str, Group]) -> pd.DataFrame:
    if market == "KR":
        panel = _kr_scores(window)
    else:
        panel = _us_scores(store, window)
    _log(f"{market} 점수 패널 {len(panel):,}행 · 세션 {panel['session'].nunique()}")
    keys = panel[["entity_id", "session"]].drop_duplicates()
    sessions = sorted(panel["session"].unique())
    for name, group in groups.items():
        if name == "score":
            continue
        if market not in group.markets:
            panel = attach_block(panel, pd.DataFrame(), group)
            continue
        if name == "raw":
            frame, _cols = _raw_block(market, keys)
        elif name == "ba":
            frame = _ba_block(store, sessions, set(panel["entity_id"].unique()))
        else:
            frame = _group_block(name, market, window)
        hit = 0 if frame is None or frame.empty else len(
            keys.merge(frame[["entity_id", "session"]].drop_duplicates(), on=["entity_id", "session"]))
        panel = attach_block(panel, frame, group)
        del frame
        _log(f"{market} {name}: 붙은 행 {hit:,} / {len(keys):,} ({hit / max(1, len(keys)):.0%})")
    return panel


def load_full_panel(markets: Sequence[str] = ("KR", "US"),
                    window: tuple[date, date] = (JUDGE_START, JUDGE_END),
                    *, root: str | Path = "data", include: Sequence[str] | None = BLOCK_ORDER,
                    cache_dir: Path = CACHE, rebuild: bool = False,
                    store: Store | None = None) -> tuple[pd.DataFrame, list[str], dict[str, list[str]], list[date]]:
    """FA 패널을 만든다 — (panel, feats, groups, sessions).

    - ``panel`` — entity_id · session · market · y5 · 피처 전부(float32). 시장별 rank-gauss, 결측 표지 0/1.
    - ``feats`` — 모델에 넣는 열 이름 전부(묶음 피처 + 표지 + is_us).
    - ``groups`` — 묶음 이름 → 그 묶음의 열(표지 포함). **묶음 단위 피처 드롭아웃**이 이걸 쓴다.
    - ``sessions`` — 판정 블록을 정하는 세션 목록. 국장이 있으면 국장 세션이다(6차 `judge` 와 같은 규칙).

    시장별로 굽고 `cache_dir` 아래 `panel-{시장}-{창}` 조각으로 캐시한다. 조립은 마지막에 한 번만 — 두 시장을 동시에
    메모리에 올려 rank-gauss 하면 9.7GB 에서 위험하다(시장별 rank-gauss 라 나눠 해도 결과가 같다).
    """
    window = check_window(window)
    store = store or Store(root=Path(root))
    groups = blocks_of(markets, include=include)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 조각 이름에 **시장 묶음**을 넣는다. 원피처 열은 시장 합집합이라 KR 단독으로 구운 조각에는 미장 전용 열이
    # 없고, 그 조각을 KR+US 조립에 쓰면 그 열이 NaN 으로 남는다(등록 규칙은 0 = 순위 중앙).
    tag = f"{'+'.join(markets)}-{window[0]:%Y%m%d}-{window[1]:%Y%m%d}"
    frames = []
    for market in markets:
        path = cache_dir / f"panel-{market}-{tag}.parquet"  # invariant-allow: data-access — 창고가 아닌 작업 파일
        if path.exists() and not rebuild:
            _log(f"{market} 캐시 사용 {path}")
            cached = pd.read_parquet(path)  # invariant-allow: data-access — 창고가 아닌 작업 파일
            # 조각을 다시 읽으면 session 이 datetime64 로 올 수 있다 — 포트·블록이 date 를 키로 쓴다.
            cached["session"] = pd.to_datetime(cached["session"]).dt.date
            frames.append(cached)
            continue
        one = _build_market(store, market, window, groups)
        one, _feats = finalize(one, groups)
        one.to_parquet(path, index=False)  # invariant-allow: data-access — 창고가 아닌 작업 파일
        _log(f"{market} 패널 캐시 {path} · {len(one):,}행 × {one.shape[1]}열")
        frames.append(one)
        del one
    panel = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    del frames
    feats = feature_names(groups)                           # 값은 시장별로 이미 정규화됐다 — 이름만 모은다
    # 조각에 없던 열이 있으면(옛 조각을 섞어 읽는 경우) 등록 규칙대로 메운다: 피처 0(순위 중앙) · 표지 1(자료 없음).
    for name, group in groups.items():
        for c in group.cols:
            if c not in panel.columns:
                panel[c] = np.float32(0.0)
        if group.flag and group.flag not in panel.columns:
            panel[group.flag] = np.float32(1.0)
        del name
    missing = panel[feats].isna().sum()
    if int(missing.sum()):
        for c in missing[missing > 0].index:
            panel[c] = panel[c].fillna(1.0 if c.startswith("miss_") else 0.0).astype(np.float32)
        _log(f"조각 사이 빈 열 {int((missing > 0).sum())}개를 등록 규칙으로 메웠다: {list(missing[missing > 0].index)[:6]}")
    primary = "KR" if "KR" in markets else markets[0]
    sessions = sorted(panel.loc[panel["market"] == primary, "session"].unique())
    _log(f"FA 패널 {len(panel):,}행 · 피처 {len(feats)}개 · 묶음 {len(groups)}개 · 블록 기준 시장 {primary} 세션 {len(sessions)}")
    return panel, feats, {k: g.feats for k, g in groups.items()}, sessions


# --------------------------------------------------------------------------- 블록·내부 검증


def blocks(sessions: list[date]) -> list[tuple[int, int]]:
    """판정 블록 — `trial_ranker_kit.blocks` 그대로다. 시행 AA·AM·AN·BA 와 **같은 블록 경계**를 쓴다.

    엠바고는 블록을 늦추는 것이 아니라 **학습 끝점을 더 당기는 것**으로 실현한다(`train_end`) — 그래야
    등록의 "판정 창은 AA·AB 와 같은 블록" 과 "퍼지 5 + 엠바고 5" 가 둘 다 지켜진다.

    반환은 **국장 세션 목록의 인덱스**다. 인덱스를 날짜 집합으로 바꿔 `isin` 으로 행을 고르면
    **국장 휴장일의 미장 행이 조용히 사라진다** — `block_span`·`block_rows` 를 써라(BE 담당이 찾았다, 2026-09-27).
    """
    return rkit.blocks(sessions)


def block_span(sessions: list[date], first: int, last: int) -> tuple[date, date | None]:
    """블록의 **반열림 날짜 구간** ``[이 블록 시작일, 다음 블록 시작일)``. 끝이 None 이면 위쪽 한계가 없다.

    닫힌 구간 ``[sessions[first], sessions[last]]`` 이던 첫 판은 **이음매에 구멍이 있었다**(BF 담당이 찾았다,
    2026-09-27): 국장 휴장일(미장만 열린 날)이 한 블록의 끝과 다음 블록의 시작 **사이**에 놓이면 그날 미장 행이
    어느 블록에도 안 들었다. 대조군 `walk_gbm` 도 같은 규칙이었으니 대조군만 미장 행을 잃는 것이 아니라
    **모든 군이 같은 날을 잃었다** — 그래도 판정 세션 수가 조용히 줄어드는 것은 그대로다.

    마지막 블록의 끝은 ``sessions[last + 1]``, 즉 **블록에 못 든 국장 나머지 세션의 첫날**이다. 그것이 없으면
    (블록이 국장 세션 끝까지 딱 맞으면) None 이고, 마지막 국장 세션 뒤의 미장 단독 세션까지 마지막 블록이 받는다.
    """
    upper = sessions[last + 1] if last + 1 < len(sessions) else None
    return sessions[first], upper


def block_rows(panel: pd.DataFrame, sessions: list[date], first: int, last: int) -> pd.DataFrame:
    """블록 안의 **모든 시장 행**. 국장 휴장일에만 열린 미장 세션도, 이음매에 놓인 날도 들어온다.

    `sessions` 는 블록 경계를 정하는 국장 축이고 미장은 달력이 다르다. `panel["session"].isin(set(블록 세션))`
    으로 고르면 국장 휴장일의 미장 행이 빠지고, 닫힌 구간으로 고르면 이음매의 날이 빠진다.
    **반열림 구간**(`block_span`)이 둘을 한 번에 막는다.

    블록 하나의 판정 행을 고르는 규칙은 여기에만 있다 — `walk_gbm`(대조군 C0·C1)도, 시행 도구(BE·BF·BG)의
    walk 도 이 함수를 부른다. 규칙이 두 곳에 있으면 대조군과 처리군이 서로 다른 날을 채점한다.
    """
    lo, hi = block_span(sessions, first, last)
    selected = panel["session"] >= lo
    if hi is not None:
        selected &= panel["session"] < hi
    return panel[selected]


def all_sessions(panel: pd.DataFrame) -> list[date]:
    """패널의 **모든 시장 세션 합집합**(국장 ∪ 미장), 정렬. 시계열 창·에피소드의 시간 축은 이것이다.

    블록·`train_end` 는 국장 축(`load_full_panel` 의 네 번째 반환값)을 쓰고, 창·에피소드는 이 축을 쓴다 —
    축 하나로 둘을 다 하면 미장 행이 빠지거나 블록 경계가 시행 AA 와 달라진다.
    """
    return sorted(pd.unique(panel["session"]))


def train_end(sessions: list[date], first: int) -> date:
    """블록 시작 인덱스 `first` 에 대한 학습 끝 세션 — 퍼지 5 + 엠바고 5(GAP=10) 만큼 앞이다.

    기존 랭커 시행은 퍼지 5 만 두었다(`walk` 의 `first - PURGE - 1`). 이 회차는 등록대로 5 를 더 비운다 —
    대조군 C0·C1 도 같은 간격으로 돈다(간격이 다르면 C0 이 "현행" 이 아니라 다른 실험이 된다).
    """
    return sessions[first - GAP - 1]


def inner_split(train: Sequence[date] | pd.DataFrame, *,
                share: float = INNER_VAL_SHARE, purge: int = PURGE) -> tuple[list[date], list[date]]:
    """학습창을 (적합, 내부 검증) 으로 가른다 — 검증은 **마지막 share**, 사이에 퍼지 `purge` 세션.

    조기 종료는 이 검증으로만 한다. 판정 블록은 학습창 뒤에 있으므로 검증은 판정 블록과 절대 겹치지 않는다
    (테스트가 이것을 지킨다). 하이퍼파라미터를 판정 창에서 고르지 않는다는 등록 규칙의 코드 쪽 짝이다.
    """
    sess = sorted(pd.unique(train["session"])) if isinstance(train, pd.DataFrame) else sorted(set(train))
    if len(sess) < 10:
        return list(sess), []
    cut = int(len(sess) * (1.0 - share))
    val = sess[cut:]
    fit = sess[: max(1, cut - purge)]
    return list(fit), list(val)


def drop_groups(groups: dict[str, list[str]], rng: np.random.Generator, p: float,
                *, keep: Sequence[str] = ("score",)) -> list[str]:
    """묶음 단위 피처 드롭아웃 — 이번 스텝에 **0 으로 덮을** 열 이름. `keep` 묶음은 절대 안 뺀다."""
    out: list[str] = []
    for name, cols in groups.items():
        if name in keep:
            continue
        if rng.random() < p:
            out += list(cols)
    return out


# --------------------------------------------------------------------------- 시장 자료·평가


@dataclass
class MarketBook:
    """한 시장의 수익·벤치마크·거래가능 명단·비용. 포트 규칙이 시장마다 다른 유일한 자리다.

    ``fund`` 는 **미장에만** 있다 — (entity_id, session, fund_raw, has_fund). 시행 AT 가 채택한 M1 합성에
    필요하다(`market_books(..., panel=...)` 가 패널에서 떼어 온다).
    """

    market: str
    ret: pd.DataFrame
    bench: pd.Series
    cost: float
    trad: dict[date, set[str]] | None = None
    fund: pd.DataFrame | None = None
    extra: dict[str, object] = field(default_factory=dict)


def market_books(store: Store, sessions: list[date], markets: Sequence[str] = ("KR", "US"), *,
                 panel: pd.DataFrame | None = None) -> dict[str, MarketBook]:
    """시장별 수익·벤치. 국장은 `trial_ranker_kit.market_data`, 미장은 `trial_us_kit` 캐시(없으면 `build` 가 만든다).

    ``panel`` 은 **미장이 포함되면 반드시 준다** — M1 합성에 쓸 `fund_raw`·`has_fund` 가 거기 있다.
    빼면 `evaluate` 가 미장에서 멈춘다(조용히 다른 규칙으로 도는 것보다 낫다).
    """
    out: dict[str, MarketBook] = {}
    if "KR" in markets:
        ret, bench, trad = rkit.market_data(store, sessions)
        out["KR"] = MarketBook("KR", ret, bench, KR_COST, trad)
    if "US" in markets:
        ukit.build(store)
        ret, bench = ukit.market()
        from tools.trial_us_index_minus_losers import cost_one_way
        cost = cost_one_way(store, datetime.combine(sessions[-1], time(23), tzinfo=UTC))
        fund = None
        if panel is not None and {"fund_raw", "has_fund"} <= set(panel.columns):
            rows = panel["market"] == "US"
            fund = panel.loc[rows, ["entity_id", "session", "fund_raw", "has_fund"]].reset_index(drop=True)
        out["US"] = MarketBook("US", ret, bench, cost, fund=fund)
    return out


def us_m1_wide(pred: pd.DataFrame, fund: pd.DataFrame) -> pd.DataFrame:
    """미장 선정 점수 — **시행 AT 채택 M1 합성**(`trial_us_kit.m1_scores`) 뒤 EMA5.

    실전 미장 규칙이 M1 이다(결측 재무를 0 으로 두고 분모에는 남긴다). AT 는 이 규칙 하나로 연 +6.0%p 를
    갈랐고, 후보 24/24 가 재무 없는 외국 발행사로 채워지는 것을 막은 자리다. **모든 군(C0·C1·BE·BF·BG)에
    똑같이 적용한다** — 한 군만 다른 합성을 쓰면 그 군의 우위가 합성 차이인지 모델 차이인지 모른다.
    평활은 합성 **뒤**에 한다: 고르는 데 쓰는 점수가 평활 대상이다.
    """
    p = pred.merge(fund.drop_duplicates(["entity_id", "session"]), on=["entity_id", "session"], how="left")
    p["has_fund"] = p["has_fund"].fillna(False).astype(bool)
    return ukit.m1_scores(p).ewm(span=SPAN).mean()


def evaluate(pred: pd.DataFrame, book: MarketBook, *, y: pd.DataFrame | None = None,
             control: pd.DataFrame | None = None, every: int = REBALANCE_EVERY) -> dict[str, float]:
    """예측 → 표준 지표. `pred` 는 **그 시장 행만** (entity_id · session · pred).

    포트는 등록 규칙: EMA5 · 상위 24 동일가중 · 완충 3N · 10세션 재조정 · 비용은 시장별.
    국장은 `trial_ranker_kit.portfolio`(비용 0.41% 내장) · 미장은 **M1 합성**(`us_m1_wide`) 뒤 `trial_us_kit.book`.
    요약은 두 시장 다 `trial_ranker_kit.summarize` — 국면(박스/급등)·비대칭·IC 가 한 형식으로 나온다.
    `control` 을 주면 상위 24 겹침을 같이 적는다(복기 규칙 ③).
    """
    if pred.empty:
        return {}
    if book.market == "KR":
        daily, extra = rkit.portfolio(pred, book.ret, book.trad, every=every)
    else:
        if book.fund is None:
            raise ValueError("미장 MarketBook 에 fund(fund_raw·has_fund)가 없다 — "
                             "market_books(..., panel=panel) 로 만들어라. M1 합성이 등록 규칙이다")
        wide = us_m1_wide(pred, book.fund)
        daily, extra = ukit.book(wide, book.ret, book.cost, n=N, exit_mult=EXIT_MULT, every=every)
        del wide
    with_y = pred if y is None else pred.merge(y, on=["entity_id", "session"], how="left")
    m = rkit.summarize(daily, book.bench, with_y if "y5" in with_y.columns else None)
    m.update({k: float(v) for k, v in extra.items()})
    if control is not None and not control.empty:
        from tools.trial_lambdarank import top_overlap
        m["overlap"] = top_overlap(pred, control)
    return m


def split_markets(pred: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """예측을 시장별로 가른다. 모델은 `market` 열을 **그대로 들고 나와야 한다** — 포트 규칙이 시장마다 다르다."""
    if "market" not in pred.columns:
        raise ValueError("예측에 market 열이 없다 — 시장별 포트 규칙을 고를 수 없다")
    return {m: part[["entity_id", "session", "pred"]].reset_index(drop=True)
            for m, part in pred.groupby("market")}


def evaluate_all(pred: pd.DataFrame, books: dict[str, MarketBook], *, y: pd.DataFrame | None = None,
                 control: pd.DataFrame | None = None,
                 every: int = REBALANCE_EVERY) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """시장별 지표와 합동 지표를 한 번에 — 세 시행(BE·BF·BG)이 부르는 자리다. ({시장: 지표}, 합동 평균)."""
    ctrl = split_markets(control) if control is not None and not control.empty else {}
    out = {m: evaluate(part, books[m], y=y, control=ctrl.get(m), every=every)
           for m, part in split_markets(pred).items() if m in books}
    return out, pooled_metrics(out)


def pooled_metrics(by_market: dict[str, dict[str, float]], *, weights: dict[str, float] | None = None) -> dict[str, float]:
    """두 시장 지표를 하나로 — 기본은 동일가중 평균. 합동 판정의 주 기준은 시장별로도 같이 본다(등록 §채택 기준)."""
    live = {k: v for k, v in by_market.items() if v}
    if not live:
        return {}
    w = weights or {k: 1.0 / len(live) for k in live}
    keys = set.intersection(*(set(v) for v in live.values()))
    total = sum(w[k] for k in live)
    return {k: float(sum(live[m][k] * w[m] for m in live) / total) for k in sorted(keys)}


# --------------------------------------------------------------------------- 대조군 C0·C1


def walk_gbm(panel: pd.DataFrame, sessions: list[date], feats: list[str], bl: list[tuple[int, int]],
             seeds: Sequence[int], *, label: str = "",
             store: Any = None, clock: Any = None) -> dict[int, pd.DataFrame]:
    """GBM 워크포워드 예측(시드별) — `trial_ranker_kit.fit`(시행 L 하이퍼파라미터) 그대로.

    블록마다 X·y 를 한 번 만들고 시드를 돌린다 — 시드마다 다시 만들면 같은 800MB 배열을 다섯 번 만든다.
    학습 끝점은 `train_end`(퍼지 5 + 엠바고 5). 반환 프레임은 entity_id · session · market · pred.

    판정 행은 **`block_rows` 로만** 고른다 — 대조군과 처리군이 같은 날을 채점하게 하는 자리다(이음매 결함, 2026-09-27).

    `store`·`clock` 을 주면 블록마다 `record_progress` 로 진행을 적는다(대조군 C0·C1 의 진행률).
    GBM 은 조기 종료를 쓰지 않으므로 손실 칸은 비운다 — **0 으로 채우지 않는다**(없는 것과 0 은 다르다).
    """
    parts: dict[int, list[pd.DataFrame]] = {int(s): [] for s in seeds}
    markets = "+".join(sorted(pd.unique(panel["market"]))) if len(panel) else ""
    for number, (first, last) in enumerate(bl):
        end = train_end(sessions, first)
        train = panel[(panel["session"] <= end) & panel["y5"].notna()]
        test = block_rows(panel, sessions, first, last)
        if train.empty or test.empty:
            continue
        X, y = train[feats].to_numpy(np.float32), train["y5"].to_numpy(np.float32)
        Xt = test[feats].to_numpy(np.float32)
        base = test[["entity_id", "session", "market"]].reset_index(drop=True)
        n_train = len(train)
        for s in seeds:
            began = monotonic()  # invariant-allow: wallclock — 블록 소요 시간 기록
            out = base.copy()
            out["pred"] = rkit.fit(X, y, seed=int(s)).predict(Xt)
            parts[int(s)].append(out)
            record_progress(store, clock, label or "GBM", source="final_round_kit.walk_gbm",
                            market=markets, seed=int(s), n_seeds=len(seeds), block=number, n_blocks=len(bl),
                            metric="손실 없음(GBM · 조기 종료를 안 쓴다)",
                            elapsed_s=monotonic() - began,  # invariant-allow: wallclock
                            note=f"학습 {n_train:,}행 ~{end} · 판정 {sessions[first]}~{sessions[last]}")
        del X, y, Xt, train, test, base
        _log(f"{label or 'GBM'} 블록 {sessions[first]}~{sessions[last]} · 학습 ~{end}")
    return {s: pd.concat(v, ignore_index=True) for s, v in parts.items() if v}


def control_tag(panel: pd.DataFrame, *, smoke: int = 0) -> str:
    """대조군 예측 캐시의 꼬리표 — 시장 묶음 + 창. **세 시행이 같은 파일을 읽게** 하는 자리다."""
    markets = "+".join(sorted(pd.unique(panel["market"])))
    days = panel["session"]
    tag = f"{markets}-{min(days):%Y%m%d}-{max(days):%Y%m%d}"
    return tag + (f"-smoke{smoke}" if smoke else "")


def control_path(arm: str, seed: int, tag: str, *, cache_dir: Path = CACHE) -> Path:
    return cache_dir / f"pred-{arm}-seed{seed}-{tag}.pkl"


def controls(panel: pd.DataFrame, feats: list[str], sessions: list[date], bl: list[tuple[int, int]],
             *, seeds: Sequence[int] = SEEDS, cache_dir: Path = CACHE, smoke: int = 0,
             arms: Sequence[str] = ("C0", "C1"),
             store: Any = None, clock: Any = None) -> dict[str, dict[int, pd.DataFrame]]:
    """C0 = 현행 6점수 GBM(시행 L 규격) · C1 = 같은 GBM 을 FA 로. 시드별 예측(entity_id·session·market·pred).

    **한 번 구우면 캐시**다(`data/_diag/final-round/pred-{군}-seed{s}-{꼬리표}.pkl`). 세 시행(BE·BF·BG)이 밤마다
    하나씩 도는데 각자 대조군을 구우면 세 배 낭비이고, 값이 조금이라도 다르면 세 판정을 견줄 수 없다 —
    9/22 복기의 "같은 산출물이라던 T0·B0·C0 이 1.6~1.7%p 달랐다" 가 그대로 되풀이된다.

    캐시가 없으면 **여기서 굽는다.** 굽는 것은 무거우므로 실제로는 `scripts/final_round_bake.sh` 가 미리 돌려
    두고(리드가 일정을 잡는다), 시행 도구는 읽기만 하는 것이 정상 경로다. 시행 도구가 대조군이 없을 때
    조용히 자기 GBM 을 짜지 않게 하려면 `require_controls` 를 써라.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = control_tag(panel, smoke=smoke)
    cols = {"C0": [*SCORE_FEATS, "is_us"], "C1": list(feats)}
    out: dict[str, dict[int, pd.DataFrame]] = {}
    for arm in arms:
        got: dict[int, pd.DataFrame] = {}
        todo: list[int] = []
        for s in seeds:
            path = control_path(arm, int(s), tag, cache_dir=cache_dir)
            if path.exists():
                got[int(s)] = pd.read_pickle(path)  # invariant-allow: data-access — 창고가 아닌 작업 캐시
            else:
                todo.append(int(s))
        if todo:
            baked = walk_gbm(panel, sessions, cols[arm], bl, todo, label=arm, store=store, clock=clock)
            for s, frame in baked.items():
                frame.to_pickle(control_path(arm, s, tag, cache_dir=cache_dir))  # invariant-allow: data-access — 작업 캐시
                _log(f"{arm} seed{s}: 예측 {len(frame):,}행 → {control_path(arm, s, tag, cache_dir=cache_dir)}")
            got.update(baked)
        out[arm] = dict(sorted(got.items()))
    return out


def require_controls(panel: pd.DataFrame, *, seeds: Sequence[int] = SEEDS, cache_dir: Path = CACHE,
                     smoke: int = 0, arms: Sequence[str] = ("C0", "C1")) -> dict[str, dict[int, pd.DataFrame]]:
    """대조군 예측이 **이미 구워져 있을 때만** 돌려준다. 없으면 어느 파일이 없는지 말하고 `SystemExit(3)`.

    시행 도구는 이것을 쓴다 — 대조군이 없을 때 자기 GBM 을 새로 짜면 시행마다 다른 C0 이 생긴다.
    """
    tag = control_tag(panel, smoke=smoke)
    missing = [str(control_path(a, int(s), tag, cache_dir=cache_dir))
               for a in arms for s in seeds if not control_path(a, int(s), tag, cache_dir=cache_dir).exists()]
    if missing:
        print("대조군 예측이 없다 — 판정을 시작하지 않는다. scripts/final_round_bake.sh 가 먼저다.\n  "
              + "\n  ".join(missing[:10]), flush=True)
        raise SystemExit(CONTROLS_EXIT)
    return controls(panel, [], [], [], seeds=seeds, cache_dir=cache_dir, smoke=smoke, arms=arms)


# --------------------------------------------------------------------------- 판정


def _mean(seeds: dict[int, dict[str, float]], key: str) -> float:
    vals = [m[key] for m in seeds.values() if key in m and np.isfinite(m[key])]
    return float(np.mean(vals)) if vals else float("nan")


def overfit_gap(train: dict[int, dict[str, float]], judge_: dict[int, dict[str, float]]) -> dict[str, float]:
    """학습창 대비 판정창 격차 — 판정 기준이 아니라 **과적합 지표**로 기록한다(등록 §과적합 억제 5)."""
    return {f"gap_{k}": _mean(train, k) - _mean(judge_, k) for k in ("ann", "sharpe", "ic")}


def judge(results: dict[int, dict[str, float]], control0: dict[int, dict[str, float]],
          control1: dict[int, dict[str, float]] | None = None, *, label: str = "") -> tuple[list[str], str]:
    """채택 기준 ①~⑥ — 등록 문서 그대로. `results`·`control0`·`control1` 은 모두 시드 → 지표.

    ① 시드 평균 연수익 ≥ C0 + 2%p ② 시드의 80% 이상이 C0 보다 높다 ③ 두 국면 모두 ≥ C0 − 1%p
    ④ ΔIC(h5) ≥ 0 ⑤ MDD 가 2%p 넘게 깊지 않고 회전 ≤ C0 × 1.2 · **모델 주장**은 ⑥ ≥ C1 + 1%p.
    ⑥ 없이 ①~⑤ 만이면 "정보가 늘어서" 이고 C1 을 채택 후보로 적는다.
    """
    # **지표 키가 빠지면 크게 멈춘다**(2026-09-27, BG 지적) — _mean 이 nan 을 내면 관문 비교가 전부 False 가 되어
    # "기각" 으로 조용히 끝난다. 모델이 자기 일수익으로 지표를 낼 때(BG) 키 하나를 빠뜨리는 것이 가장 흔한 길이다.
    required = ("ann", "box_ann", "rally_ann", "mdd", "turn", "ic")
    for name, table in (("results", results), ("control0", control0), ("control1", control1 or {})):
        for seed, metrics in table.items():
            missing = [k for k in required if k not in metrics or not np.isfinite(metrics[k])]
            if missing:
                raise ValueError(f"judge: {name} 시드 {seed} 에 지표 {missing} 가 없다 — 조용히 기각하지 않는다")
    seeds = sorted(set(results) & set(control0))
    if not seeds:
        raise ValueError("judge: results 와 control0 이 겹치는 시드가 없다")
    wins = sum(results[s].get("ann", -9) > control0[s].get("ann", 9) for s in seeds)
    share = wins / len(seeds) if seeds else 0.0
    c = [
        _mean(results, "ann") >= _mean(control0, "ann") + GATE_MEAN,
        share >= GATE_SHARE,
        (_mean(results, "box_ann") >= _mean(control0, "box_ann") + GATE_REGIME
         and _mean(results, "rally_ann") >= _mean(control0, "rally_ann") + GATE_REGIME),
        _mean(results, "ic") - _mean(control0, "ic") >= GATE_IC,
        (_mean(results, "mdd") >= _mean(control0, "mdd") - GATE_MDD
         and _mean(results, "turn") <= _mean(control0, "turn") * GATE_TURN),
    ]
    model_claim = None
    if control1 is not None:
        model_claim = _mean(results, "ann") >= _mean(control1, "ann") + GATE_MODEL
    mk = rkit.mark
    lines = [
        f"{label}①평균 {_mean(results, 'ann') - _mean(control0, 'ann'):+.1%}p {mk(c[0])} · "
        f"②{wins}/{len(seeds)} ({share:.0%}) {mk(c[1])} · "
        f"③국면 {_mean(results, 'box_ann') - _mean(control0, 'box_ann'):+.1%}p/"
        f"{_mean(results, 'rally_ann') - _mean(control0, 'rally_ann'):+.1%}p {mk(c[2])} · "
        f"④ΔIC {_mean(results, 'ic') - _mean(control0, 'ic'):+.4f} {mk(c[3])} · "
        f"⑤MDD {_mean(results, 'mdd'):.1%} 대 {_mean(control0, 'mdd'):.1%} · 회전 "
        f"{_mean(results, 'turn'):.1f} 대 {_mean(control0, 'turn'):.1f} {mk(c[4])}",
    ]
    if model_claim is not None:
        lines.append(f"⑥ 대 C1 {_mean(results, 'ann') - _mean(control1, 'ann'):+.1%}p {mk(model_claim)}")
    if all(c) and model_claim:
        verdict = "채택 — 모델이 나아서(⑥ 통과)"
    elif all(c):
        verdict = "채택 후보 C1 — 정보가 늘어서(①~⑤ 통과, ⑥ 미통과)"
    else:
        verdict = "기각"
    lines.append(f"판정: {verdict}")
    return lines, verdict


__all__ = [
    "BLOCK_ORDER",
    "BOX_END",
    "CACHE",
    "GAP",
    "GROUPS",
    "PROGRESS_FIELDS",
    "PROGRESS_TABLE",
    "PURGE",
    "REBALANCE_EVERY",
    "SCORE_FEATS",
    "SEEDS",
    "VAULT_START",
    "Group",
    "MarketBook",
    "N",
    "attach_block",
    "blocks",
    "blocks_of",
    "check_window",
    "daily_ic",
    "drop_groups",
    "evaluate",
    "feature_names",
    "finalize",
    "inner_split",
    "judge",
    "load_full_panel",
    "market_books",
    "overfit_gap",
    "pooled_metrics",
    "record_progress",
    "rss_mb",
    "train_end",
]

if os.environ.get("FINAL_ROUND_SELFTEST"):  # pragma: no cover — 손으로 배선만 볼 때
    print(blocks_of(("KR", "US")).keys())
