"""FINRA 공매도 — `flow_us` Analyst 의 입력 (태스크 #50).

## 왜 이것인가

`flow_us` 는 입력이 0건이라 측정 자체가 안 됐다. 국장 `flow_kr` 은 투자자별
순매수(외국인·기관)를 쓰는데 미국에는 그 공개가 없다. **규제 데이터로
대신한다** — FINRA 가 회원사에서 걷은 공매도를 무료로 공개한다.

## 표가 둘이고 주기가 다르다

    volume    매일   통합 공매도 거래량(CNMS). flow_us 의 주력
    interest  월 2회 공매도 잔고. 느리지만 포지션의 크기를 말한다

**한 계열로 섞으면 안 된다.** 반월 값을 일별인 척 넣으면 발표 사이 구간에서
같은 값이 매일 반복되는데, 그게 피처에서는 "변화 없음" 이 아니라 "관측됨"
으로 읽힌다. `short_flow` 의 자연키에 `kind` 를 넣어 가른다.

## 비율을 저장하지 않는다

`short_volume / total_volume` 이 신호이지만 그 나눗셈은 여기서 하지 않는다.
분자·분모를 그대로 두면 나중에 "5일 평균 대비" 든 "거래대금 가중" 이든 다르게
물을 수 있다. 비율만 남기면 되돌릴 수 없다.

## CNMS 를 쓴다 — FNSQ 는 나스닥만이다

같은 날 두 파일이 있고 숫자가 다르다(2026-08-14 실측: A 종목이 CNMS
135,784 · FNSQ 129,197). CNMS 가 B·Q·N 통합이라 시장 전체를 본다. 섞어
쓰면 종목마다 다른 모집단을 보게 된다.

## 비율 50% 는 정상이다 — 수준이 아니라 편차를 본다

실측 2026-08-14: 12,195종목의 **공매도 비율 중앙값이 0.4992** 였다. 이걸
"거래의 절반이 공매도" 로 읽으면 안 된다. FINRA 의 공매도 거래량에는
**시장조성자의 헤지가 섞여 있다** — 매수 주문을 받아준 뒤 재고를 털기 위한
매도도 공매도로 집계된다. 방향성 베팅이 아니다.

그래서 신호는 **수준이 아니라 편차**다: 그 종목의 평소 비율 대비 오늘이
얼마나 높은가. Analyst 가 그 계산을 하도록 분자·분모를 그대로 남긴다.

이 사실을 모르면 "비율 0.5 이상이면 공매도 과열" 같은 규칙을 만들게 되고,
그건 시장 전체의 절반을 과열로 판정한다.

## 인증이 없다

CDN 파일도 잔고 API 도 키가 필요 없다(2026-08-19 실측). User-Agent 만 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

SHORT_FLOW = "short_flow"
SOURCE = "finra"
MARKET = "US"
EASTERN = ZoneInfo("America/New_York")

#: 통합 일별 공매도 거래량. **FNSQ(나스닥 전용)와 섞지 않는다.**
DAILY_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"

#: 공매도 잔고. 월 2회.
INTEREST_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)

#: 잔고는 **결제일 기준 집계 후 며칠 뒤에 공표된다.** 정확한 시각을 주지
#: 않으므로 보수적으로 잡는다 — 발표일 18:00 ET 로 두면 그날 종가 뒤라
#: 다음 날부터 쓰인다.
PUBLISH_HOUR_ET = 18


class FinraUnavailable(RuntimeError):
    """받아오지 못했다. **빈 결과로 위장하지 않는다** — 그러면 그날이
    "공매도가 없던 날" 로 기록되어 영원히 안 채워진다."""


def parse_daily(text: str, *, observed_at: datetime) -> list[dict[str, Any]]:
    """CNMS 파일 → `short_flow` 행.

    형식: ``Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market``

    **총거래량이 0 인 행은 버린다.** 비율의 분모가 되는 값이라 0 이면 그
    종목의 신호를 만들 수 없고, 0 을 남겨 두면 읽는 쪽이 매번 나눗셈 앞에서
    막아야 한다.
    """
    rows: list[dict[str, Any]] = []
    lines = text.strip().splitlines()
    if not lines:
        return rows
    header = lines[0].split("|")
    if header[:2] != ["Date", "Symbol"]:
        raise FinraUnavailable(f"머리글이 예상과 다르다: {lines[0][:80]!r}")

    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        stamp, symbol = parts[0].strip(), parts[1].strip().upper()
        if not symbol or len(stamp) != 8:
            continue
        try:
            short = float(parts[2] or 0.0)
            exempt = float(parts[3] or 0.0)
            total = float(parts[4] or 0.0)
        except ValueError:
            continue
        if total <= 0.0:
            continue
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        rows.append({
            "entity_id": f"US:{symbol}",
            "valid_from": datetime(day.year, day.month, day.day, tzinfo=UTC),
            "observed_at": observed_at,
            "source": SOURCE,
            "market": MARKET,
            "kind": "volume",
            "short_volume": short,
            "short_exempt_volume": exempt,
            "total_volume": total,
            "short_position": None,
            "previous_short_position": None,
            "days_to_cover": None,
            "average_daily_volume": None,
        })
    return rows


def daily_run_id(day: date) -> str:
    return f"finra-shortvol-{day.isoformat()}"


def publish_moment(day: date, *, hour_et: int = PUBLISH_HOUR_ET) -> datetime:
    """관측시각. 미장 종가는 16:00 ET 이므로 18:00 은 그 뒤다 —
    **그날 종가 피처에는 안 들어가고 다음 날부터 쓰인다.**"""
    local = datetime(day.year, day.month, day.day, hour_et, tzinfo=EASTERN)
    return local.astimezone(UTC)


@dataclass
class DailyResult:
    day: date
    rows: int
    skipped: bool = False
    error: str = ""


@dataclass
class ShortVolumeBackfiller:
    """날짜 축. 하루가 파일 하나라 콜이 하루 1건이다."""

    store: Any
    fetch: Any  # (url) -> str
    clock: Any
    _seen: set[str] = field(default_factory=set)

    def plan(self, start: date, end: date) -> list[date]:
        """달력일 전부. **주말·휴일 파일은 404 다** — 그건 오류가 아니라
        장이 안 선 날이다. `run_day` 가 구분한다."""
        days: list[date] = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor = date.fromordinal(cursor.toordinal() + 1)
        return days

    def run_day(self, day: date) -> DailyResult:
        run_id = daily_run_id(day)
        if self.store.ingest_run_recorded(SHORT_FLOW, run_id):
            return DailyResult(day, 0, skipped=True)
        observed_at = publish_moment(day)
        if observed_at > self.clock.now():
            # 아직 알 수 없는 날이다. 이력에 안 남기므로 다음 실행이 받는다.
            return DailyResult(day, 0, error="아직 공표 전")
        try:
            text = self.fetch(DAILY_URL.format(day=day.strftime("%Y%m%d")))
        except FileNotFoundError:
            # 휴장일. 파일 자체가 없다 — 오류가 아니다.
            return DailyResult(day, 0)
        except Exception as error:  # noqa: BLE001
            return DailyResult(day, 0, error=str(error))

        rows = parse_daily(text, observed_at=observed_at)
        if not rows:
            return DailyResult(day, 0)
        written = self.store.append(
            SHORT_FLOW, rows, ingest_run_id=run_id, source=SOURCE
        )
        return DailyResult(day, written)
