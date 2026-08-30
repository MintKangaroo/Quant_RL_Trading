"""사전등록 시행 G — LLM Analyst. docs/protocols/new-sources-2026-09.md 의 시행 G 대로 잰다.

    .venv/bin/python tools/trial_llm_analyst.py --dry-run     # 호출 없이 입력·비용만
    .venv/bin/python tools/trial_llm_analyst.py [--save]      # 실제 측정

기준을 여기서 바꾸지 않는다. 채택 기준은 프로토콜에 있고 이 도구는 숫자만 낸다.
`--save` 는 research_trials 에 family `sources` 1행(entity `new-sources-2026-09:G`)을 적는다.

## 프로토콜을 코드로 옮기며 정한 것 (문서에 안 적힌 세부)

- **"60세션 격주"** — 2026-04-01~06-30 은 거래일이 62개뿐이라 "격주 60세션" 을 문자 그대로
  읽으면 구간을 벗어난다. **그 구간의 모든 거래일**(≈62)을 쓰되, 표본 상한을 `--sessions`
  로 두고 기본 60 으로 자른다. 격주로 띄엄띄엄 뽑으면 세션이 13개로 줄어 IC 의 일수가
  13일이 되는데, 그 표본으로 NW t 를 논할 수 없다.
- **무작위 120종목** — 세션마다 다시 뽑는다(프로토콜의 "같은 종목이 여러 세션에 뽑히는 것은
  허용"). 시드는 `--seed`(기본 0)에 세션 서수를 섞어 고정한다 — 같은 인자면 같은 표본이다.
- **시총 상위 300** 은 그 세션 시점의 `market_stats` 로 정한다. 오늘 명단으로 과거를 뽑으면
  생존편향이 들어간다.
- **홀드아웃은 끝까지 닫는다.** 세션은 2026-06-30 이전만, 시세·지수 조회의 as_of 도 그 안이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quant_rl_trading.analysts.llm_pick import (  # noqa: E402
    SYSTEM,
    TOOL,
    LlmPickAnalyst,
)
from quant_rl_trading.collectors.market_hours import Market, trading_days  # noqa: E402
from quant_rl_trading.replay.clock import ReplayClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.trial_analyst_features import (  # noqa: E402
    HOLDOUT_START,
    MARKET,
    SEOUL,
    _targets,
    _tradable,
)
from tools.trial_new_sources import FAMILY, _judge_signal, _record_trial  # noqa: E402

TRIAL = "G"
#: 표본 구간 — 프로토콜의 판정 세션.
WINDOW_START = date(2026, 4, 1)
WINDOW_END = date(2026, 6, 30)
#: 세션당 후보 풀(시총 상위)과 뽑는 수.
TOP_POOL = 300
SAMPLE_PER_SESSION = 120
MAX_SESSIONS = 60
#: 비용 추정용 단가 — config `llm.pricing` 에서 읽는다. 못 읽으면 sonnet 정가.
FALLBACK_PRICE = {"input_per_mtok": 3.0, "output_per_mtok": 15.0}
#: 문자 → 토큰 어림. 한국어 섞인 숫자 JSON 에서 대략 이 비율이다. 입력 문자 수는 **실제로
#: 만든 payload 를 세서** 쓰므로, 어림이 들어가는 것은 이 나눗셈 하나뿐이다.
CHARS_PER_TOKEN = 3.2
#: 출력은 종목당 (id·outlook·confidence·한 문장) — 실측 전이라 어림이다.
TOKENS_OUT_PER_ITEM = 60


def _moment(day: date) -> datetime:
    """그 세션의 결정 시각. 다른 시행과 같은 15:40 KST."""
    return datetime.combine(day, time(15, 40), tzinfo=SEOUL)


def _sessions(limit: int) -> list[date]:
    days = [d for d in trading_days(Market.KR, WINDOW_START, WINDOW_END) if d < HOLDOUT_START]
    return days[-limit:] if limit and len(days) > limit else days


def _top_pool(store: Store, as_of: datetime, size: int) -> list[str]:
    """그 시점 시총 상위 N. **오늘 명단이 아니라 그때 명단**이다."""
    frame = store.get(
        "market_stats", as_of=as_of, lookback=45, until=as_of, market=MARKET,
        columns=["entity_id", "metric", "value", "valid_from"],
    )
    if frame.empty:
        return []
    caps = frame[frame["metric"] == "market_cap"]
    if caps.empty:
        return []
    latest = caps.sort_values("valid_from").groupby("entity_id")["value"].last()
    latest = latest[latest > 0]
    return [str(entity) for entity in latest.nlargest(size).index]


def _sample(pool: list[str], size: int, *, seed: int, ordinal: int) -> frozenset[str]:
    if not pool:
        return frozenset()
    rng = np.random.default_rng(seed * 1000 + ordinal)
    picked = rng.choice(len(pool), size=min(size, len(pool)), replace=False)
    return frozenset(pool[int(i)] for i in picked)


def _pricing(store: Store, as_of: datetime, model: str) -> dict[str, float]:
    try:
        table = store.config("llm.pricing", as_of=as_of)
        entry = dict(table[model])
        return {
            "input_per_mtok": float(entry["input_per_mtok"]),
            "output_per_mtok": float(entry["output_per_mtok"]),
        }
    except Exception:  # 단가를 못 찾으면 추정치라고 말하고 정가를 쓴다
        return dict(FALLBACK_PRICE)


def _dry_run(store: Store, sessions: list[date], *, seed: int, sample: int, batch: int) -> int:
    tradable = _tradable()
    total_items, total_chars, example = 0, 0, None
    for ordinal, day in enumerate(sessions):
        as_of = _moment(day)
        pool = _top_pool(store, as_of, TOP_POOL)
        if tradable is not None:
            pool = [entity for entity in pool if entity in tradable]
        picked = _sample(pool, sample, seed=seed, ordinal=ordinal)
        analyst = LlmPickAnalyst(store, ReplayClock(as_of), entities=picked, batch_size=batch)
        payloads = analyst.payloads(as_of)
        total_items += len(payloads)
        total_chars += sum(
            len(json.dumps(item, ensure_ascii=False)) for item in payloads.values()
        )
        if example is None and payloads:
            example = next(iter(payloads.values()))
        if ordinal == 0:
            print(f"  첫 세션 {day}: 풀 {len(pool)} · 뽑음 {len(picked)} · 입력 만들어진 종목 {len(payloads)}")
    model = LlmPickAnalyst(store, ReplayClock(_moment(sessions[-1]))).model
    price = _pricing(store, _moment(sessions[-1]), model)
    calls = int(np.ceil(total_items / batch)) if batch else 0
    # 입력 토큰 = 실제 payload 문자 + 배치마다 붙는 system·tool 스키마.
    fixed_chars = len(SYSTEM) + len(json.dumps(TOOL, ensure_ascii=False))
    in_tokens = (total_chars + calls * fixed_chars) / CHARS_PER_TOKEN
    out_tokens = total_items * TOKENS_OUT_PER_ITEM
    cost = (
        in_tokens / 1e6 * price["input_per_mtok"]
        + out_tokens / 1e6 * price["output_per_mtok"]
    )
    print(f"\n세션 {len(sessions)} · 물어볼 종목-세션 {total_items:,} · 배치 {batch} → 호출 {calls:,}회")
    print(f"모델 {model} · 단가 in ${price['input_per_mtok']}/Mtok out ${price['output_per_mtok']}/Mtok")
    print(
        f"입력 {in_tokens/1000:,.0f}k 토큰(실측 문자 {total_chars:,} + 배치 고정 {fixed_chars}×{calls}) "
        f"· 출력 {out_tokens/1000:,.0f}k 토큰(종목당 {TOKENS_OUT_PER_ITEM} 가정)"
    )
    print(f"**추정 비용 ${cost:,.2f}** — 캐시가 비어 있을 때. 다시 돌리면 0 이다")
    if example is not None:
        print("\n입력 예시 한 건:")
        print(json.dumps(example, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


def _measure(store: Store, sessions: list[date], *, seed: int, sample: int, batch: int, save: bool) -> int:
    tradable = _tradable()
    rows: list[pd.DataFrame] = []
    calls = cache_hits = skipped = 0
    model = ""
    for ordinal, day in enumerate(sessions):
        as_of = _moment(day)
        pool = _top_pool(store, as_of, TOP_POOL)
        if tradable is not None:
            pool = [entity for entity in pool if entity in tradable]
        picked = _sample(pool, sample, seed=seed, ordinal=ordinal)
        if not picked:
            continue
        analyst = LlmPickAnalyst.from_store(
            store, ReplayClock(as_of), as_of=as_of, entities=picked
        )
        analyst.batch_size = batch
        model = analyst.model
        frame = analyst.features(as_of)
        calls += analyst.calls
        cache_hits += analyst.cache_hits
        skipped += analyst.skipped_budget
        if analyst.failures:
            print(f"  {day} 실패 {analyst.failures[:2]}")
        if frame.empty:
            continue
        scored = frame.reset_index()[["entity_id", "llm_outlook"]].rename(
            columns={"llm_outlook": "signal"}
        )
        scored["session"] = day
        rows.append(scored[["entity_id", "session", "signal"]])
        if (ordinal + 1) % 10 == 0:
            print(f"  … {ordinal + 1}/{len(sessions)} 세션 · 호출 {calls} · 캐시 {cache_hits}", flush=True)

    if not rows:
        print("\n판정 보류 — 점수가 0행이다 (예산 소진이거나 키가 없다). 시행을 소비하지 않는다.")
        return 3
    signal = pd.concat(rows, ignore_index=True)
    covered = signal["session"].nunique()
    print(
        f"\n표본: {covered}세션 · {len(signal):,}(종목×세션) · 호출 {calls} · 캐시 {cache_hits}"
        f" · 예산으로 건너뜀 {skipped} · 모델 {model}"
    )
    if skipped:
        print("  ⚠️ 예산 소진으로 물어보지 못한 종목이 있다 — 표본이 프로토콜보다 작다")

    t5, t20 = _targets(5), _targets(20)
    print("\nLLM 점수 판정")
    result = _judge_signal(store, "llm", signal, tradable, t5, t20)
    ic5 = float(result.get("ic5", float("nan")))
    t5v = float(result.get("t5", float("nan")))
    delta_t = float(result.get("delta_t", float("nan")))
    # 채택 기준(프로토콜): IC(h5) > 0 ∧ NW t ≥ 2.0 ∧ 한계기여 ΔIC(h5) NW t ≥ 2.0
    adopt = bool(
        np.isfinite(ic5) and ic5 > 0
        and np.isfinite(t5v) and t5v >= 2.0
        and np.isfinite(delta_t) and delta_t >= 2.0
    )
    print(
        f"\n판정 G: {'채택' if adopt else '기각'} — IC(h5) {ic5:+.4f}(t {t5v:+.2f}, 기준 >0·t≥2) · "
        f"한계기여 t {delta_t:+.2f}(기준 ≥2)"
    )
    if adopt:
        print("  채택 시 새 Analyst `llm` — **관찰 모드(가중치 0)** 로 시작한다 (CLAUDE.md).")
    if save:
        detail = json.dumps(
            {
                **{k: v for k, v in result.items() if k != "rows"},
                "sessions": covered, "rows": len(signal), "model": model,
                "calls": calls, "cache_hits": cache_hits, "skipped_budget": skipped,
                "system_prompt_sha": _prompt_digest(),
            },
            ensure_ascii=False, default=float,
        )
        _record_trial(store, trial=TRIAL, detail=detail)
    return 0


def _prompt_digest() -> str:
    """프롬프트·스키마의 지문 — 재현성 조항. 프롬프트가 바뀌면 같은 시행이 아니다."""
    import hashlib

    payload = SYSTEM + json.dumps(TOOL, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--sessions", type=int, default=MAX_SESSIONS)
    parser.add_argument("--sample", type=int, default=SAMPLE_PER_SESSION)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="호출 없이 입력·비용만")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    store = Store(root=Path(args.root))
    sessions = _sessions(args.sessions)
    if not sessions:
        print("판정 세션이 없다", file=sys.stderr)
        return 1
    print(
        f"=== 시행 {TRIAL} — docs/protocols/new-sources-2026-09.md "
        f"(세션 {sessions[0]}~{sessions[-1]}, 홀드아웃 {HOLDOUT_START} 부터는 안 연다) ==="
    )
    print(f"프롬프트 지문 {_prompt_digest()} · family {FAMILY}")
    if args.dry_run:
        return _dry_run(store, sessions, seed=args.seed, sample=args.sample, batch=args.batch)
    return _measure(
        store, sessions, seed=args.seed, sample=args.sample, batch=args.batch, save=args.save
    )


if __name__ == "__main__":
    raise SystemExit(main())
