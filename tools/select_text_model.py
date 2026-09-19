"""시행 X — 공시 임베딩 모델 선정. docs/protocols/filing-text-embedding-2026-09.md "후보와 비교 규칙" 그대로.

    .venv/bin/python tools/select_text_model.py [--max-length 512] [--threads 12]

**판정 창 밖(2024-01-01 전) 원문만 읽는다.** 수익·가격은 보지 않는다. 라벨은 가장 흔한 공시 제목 20종이고,
본문에서 제목 문자열을 지운 뒤 임베딩한다 — 안 지우면 두 모델 다 제목을 베껴 만점이 나온다.
결과 표와 판정만 찍는다. 문서에 적고 커밋하는 것은 사람(또는 다음 단계)이 한다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402

#: 등록된 후보 — (이름, 입력 앞머리). 순서가 표의 순서다.
CANDIDATES = (("jhgan/ko-sroberta-multitask", ""), ("intfloat/multilingual-e5-small", "passage: "))
UNTIL = pd.Timestamp("2024-01-01", tz="UTC")
TOP_TITLES = 20
BATCH = 16
MIN_DOCS_PER_SEC = 2.2
TIE = 0.02
FOLDS = 5
#: 제목 앞 괄호 표기 — [기재정정]·[첨부정정] 같은 것은 같은 보고서다.
_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", _PREFIX.sub("", str(title or "")))


def load_sample(store: Store) -> pd.DataFrame:
    frame = store.get(docs.DOCUMENTS, as_of=LiveClock().now(), lookback=1500,
                      columns=["entity_id", "valid_from", "doc_id", "doc_type", "title", "raw_path", "source"])
    frame = frame[(frame["valid_from"] < UNTIL)]
    path = frame["raw_path"].fillna("").astype(str)
    frame = frame[(path.str.len() > 1) & (path != "None")].copy()
    frame["label"] = frame["title"].map(normalize_title)
    top = frame["label"].value_counts().head(TOP_TITLES).index
    frame = frame[frame["label"].isin(top)].copy()
    texts = []
    for raw_path, title, label in zip(frame["raw_path"], frame["title"], frame["label"], strict=True):
        text = docs.read_text(Path(raw_path))
        # 제목을 지운다 — 원문 그대로, 괄호 뗀 것, 공백 없앤 것 셋 다.
        for needle in {str(title or ""), _PREFIX.sub("", str(title or "")), label}:
            if needle:
                text = text.replace(needle, " ")
        texts.append(text)
    frame["text"] = texts
    return frame.reset_index(drop=True)


def embed(name: str, prefix: str, texts: list[str], *, max_length: int) -> tuple[np.ndarray, float, str]:
    """(임베딩, 초당 건수, 모델 커밋 해시). 첫 배치는 예열이라 속도에서 뺀다."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name).eval()
    revision = str(getattr(model.config, "_commit_hash", "") or "")
    out: list[np.ndarray] = []
    timed_docs, timed_secs = 0, 0.0
    with torch.inference_mode():
        for start in range(0, len(texts), BATCH):
            batch = [prefix + t for t in texts[start:start + BATCH]]
            began = time.perf_counter()
            enc = tokenizer(batch, padding=True, truncation=True, max_length=max_length, return_tensors="pt")
            hidden = model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            out.append(pooled.numpy())
            if start > 0:
                timed_docs += len(batch); timed_secs += time.perf_counter() - began
    speed = timed_docs / timed_secs if timed_secs > 0 else float("nan")
    return np.vstack(out), speed, revision


def macro_f1(x: np.ndarray, labels: pd.Series) -> float:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    folds = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=0)
    return float(cross_val_score(model, x, labels, cv=folds, scoring="f1_macro").mean())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--save-dir", default="data/_diag/text-embed", help="고른 모델의 표본 임베딩(PCA 적합용)")
    args = parser.parse_args(argv)
    import torch

    torch.set_num_threads(args.threads)
    sample = load_sample(Store(root=Path(args.root)))
    counts = sample["label"].value_counts()
    # 교차검증이 되려면 라벨마다 FOLDS 건 이상.
    sample = sample[sample["label"].isin(counts[counts >= FOLDS].index)].reset_index(drop=True)
    print(f"창 밖 표본 {len(sample):,}건 · 라벨 {sample['label'].nunique()}종 · {sample['valid_from'].min():%Y-%m-%d}~"
          f"{sample['valid_from'].max():%Y-%m-%d} · 유형 {sample['doc_type'].value_counts().to_dict()}", flush=True)
    results = {}
    for name, prefix in CANDIDATES:
        vectors, speed, revision = embed(name, prefix, sample["text"].tolist(), max_length=args.max_length)
        f1 = macro_f1(vectors, sample["label"])
        results[name] = {"f1": f1, "speed": speed, "revision": revision, "dim": vectors.shape[1], "vectors": vectors}
        print(f"{name}: macro-F1 {f1:.4f} · 초당 {speed:.2f}건 · {vectors.shape[1]}차원 · 해시 {revision}", flush=True)
    passed = {n: r for n, r in results.items() if r["speed"] >= MIN_DOCS_PER_SEC}
    if not passed:
        print(f"판정: 두 후보 모두 초당 {MIN_DOCS_PER_SEC}건 미달 — "
              f"{'256 토큰으로 다시 잰다(등록 규칙)' if args.max_length > 256 else '시행 보류(등록 규칙)'}")
        return 3
    ranked = sorted(passed, key=lambda n: passed[n]["f1"], reverse=True)
    chosen = ranked[0]
    if len(ranked) > 1 and passed[ranked[0]]["f1"] - passed[ranked[1]]["f1"] < TIE:
        chosen = max(ranked[:2], key=lambda n: passed[n]["speed"])
        print(f"macro-F1 차이 {passed[ranked[0]]['f1'] - passed[ranked[1]]['f1']:.4f} < {TIE} — 빠른 쪽")
    print(f"판정: {chosen} (해시 {results[chosen]['revision']}, 입력 {args.max_length} 토큰)")
    save = Path(args.save_dir); save.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")  # invariant-allow: wallclock — 작업 파일 이름
    np.savez_compressed(save / f"sample-{stamp}.npz", vectors=results[chosen]["vectors"],
                        doc_id=sample["doc_id"].to_numpy(dtype=str), model=chosen, revision=results[chosen]["revision"])
    print(f"표본 임베딩 저장: {save}/sample-{stamp}.npz (PCA 적합용)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
