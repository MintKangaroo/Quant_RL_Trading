"""LLM 공시 정보 추출(시행 L1) — 창고의 DART 원문 → `filing_events`. 설계: docs/design/ai-full-stack.md §4.

    .venv/bin/python tools/extract_filing_events.py --estimate            # 호출 없이 최근 30일 실측 → 월 비용
    .venv/bin/python tools/extract_filing_events.py --sample 5            # 표본 호출(≤5), 창고에 안 적는다
    .venv/bin/python tools/extract_filing_events.py --lookback 3          # 운영: 최근 3일 접수분 추출·적재

**판정은 전방만이다.** 과거 공시를 이 도구로 뽑아도 행의 observed_at 은 오늘이라 과거 as_of 조회에
안 잡힌다(`llm_filing_events` docstring). 백필은 기록일 뿐이고 등록 초안
(docs/protocols/llm-filing-events-2026-10.md)의 판정 창에 들어가지 않는다.

rc: 0 = 뽑았거나 뽑을 것이 없다 · 1 = 대상이 있었는데 한 건도 못 뽑았다(키 없음·API 오류) ·
2 = 예산에 막혀 멈췄다(소진 또는 예산 키 미시딩 — 남은 대상은 다음 회차).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.collectors import llm_filing_events as l1  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.settings import load_env  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

COLUMNS = ["entity_id", "valid_from", "observed_at", "doc_id", "doc_type", "title", "source", "revision", "raw_path"]
#: 표본 호출 상한 — 스키마·파싱 검증용이다. 이보다 많이 부르려면 운영 경로(예산 적용)를 쓴다.
SAMPLE_MAX = 10
#: 표본 한 회의 지출 상한($). 넘으면 거기서 멈춘다(리드 허락 2026-09-28: 10건 $0.12 이내).
SAMPLE_BUDGET_USD = 0.12
#: 표본 총상한의 상한 — `--cap` 을 크게 줘도 이 이상은 못 쓴다(리드 허락 2026-09-28: Haiku·Sonnet 비교 $0.60).
SAMPLE_CAP_MAX = 0.60
#: --estimate 의 문자→토큰 어림(Haiku 4.5). v2 표본 10건(2026-09-28) 첫 시도의 직선 적합:
#: 입력 토큰 ≈ 2,657 + 0.925 × 원문 문자, 출력 평균 176. (Sonnet 5: 2,393 + 0.930 × 문자, 출력 239.)
CHARS_PER_TOKEN = 1.08
PROMPT_TOKENS = 2_660
OUTPUT_TOKENS = 180


def _documents(store: Store, clock: Clock, *, lookback: int) -> pd.DataFrame:
    now = clock.now()
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=lookback, market="KR", columns=COLUMNS)
    if frame.empty:
        return frame
    return frame[frame["valid_from"] >= pd.Timestamp(now - timedelta(days=lookback))]


def _done(store: Store, clock: Clock, *, lookback: int) -> set[str]:
    """이미 **이 프롬프트로** 행이 있는 doc_id. 실패 행도 포함한다 — 같은 입력을 다시 부르지 않는다."""
    # 정정은 원공시의 키(doc_id)로 적히므로, "읽었다" 는 실제로 읽은 공시(source_doc_id)로 센다.
    frame = store.get(l1.TABLE, as_of=clock.now(), lookback=lookback + 1, market="KR",
                      columns=["source_doc_id", "prompt_hash"])
    if frame.empty:
        return set()
    return set(frame.loc[frame["prompt_hash"] == l1.PROMPT_HASH, "source_doc_id"].astype(str))


def estimate(store: Store, clock: Clock) -> int:
    frame = _documents(store, clock, lookback=30)
    todo = l1.targets(frame)
    if todo.empty:
        print("최근 30일 대상 원문 0건")
        return 0
    # 메시지 전체(후보 칸·코드 계산 포함) 길이로 잰다. 파서가 다루는 잠정실적·빈 잠정실적은 부르지 않으므로 뺀다.
    lengths: list[int] = []
    for title, path in zip(todo["title"].astype(str), todo["raw_path"].astype(str), strict=True):
        text = docs.read_text(Path(path))
        kind = l1.event_type_of(title)
        if l1.covered_by_parser(kind, title, text) is None:
            lengths.append(len(l1._user_message({"title": title}, text, kind, l1.derived_values(kind, text))))
    if not lengths:
        print("최근 30일 LLM 대상 0건(전부 파서가 다룬다)")
        return 0
    chars = np.array(lengths)
    days = todo["valid_from"].dt.tz_convert("Asia/Seoul").dt.date.nunique()
    price = l1.Budget.from_store(store, as_of=clock.now()).price
    tokens_in = PROMPT_TOKENS + chars / CHARS_PER_TOKEN
    per_doc = (tokens_in * price.get("input_per_mtok", 0) + OUTPUT_TOKENS * price.get("output_per_mtok", 0)) / 1e6
    kind = todo["doc_type"].where(~todo["title"].astype(str).str.replace(" ", "").str.contains("최대주주변경"), "control")
    print(f"최근 30일 대상 {len(todo):,}건 중 LLM 호출 {len(chars):,}건(접수일 {days}일) · 모델 {l1.MODEL} · 단가 {price}")
    print(kind.value_counts().to_string())
    print(f"원문 문자 중앙 {int(np.median(chars)):,} · 평균 {int(chars.mean()):,} · 최대 {int(chars.max()):,} · "
          f"{l1.MAX_INPUT_CHARS:,}자 초과(부르지 않음) {int((chars > l1.MAX_INPUT_CHARS).sum())}건")
    print(f"건당 ${per_doc.mean():.4f}(입력 {tokens_in.mean():,.0f}·출력 {OUTPUT_TOKENS} 토큰) → "
          f"30일 ${per_doc.sum():.2f} · 재시도 포함(×1.22, v2 표본 실측) ${per_doc.sum() * 1.22:.2f}")
    return 0


def sample(store: Store, clock: Clock, *, size: int, lookback: int, api_key: str,
           dump: Path | None = None, models: tuple[str, ...] = (l1.MODEL,),
           doc_ids: tuple[str, ...] = (), cap_usd: float = SAMPLE_BUDGET_USD) -> int:
    """표본 호출 — 창고에 안 적는다. 모델 여럿이면 **같은 공시**를 모델마다 읽히고, 지출은 `cap_usd` 하나를 나눠 쓴다."""
    size = min(size, SAMPLE_MAX)
    todo = l1.targets(_documents(store, clock, lookback=lookback))
    if doc_ids:
        picked = todo[todo["doc_id"].astype(str).isin(doc_ids)]
        missing = sorted(set(doc_ids) - set(picked["doc_id"].astype(str)))
        if missing:
            print(f"창에 없는 doc_id {missing} — lookback 을 늘려라", file=sys.stderr)
            return 1
    else:
        # 유형마다 고르게(유형당 size÷유형 수) — 적은 표본으로 스키마의 여러 갈래를 본다.
        per_type = max(1, size // max(1, todo["doc_type"].nunique())) if not todo.empty else 0
        picked = todo.groupby("doc_type", sort=False).head(per_type).head(size)
    if picked.empty:
        print("대상 0건")
        return 0
    spent_total = 0.0
    everything: list[dict[str, object]] = []
    for model in models:
        budget = l1.Budget.from_store(store, as_of=clock.now(), model=model)
        # 표본은 예산 키 시딩 전에도 돈다 — 건수(SAMPLE_MAX)와 지출(cap_usd, 모델 전부 합쳐) 상한이 코드에 박혀 있다.
        budget.agent_usd, budget.agent_spent = cap_usd, spent_total
        # 표본은 지난 공시로 채점한다 — 축적 시작일 전 정정 건너뛰기를 끈다(운영 경로는 켠다).
        extractor = l1.FilingEventExtractor(store, clock, budget, api_key=api_key, write=False, model=model,
                                            forward_start=None)
        rows = extractor.run(picked)
        spent = budget.agent_spent - spent_total
        spent_total = budget.agent_spent
        print(f"\n=== {model} · 지문 {extractor.prompt} · 호출 {extractor.calls} · 재시도 {extractor.retries} · "
              f"${spent:.4f} (누적 ${spent_total:.4f} / 상한 ${cap_usd})"
              + f" · 파서 몫 {extractor.parser_covered}"
              + (f" · 멈춤: {extractor.stopped}" if extractor.stopped else ""))
        for row in rows:
            print(json.dumps({k: row[k] for k in ("source_doc_id", "doc_id", "revision", "amended", "event_type",
                                                    "status", "direction", "magnitude", "magnitude_unit", "vs_prior",
                                                    "confidence", "evidence", "error")},
                             ensure_ascii=False, default=str))
            for trace in (t for t in extractor.responses if t["doc_id"] == row["source_doc_id"]):
                print(f"  시도 {trace['attempt']} usage {trace['usage']} 검증 {trace['error'] or '통과'}")
                print(f"    날것 {json.dumps(trace['raw'], ensure_ascii=False, default=str)}")
        for error in extractor.api_errors:
            print(f"  API 오류 {error}", file=sys.stderr)
        paths = dict(zip(picked["doc_id"].astype(str), picked["raw_path"].astype(str), strict=True))
        titles = dict(zip(picked["doc_id"].astype(str), picked["title"].astype(str), strict=True))
        everything += [
            {**row, "title": titles[row["source_doc_id"]], "raw_path": paths[row["source_doc_id"]],
             "traces": [t for t in extractor.responses if t["doc_id"] == row["source_doc_id"]]}
            for row in rows
        ]
        if extractor.stopped:
            break
    if dump is not None:
        dump.write_text(json.dumps(everything, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"날것 답 → {dump}")
    return 0 if everything else 1


def run(store: Store, clock: Clock, *, lookback: int, limit: int, api_key: str) -> int:
    done = _done(store, clock, lookback=lookback)
    todo = l1.targets(_documents(store, clock, lookback=lookback), done=done)
    if limit:
        todo = todo.head(limit)
    if todo.empty:
        print("뽑을 공시가 없다")
        return 0
    extractor = l1.FilingEventExtractor(
        store, clock, l1.Budget.from_store(store, as_of=clock.now()), api_key=api_key
    )
    rows = extractor.run(todo)
    status = pd.Series([r["status"] for r in rows], dtype=str).value_counts().to_dict() if rows else {}
    print(f"대상 {len(todo)} · 적재 {len(rows)} {status} · 호출 {extractor.calls} · 재시도 {extractor.retries} · "
          f"캐시 {extractor.cache_hits} · 파서 몫 {extractor.parser_covered} · 시작일 전 정정 {extractor.skipped_pre_start} · "
          f"기존 사건 {extractor.superseded} · API 오류 {len(extractor.api_errors)} · 이달 추출 지출 "
          f"${extractor.budget.agent_spent:.2f}/{extractor.budget.agent_usd}")
    for error in extractor.api_errors[:5]:
        print(f"  {error}", file=sys.stderr)
    if extractor.stopped is not None:
        print(f"멈춤: {extractor.stopped}", file=sys.stderr)
        return 2 if "예산" in extractor.stopped or "단가" in extractor.stopped else 1
    if not rows:
        print("대상은 있었는데 한 건도 못 뽑았다", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--lookback", type=int, default=3, help="접수일 창(일)")
    parser.add_argument("--limit", type=int, default=300, help="한 회차 상한(0 = 전부)")
    parser.add_argument("--estimate", action="store_true", help="호출 없이 최근 30일로 월 비용 추정")
    parser.add_argument("--sample", type=int, default=0, help=f"표본 호출(≤{SAMPLE_MAX}), 창고에 안 적는다")
    parser.add_argument("--dump", type=Path, default=None, help="표본의 날것 답·검증 사유를 JSON 으로")
    parser.add_argument("--models", default=l1.MODEL, help=f"표본 모델(쉼표로). 가능: {','.join(l1.MODEL_PARAMS)}")
    parser.add_argument("--doc-ids", default="", help="표본 공시를 접수번호로 고정(쉼표로)")
    parser.add_argument("--cap", type=float, default=SAMPLE_BUDGET_USD, help=f"표본 총지출 상한($, ≤{SAMPLE_CAP_MAX})")
    args = parser.parse_args(argv)

    load_env()
    clock = LiveClock()
    store = Store(root=Path(args.root))
    if args.estimate:
        return estimate(store, clock)
    api_key = (os.environ.get(l1.KEY_ENV) or "").strip()
    if args.sample:
        models = tuple(m.strip() for m in args.models.split(",") if m.strip())
        unknown = [m for m in models if m not in l1.MODEL_PARAMS]
        if unknown:
            print(f"모르는 모델 {unknown}", file=sys.stderr)
            return 1
        return sample(store, clock, size=args.sample, lookback=max(args.lookback, 30), api_key=api_key,
                      dump=args.dump, models=models,
                      doc_ids=tuple(d.strip() for d in args.doc_ids.split(",") if d.strip()),
                      cap_usd=min(args.cap, SAMPLE_CAP_MAX))
    return run(store, clock, lookback=args.lookback, limit=args.limit, api_key=api_key)


if __name__ == "__main__":
    raise SystemExit(main())
