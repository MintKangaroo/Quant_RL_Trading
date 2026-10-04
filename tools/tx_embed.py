#!/usr/bin/env python
"""시행 TX — 수시공시 원문 → 문서 벡터(연구 캐시). 등록 초안: docs/protocols/tx-filing-text-2026-10.md.

    .venv/bin/python tools/tx_embed.py --start 2021-08 --end 2026-09          # 달마다, 이어받기
    .venv/bin/python tools/tx_embed.py --status                               # 달별 남은 건수만
    .venv/bin/python tools/tx_embed.py --smoke 40                             # 실제 원문 40건 속도만(저장 안 함)

**계산이지 새 사실이 아니다.** 인코더(`jhgan/ko-sroberta-multitask`, 커밋 고정)는 학습하지 않고, 수익·라벨은
여기서 한 칸도 읽지 않는다. 벡터는 창고가 아니라 `data/_diag/tx/vectors/<연월>/part-*.npz` 에 둔다 —
관측 시각 규칙(접수 다음 날 08:00 KST)은 피처 빌더(`tools/tx_features.py`)가 세션으로 옮길 때 건다.

입력 처리(등록 초안 '공통 — 수시공시 문서 벡터' 그대로):
1. 머리말 줄(귀중·제출일·회사명·대표이사·본점·전화·홈페이지·작성책임자·정정 안내 문구) 제거.
2. 회사명(`documents.filer` 와 그 변형)·6자리 종목코드를 `[회사]`·`[코드]` 로 가린다 — 이름에 붙은 사후 지식 통로를 막는다.
3. 512 토큰 조각(겹침 없음, 특수 토큰 포함) 앞에서 최대 4개 → 조각마다 평균 풀링 → 토큰 수 가중 평균 → L2 정규화.

시간 관문: DART 원문 백필(00:45~07:30)과 국장 장 중(평일 08:50~15:40)에는 돌지 않는다 — 시작 때 닫혀 있으면
아무것도 안 하고, 도는 중에 닫히면 받은 만큼 저장하고 멈춘다(rc 0, 다음 회차가 잇는다).
rc: 0 = 했거나(관문에서 멈춤 포함) 할 일 없음 · 1 = 할 일이 있었는데 한 건도 못 했다 · 2 = 모델이 캐시에 없다.
`--status` 는 남은 것이 없으면 0, 있으면 5(러너가 다음 단계로 갈지 가른다).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as time_module
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.replay.clock import Clock, LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.collect_filing_texts import DEFAULT_TITLES, DEFAULT_TYPES  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
#: 등록 초안 권고 인코더 A. 말뭉치(KLUE·KorNLI/KorSTS)가 2020 이전이라 판정 창을 볼 수 없었다.
#: 모델 카드에 라이선스 표기가 없다 — 기반(KLUE-RoBERTa·KorNLI/KorSTS)은 CC BY-SA 4.0. 내부 연구용으로만 쓴다.
MODEL = "jhgan/ko-sroberta-multitask"
REVISION = "8fca7c9c98c26599be0e14b9916b11a756a26f19"
#: 정제·가림·조각 규칙의 판. 바꾸면 다른 벡터다 — 캐시 경로 판 디렉터리가 갈린다.
CLEAN_VERSION = "tx-clean-v1"
CHUNK_TOKENS = 512
MAX_CHUNKS = 4
#: 토크나이저에 넘기기 전 글자 상한. 4조각 × 510토큰 × 글자/토큰 1.86(실측) ≈ 3,800자 — 넉넉히 두 배.
MAX_CHARS = 8000
BATCH_CHUNKS = 16
SAVE_EVERY = 1000
VECTOR_ROOT = Path("data/_diag/tx/vectors") / CLEAN_VERSION
FLOOR_MONTH = "2021-08"
BACKFILL_PROCESS = "tools/backfill_filing_texts.py"
#: 닫힌 시간(KST). (시작, 끝, 평일만)
CLOSED: tuple[tuple[time, time, bool], ...] = (
    (time(0, 40), time(7, 35), False),     # DART 원문 백필 00:45~07:30 — 디스크·CPU 를 나누지 않는다
    (time(8, 50), time(15, 40), True),     # 국장 장 중 — 머신을 나누지 않는다
)
COLUMNS = ["entity_id", "valid_from", "revision", "source", "doc_id", "doc_type", "title", "filer", "raw_path"]

_PERIODIC = re.compile(r"^(분기|반기|사업)보고서(\(|$)")
_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
#: 머리말 줄 — 공백을 뗀 줄의 머리에 건다. 값이 다음 줄에 오는 라벨은 다음 줄까지 지운다.
_ADDRESSEE = re.compile(r"^(?:금융위원회|한국거래소|코스닥시장본부).*귀중")
#: 수신처(귀중) 바로 다음 줄의 제출일. 본문의 날짜(신청일자 등)는 지우지 않는다.
_SUBMIT_DATE = re.compile(r"^\d{4}년\d{1,2}월\d{1,2}일$")
_HEADER_LINE = re.compile(r"귀중$|^\(전화\)|^\(홈페이지\)|^\(직책\)|^\(성명\)"
                          r"|^▶정정문서|이문구는인쇄되지|^일반-정정")
_HEADER_LABEL = re.compile(r"^(?:회사명|대표이사|본점소재지|작성책임자)[:：]?")
_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_CORP_FORMS = re.compile(r"\(주\)|㈜|주식회사|\(株\)")


def is_periodic(title: Any) -> bool:
    return bool(_PERIODIC.match(re.sub(r"\s+", "", _PREFIX.sub("", str(title or "")))))


# -- 정제·가림 ------------------------------------------------------------------------


def strip_header(text: str) -> str:
    """머리말 줄을 지운다. 라벨만 있는 줄(`회 사 명 :`)은 다음 값 줄까지 지운다."""
    out: list[str] = []
    drop_value = False
    after_addressee = False
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", line)
        if not compact:
            out.append("")
            continue
        if drop_value:
            drop_value = False
            continue
        if after_addressee:
            after_addressee = False
            if _SUBMIT_DATE.match(compact):
                continue
        if _ADDRESSEE.search(compact):
            after_addressee = True
            continue
        if _HEADER_LINE.search(compact):
            continue
        label = _HEADER_LABEL.match(compact)
        if label:
            drop_value = compact == label.group(0)
            continue
        out.append(line)
    return "\n".join(out)


def name_variants(filer: str) -> list[str]:
    """`삼성중공업(주)` → [삼성중공업(주), 삼성중공업]. 두 글자 미만은 가리지 않는다(흔한 낱말을 지운다)."""
    base = str(filer or "").strip()
    bare = _CORP_FORMS.sub("", base).strip()
    found = {v for v in (base, bare) if len(v) >= 2}
    return sorted(found, key=len, reverse=True)


def mask(text: str, filer: str, code: str) -> str:
    for name in name_variants(filer):
        text = text.replace(name, "[회사]")
        spaced = re.escape(name).replace(r"\ ", r"\s*")
        if " " in name:
            text = re.sub(spaced, "[회사]", text)
    if code:
        text = text.replace(code, "[코드]")
    return _CODE.sub("[코드]", text)


def clean(text: str, *, filer: str, entity_id: str) -> str:
    code = str(entity_id).split(":")[-1]
    body = mask(strip_header(text), filer, code)
    body = re.sub(r"[ \t ]+", " ", body)
    body = re.sub(r"\n{2,}", "\n", body).strip()
    return body[:MAX_CHARS]


# -- 고르기 ---------------------------------------------------------------------------


def month_frame(store: Store, now: datetime, period: pd.Period) -> pd.DataFrame:
    """그 달 국장 DART 수시공시 중 원문이 있는 것(마지막 정정본 기준). 수집 대상 유형만."""
    start = period.start_time.date()
    end = (period + 1).start_time.date()
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=(now.date() - start).days + 2,
                      until=datetime.combine(end, time(), UTC), columns=COLUMNS, market="KR")
    if frame.empty:
        return frame
    frame = frame[(frame["valid_from"] >= pd.Timestamp(start, tz="UTC")) & (frame["valid_from"] < pd.Timestamp(end, tz="UTC"))]
    frame = frame.sort_values("revision").groupby(["entity_id", "valid_from", "doc_id"], as_index=False).tail(1)
    frame = frame[frame["source"] == docs.SOURCE]
    path = frame["raw_path"].fillna("").astype(str)
    frame = frame[(path.str.len() > 1) & (path != "None")]
    compact = frame["title"].fillna("").astype(str).str.replace(" ", "", regex=False)
    keep = frame["doc_type"].isin(DEFAULT_TYPES) | compact.str.contains(DEFAULT_TITLES, regex=True)
    frame = frame[keep & ~frame["title"].map(is_periodic)]
    return frame.sort_values(["valid_from", "doc_id"]).reset_index(drop=True)


# -- 캐시 -----------------------------------------------------------------------------


def month_dir(root: Path, period: pd.Period) -> Path:
    return root / period.strftime("%Y%m")


def done_ids(directory: Path) -> set[str]:
    out: set[str] = set()
    for part in sorted(directory.glob("part-*.npz")):
        with np.load(part, allow_pickle=False) as data:  # invariant-allow: data-access — 연구 캐시
            out.update(str(x) for x in data["doc_id"])
    return out


def save_part(directory: Path, rows: list[dict[str, Any]], vectors: list[np.ndarray], stamp: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"part-{stamp}-{rows[0]['doc_id']}.npz"
    staging = path.with_suffix(".part.npz")
    np.savez_compressed(
        staging,
        doc_id=np.array([r["doc_id"] for r in rows]),
        entity_id=np.array([r["entity_id"] for r in rows]),
        valid_from=np.array([r["valid_from"] for r in rows]),
        doc_type=np.array([r["doc_type"] for r in rows]),
        title=np.array([r["title"] for r in rows]),
        n_chunks=np.array([r["n_chunks"] for r in rows], dtype=np.int16),
        n_tokens=np.array([r["n_tokens"] for r in rows], dtype=np.int32),
        vec=np.vstack(vectors).astype(np.float16),
        model=np.array(f"{MODEL}@{REVISION}"), clean=np.array(CLEAN_VERSION),
    )
    staging.replace(path)
    return path


# -- 인코더 ---------------------------------------------------------------------------


@dataclass
class Encoder:
    """조각 → 벡터. 오프라인(캐시)에서만 연다 — 판정 중에 모델이 바뀌면 다른 시행이다."""

    threads: int = 8
    tokenizer: Any = None
    model: Any = None

    def load(self) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        import torch
        from transformers import AutoModel, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()  # type: ignore[no-untyped-call]   # "512 토큰보다 길다" 경고 — 조각을 우리가 자르므로 헛경보다
        torch.set_num_threads(self.threads)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)  # type: ignore[no-untyped-call]
        self.model = AutoModel.from_pretrained(MODEL, revision=REVISION).eval()
        got = str(getattr(self.model.config, "_commit_hash", "") or "")
        if got != REVISION:
            raise RuntimeError(f"모델 커밋이 다르다: {got} ≠ {REVISION}")

    def chunks(self, text: str) -> list[list[int]]:
        ids = self.tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]
        body = CHUNK_TOKENS - 2
        pieces = [ids[i:i + body] for i in range(0, len(ids), body)][:MAX_CHUNKS] or [[]]
        cls, sep = self.tokenizer.cls_token_id, self.tokenizer.sep_token_id
        return [[cls, *p, sep] for p in pieces]

    def encode_chunks(self, pieces: Sequence[list[int]]) -> np.ndarray:
        import torch

        out: list[np.ndarray] = []
        pad = self.tokenizer.pad_token_id
        with torch.inference_mode():
            for start in range(0, len(pieces), BATCH_CHUNKS):
                batch = pieces[start:start + BATCH_CHUNKS]
                width = max(len(p) for p in batch)
                ids = torch.full((len(batch), width), pad, dtype=torch.long)
                att = torch.zeros((len(batch), width), dtype=torch.long)
                for row, p in enumerate(batch):
                    ids[row, :len(p)] = torch.tensor(p)
                    att[row, :len(p)] = 1
                hidden = self.model(input_ids=ids, attention_mask=att).last_hidden_state
                m = att.unsqueeze(-1).float()
                out.append(((hidden * m).sum(1) / m.sum(1).clamp(min=1e-9)).numpy())
        return np.vstack(out)


def pool_documents(chunk_vecs: np.ndarray, owners: Sequence[int], weights: Sequence[int], n_docs: int) -> np.ndarray:
    """조각 벡터 → 문서 벡터: 토큰 수 가중 평균 → L2 정규화."""
    out = np.zeros((n_docs, chunk_vecs.shape[1]), dtype=np.float64)
    total = np.zeros(n_docs)
    for vec, owner, weight in zip(chunk_vecs, owners, weights, strict=True):
        out[owner] += weight * vec
        total[owner] += weight
    out /= np.maximum(total, 1e-9)[:, None]
    out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
    return out.astype(np.float32)


# -- 시간 관문 ------------------------------------------------------------------------


def gate_closed(moment: datetime) -> str:
    """닫혀 있으면 이유, 열려 있으면 ''."""
    local = moment.astimezone(KST)
    now_t = local.time()
    for start, end, weekdays_only in CLOSED:
        if weekdays_only and local.weekday() >= 5:
            continue
        if start <= now_t < end:
            return f"{start:%H:%M}~{end:%H:%M} 닫힘"
    return ""


def other_running(marker: str, own_pid: int | None = None) -> bool:
    own = own_pid if own_pid is not None else os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if marker in cmdline and "python" in cmdline:
            return True
    return False


# -- 한 회차 --------------------------------------------------------------------------


@dataclass
class EmbedRun:
    store: Store
    clock: Clock
    encoder: Any
    root: Path = VECTOR_ROOT
    read_text: Callable[[Path], str] = docs.read_text
    gate: Callable[[datetime], str] = gate_closed
    busy: Callable[[], bool] = lambda: other_running(BACKFILL_PROCESS)
    log: Callable[[str], None] = print
    done: int = 0
    failed: int = 0
    stopped: str = ""
    seconds: float = field(default=0.0)

    def _closed(self) -> str:
        reason = self.gate(self.clock.now())
        if not reason and self.busy():
            reason = "DART 원문 백필이 돈다"
        return reason

    def month(self, period: pd.Period, frame: pd.DataFrame) -> None:
        directory = month_dir(self.root, period)
        have = done_ids(directory)
        todo = frame[~frame["doc_id"].astype(str).isin(have)]
        if todo.empty:
            return
        self.log(f"{self.clock.now().astimezone(KST):%H:%M} {period} 남은 {len(todo):,} / {len(frame):,}")
        rows: list[dict[str, Any]] = []
        vecs: list[np.ndarray] = []
        records = todo.to_dict(orient="records")
        for start in range(0, len(records), BATCH_CHUNKS):
            reason = self._closed()
            if reason:
                self.stopped = reason
                break
            group = records[start:start + BATCH_CHUNKS]
            texts, kept = [], []
            for record in group:
                try:
                    raw = self.read_text(Path(str(record["raw_path"])))
                except (OSError, EOFError, ValueError) as error:
                    self.failed += 1
                    self.log(f"  {record['doc_id']} 원문을 못 읽었다 ({type(error).__name__}) — 건너뛴다")
                    continue
                texts.append(clean(raw, filer=str(record.get("filer") or ""), entity_id=str(record["entity_id"])))
                kept.append(record)
            if not kept:
                continue
            began = time_module.perf_counter()  # invariant-allow: wallclock — 속도 측정만
            pieces, owners, weights = [], [], []
            per_doc: list[tuple[int, int]] = []
            for i, text in enumerate(texts):
                cs = self.encoder.chunks(text)
                per_doc.append((len(cs), sum(len(c) for c in cs)))
                for c in cs:
                    pieces.append(c)
                    owners.append(i)
                    weights.append(len(c))
            pooled = pool_documents(self.encoder.encode_chunks(pieces), owners, weights, len(kept))
            self.seconds += time_module.perf_counter() - began  # invariant-allow: wallclock — 속도 측정만
            for record, vec, (n_chunks, n_tokens) in zip(kept, pooled, per_doc, strict=True):
                rows.append({"doc_id": str(record["doc_id"]), "entity_id": str(record["entity_id"]),
                             "valid_from": pd.Timestamp(record["valid_from"]).date().isoformat(),
                             "doc_type": str(record["doc_type"]), "title": str(record["title"]),
                             "n_chunks": n_chunks, "n_tokens": n_tokens})
                vecs.append(vec)
            self.done += len(kept)
            if len(rows) >= SAVE_EVERY:
                self._save(directory, rows, vecs)
                rows, vecs = [], []
        if rows:
            self._save(directory, rows, vecs)

    def _save(self, directory: Path, rows: list[dict[str, Any]], vecs: list[np.ndarray]) -> None:
        save_part(directory, rows, vecs, f"{self.clock.now():%Y%m%dT%H%M%S}")
        speed = self.done / self.seconds if self.seconds else float("nan")
        self.log(f"{self.clock.now().astimezone(KST):%H:%M}  저장 {len(rows):,} · 이번 회차 {self.done:,}건 · {speed:.2f}건/초")

    def run(self, periods: Iterable[pd.Period]) -> int:
        reason = self._closed()
        if reason:
            self.log(f"시간 관문 — {reason}. 아무것도 안 하고 끝낸다")
            return 0
        had_work = False
        for period in periods:
            frame = month_frame(self.store, self.clock.now(), period)
            if frame.empty:
                continue
            have = done_ids(month_dir(self.root, period))
            if len(set(frame["doc_id"].astype(str)) - have) > 0:
                had_work = True
            self.month(period, frame)
            del frame
            if self.stopped:
                self.log(f"시간 관문 — {self.stopped}. 저장하고 멈춘다(다음 회차가 잇는다)")
                break
        self.log(f"끝 · 벡터 {self.done:,}건 · 못 읽음 {self.failed} · 멈춘 이유 {self.stopped or '-'}")
        if had_work and self.done == 0 and not self.stopped:
            self.log("할 일이 있었는데 한 건도 못 했다")
            return 1
        return 0


def periods_between(start: str, end: str) -> list[pd.Period]:
    return list(pd.period_range(start=start, end=end, freq="M"))


def status(store: Store, now: datetime, periods: Iterable[pd.Period], root: Path) -> int:
    total = left = 0
    for period in periods:
        frame = month_frame(store, now, period)
        have = done_ids(month_dir(root, period))
        n = len(frame)
        r = len(set(frame["doc_id"].astype(str)) - have) if n else 0
        total += n
        left += r
        print(f"  {period} 대상 {n:,} · 남은 {r:,}")
    print(f"합계 대상 {total:,} · 남은 {left:,}")
    return 0 if left == 0 else 5


def smoke(store: Store, now: datetime, encoder: Encoder, n: int) -> int:
    """실제 원문 n건으로 속도만 잰다. 저장하지 않는다 — 수익·라벨은 안 본다."""
    frame = month_frame(store, now, pd.Period("2025-06", freq="M"))
    frame = frame.sample(min(n, len(frame)), random_state=0)
    texts = [clean(docs.read_text(Path(p)), filer=str(f), entity_id=str(e))
             for p, f, e in zip(frame["raw_path"], frame["filer"], frame["entity_id"], strict=True)]
    began = time_module.perf_counter()  # invariant-allow: wallclock — 속도 측정만
    pieces = [encoder.chunks(t) for t in texts]
    flat = [c for cs in pieces for c in cs]
    encoder.encode_chunks(flat)
    secs = time_module.perf_counter() - began  # invariant-allow: wallclock — 속도 측정만
    print(f"스모크 {len(texts)}건 · 조각 {len(flat)}(문서당 {len(flat) / max(len(texts), 1):.2f}) · {secs:.1f}초 · "
          f"{len(texts) / secs:.2f}건/초 · {len(flat) / secs:.2f}조각/초")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data")
    parser.add_argument("--start", default=FLOOR_MONTH)
    parser.add_argument("--end", default="")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--smoke", type=int, default=0)
    args = parser.parse_args(argv)

    clock = LiveClock()
    store = Store(root=Path(args.root))
    now = clock.now()
    end = args.end or f"{now.astimezone(KST):%Y-%m}"
    periods = periods_between(args.start, end)
    root = Path(args.root) / VECTOR_ROOT.relative_to("data")
    if args.status:
        return status(store, now, periods, root)
    encoder = Encoder(threads=args.threads)
    try:
        encoder.load()
    except OSError as error:
        print(f"모델이 캐시에 없다({error}) — 먼저 내려받는다", file=sys.stderr)
        return 2
    if args.smoke:
        return smoke(store, now, encoder, args.smoke)
    job = EmbedRun(store=store, clock=clock, encoder=encoder, root=root, log=lambda s: print(s, flush=True))
    rc = job.run(periods)
    (root / "LAST_RUN.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "LAST_RUN.json").write_text(json.dumps({"at": clock.now().isoformat(), "done": job.done, "failed": job.failed,
                                                   "stopped": job.stopped, "rc": rc, "model": f"{MODEL}@{REVISION}"},
                                                  ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
