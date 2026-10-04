"""원달러 환율 일봉 (Yahoo `KRW=X`) — **폐기. 실행하면 거부한다** (2026-10-04, 사용자 승인).

`query1/query2.finance.yahoo.com` 의 robots.txt 는 `Disallow: /` 다. robots 가 막은 호스트는 자동 수집
원천으로 쓰지 않는다(`docs/design/data-contract.md` §4-2). 대체: `tools/collect_fx_ls.py` (LS t3518 USDKRWSMBS).

파일은 크론·스크립트에 남은 옛 호출을 **조용히 성공시키지 않으려고** 남긴다 — 부르면 이유를 찍고 rc=2.
예전 구현은 git 이력에 있다.
"""

from __future__ import annotations

import sys

REPLACEMENT = "tools/collect_fx_ls.py"
REASON = (
    "Yahoo(query1/query2.finance.yahoo.com) 는 robots.txt 가 전체 금지(Disallow: /)라 쓰지 않는다 — "
    f"{REPLACEMENT} 를 쓴다 (docs/design/data-contract.md §4-2)"
)


def main(argv: list[str] | None = None) -> int:
    print(f"거부: {REASON}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
