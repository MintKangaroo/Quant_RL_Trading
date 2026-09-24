#!/usr/bin/env python
"""시행 X — 공시 원문 임베딩 → 주성분 3개를 창고에 적재.

    .venv/bin/python tools/build_text_features.py --stage pca      # 창 밖 표본으로 PCA 적합(한 번)
    .venv/bin/python tools/build_text_features.py --stage embed --start 2025-01 --end 2026-06

등록 문서: docs/protocols/filing-text-embedding-2026-09.md.
모델은 거기 고정돼 있다(`intfloat/multilingual-e5-small`, 512 토큰). **PCA 는 판정 창 밖 표본에서만 적합한다** —
판정 창에서 적합하면 판정 창을 본 것이다. 임베딩은 새 사실이 아니라 계산이므로 valid_from·observed_at 은
원 공시(documents)의 값을 그대로 옮긴다.

달(month)마다 실행 id 를 남기므로 다시 돌리면 안 한 달만 한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant_rl_trading.collectors import dart_documents as docs  # noqa: E402
from quant_rl_trading.replay.clock import LiveClock  # noqa: E402
from quant_rl_trading.store import Store  # noqa: E402
from tools.collect_filing_texts import DEFAULT_TYPES  # noqa: E402
from tools.select_text_model import embed  # noqa: E402

TABLE = "document_embeddings"
#: 등록 문서에서 고정한 모델(2026-09-20 선정, 해시).
MODEL = "intfloat/multilingual-e5-small"
PREFIX = "passage: "
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MAX_LENGTH = 512
COMPONENTS = 3
MODEL_DIR = Path("data/models")
SAMPLE_DIR = Path("data/_diag/text-embed")


def model_id() -> str:
    return f"{MODEL}@{REVISION[:12]}"


def fit_pca(sample_path: Path, out: Path) -> str:
    """창 밖 표본 임베딩 → 평균·주성분. numpy 만 쓴다(SVD 하나면 된다)."""
    with np.load(sample_path, allow_pickle=False) as data:  # invariant-allow: data-access — 창고가 아닌 작업 파일
        vectors = np.asarray(data["vectors"], dtype=np.float64)
    mean = vectors.mean(axis=0)
    _, singular, right = np.linalg.svd(vectors - mean, full_matrices=False)
    components = right[:COMPONENTS]
    explained = float((singular[:COMPONENTS] ** 2).sum() / (singular**2).sum())
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, mean=mean, components=components, model=MODEL, revision=REVISION,
                        sample=sample_path.name, explained=explained)
    print(f"PCA 적합: {sample_path.name} {vectors.shape} → {out} · 설명분산 {explained:.1%}")
    return out.stem


def load_pca(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:  # invariant-allow: data-access — 모델 산출물
        return np.asarray(data["mean"]), np.asarray(data["components"])


def months(start: str, end: str) -> list[pd.Period]:
    return list(pd.period_range(start=start, end=end, freq="M"))


def embed_month(store: Store, period: pd.Period, *, mean: np.ndarray, components: np.ndarray,
                pca_id: str, market: str, dry_run: bool, force: bool = False) -> int:
    now = LiveClock().now()
    span = (now.date() - period.start_time.date()).days + 45
    frame = store.get(docs.DOCUMENTS, as_of=now, lookback=span, market=market,
                      columns=["entity_id", "valid_from", "observed_at", "source", "doc_id", "doc_type", "raw_path"])
    # **수집 대상 유형만 본다.** 수집기(collect_filing_texts)가 받지 않는 유형까지 '미수집' 으로 세면
    # 그 달은 영원히 막힌다 — 원문이 영영 안 올 유형이기 때문이다.
    frame = frame[(frame["source"] == docs.SOURCE) & (frame["doc_type"].isin(DEFAULT_TYPES))]
    # 공시마다 **마지막 정정본**만 본다 — 수집기는 원문 경로·"원문 없음" 표식을 정정본으로 덧붙인다(append-only).
    frame = frame.sort_values("observed_at").drop_duplicates("doc_id", keep="last")
    within = frame["valid_from"].dt.to_period("M") == period
    path = frame["raw_path"].fillna("").astype(str)
    # **"원문 없음" 표식(docs.NO_TEXT)은 미수집이 아니다.** DART 가 파일이 없다고 답한 공시(status 014)라 영영 안 온다.
    # 길이 1 이라 미수집으로 세면 그 달은 영원히 막힌다 — 2026-09-25 에 18개월 전부가 그렇게 0건으로 끝났다.
    no_text = path == docs.NO_TEXT
    # **원문이 덜 모인 달은 건너뛴다.** 한 번 적재하면 실행 id 가 남아 다시 안 도는데,
    # 그때 빠진 공시는 영영 빠진다(그 달만 정보가 얇아져 판정이 그 달에서 조용히 약해진다).
    missing = int((within & ~no_text & ((path.str.len() <= 1) | (path == "None"))).sum())
    if missing and not force:
        print(f"{period}: 원문 미수집 {missing:,}건 — 건너뜀(완비 뒤 다시 돌려라, --force 로 강행 가능)", flush=True)
        return 0
    frame = frame[within & (path.str.len() > 1) & (path != "None")].copy()
    if frame.empty:
        print(f"{period}: 원문 0건 — 건너뜀", flush=True)
        return 0
    texts = [docs.read_text(Path(p)) for p in frame["raw_path"]]
    vectors, speed, _ = embed(MODEL, PREFIX, texts, max_length=MAX_LENGTH)
    scores = (vectors - mean) @ components.T
    rows = [
        {
            "entity_id": record["entity_id"], "valid_from": record["valid_from"],
            "observed_at": record["observed_at"], "source": "text-embed", "market": market,
            "doc_id": str(record["doc_id"]), "doc_type": str(record["doc_type"]),
            "model_id": model_id(), "pca_id": pca_id,
            "pc1": float(score[0]), "pc2": float(score[1]), "pc3": float(score[2]),
        }
        for record, score in zip(frame.to_dict(orient="records"), scores, strict=True)
    ]
    print(f"{period}: {len(rows):,}건 · 초당 {speed:.1f}건", flush=True)
    if not dry_run:
        store.append(TABLE, rows, ingest_run_id=run_id(period, market), source="text-embed")
    return len(rows)


def run_id(period: pd.Period, market: str) -> str:
    return f"text-embed-{market}-{period}-{REVISION[:8]}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("pca", "embed"), required=True)
    parser.add_argument("--root", default="data")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--start", default="2025-01", help="YYYY-MM (embed)")
    parser.add_argument("--end", default="2026-06", help="YYYY-MM (embed)")
    parser.add_argument("--sample", default="", help="창 밖 표본 npz (pca). 비우면 가장 최근 것")
    parser.add_argument("--pca", default="", help="PCA 산출물 npz (embed). 비우면 가장 최근 것")
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="원문이 덜 모인 달도 강행(권장 안 함)")
    args = parser.parse_args(argv)
    import torch

    torch.set_num_threads(args.threads)
    if args.stage == "pca":
        sample = Path(args.sample) if args.sample else sorted(SAMPLE_DIR.glob("sample-*.npz"))[-1]
        fit_pca(sample, MODEL_DIR / "text-pca.npz")
        return 0

    pca_path = Path(args.pca) if args.pca else MODEL_DIR / "text-pca.npz"
    if not pca_path.exists():
        print(f"PCA 산출물이 없다: {pca_path} — --stage pca 를 먼저 돌려라", file=sys.stderr)
        return 2
    mean, components = load_pca(pca_path)
    store = Store(root=Path(args.root))
    total = 0
    for period in months(args.start, args.end):
        if not args.dry_run and store.ingest_run_recorded(TABLE, run_id(period, args.market)):
            print(f"{period}: 이미 했다 — 건너뜀", flush=True)
            continue
        total += embed_month(store, period, mean=mean, components=components,
                             pca_id=pca_path.stem, market=args.market, dry_run=args.dry_run, force=args.force)
    print(f"끝 — {total:,}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
