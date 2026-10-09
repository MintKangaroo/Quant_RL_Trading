"""공급망 선행 진단 — 미국 고객사 주가가 한국 공급사(K200)를 앞서는가 (2026-10-09, 탐색 · 시행 아님).

    .venv/bin/python tools/diag_supply_chain.py extract [--limit N]   # 사업보고서 '주요 매출처' → Haiku 로 고객사 추출(예산 잠금)
    .venv/bin/python tools/diag_supply_chain.py leadlag                # 미국 고객사 잔차 수익 → 다음 국장 세션 공급사 초과수익

문헌: Cohen·Frazzini(2008, JF) 고객사 충격의 느린 전달 · Albuquerque·Ramadorai·Watugala(2015, JFE) 국가 간.
메모리 lit-review-2026-10-09 후보 F②. 원문은 `data/_docs/dart/<연>/<월>/<접수번호>.txt.gz`(창고 documents 의 doc_id).
**금고 앞(2026-06-30)** 까지의 사업보고서·가격만 읽는다. 창고에는 LLM 사용량(`llm_usage`)만 적는다 — 예산을 세야 해서다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.store import Store  # noqa: E402

END = date(2026, 6, 30)
AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
OUT = Path("data/_diag/supply-chain")
DOCS = Path("data/_docs/dart")
AGENT = "supply_chain_diag"
MODEL = "claude-haiku-4-5"
#: 이 진단만의 지출 상한($) — 전체 월 예산(config llm.monthly_budget_usd)과 함께 본다. 200건 × 약 6천 토큰 ≈ $1~2.
CAP_USD = 3.0
KEYWORDS = ("주요 매출처", "주요매출처", "주요 고객", "주요고객", "매출처", "주요 거래처", "고객사")
WINDOW, MAX_CHARS = 1500, 7000
TOOL = {
    "name": "report_customers",
    "description": "사업보고서 발췌에 이름이 나온 주요 고객사(매출처)를 보고한다.",
    "input_schema": {
        "type": "object",
        "properties": {"customers": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string", "description": "발췌에 적힌 고객사 이름 그대로"},
            "country": {"type": "string", "description": "본사 국가(모르면 빈 문자열)"},
            "us_ticker": {"type": "string", "description": "미국 상장사이고 티커를 확실히 알 때만(예: AAPL). 아니면 빈 문자열"},
            "share_pct": {"type": "number", "description": "발췌에 매출 비중(%)이 적혀 있으면 그 값, 없으면 -1"},
        }, "required": ["name", "country", "us_ticker", "share_pct"]}}},
        "required": ["customers"],
    },
}
SYSTEM = ("너는 한국 사업보고서에서 사실만 뽑는다. 발췌에 이름이 실제로 나온 고객사만 적는다 — 추측으로 회사를 더하지 않는다. "
          "'A사'·'해외 고객' 처럼 익명이면 적지 않는다. 계열사·자회사 매출도 고객으로 적되 country 는 본사 기준이다.")


def k200_reports(store: Store) -> pd.DataFrame:
    """2026-06-30 K200 구성 · 종목마다 그날까지 관측된 마지막 사업보고서(정정 제외) 한 건."""
    im = store.get("index_members", as_of=AS_OF, lookback=40, market="KR")
    im = im[im["index_id"].astype(str).str.contains("KOSPI200")]
    last = pd.to_datetime(im["valid_from"]).max()
    k200 = set(im[pd.to_datetime(im["valid_from"]) == last]["entity_id"])
    d = store.get("documents", as_of=AS_OF, lookback=700, market="KR", columns=["entity_id", "observed_at", "title", "doc_id", "filer"])
    t = d["title"].astype(str)
    d = d[d["entity_id"].isin(k200) & t.str.contains("사업보고서") & ~t.str.contains("정정")]
    return d.sort_values("observed_at").groupby("entity_id").tail(1).reset_index(drop=True)


def excerpt(text: str) -> str:
    """키워드 둘레 창들을 겹침 없이 이어 붙인다(최대 MAX_CHARS). 없으면 빈 문자열 — 부르지 않는다."""
    spans: list[tuple[int, int]] = []
    for kw in KEYWORDS:
        start = 0
        while (i := text.find(kw, start)) >= 0 and len(spans) < 12:
            spans.append((max(0, i - 300), min(len(text), i + WINDOW)))
            start = i + len(kw)
    if not spans:
        return ""
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return "\n…\n".join(text[a:b] for a, b in merged)[:MAX_CHARS]


def spent(store: Store, agent: str | None) -> float:
    from quant_rl_trading.collectors.llm_filing_events import month_spend
    return month_spend(store, as_of=datetime.now(UTC), agent=agent)  # invariant-allow: wallclock — 이달 지출


def cmd_extract(store: Store, limit: int | None) -> int:
    import anthropic

    from quant_rl_trading.collectors.llm_filing_events import call_cost
    from quant_rl_trading.dashboard.services.ai_review import _pricing_table
    from quant_rl_trading.settings import load_env

    load_env()
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        print("ANTHROPIC_API_KEY 없음 — 멈춘다", flush=True)
        return 2
    now = datetime.now(UTC)  # invariant-allow: wallclock — 예산·사용량 시각
    total_cap = float(store.config("llm.monthly_budget_usd", as_of=now))
    price = _pricing_table(store, as_of=now).get(MODEL, {})
    if not price:
        print(f"{MODEL} 단가가 config 에 없다 — 멈춘다", flush=True)
        return 2
    mine, total = spent(store, AGENT), spent(store, None)
    (OUT / "customers").mkdir(parents=True, exist_ok=True)
    reps = k200_reports(store)
    client = anthropic.Anthropic(api_key=key)
    done = calls = 0
    for _, r in reps.iterrows():
        if limit is not None and done >= limit:
            break
        path_out = OUT / "customers" / f"{str(r['entity_id']).replace(':', '_')}.json"
        if path_out.exists():
            continue
        if mine >= CAP_USD or total >= total_cap:
            print(f"예산 멈춤 — 이 진단 ${mine:.3f}/{CAP_USD} · 전체 ${total:.2f}/{total_cap}", flush=True)
            break
        doc = str(r["doc_id"])
        src = DOCS / doc[:4] / doc[4:6] / f"{doc}.txt.gz"
        text = gzip.open(src, "rt", encoding="utf-8", errors="ignore").read() if src.exists() else ""
        ex = excerpt(text)
        record: dict[str, Any] = {"entity_id": r["entity_id"], "filer": r["filer"], "doc_id": doc, "excerpt_chars": len(ex)}
        if ex:
            resp = client.messages.create(model=MODEL, max_tokens=1024, temperature=0.0, system=SYSTEM, tools=[TOOL],
                                          tool_choice={"type": "tool", "name": TOOL["name"]},
                                          messages=[{"role": "user", "content": f"회사: {r['filer']}\n발췌:\n{ex}"}])
            calls += 1
            cost = call_cost(resp.usage, price)
            mine, total = mine + cost, total + cost
            at = datetime.now(UTC)  # invariant-allow: wallclock — 사용량 시각
            store.append("llm_usage", [{
                "entity_id": AGENT, "valid_from": at, "observed_at": at, "source": AGENT, "agent": AGENT,
                "agent_version": "v1", "model": MODEL, "request_id": str(resp.id), "items": 1,
                "input_tokens": int(resp.usage.input_tokens), "output_tokens": int(resp.usage.output_tokens),
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "computed_at": at,
            }], ingest_run_id=f"{AGENT}-usage-{at:%Y%m%dT%H%M%S%f}-{resp.id}")
            block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
            record["customers"] = (block.input or {}).get("customers", []) if block is not None else []
        else:
            record["customers"] = []
        path_out.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        done += 1
        print(f"  {r['entity_id']} {r['filer']}: 발췌 {len(ex):,}자 · 고객 {len(record['customers'])} · "
              f"미국 티커 {[c['us_ticker'] for c in record['customers'] if c.get('us_ticker')]}", flush=True)
    print(f"끝 — 처리 {done} · 호출 {calls} · 이 진단 지출 ${mine:.3f}", flush=True)
    return 0


def cmd_leadlag(store: Store) -> int:
    """US 고객사 잔차 수익(고객 − SPY, 미국 세션 d) → 공급사의 d 다음 국장 세션 초과수익(시가→종가 · 종가→종가, 대 K200).

    미국 종가(d)는 한국 d+1 개장 전에 나온다 — 시가→종가 는 개장 뒤 들어가 실행 가능한 쪽이다.
    """
    from quant_rl_trading.analysts import ic as icm
    from quant_rl_trading.store.prices import read_prices
    from tools.trial_overlay import _index

    pairs = []
    for f in sorted((OUT / "customers").glob("*.json")):
        rec = json.loads(f.read_text())
        for c in rec.get("customers", []):
            tk = str(c.get("us_ticker") or "").strip().upper()
            if tk:
                pairs.append((rec["entity_id"], f"US:{tk}"))
    pairs = sorted(set(pairs))
    print(f"짝 {len(pairs)} · 공급사 {len({a for a, _ in pairs})} · 미국 고객 {len({b for _, b in pairs})}", flush=True)
    if not pairs:
        return 0
    us = read_prices(store, as_of=AS_OF, lookback=1700, columns=["close"], adjusted=True, market="US")
    us["day"] = pd.to_datetime(us["valid_from"]).dt.date
    us = us[us["entity_id"].isin({b for _, b in pairs} | {"US:SPY"}) & (us["day"] <= END)]
    uc = us.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    ur = uc.pct_change()
    if "US:SPY" in ur:
        ur = ur.sub(ur["US:SPY"], axis=0)
    kr = read_prices(store, as_of=AS_OF, lookback=1700, columns=["open", "close"], adjusted=True, market="KR")
    kr["day"] = pd.to_datetime(kr["valid_from"]).dt.date
    kr = kr[kr["entity_id"].isin({a for a, _ in pairs}) & (kr["day"] <= END)]
    ko = kr.pivot_table(index="day", columns="entity_id", values="open", aggfunc="last").sort_index()
    kc = kr.pivot_table(index="day", columns="entity_id", values="close", aggfunc="last").sort_index()
    idx = _index(store, [date(2021, 6, 1), END])
    k_oc = (kc / ko - 1)
    k_cc = kc.pct_change()
    bi = idx.pct_change()
    kdays = list(kc.index)
    nxt = {d: next((k for k in kdays if k > d), None) for d in ur.index}
    rows = []
    for sup, cust in pairs:
        if cust not in ur or sup not in kc:
            continue
        for d, x in ur[cust].dropna().items():
            k = nxt.get(d)
            if k is None:
                continue
            rows.append({"d": k, "sup": sup, "x": x, "oc": k_oc.at[k, sup] if k in k_oc.index else np.nan,
                         "cc": (k_cc.at[k, sup] - bi.get(k, np.nan)) if k in k_cc.index else np.nan})
    f = pd.DataFrame(rows).dropna()
    for col, name in (("oc", "다음 세션 시가→종가(실행 가능)"), ("cc", "다음 세션 종가→종가 − K200(갭 포함)")):
        q = pd.qcut(f["x"], 5, labels=False)
        by = f.groupby(q)[col].mean()
        slope = np.polyfit(f["x"].clip(-0.2, 0.2), f[col].clip(-0.3, 0.3), 1)[0]
        top = f[q == 4].groupby("d")[col].mean()
        print(f"{name}: 고객 잔차 5분위 → " + " · ".join(f"Q{k + 1} {v:+.3%}" for k, v in by.items())
              + f" · 기울기 {slope:+.3f} · Q5 일별 t {top.mean() / top.std() * np.sqrt(len(top)):+.2f} (n {len(f):,})", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("extract", "leadlag"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    store = Store(root=Path("data"))
    return cmd_extract(store, args.limit) if args.what == "extract" else cmd_leadlag(store)


if __name__ == "__main__":
    raise SystemExit(main())
