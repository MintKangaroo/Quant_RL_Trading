"""시스템 탭 집계 — **지금 이 시스템이 제대로 돌고 있는가.**

Data Quality 가 "데이터가 썩고 있나", Agent Health 가 "에이전트가 썩고
있나" 를 본다면 이 화면은 **배관** 을 본다 — 크론이 실제로 돌았는가,
창고가 오늘 것을 알고 있는가, 안전장치가 걸려 있는가, 설정이 어느
판번호인가, LLM 캐시·실제 호출 비용이 쌓이고 있는가.

## 두 종류의 시각이 섞여 있다

`job_history` 는 ``logs/*.log`` 를 읽는다 — 창고가 아니라 운영 로그다.
``jobs.py`` (백필·IC 진행률)와 같은 이유로 **as_of 로 되감기지 않는다.**
어제 시점의 "크론이 성공했나" 는 로그 파일에 여전히 남아 있지만, 그건
"그때 알 수 있었던 것"이 아니라 "지금 로그에 뭐가 있나" 이므로 이중시간의
대상이 아니다. 규약대로 ``as_of`` 는 받아 되돌려주되 내용은 언제나 최신이다.

``table_freshness`` · ``cache_stats`` · ``llm_usage_summary`` · ``safety_summary``
는 반대로 창고를 경유한다 (불변식 1) — 그래서 이쪽은 정직하게 되감긴다.

## LLM 캐시와 LLM 호출은 서로 다른 표다

``agent_cache`` 는 판정·해설을 항목별로 캐싱한다 — 캐시를 맞힌 호출은
새 행을 쓰지 않으므로 ``cache_stats`` 는 **적재 건수**만 셀 수 있고
적중률·비용은 못 잰다(``cache_stats`` docstring). ``llm_usage`` 는 반대로
HTTP 왕복 하나하나를 기록하는 표라 호출 횟수·토큰이 실측이고,
``store.config("llm").pricing`` 을 곱하면 달러 비용까지 나온다
(``llm_usage_summary``). 캐시 적재 건수와 llm_usage 호출 건수를 더해서는
안 된다 — 하나는 "무엇을 알고 있나", 하나는 "무엇을 새로 계산했나"다.

## 큰 테이블을 스캔하지 않는다

``flows`` 는 파일이 109만 개다. 그래서 최신성 확인은 **짧은 lookback**
으로만 연다 — 파티션 하한 프루닝이 걸려 있는 테이블(대부분)은 최근 며칠치
파티션만 열리고, 선언이 없는 몇 개(fundamentals·documents·events·
verdicts·analyst_weights·agent_cache·llm_usage)는 통째로 열리지만 전부
합쳐도 150MB 남짓이라 문제되지 않는다. ``llm_usage_summary`` 는 그마저도
``lookback`` 인자로 창을 짧게 잡는다(``cache_stats`` 와 같은 패턴).

## 서버 리소스는 창고를 아예 거치지 않는다

``server_resources`` · ``project_processes`` 는 ``/proc`` 만 읽는다 — CPU
load·메모리·프로세스는 창고의 사실이 아니라 **이 기계의 사실**이라
불변식 1(store.get 경유)과 무관하다. 디스크 사용량은 ``os.statvfs`` 로
파일시스템 통계만 묻는다 — 파일을 열거나 파티션을 나열하지 않는다.
``flows`` 디렉터리 전체 크기를 재려면 ``du``/``os.walk`` 로 109만 개
파일을 훑어야 하는데(실측 11~20초), 그건 이 탭이 막으려는 바로 그 사고라
**테이블별 저장소 크기는 만들지 않는다.**
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from quant_rl_trading.collectors.market_hours import Market, trading_days
from quant_rl_trading.executor import guards
from quant_rl_trading.store import Store
from quant_rl_trading.store.tables import CONFIG_TABLE, table_names

AGENT_CACHE = "agent_cache"
LLM_USAGE = "llm_usage"

#: 프로세스 표에 실을 최대 행수.
PROCESS_ROWS = 12

#: 로그 타임스탬프의 출처. run_daily.sh 등이 ``date '+%F %T'`` 로 찍는 값은
#: 이 머신의 지역시각(KST)이다 — 벽시계를 여기서 읽는 것이 아니라 이미 파일에
#: 적혀 있는 과거 기록을 해석하는 것이므로 불변식 2(Clock 주입)와 무관하다.
KST = ZoneInfo("Asia/Seoul")

#: 최근 파티션 확인 창(일). 전체 창고 크기가 아니라 "오늘 것이 들어왔나" 가
#: 목적이라 짧게 잡는다 — flows 처럼 파티션이 109만 개인 테이블도 이 창이면
#: 최근 며칠치만 연다.
TABLE_LOOKBACK_DAYS = 10

#: 표에 실을 최근 실행 횟수.
RUN_HISTORY_LIMIT = 8

RUN_HEADER = re.compile(r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) market=(\S+)", re.M)
RC_LINE = re.compile(r"\brc=(-?\d+)")


@dataclass(frozen=True)
class JobDef:
    """crontab 에 등록된 작업 하나. 실제 등록 여부는 ``crontab -l`` 로 확인했다."""

    key: str
    label: str
    log_prefix: str
    schedule: str


#: crontab 에 실제로 등록된 4개 작업 (2026-08-14 확인). 여기 없는 것을
#: "돌고 있다"고 지어내지 않는다 — 명단이 곧 진실의 경계다.
JOB_DEFS: list[JobDef] = [
    JobDef("collect_daily", "일일 수집 (시세·유니버스·수급·거시)", "collect", "평일 15:55, 22:40"),
    JobDef("run_daily", "일일 실행 (Analyst 점수·News/SNS 판정)", "daily", "평일 16:10"),
    JobDef("run_shadow", "shadow 운용", "shadow", "평일 16:30"),
    JobDef("collect_news", "뉴스 수집", "news", "평일 08:20, 15:40"),
]


@dataclass(frozen=True)
class Run:
    """로그 한 블록 — ``=== ... ===`` 헤더부터 다음 헤더 전까지."""

    started_at: str
    market: str
    steps: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool | None:
        """세 값 논리다. ``rc=`` 를 하나도 못 찾았으면 완주하지 못한 것이지
        성공도 실패도 아니다 — 그걸 실패로 찍으면 로그 형식이 바뀌었을 때
        "매번 실패"로 오판하고, 성공으로 찍으면 죽은 작업을 놓친다."""
        if not self.steps:
            return None
        return all(rc == 0 for rc in self.steps)


def _parse_runs(text: str) -> list[Run]:
    headers = list(RUN_HEADER.finditer(text))
    runs: list[Run] = []
    for index, match in enumerate(headers):
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[start:end]
        runs.append(
            Run(
                started_at=match.group(1),
                market=match.group(2),
                steps=[int(value) for value in RC_LINE.findall(body)],
            )
        )
    return runs


def _to_utc(local_stamp: str) -> str:
    """``YYYY-MM-DD HH:MM:SS`` (KST, naive) → ISO8601 UTC.

    로그가 naive 인 이유는 벽시계를 그대로 ``date`` 로 찍은 운영 스크립트라서다.
    화면에 내보낼 때는 이 프로젝트의 다른 모든 시각과 같이 타임존을 붙인다.
    """
    naive = datetime.strptime(local_stamp, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=KST).isoformat()


#: 레포 뿌리. **창고 경로에서 유도하지 않는다.**
#: `store.root.parent` 로 잡으면 실전 창고(`data/`)일 때만 우연히 맞고,
#: `data/_demo`·`data/_shadow` 에서는 `data/logs` 를 찾다가 못 찾는다. 그러면
#: 화면이 작업 넷을 전부 "완주 여부를 로그에서 확인할 수 없음" 으로 띄운다 —
#: 크론은 멀쩡히 도는데 화면만 고장으로 보이고, 그 오판이 제일 비싸다.
#: 로그는 창고가 아니라 레포에 딸린 운영 기록이므로 모듈 위치에서 잡는다.
#:
#: 환경변수로 덮을 수 있게 둔 이유는 **테스트가 가짜 로그를 심어야 하기**
#: 때문이다. 위치를 상수로 굳히면 로그 해석 로직을 시험할 방법이 없어지고,
#: 시험할 수 없는 파서는 형식이 바뀐 날 조용히 틀린다.
LOGS_DIR_ENV = "QUANT_RL_LOGS_DIR"
REPO_ROOT = Path(__file__).resolve().parents[3]


def logs_dir() -> Path:
    override = os.environ.get(LOGS_DIR_ENV)
    return Path(override) if override else REPO_ROOT / "logs"


def _log_files(prefix: str) -> list[Path]:
    directory = logs_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{prefix}-*.log"))


def _read_runs(prefix: str) -> list[Run]:
    # 최근 두 파일(이번 달 + 지난달)이면 충분하다 — 로그는 매달 새 파일로
    # 갈리고, 표에는 최근 실행 몇 건만 싣는다.
    runs: list[Run] = []
    for path in _log_files(prefix)[-2:]:
        try:
            runs.extend(_parse_runs(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    runs.sort(key=lambda run: run.started_at)
    return runs


def job_history(store: Store) -> list[dict[str, Any]]:
    """crontab 작업 4종의 최근 실행 이력.

    **as_of 로 되감기지 않는다** — 운영 로그이지 창고가 아니다 (모듈 docstring).
    """
    out: list[dict[str, Any]] = []
    for job in JOB_DEFS:
        runs = _read_runs(job.log_prefix)
        recent = runs[-RUN_HISTORY_LIMIT:]
        last_success = next((run for run in reversed(runs) if run.ok), None)
        out.append(
            {
                "key": job.key,
                "label": job.label,
                "schedule": job.schedule,
                "runs": [
                    {
                        "started_at": _to_utc(run.started_at),
                        "market": run.market,
                        "ok": run.ok,
                        "steps": run.steps,
                    }
                    for run in reversed(recent)
                ],
                "last_run_ok": recent[-1].ok if recent else None,
                "last_success_at": _to_utc(last_success.started_at) if last_success else None,
                "run_count": len(runs),
            }
        )
    return out


#: 최신성 계산 결과를 잠깐 들고 있는다. **한 화면이 이걸 두 번 계산하기 때문이다** —
#: `summary()` 가 안에서 부르고, 화면이 `/api/system/tables` 로 또 부른다. 등록된
#: 표를 전부 여는 계산이라 한 번에 2.5초쯤 걸리고, 그게 두 번이면 시스템 탭만
#: 5초를 쓴다.
#:
#: **되감은 화면에는 절대 쓰지 않는다.** `live=True` — 즉 사용자가 as_of 를 주지
#: 않아 "지금" 을 보는 경우 — 에만 공유한다. 타임머신은 언제나 그 시점을 다시
#: 계산한다. as_of 를 열쇠에 넣는 것만으로는 부족한데, 라이브 요청의 as_of 는
#: 매 요청마다 달라서(그 순간의 시각) 열쇠가 늘 빗나가기 때문이다.
#: 수명이 짧은 이유는 이 값이 "지금 배관이 도는가" 라서다 — 오래 들고 있으면
#: 크론이 방금 채운 것을 화면이 모른다.
_FRESHNESS_TTL_SEC = 20.0
_freshness_cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}


#: **신선도 경보를 걸지 않는 테이블.** 여기 있는 것은 매일 생기는 것이 아니라
#: 사건이 있을 때만 생긴다 — "며칠째 안 들어왔다" 가 고장이 아니라 정상이다.
#:
#: `capital_flows` 는 입출금이다. 한 달에 한 번 넣을 수도, 반년을 안 넣을 수도
#: 있다. 여기에 지연 경보를 걸면 **아무 일도 없었다는 사실이 매일 빨간불**로
#: 뜨고, 그 빨간불이 일상이 되면 진짜 고장도 같이 안 보이게 된다.
#:
#: 표에서 지우는 것이 아니라 **경보만 안 건다** — 마지막이 언제인지는 계속 보여준다.
NO_STALENESS_ALARM = frozenset({"capital_flows"})


def _trading_days_since(latest: datetime, as_of: datetime) -> int:
    """``latest`` 이후 지난 **거래일** 수. 달력일이 아니다.

    달력일로 세면 연휴마다 전 테이블이 빨간불이 된다 — 2026-08-15(토)·16(일)·
    17(광복절 대체공휴일)이 붙어 사흘을 쉬었고, 그래서 8/18 아침에 flows·
    universe·fx 가 "4일 지연" 으로 떴다. 넷 다 마지막 거래일(8/14) 것이
    정상으로 들어와 있었다.

    **휴장인지 수집 실패인지는 날짜가 아니라 거래일 달력이 가른다.** 같은
    고침을 브리핑(`benchmark.py`)에서 이미 한 번 했다.

    국장 달력으로 센다. 미장 전용 테이블도 있지만, 이 화면이 답하는 질문은
    "우리 배관이 오늘 돌았나" 이고 배관은 국장 일정으로 돈다.
    """
    if latest >= as_of:
        return 0
    days = trading_days(Market.KR, latest.date(), as_of.date())
    # 양끝을 다 포함하므로 마지막 거래일 자신을 뺀다. 같은 날이면 0 이다.
    return max(0, len(days) - 1)


def table_freshness(
    store: Store,
    *,
    as_of: datetime,
    lookback: int = TABLE_LOOKBACK_DAYS,
    live: bool = False,
) -> list[dict[str, Any]]:
    """등록된 테이블마다 최근 창의 행수와 최신 ``valid_from``.

    전체 행수가 아니다 — "얼마나 큰가"가 아니라 "오늘 것이 들어왔나"를 답한다.
    아직 한 행도 없는 테이블(M3 회계·집행)은 0/None 으로 정직하게 남긴다.
    """
    key = (str(store.root), lookback)
    now = monotonic()
    if live:
        hit = _freshness_cache.get(key)
        if hit is not None and now - hit[0] < _FRESHNESS_TTL_SEC:
            return hit[1]

    out: list[dict[str, Any]] = []
    for name in table_names():
        if name == CONFIG_TABLE:
            continue
        # valid_from 하나만 받는다 — 최신성 확인에 값 컬럼은 필요 없고, 안
        # 받으면 큰 테이블에서도 가볍다. natural_key 에 맡기지 않는 이유는
        # ``events`` 처럼 자연키가 (entity_id, seq) 라 valid_from 이 안 딸려
        # 오는 테이블이 있기 때문이다 — 모든 테이블이 valid_from 을 갖는다는
        # 사실(schema.py REQUIRED_COLUMNS)만 믿는다.
        frame = store.get(name, as_of=as_of, lookback=lookback, columns=["valid_from"])
        # **미래 표지는 최신성 판정에서 뺀다.** `market_stats` 의 미장
        # 상장주식수는 SEC 공시의 유효일이 앞날로 찍히는 행이 있어(2028-08-01
        # 실측) 그 행 하나가 표를 영원히 "지연 0" 으로 만든다. 그러면 같은
        # 표의 국장 시총이 사흘 밀려도 화면은 최신이라고 말한다 — 실제로
        # 2026-08-18 에 그랬다.
        #
        # 지우지는 않는다. 읽는 쪽(us_shares)은 그 행을 정상으로 쓰고 있고,
        # 여기서 답하려는 질문은 "**오늘 것이 들어왔나**" 뿐이다.
        if not frame.empty:
            frame = frame[frame["valid_from"] <= pd.Timestamp(as_of)]
        if frame.empty:
            out.append(
                {
                    "table": name,
                    "rows_recent": 0,
                    "latest_valid_from": None,
                    "stale_days": None,
                    "alarm": name not in NO_STALENESS_ALARM,
                }
            )
            continue
        if frame.empty:
            out.append(
                {
                    "table": name,
                    "rows_recent": 0,
                    "latest_valid_from": None,
                    "stale_days": None,
                    "alarm": name not in NO_STALENESS_ALARM,
                }
            )
            continue
        latest = pd.Timestamp(frame["valid_from"].max())
        out.append(
            {
                "table": name,
                "rows_recent": len(frame),
                "latest_valid_from": latest.isoformat(),
                # **거래일로 센다.** 달력일로 세면 연휴마다 전부 빨간불이다.
                "stale_days": _trading_days_since(latest.to_pydatetime(), as_of),
                # 사건 테이블은 경보 대상이 아니다 — 표에는 남기되 경고는 안 만든다.
                "alarm": name not in NO_STALENESS_ALARM,
            }
        )
    if live:
        _freshness_cache[key] = (now, out)
    return out


def cache_stats(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """LLM/에이전트 캐시(``agent_cache``) 적재 현황.

    **적중률과 실제 비용은 창고에 없다.** ``agent_cache`` 는 계산된 출력만
    담고 몇 번 재사용됐는지는 세지 않는다 — 캐시를 맞힌 호출은 애초에 새
    행을 쓰지 않기 때문이다. 없는 값을 지어내는 대신 여기서 셀 수 있는 것
    (에이전트별 적재 건수·최신 계산 시각)만 보여주고, 예산은 참고용으로
    설정값을 그대로 싣는다.
    """
    frame = store.get(
        AGENT_CACHE,
        as_of=as_of,
        lookback=lookback,
        columns=["agent", "agent_version", "computed_at"],
    )
    if frame.empty:
        return {"rows": [], "total": 0}

    grouped = (
        frame.groupby(["agent", "agent_version"])
        .agg(entries=("agent", "size"), last_computed_at=("computed_at", "max"))
        .reset_index()
        .sort_values("entries", ascending=False)
    )
    rows = [
        {
            "agent": str(row["agent"]),
            "agent_version": str(row["agent_version"]),
            "entries": int(row["entries"]),
            "last_computed_at": pd.Timestamp(row["last_computed_at"]).isoformat(),
        }
        for row in grouped.to_dict(orient="records")
    ]
    return {"rows": rows, "total": len(frame)}


def llm_usage_summary(store: Store, *, as_of: datetime, lookback: int) -> dict[str, Any]:
    """LLM 실제 호출량·비용(``llm_usage``) — 모델·에이전트별 토큰 합계와 단가 환산.

    ``cache_stats`` 가 못 세는 "적중률·비용"과는 다른 표다. 이 표는 HTTP
    왕복 하나하나를 기록하므로 **호출 횟수·토큰 합계는 실측**이다. 달러
    환산은 ``store.config("llm").pricing`` 을 곱해서 구한다(불변식 10) —
    코드에 단가를 박지 않는다. ``llm_usage.model`` 문자열이 단가표 키와
    맞지 않는 모델(신규 모델·단가 미등록)이 섞여 있으면 그 모델의
    ``cost_usd`` 는 ``None`` 으로 남기고 ``unpriced_models`` 로 알린다 —
    0으로 채우면 그 모델은 공짜로 계산돼 전체 합계가 실제보다 작게 나온다.
    """
    llm_config = store.config("llm", as_of=as_of)
    # store.config 는 YAML 을 평평하게 저장한다(config.py flatten) — 섹션
    # 조회(``read_section``)는 접두사("llm.")만 한 번 벗기고, 그 아래
    # 중첩(``pricing.<model>.<field>``)은 다시 묶어주지 않는다. 그래서
    # "pricing." 로 시작하는 키를 여기서 직접 3단으로 다시 묶는다.
    pricing: dict[str, dict[str, float]] = {}
    for key, value in llm_config.items():
        if key.startswith("pricing."):
            _, model_key, field = key.split(".", 2)
            pricing.setdefault(model_key, {})[field] = value
    usd_krw_rate = llm_config.get("usd_krw_rate")

    frame = store.get(
        LLM_USAGE,
        as_of=as_of,
        lookback=lookback,
        columns=[
            "agent",
            "model",
            "items",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "computed_at",
        ],
    )
    if frame.empty:
        return {
            "rows": [],
            "calls": 0,
            "total_tokens": 0,
            "total_cost_usd": None,
            "usd_krw_rate": usd_krw_rate,
            "unpriced_models": [],
        }

    grouped = (
        frame.groupby(["agent", "model"])
        .agg(
            calls=("agent", "size"),
            items=("items", "sum"),
            input_tokens=("input_tokens", "sum"),
            output_tokens=("output_tokens", "sum"),
            cache_creation_tokens=("cache_creation_input_tokens", "sum"),
            cache_read_tokens=("cache_read_input_tokens", "sum"),
            last_call_at=("computed_at", "max"),
        )
        .reset_index()
        .sort_values("calls", ascending=False)
    )

    rows: list[dict[str, Any]] = []
    unpriced_models: set[str] = set()
    total_cost: float | None = 0.0
    for row in grouped.to_dict(orient="records"):
        model = str(row["model"])
        price = pricing.get(model)
        cost: float | None
        if price is None:
            unpriced_models.add(model)
            cost = None
            total_cost = None
        else:
            cost = (
                row["input_tokens"] * price["input_per_mtok"]
                + row["output_tokens"] * price["output_per_mtok"]
                + row["cache_creation_tokens"] * price["cache_write_per_mtok"]
                + row["cache_read_tokens"] * price["cache_read_per_mtok"]
            ) / 1e6
            if total_cost is not None:
                total_cost += cost
        rows.append(
            {
                "agent": str(row["agent"]),
                "model": model,
                "calls": int(row["calls"]),
                "items": int(row["items"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "cache_creation_tokens": int(row["cache_creation_tokens"]),
                "cache_read_tokens": int(row["cache_read_tokens"]),
                "cost_usd": round(cost, 4) if cost is not None else None,
                "last_call_at": pd.Timestamp(row["last_call_at"]).isoformat(),
            }
        )

    total_tokens = int(
        frame[
            [
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ]
        ]
        .sum()
        .sum()
    )
    return {
        "rows": rows,
        "calls": len(frame),
        "total_tokens": total_tokens,
        # 단가 미등록 모델이 하나라도 섞이면 합계 전체를 None 으로 둔다 —
        # "일부만 더한 값"을 총액인 것처럼 보여주면 실제보다 적게 지출한
        # 것처럼 읽힌다.
        "total_cost_usd": round(total_cost, 2) if total_cost is not None else None,
        "usd_krw_rate": usd_krw_rate,
        "unpriced_models": sorted(unpriced_models),
    }


def safety_summary(store: Store, *, as_of: datetime) -> dict[str, Any]:
    """킬스위치·설정 판번호·LLM 예산 — "안전장치가 걸려 있나" 의 답."""
    state, reason = guards.killswitch_state(store, as_of=as_of)
    return {
        "killswitch_engaged": state is guards.KillswitchState.ENGAGED,
        "killswitch_reason": reason,
        "config_version": int(store.config("config_version", as_of=as_of)),
        "llm_monthly_budget_usd": float(store.config("llm.monthly_budget_usd", as_of=as_of)),
    }


def server_resources(root: Path) -> dict[str, Any]:
    """CPU load·메모리·디스크 — 이 기계 자체의 상태 (모듈 docstring).

    LS_KR 참고 구현(``system-stats``)의 이식이다. 못 읽으면(리눅스가 아니거나
    권한이 없으면) 그 항목만 ``None`` — 나머지는 살아 있어야 한다.
    """
    cpu: dict[str, Any] | None
    try:
        cores = os.cpu_count() or 1
        load1, load5, load15 = os.getloadavg()
        cpu = {
            "cores": cores,
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
            "load1_pct": round(min(100.0, load1 / cores * 100), 1),
        }
    except OSError:
        cpu = None

    memory: dict[str, Any] | None
    try:
        fields: dict[str, float] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            parts = value.strip().split()
            if parts:
                fields[key.strip()] = float(parts[0])
        total = fields.get("MemTotal", 0.0) / 1024 / 1024
        avail = fields.get("MemAvailable", 0.0) / 1024 / 1024
        used = total - avail
        memory = {
            "total_gb": round(total, 2),
            "used_gb": round(used, 2),
            "avail_gb": round(avail, 2),
            "used_pct": round(used / total * 100, 1) if total else None,
        }
    except OSError:
        memory = None

    disk: dict[str, Any] | None
    try:
        # 통계만 묻는다 — 파일을 열지 않으므로 파티션이 몇 개든 O(1)이다.
        stats = os.statvfs(str(root))
        total = stats.f_blocks * stats.f_frsize / 1e9
        free = stats.f_bavail * stats.f_frsize / 1e9
        used = total - free
        disk = {
            "total_gb": round(total, 1),
            "used_gb": round(used, 1),
            "free_gb": round(free, 1),
            "used_pct": round(used / total * 100, 1) if total else None,
        }
    except OSError:
        disk = None

    return {"cpu": cpu, "memory": memory, "disk": disk}


def project_processes(root: Path) -> dict[str, Any]:
    """이 프로젝트 아래에서 도는 프로세스 — ``cwd`` 가 레포 루트 밑인 것만.

    자기 자신(대시보드)도 포함한다 — 뺄 이유가 없다. jobs.py 의
    ``_running_commands`` 가 자기 자신을 빼는 것은 "돌고 있는지" 라는 예/아니오
    판정에 쓰기 때문이고, 여기는 "지금 뭐가 도는가" 를 있는 그대로 보여주는
    것이 목적이라 다르다.

    **레포 루트는 ``root.parent`` 로 구하지 않는다.** 그렇게 하면 실전 창고
    (``data/``)일 때만 우연히 맞고, ``data/_shadow``·``data/_demo`` 로 띄우면
    ``data/`` 를 레포 루트로 보아 **아무 프로세스도 안 걸린다** — 화면은 고장이
    아니라 "도는 게 없다" 고 말하므로 아무도 이상하게 여기지 않는다. 실측:
    ``data`` 12개 · ``data/_shadow`` 0개 · ``data/_demo`` 0개 (2026-08-15).

    같은 결함을 크론 이력에서 한 번 잡았는데(``_log_dir`` 의 ``REPO_ROOT``)
    이 함수에 그대로 남아 있었다. ``root`` 는 창고 위치일 뿐 레포 위치가 아니다.
    """
    repo_root = str(REPO_ROOT)
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        clk = os.sysconf("SC_CLK_TCK")
        uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError):
        return {"processes": [], "total_rss_mb": None}

    procs: list[dict[str, Any]] = []
    try:
        pids = [entry.name for entry in Path("/proc").iterdir() if entry.name.isdigit()]
    except OSError:
        return {"processes": [], "total_rss_mb": None}

    for pid in pids:
        entry = Path("/proc") / pid
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if repo_root not in cwd:
            continue
        try:
            raw_cmdline = (entry / "cmdline").read_bytes()
            cmdline = raw_cmdline.replace(b"\x00", b" ").decode(errors="replace").strip()
            stat_fields = (entry / "stat").read_text(encoding="utf-8").split()
        except OSError:
            continue
        if len(stat_fields) < 24:
            continue
        rss_mb = int(stat_fields[23]) * page / 1024 / 1024
        utime, stime, starttime = int(stat_fields[13]), int(stat_fields[14]), int(stat_fields[21])
        proc_uptime = uptime - starttime / clk
        cpu_pct = ((utime + stime) / clk / proc_uptime * 100) if proc_uptime > 0 else 0.0
        procs.append(
            {
                "pid": int(pid),
                "command": (cmdline or "?")[:120],
                "rss_mb": round(rss_mb, 1),
                "cpu_pct": round(cpu_pct, 1),
                "uptime_h": round(proc_uptime / 3600, 1),
            }
        )

    procs.sort(key=lambda p: -p["rss_mb"])
    total_rss = round(sum(p["rss_mb"] for p in procs), 1)
    return {"processes": procs[:PROCESS_ROWS], "total_rss_mb": total_rss}


def summary(
    store: Store,
    *,
    as_of: datetime,
    lookback: int,
    thresholds: dict[str, Any],
    live: bool = False,
) -> dict[str, Any]:
    """KPI 스트립. 경고 판정도 여기서 한다 (불변식 10)."""
    jobs = job_history(store)
    tables = table_freshness(store, as_of=as_of, live=live)
    cache = cache_stats(store, as_of=as_of, lookback=int(thresholds["cache_lookback_days"]))
    usage = llm_usage_summary(store, as_of=as_of, lookback=int(thresholds["cache_lookback_days"]))
    safety = safety_summary(store, as_of=as_of)
    resources = server_resources(store.root)

    table_warn_days = int(thresholds["table_stale_warn_days"])
    stale_tables = [
        t
        for t in tables
        if t.get("alarm", True)
        and t["stale_days"] is not None
        and t["stale_days"] > table_warn_days
    ]
    failed_jobs = [j for j in jobs if j["last_run_ok"] is False]
    silent_jobs = [j for j in jobs if j["last_run_ok"] is None]

    job_stale_hours = float(thresholds["job_stale_warn_hours"])
    stale_success = [
        j
        for j in jobs
        if j["last_success_at"] is not None
        and (as_of - datetime.fromisoformat(j["last_success_at"])).total_seconds() / 3600
        > job_stale_hours
    ]

    warnings: list[str] = []
    if safety["killswitch_engaged"]:
        warnings.append(f"킬스위치 발동 중 — {safety['killswitch_reason']}")
    for job in failed_jobs:
        warnings.append(f"{job['label']} 최근 실행 실패")
    for job in silent_jobs:
        warnings.append(f"{job['label']} 완주 여부를 로그에서 확인할 수 없음")
    for job in stale_success:
        warnings.append(f"{job['label']} 마지막 성공이 {job_stale_hours:.0f}시간을 넘었다")
    for table in stale_tables:
        warnings.append(f"{table['table']} 최신 데이터가 {table['stale_days']}거래일 지연")
    # cache_lookback_days(기본 30일)를 "최근 한 달"의 근사로 써서 예산과
    # 비교한다 — as_of 시점의 정확히 달력상 이번 달은 아니라는 점을 문구에
    # 남긴다. 단가 미등록 모델이 섞여 total_cost_usd 가 None 이면 비교 자체가
    # 성립하지 않으므로 경고를 만들지 않는다(불변식 10 — 모르는 값으로 판정 금지).
    usage_cost = usage["total_cost_usd"]
    budget = safety["llm_monthly_budget_usd"]
    if usage_cost is not None and usage_cost > budget:
        warnings.append(
            f"최근 {thresholds['cache_lookback_days']}일 LLM 실측 지출 "
            f"${usage_cost:.2f}이 월 예산 ${budget:.0f}을 넘었다"
        )

    return {
        "config_version": safety["config_version"],
        "killswitch_engaged": safety["killswitch_engaged"],
        "killswitch_reason": safety["killswitch_reason"],
        "llm_monthly_budget_usd": safety["llm_monthly_budget_usd"],
        "llm_usage_cost_usd": usage["total_cost_usd"],
        "llm_usage_unpriced_models": usage["unpriced_models"],
        "job_count": len(jobs),
        "job_failed_count": len(failed_jobs),
        "job_silent_count": len(silent_jobs),
        "table_count": len(tables),
        "table_stale_count": len(stale_tables),
        "cache_recent_entries": cache["total"],
        "mem_used_pct": (resources["memory"] or {}).get("used_pct"),
        "disk_used_pct": (resources["disk"] or {}).get("used_pct"),
        "warnings": warnings,
    }


__all__ = [
    "JOB_DEFS",
    "cache_stats",
    "job_history",
    "llm_usage_summary",
    "project_processes",
    "safety_summary",
    "server_resources",
    "summary",
    "table_freshness",
]
