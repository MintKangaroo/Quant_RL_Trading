"""네이버 증권 종목 통합 정보 → `consensus` 행 (방향 ③, 2026-09-02).

투자의견·목표주가(`consensusInfo.recommMean` · `priceTargetMean`)와 PER/EPS·
추정PER/EPS·PBR·배당수익률(`totalInfos` 의 `per`·`eps`·`cnsPer`·`cnsEps`·`pbr`·
`dividendYieldRatio`)이 있다. **지금 값만** 보여주므로 매일 긁어야 이력이 되고,
이력이 200일은 있어야 IC 를 잰다.

값이 없는 종목(커버리지 없음)은 행을 **안 만든다** — 0 을 적으면 "추정 EPS 0" 이
된다. 응답 자체를 못 받거나 형식이 다르면 예외를 올린다(빈 결과로 위장하지 않는다).

## 출처 변경 (2026-09-11 전후)

처음엔 ``finance.naver.com/item/main.naver`` HTML 을 긁었다. 네이버가 이 주소를
새 증권 사이트(``stock.naver.com/domestic/stock/{code}/price``, Next.js)로
**리다이렉트**하기 시작하면서 옛 표(`summary="투자의견 정보"`, `id="_eps"`)가
사라졌고, 9/11 부터 2,800 종목 전부가 ``ConsensusUnavailable`` 로 떨어졌다.
같은 값은 네이버 증권 모바일/새 사이트가 화면을 그릴 때 부르는 공개 JSON
``m.stock.naver.com/api/stock/{code}/integration`` 에 그대로 있다(로그인 불필요).
9/8·9/9 에 옛 페이지로 받은 값과 단위·척도가 같다(투자의견 1~5 평균, 목표주가 원).
없는 종목 코드는 409 ``{"code":"StockConflict"}`` 가 온다 — 실패로 센다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

CONSENSUS = "consensus"
SOURCE = "naver_finance"
MARKET = "KR"
URL = "https://m.stock.naver.com/api/stock/{code}/integration"

# totalInfos 의 code → 우리 열
_TOTAL_INFOS = {
    "per": "per_ttm", "eps": "eps_ttm", "cnsPer": "per_fwd", "cnsEps": "eps_fwd",
    "pbr": "pbr", "dividendYieldRatio": "dividend_yield",
}


class ConsensusUnavailable(RuntimeError):
    """응답을 못 받았거나 형식이 바뀌었다."""


def _number(text: Any) -> float | None:
    if text is None:
        return None
    text = str(text).replace(",", "").replace("배", "").replace("원", "").replace("%", "").strip()
    if text in ("", "-", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_integration(payload: Any) -> dict[str, float | None]:
    """통합 정보 JSON → 필드. 종목 응답 형식이 아니면 예외."""
    if not isinstance(payload, dict) or not isinstance(payload.get("totalInfos"), list):
        raise ConsensusUnavailable("종목 통합 정보 형식이 아니다")
    out: dict[str, float | None] = {
        "rating": None, "target_price": None, "eps_ttm": None, "per_ttm": None,
        "eps_fwd": None, "per_fwd": None, "pbr": None, "dividend_yield": None,
    }
    for item in payload["totalInfos"]:
        column = _TOTAL_INFOS.get(item.get("code")) if isinstance(item, dict) else None
        if column:
            out[column] = _number(item.get("value"))
    cns = payload.get("consensusInfo")
    if isinstance(cns, dict):
        out["rating"] = _number(cns.get("recommMean"))
        out["target_price"] = _number(cns.get("priceTargetMean"))
    return out


def row_for(code: str, *, day: date, observed_at: datetime, parsed: dict[str, float | None]) -> dict[str, Any] | None:
    """컨센서스가 하나도 없으면 None — 커버리지 없는 종목은 행을 안 만든다."""
    if all(parsed.get(k) is None for k in ("rating", "target_price", "eps_fwd")):
        return None
    return {
        "entity_id": f"{MARKET}:{code}",
        "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
        "observed_at": observed_at,
        "source": SOURCE,
        "market": MARKET,
        **parsed,
    }


def fail_verdict(fails: int, total: int, *, max_fail_ratio: float) -> str | None:
    """실패 비율이 문턱을 넘으면 사유 문자열, 아니면 None.

    평소에도 상장폐지 직전·신규 종목 등 수십~백여 건은 실패한다(옛 페이지 시절
    2,800 중 ~135). 문턱을 넘으면 원본이 바뀌었거나 막힌 것이다 — rc≠0 으로 나간다.
    """
    if total <= 0:
        return "대상 종목이 0개다"
    ratio = fails / total
    if ratio > max_fail_ratio:
        return (
            f"실패 {fails}/{total} ({ratio:.1%}) 가 문턱 {max_fail_ratio:.0%} 를 넘었다"
            " — 원본 형식 변경·차단을 의심"
        )
    return None


def run_id_for(day: date, *, limit: int = 0) -> str:
    """전체 실행과 배관 확인(--limit)의 run id 를 가른다 — 확인 실행이 그날의 전체
    실행을 "이미 받았다" 로 막았던 실수(2026-09-02)를 되풀이하지 않기 위해서다."""
    base = f"consensus-naver-{day.isoformat()}"
    return f"{base}-smoke{limit}" if limit else f"{base}-full"
