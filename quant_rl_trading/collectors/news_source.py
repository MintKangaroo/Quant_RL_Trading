"""뉴스 수집 — newsapi.org.

## 왜 스크래핑이 아닌가

선행 프로젝트는 네이버 HTML 을 긁는 폴백 체인을 썼고, 그건 이식하지 않기로
이미 결정돼 있다 (``document_collector`` 모듈 docstring, postmortem-ls §6-7).
바깥 HTML 구조가 바뀌면 조용히 빈 결과를 내는데, 뉴스 필터가 조용히 비면
"악재가 없다" 와 구분되지 않는다. API 는 최소한 에러를 낸다.

## 후보만 조회한다 — 전 종목이 아니다

무료 티어는 **하루 100 요청**이다. 국장만 2,800종목이라 전 종목 조회는
불가능하다.

그런데 애초에 필요가 없다. News·SNS 는 점수를 내는 Analyst 가 아니라
**후보를 걸러내는 필터**다 (``verdicts.VerdictAnalyst.candidates_to_block``).
Selector 가 추린 20~50종목만 보면 되고, 그건 하루 한도 안에 들어간다.
전 종목을 긁는 것은 쓰지도 않을 데이터를 위해 한도를 태우는 일이다.

## 시간 두 개

``valid_from`` 은 기사 발행시각, ``observed_at`` 은 우리가 받아온 시각이다.
**발행시각은 믿을 수 없다** — 언론사가 사후에 고치고, 우리가 그걸 알 방법이
없다 (data-contract §4). 그래서 발행시각을 관측시각으로 쓰지 않는다. 그렇게
쓰면 오늘 수정된 어제 기사가 어제부터 알던 사실이 된다.

## 백필하지 않는다

무료 티어는 최근 한 달만 준다. 그보다 더 근본적으로, 뉴스는 시점 정합성 있는
과거 데이터를 만들 수 없다 — 삭제된 기사는 백필에서 아예 사라지므로 남은
것만 보면 생존편향이다. 그래서 이 Analyst 는 IC 검증 대상이 아니고
(``verdicts`` 모듈 docstring), 이 수집기는 **라이브 경로 전용**이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from quant_rl_trading.collectors.errors import CollectorError, MissingCredentials
from quant_rl_trading.collectors.market_hours import Market
from quant_rl_trading.replay.clock import Clock

DOCUMENTS = "documents"
SOURCE = "newsapi"
DOC_TYPE = "news"

NEWSAPI_URL = "https://newsapi.org/v2/everything"
KEY_ENV = "NEWS_API_KEY"

#: 시장별 검색 언어. 없는 시장은 조회하지 않는다.
LANGUAGES = {Market.KR: "ko", Market.US: "en"}

#: 종목당 받아올 기사 수. 늘려도 필터가 보는 것은 제목뿐이라 이득이 적고,
#: 한도만 빨리 닳는다.
PAGE_SIZE = 20


class NewsUnavailable(CollectorError):
    """newsapi 가 응답을 주지 않았다. 한도 초과가 가장 흔한 원인이다."""


def _published_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class NewsSource:
    """newsapi.org 조회. 종목 하나에 요청 하나."""

    api_key: str
    name: str = SOURCE
    timeout: float = 20.0
    client: httpx.Client | None = None
    page_size: int = PAGE_SIZE

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> NewsSource:
        source = env if env is not None else dict(os.environ)
        return cls(api_key=(source.get(KEY_ENV) or "").strip())

    def usable(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, *, market: Market) -> list[dict[str, Any]]:
        """``query`` 로 최근 기사. 실패는 예외로 낸다 — 조용히 비우지 않는다."""
        if not self.usable():
            raise MissingCredentials(f"{KEY_ENV} 미설정")

        language = LANGUAGES.get(market)
        if language is None:
            raise NewsUnavailable(f"{market} 검색 언어가 정의되지 않았다")

        owned = self.client is None
        http = self.client or httpx.Client(timeout=self.timeout)
        try:
            response = http.get(
                NEWSAPI_URL,
                params={
                    "q": query,
                    "language": language,
                    "pageSize": self.page_size,
                    "sortBy": "publishedAt",
                },
                headers={"X-Api-Key": self.api_key},
            )
        finally:
            if owned:
                http.close()

        if response.status_code != 200:
            raise NewsUnavailable(
                f"newsapi {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        if payload.get("status") != "ok":
            raise NewsUnavailable(f"newsapi {payload.get('code')}: {payload.get('message')}")
        return list(payload.get("articles") or [])


def article_rows(
    articles: list[dict[str, Any]],
    *,
    entity_id: str,
    observed_at: datetime,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """기사 → documents 행.

    ``doc_id`` 는 URL 이다. 자연키가 (entity_id, valid_from, doc_id) 라서,
    같은 기사를 두 번 받아도 같은 행으로 접힌다 — 제목만 바뀐 재게시는
    revision 으로 쌓인다. 제목을 키에 넣으면 언론사가 제목을 고칠 때마다
    새 기사로 세게 된다.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, datetime]] = set()

    for article in articles:
        url = str(article.get("url") or "").strip()
        published = _published_at(article.get("publishedAt"))
        # URL 도 발행시각도 없으면 같은 기사인지 판별할 방법이 없다. 버린다.
        if not url or published is None:
            continue
        if (url, published) in seen:
            continue
        seen.add((url, published))

        rows.append(
            {
                "entity_id": entity_id,
                # 기사가 주장하는 발행시각. 관측시각으로는 쓰지 않는다.
                "valid_from": published,
                "observed_at": observed_at,
                "source": source,
                "doc_id": url,
                "doc_type": DOC_TYPE,
                "title": str(article.get("title") or ""),
                "filer": str((article.get("source") or {}).get("name") or ""),
                "url": url,
                "raw_path": "",
            }
        )
    return rows


def news_run_id(market: Market, moment: datetime) -> str:
    """수집 회차 id. 후보 목록이 매번 달라지므로 시각으로 건다."""
    return f"news-{market}-{moment:%Y%m%dT%H%M%S}"


@dataclass
class NewsCollector:
    """후보 종목의 뉴스를 모아 ``documents`` 에 넣는다.

    라이브 경로 전용이다. 백필하지 않는 이유는 모듈 docstring 에 있다.
    """

    store: Any
    source: NewsSource
    clock: Clock
    archive: Any
    market: Market = Market.KR
    #: 조회 실패한 종목. 한도 초과와 이름 없음을 구분해 보관한다.
    failures: dict[str, str] = field(default_factory=dict)

    def collect(self, entities: dict[str, str]) -> int:
        """``{entity_id: 검색어}`` 만큼 조회해 적재. 적재 행수를 돌려준다.

        검색어는 종목명이다 (``universe.name``). 티커로 찾으면 국장에서는
        거의 안 걸리고, 미장에서는 동음이의어가 쏟아진다.
        """
        if not entities:
            return 0

        observed_at = self.clock.now()
        run_id = news_run_id(self.market, observed_at)
        if self.store.ingest_run_recorded(DOCUMENTS, run_id):
            return 0

        rows: list[dict[str, Any]] = []
        payloads: dict[str, Any] = {}
        self.failures = {}

        for entity_id, query in entities.items():
            if not query.strip():
                self.failures[entity_id] = "검색어 없음"
                continue
            try:
                articles = self.source.search(query, market=self.market)
            except CollectorError as error:
                # 한 종목 실패로 회차 전체를 버리지 않는다. 한도 초과면
                # 나머지도 줄줄이 실패하는데, 그때까지 받은 것은 유효하다.
                self.failures[entity_id] = str(error)
                continue
            payloads[entity_id] = articles
            rows.extend(article_rows(articles, entity_id=entity_id, observed_at=observed_at))

        if not rows:
            # 빈 것을 완료로 기록하면 같은 회차 id 로 다시 시도할 수 없다.
            return 0

        self.archive.save(
            self.source.name,
            payloads,
            observed_at=observed_at,
            ingest_run_id=run_id,
            label=f"news-{self.market}-{observed_at:%Y%m%d}",
        )
        return int(self.store.append(DOCUMENTS, rows, ingest_run_id=run_id))
