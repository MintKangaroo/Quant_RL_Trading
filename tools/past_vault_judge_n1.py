"""과거 금고 N1 판정 — docs/protocols/past-vault-n1-2026-10.md(등록, 해시 고정 뒤)의 기준 그대로. 순주식발행 상위 10% 빼기.

    .venv/bin/python tools/past_vault_judge_n1.py               # 2026-10-13 16:45 KST 뒤에만, 해시가 고정·일치할 때만 봉인 창고를 연다
    .venv/bin/python tools/past_vault_judge_n1.py --rehearse    # 리허설: 본 창고 탐색 구간(2022-09~2026-06, 이미 본 구간) — 배관 확인용, 판정 아님

판정 틀은 `tools/past_vault_judge.judge_one` 을 **그대로** 부른다(기준 셋·대상·결정 세션·비용·하루 늦춤이 TF·TB·TG 와 같다).
다른 것은 제외 규칙 하나 — 대상 n 중 `nsi_12m_c` **상위** ⌊0.1n⌋(점수 결측 = 사건 창 제외 종목은 빼지 않는다).
그 규칙은 이 도구 안에서만 `vault_judge.TF_RULES["N1"] = (0, 0.10, 0)` 으로 얹는다(``hi`` = nlargest) — vault_judge 의 기존 판정은 그대로다.
입력: 봉인 창고 prices·market_stats, 굽기 `data/_past_vault/_bake/n1.pkl`(tools/past_vault_n1_bake.py).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from quant_rl_trading.replay.clock import LiveClock
from quant_rl_trading.store import Store
from tools import past_vault_judge as pj
from tools import past_vault_n1_bake as nb
from tools import vault_judge as vj

PROTOCOL = Path("docs/protocols/past-vault-n1-2026-10.md")
#: 리드가 독립 검토 뒤 고정하면 sha256 앞 16자를 적는다. None 이면 판정을 거부한다(리허설만 된다).
PROTOCOL_HASH = "0f4c2d8f722f50ee"   # 2026-10-11 고정(독립 검토 2회)
OPEN_AT = datetime(2026, 10, 13, 16, 45, tzinfo=ZoneInfo("Asia/Seoul"))
KIND = "N1"
RULE = (0.0, 0.10, 0.0)   # (하위, 상위, 결합) — 상위 10% 빼기
#: 굽기 산출 지문 — 등록 문서 '점수' 절에 같은 값을 적는다. 판정은 파일·문서 둘 다와 맞을 때만 연다.
BAKE_ROWS = 4_447_694
BAKE_SHA256 = "dd88fd894227cf153c58f605067b823f996d271d5b767ce666f70df99e2d2d7f"


def refuse(reason: str, rc: int) -> int:
    """거부는 결과 파일에 한 줄로 남긴다 — switch_report 가 '(거부: 사유)' 로 보인다."""
    print(f"{KIND} 거부: {reason}", flush=True)
    return rc


def install_rule() -> None:
    vj.TF_RULES[KIND] = RULE


def assert_rule() -> None:
    """판정 직전 — 얹은 규칙이 정말 '상위 10% 빼기' 인지(다른 코드가 덮어쓰지 않았는지)."""
    got = vj.TF_RULES.get(KIND)
    if got != RULE:
        raise SystemExit(f"{KIND} 제외 규칙이 {got} — 등록은 {RULE}(상위 10%)")


def check_bake(path: Path, doc_text: str) -> str | None:
    """굽기 파일의 sha256·행 수가 도구 상수와, 그 상수가 등록 문서와 맞는지. 어긋나면 사유 문자열."""
    if BAKE_SHA256 not in doc_text or f"{BAKE_ROWS:,}" not in doc_text:
        return "도구의 굽기 지문이 등록 문서에 없다"
    got = nb.file_sha256(path)
    if got != BAKE_SHA256:
        return f"굽기 sha256 {got[:16]}… ≠ 등록 {BAKE_SHA256[:16]}…"
    rows = len(pd.read_pickle(path))  # invariant-allow: data-access — 봉인 창고 굽기(행 수만)
    if rows != BAKE_ROWS:
        return f"굽기 행 수 {rows:,} ≠ 등록 {BAKE_ROWS:,}"
    return None


def rehearse() -> int:
    """본 창고 2022-09~2026-06(탐색 진단이 이미 본 구간)에서 같은 배관을 끝까지 돌린다. 판정 아님."""
    store = Store(root=Path("data"))
    S, F = nb.read_inputs(store, date(2020, 9, 1), date(2026, 6, 30))
    scores = nb.to_long(nb.nsi_panel(S, F), date(2022, 8, 1))
    print(f"[리허설] 점수 {len(scores):,}행 · 세션 {scores['session'].nunique()}", flush=True)
    close, value, mcap = pj.load_market(store, date(2026, 6, 30), date(2020, 9, 1))
    per = (("박스 2022-09~2024", date(2022, 9, 1), date(2024, 12, 31)), ("급등 2025~2026-06", date(2025, 1, 1), date(2026, 6, 30)))
    assert_rule()
    lines, verdict, _ = pj.judge_one(KIND, scores, close, value, mcap, date(2022, 9, 1), date(2026, 6, 19), per)
    print("[리허설 — 판정 아님, 본 창고 탐색 구간]")
    print("\n".join(lines))
    print(f"  → (리허설) {verdict}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehearse", action="store_true")
    args = ap.parse_args(argv)
    install_rule()
    if args.rehearse:
        return rehearse()
    if PROTOCOL_HASH is None:
        return refuse("등록 문서 미고정(PROTOCOL_HASH 없음)", 2)
    got = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()[:16]
    if got != PROTOCOL_HASH:
        return refuse(f"등록 문서 해시 {got} ≠ 고정 {PROTOCOL_HASH}", 2)
    now = LiveClock().now()
    if now < OPEN_AT:
        return refuse(f"개봉 시각({OPEN_AT:%F %H:%M} KST) 전", 2)
    f = nb.OUT / "n1.pkl"
    if not f.exists():
        return refuse(f"{f} 없음 — tools/past_vault_n1_bake.py 가 먼저다", 3)
    bad = check_bake(f, PROTOCOL.read_text())
    if bad:
        return refuse(bad, 3)
    assert_rule()
    store = Store(root=pj.ROOT)
    close, value, mcap = pj.load_market(store, date(2021, 8, 10), date(2009, 12, 1))
    scores = pd.read_pickle(f)  # invariant-allow: data-access — 봉인 창고 굽기
    lines, verdict, info = pj.judge_one(KIND, scores, close, value, mcap, pj.FIRST, pj.LAST, pj.PERIODS)
    print("\n".join(lines))
    print(f"  → {KIND}: {verdict}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
