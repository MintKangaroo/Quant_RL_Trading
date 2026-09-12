"""공시 **원문** 수집 — `documents.raw_path` 를 채운다.

## 왜 필요한가

창고의 `documents` 는 34만 건인데 **제목과 링크만 있고 본문이 없다**
(`raw_path` 가 전 행 비어 있다, 2026-09-12 확인). 제목만으로는 "무슨 일이
있었는지" 를 분류한 `doc_type` 이상을 못 얻는다. 텍스트 임베딩을 첫 비지도
재료로 쓰려면(재료 대장 #6, 사용자 승인 2026-09-08 "준비되면") 본문이 먼저다.

DART 는 공시서류 원본을 ``/document.xml`` 로 준다. 응답은 **ZIP** 이고 그
안에 ``{접수번호}.xml`` 이 들어 있는데, 내용은 이름과 달리 HTML 이다.
헤더는 ``charset=euc-kr`` 라고 적어 놓고 실제 바이트는 UTF-8 인 문서가 있다
(2026-09-12 실측) — 그래서 **선언을 믿지 않고 순서대로 디코딩한다.**

## 본문은 창고에 넣지 않는다

한 건이 평균 7KB 다. 34만 건이면 2.4GB 이고, 이중시간 append-only 창고에
넣으면 정정본이 쌓일 때마다 그만큼 다시 쌓인다. 그래서 파일로 두고
**창고에는 경로만** 적는다 — 모델 파일(`data/models/`)·진단 조각
(`data/_diag/`)과 같은 취급이다. 경로는 저장소 루트 기준 상대경로다.

## 정정본으로 채운다

`documents` 의 자연키는 (entity_id, valid_from, doc_id) 다. 원문을 받았다는
사실은 **같은 공시에 대한 새 관측**이므로 revision 을 올린 행으로 넣는다.
원래 열(doc_type·title·filer·url)은 그대로 옮긴다 — 정정본이 옛 값을 지우면
안 된다.

## 한 번에 다 받지 않는다

DART 일 한도는 20,000 콜이다. 34만 건은 한 번에 못 받고, 받을 이유도 없다.
최근 것부터, 필요한 `doc_type` 부터 끊어 받는다(`pending`). 이어받기는 이미
`raw_path` 가 찬 행을 건너뛰는 것으로 된다 — 따로 상태를 들지 않는다.
"""

from __future__ import annotations

import gzip
import html
import io
import re
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

DOCUMENTS = "documents"
SOURCE = "dart"

#: 본문을 두는 곳. 창고가 아니다 — 경로만 창고에 적는다.
TEXT_ROOT = Path("data/_docs/dart")

#: 디코딩 순서. **선언된 charset 을 믿지 않는다** — euc-kr 이라고 적어 두고
#: UTF-8 바이트를 주는 문서가 있다(2026-09-12 실측).
ENCODINGS = ("utf-8", "cp949", "euc-kr")

#: 본문에서 걷어낼 것. 스타일·스크립트는 통째로 버린다.
_DROP = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile("[ \t\u00a0]+")
_BLANK = re.compile(r"\n{3,}")


def decode(raw: bytes) -> str:
    """바이트를 문자열로. 순서대로 시도하고 마지막은 손실을 감수한다."""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode(ENCODINGS[0], errors="replace")


def to_text(markup: str) -> str:
    """HTML 을 읽을 수 있는 평문으로.

    파서를 새로 들이지 않는다(bs4·lxml 둘 다 없다). 공시 원문은 표가 많은
    단순한 HTML 이라 태그를 걷어내고 공백을 접는 것으로 충분하다. **줄바꿈은
    살린다** — 임베딩이 문단 경계를 보기 때문이다.
    """
    text = _DROP.sub(" ", markup)
    text = re.sub(r"<(br|/tr|/p|/div|/table)[^>]*>", "\n", text, flags=re.I)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK.sub("\n\n", text).strip()


def extract(content: bytes) -> str:
    """``/document.xml`` 응답(ZIP)에서 평문을 뽑는다. 항목이 여럿이면 잇는다."""
    if content[:2] != b"PK":
        raise ValueError("DART 원문 응답이 ZIP 이 아니다")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            raise ValueError("DART 원문 ZIP 이 비었다")
        parts = [to_text(decode(archive.read(name))) for name in sorted(names)]
    return "\n\n".join(part for part in parts if part)


def text_path(doc_id: str, valid_from: datetime, *, root: Path = TEXT_ROOT) -> Path:
    """``<root>/<연>/<월>/<접수번호>.txt.gz``. 한 폴더에 34만 개를 쌓지 않는다."""
    return root / f"{valid_from:%Y}" / f"{valid_from:%m}" / f"{doc_id}.txt.gz"


def save_text(text: str, path: Path) -> int:
    """gzip 으로 적는다. 임시 파일 → replace 라 중간에 끊겨도 반쪽이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".part")
    with gzip.open(staging, "wt", encoding="utf-8") as handle:  # invariant-allow: data-access
        handle.write(text)
    staging.replace(path)
    return path.stat().st_size


def read_text(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as handle:  # invariant-allow: data-access
        return handle.read()


def pending(
    frame: pd.DataFrame, *, limit: int = 0, doc_types: Iterable[str] | None = None
) -> pd.DataFrame:
    """원문이 아직 없는 공시. **최근 것부터** 준다.

    이어받기 상태를 따로 들지 않는다 — 이미 채운 행은 여기서 빠진다.
    """
    if frame.empty:
        return frame
    latest = frame.sort_values("revision").groupby(
        ["entity_id", "valid_from", "doc_id"], as_index=False
    ).tail(1)
    missing = latest[latest["raw_path"].fillna("").astype(str).str.len() == 0]
    if doc_types is not None:
        wanted = set(doc_types)
        missing = missing[missing["doc_type"].isin(wanted)]
    missing = missing.sort_values("valid_from", ascending=False)
    return missing.head(limit) if limit else missing


def revision_row(record: dict[str, Any], *, raw_path: str, observed_at: datetime) -> dict[str, Any]:
    """원문을 받았다는 **새 관측**. 옛 열을 그대로 옮기고 raw_path 만 채운다."""
    return {
        "entity_id": str(record["entity_id"]),
        "valid_from": pd.Timestamp(record["valid_from"]).to_pydatetime(),
        "observed_at": observed_at,
        "source": SOURCE,
        "revision": int(record["revision"]) + 1,
        "doc_id": str(record["doc_id"]),
        "doc_type": str(record["doc_type"]),
        "title": str(record["title"]),
        "filer": str(record.get("filer") or ""),
        "url": str(record.get("url") or ""),
        "raw_path": raw_path,
    }


def run_id(moment: datetime, *, limit: int) -> str:
    return f"dart-docs-{moment:%Y%m%dT%H%M%S}-{limit or 'all'}"
