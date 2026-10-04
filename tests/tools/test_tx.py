"""시행 TX 준비 코드(tools/tx_embed.py · tools/tx_features.py)의 경계. 합성 자료만 — 모델·네트워크·수익을 안 탄다.

1. 정제·가림: 머리말·회사명·종목코드는 지우고 본문(날짜 포함)은 남긴다.
2. 조각: 512 토큰(특수 토큰 포함) × 최대 4, 토큰 수 가중 평균 + L2 정규화.
3. 시간 관문: DART 백필(00:40~07:35)·평일 장 중(08:50~15:40)엔 안 돈다. 도는 중에 닫히면 저장하고 멈춘다.
4. 이어받기: 저장한 문서는 다시 안 한다. 정기보고서·수집 대상 밖 유형은 벡터를 안 만든다.
5. **누수 차단**: 얼리는 적합은 컷오프(2023-01-31) 뒤 문서의 벡터·라벨에 한 자리도 반응하지 않는다. 늦은 컷오프는 거절.
6. 관측 규칙: 접수일 d 의 문서는 d 보다 뒤 첫 세션부터 보인다.
7. Lazy Prices: 전년 같은 종류 보고서 대비 1 − cos, 다음 보고서까지 유지, 400일 만료. 목차가 아니라 본문 절을 읽는다.
8. 등록이 초안이면 적합 단계는 rc 4.
"""

from __future__ import annotations

import gzip
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_rl_trading.collectors import dart_documents as docs
from quant_rl_trading.replay.clock import ReplayClock
from quant_rl_trading.store import Store
from tools import tx_embed as emb
from tools import tx_features as feat

KST_SAT_NOON = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)      # 토 12:00 KST


# -- 정제·가림 ------------------------------------------------------------------------

HEADER = """주요사항보고서(유상증자결정)
금융위원회 / 한국거래소 귀중
2024 년 05 월 23 일
회 사 명 :
주식회사 테스트전자
대 표 이 사 :
홍길동
본 점 소 재 지 :
서울시 어딘가
(전 화) 02-000-0000
(홈페이지)http://example.com
작 성 책 임 자 :
(직 책) 이사
(성 명) 김아무
유상증자 결정
1. 신주의 종류와 수
테스트전자(주) 보통주 (종목코드 123456)
4. 신청일자
2024년 05월 23일
"""


def test_머리말과_회사명_종목코드를_지우고_본문_날짜는_남긴다() -> None:
    out = emb.clean(HEADER, filer="테스트전자", entity_id="KR:123456")
    for gone in ("귀중", "홍길동", "서울시", "02-000-0000", "example.com", "김아무", "테스트전자", "123456"):
        assert gone not in out
    assert "[회사]" in out and "[코드]" in out
    assert "2024년 05월 23일" in out          # 본문의 날짜는 남는다(제출일 줄만 지운다)
    assert "2024 년 05 월 23 일" not in out
    assert "유상증자 결정" in out


def test_한_글자_회사명은_가리지_않는다() -> None:
    assert emb.name_variants("가") == []
    assert emb.name_variants("삼성중공업(주)") == ["삼성중공업(주)", "삼성중공업"]


class FakeTokenizer:
    cls_token_id, sep_token_id, pad_token_id = 0, 2, 1

    def __call__(self, text: str, **_: object) -> dict:
        return {"input_ids": [10 + (ord(c) % 50) for c in text]}


def test_조각은_512토큰_최대_4개() -> None:
    enc = emb.Encoder(tokenizer=FakeTokenizer())
    pieces = enc.chunks("가" * 5000)
    assert len(pieces) == emb.MAX_CHUNKS
    assert all(len(p) <= emb.CHUNK_TOKENS and p[0] == 0 and p[-1] == 2 for p in pieces)
    assert len(enc.chunks("짧다")) == 1


def test_문서_벡터는_토큰수_가중_평균에_정규화() -> None:
    chunk = np.array([[1.0, 0.0], [0.0, 1.0], [3.0, 4.0]])
    out = emb.pool_documents(chunk, owners=[0, 0, 1], weights=[3, 1, 10], n_docs=2)
    expect = np.array([0.75, 0.25]) / np.linalg.norm([0.75, 0.25])
    np.testing.assert_allclose(out[0], expect, rtol=1e-6)
    np.testing.assert_allclose(out[1], [0.6, 0.8], rtol=1e-6)


@pytest.mark.parametrize(("kst", "closed"), [
    (datetime(2026, 10, 10, 1, 0), True),     # 토 01:00 — DART 백필
    (datetime(2026, 10, 12, 10, 0), True),    # 월 10:00 — 장 중
    (datetime(2026, 10, 10, 10, 0), False),   # 토 10:00
    (datetime(2026, 10, 12, 20, 0), False),   # 월 20:00
    (datetime(2026, 10, 12, 7, 40), False),   # 월 07:40
])
def test_시간_관문(kst: datetime, closed: bool) -> None:
    moment = kst.replace(tzinfo=emb.KST)
    assert bool(emb.gate_closed(moment)) is closed


# -- 임베딩 회차 ----------------------------------------------------------------------


def write_text(root: Path, doc_id: str, body: str) -> str:
    path = root / f"{doc_id}.txt.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as h:
        h.write(body)
    return str(path)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(root=tmp_path / "warehouse")
    day = datetime(2025, 6, 2, tzinfo=UTC)

    def row(doc_id: str, title: str, doc_type: str, body: str | None) -> dict:
        return {"entity_id": "KR:123456", "valid_from": day, "observed_at": day + timedelta(hours=9), "source": "dart",
                "revision": 0, "doc_id": doc_id, "doc_type": doc_type, "title": title, "filer": "테스트전자", "url": "u",
                "raw_path": write_text(tmp_path / "texts", doc_id, body) if body is not None else None}

    store.append(docs.DOCUMENTS, [
        row("A1", "유상증자결정", "dilution", "테스트전자 유상증자 본문"),
        row("A2", "단일판매ㆍ공급계약체결", "contract", "계약 본문 " * 50),
        row("A3", "분기보고서 (2025.03)", "earnings", "정기보고서"),     # 정기보고서 — 벡터 안 만든다
        row("A4", "기타경영사항", "other", "기타"),                     # 수집 대상 밖
        row("A5", "주권매매거래정지", "distress", None),                # 원문 없음
    ], ingest_run_id="seed")
    return store


class FakeEncoder:
    def __init__(self) -> None:
        self.texts: list[int] = []

    def chunks(self, text: str) -> list[list[int]]:
        self.texts.append(len(text))
        return [[0, len(text) % 7 + 3, 2]]

    def encode_chunks(self, pieces):  # type: ignore[no-untyped-def]
        return np.array([[float(p[1]), 1.0, 0.0] for p in pieces])


def make_run(store: Store, tmp_path: Path, *, gate=lambda _m: "", busy=lambda: False):  # type: ignore[no-untyped-def]
    lines: list[str] = []
    run = emb.EmbedRun(store=store, clock=ReplayClock(KST_SAT_NOON), encoder=FakeEncoder(), root=tmp_path / "vec",
                       gate=gate, busy=busy, log=lines.append)
    return run, lines


def test_수시공시만_벡터로_만들고_이어받는다(store: Store, tmp_path: Path) -> None:
    run, _ = make_run(store, tmp_path)
    assert run.run([pd.Period("2025-06", freq="M")]) == 0
    assert run.done == 2
    meta, X = feat.load_vectors(tmp_path / "vec")
    assert sorted(meta["doc_id"]) == ["A1", "A2"]
    assert X.shape == (2, 3) and X.dtype == np.float32
    again, _ = make_run(store, tmp_path)
    assert again.run([pd.Period("2025-06", freq="M")]) == 0
    assert again.done == 0


def test_관문이_닫혀_있으면_아무것도_안_한다(store: Store, tmp_path: Path) -> None:
    run, lines = make_run(store, tmp_path, gate=lambda _m: "장 중")
    assert run.run([pd.Period("2025-06", freq="M")]) == 0
    assert run.done == 0 and any("시간 관문" in x for x in lines)


def test_DART_백필이_돌면_기다리지_않고_멈춘다(store: Store, tmp_path: Path) -> None:
    run, lines = make_run(store, tmp_path, busy=lambda: True)
    assert run.run([pd.Period("2025-06", freq="M")]) == 0
    assert run.done == 0 and any("DART 원문 백필" in x for x in lines)


def test_원문을_하나도_못_읽으면_rc1(store: Store, tmp_path: Path) -> None:
    run, _ = make_run(store, tmp_path)

    def broken(_p: Path) -> str:
        raise OSError("없다")

    run.read_text = broken
    assert run.run([pd.Period("2025-06", freq="M")]) == 1


# -- 세션·누수 -------------------------------------------------------------------------

SESSIONS = np.array(pd.bdate_range("2022-01-03", "2023-06-30").date, dtype="datetime64[D]")


def test_접수일_다음_날_08시_관측_뒤_첫_세션() -> None:
    fri, mon, tue = date(2022, 1, 7), date(2022, 1, 10), date(2022, 1, 11)
    idx = feat.visible_index([fri, mon], SESSIONS)
    assert pd.Timestamp(SESSIONS[idx[0]]).date() == mon      # 금요일 공시 → 월요일
    assert pd.Timestamp(SESSIONS[idx[1]]).date() == tue      # 세션 날 공시 → 그다음 세션(같은 날 개장은 못 본다)


def synthetic(n: int = 600, d: int = 8, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray, pd.Series]:
    rng = np.random.default_rng(seed)
    filed = rng.choice(pd.to_datetime(SESSIONS[:-5]).date, n)
    meta = pd.DataFrame({"doc_id": [f"D{i}" for i in range(n)], "entity_id": [f"KR:{i % 40:06d}" for i in range(n)],
                         "valid_from": filed, "doc_type": rng.choice(["dilution", "contract", "buyback"], n),
                         "title": rng.choice(["유상증자결정", "단일판매ㆍ공급계약체결", "자기주식취득결정"], n)})
    meta = feat.attach_sessions(meta, SESSIONS).reset_index(drop=True)
    X = rng.normal(size=(len(meta), d)).astype(np.float32)
    y = pd.Series(X[:, 0] * 0.5 + rng.normal(size=len(meta)))
    return meta, X, y


def test_얼린_적합은_컷오프_뒤_벡터와_라벨에_반응하지_않는다() -> None:
    meta, X, y = synthetic()
    frozen, oof = feat.fit_frozen(meta, X, y)
    after = (pd.to_datetime(meta["visible"]).dt.date > feat.CUTOFF).to_numpy()
    assert after.sum() > 50 and (~after).sum() > 50
    X2, y2 = X.copy(), y.copy()
    X2[after] = 1e6
    y2[after] = -1e6
    frozen2, oof2 = feat.fit_frozen(meta, X2, y2)
    assert frozen.digest() == frozen2.digest()
    pd.testing.assert_series_equal(oof, oof2)
    assert frozen.last_fit_visible <= feat.CUTOFF
    assert set(oof.index) <= set(meta.loc[~after, "doc_id"])


def test_등록_컷오프보다_늦은_컷오프는_거절() -> None:
    meta, X, y = synthetic()
    with pytest.raises(feat.CutoffViolation):
        feat.fit_frozen(meta, X, y, cutoff=feat.CUTOFF + timedelta(days=1))


def test_적합_구간_문서는_OOF_컷오프_뒤는_얼린_머리(tmp_path: Path) -> None:
    meta, X, y = synthetic()
    frozen, oof = feat.fit_frozen(meta, X, y)
    scores = feat.doc_scores(meta, X, frozen, oof)
    fitted = oof.index[0]
    assert scores[fitted] == pytest.approx(oof[fitted])
    late = meta.loc[pd.to_datetime(meta["visible"]).dt.date > feat.CUTOFF].index[0]
    np.testing.assert_allclose(scores[meta.loc[late, "doc_id"]], frozen.score(meta.loc[[late]], X[[late]])[0])
    path = frozen.save(tmp_path)
    assert feat.FrozenTx.load(path).digest() == frozen.digest()


def test_얼린_파일의_마지막_관측이_컷오프_뒤면_불러오지_않는다(tmp_path: Path) -> None:
    meta, X, y = synthetic()
    frozen, _ = feat.fit_frozen(meta, X, y)
    frozen.last_fit_visible = feat.CUTOFF + timedelta(days=3)
    with pytest.raises(feat.CutoffViolation):
        feat.FrozenTx.load(frozen.save(tmp_path))


def test_세션_피처는_보인_세션부터_20세션_평균과_60세션_최솟값() -> None:
    sessions = SESSIONS[:100]
    meta = pd.DataFrame({"doc_id": ["a", "b"], "entity_id": ["KR:1", "KR:1"], "sidx": [10, 15]})
    out = feat.aggregate(meta, pd.Series({"a": 1.0, "b": -3.0}), sessions).set_index("session")
    day = lambda i: pd.Timestamp(sessions[i]).date()  # noqa: E731
    assert day(9) not in out.index                             # 보이기 전엔 없다
    assert out.loc[day(10), "tx_tone20"] == 1.0
    assert out.loc[day(15), "tx_tone20"] == -1.0               # (1 − 3) / 2
    assert out.loc[day(29), "tx_tone20"] == -1.0
    assert out.loc[day(30), "tx_tone20"] == -3.0               # a 는 20세션 창을 벗어났다
    assert np.isnan(out.loc[day(35), "tx_tone20"])             # 둘 다 벗어났다
    assert out.loc[day(35), "tx_worst60"] == -3.0              # 최솟값은 60세션
    assert out.loc[day(74), "tx_worst60"] == -3.0              # b(15) 는 74 까지 창 안
    assert day(75) not in out.index                            # 60세션도 지났다


# -- Lazy Prices -----------------------------------------------------------------------

REPORT = """목 차
II. 사업의 내용
III. 재무에 관한 사항
I. 회사의 개요
회사 소개
II. 사업의 내용
{body}
III. 재무에 관한 사항
재무제표 1,234
IV. 이사의 경영진단 및 분석의견
{mdna}
V. 회계감사인의 감사의견 등
"""


def test_목차가_아니라_본문_절을_읽는다() -> None:
    text = REPORT.format(body="반도체 메모리 사업 설명 " * 5, mdna="영업 환경 분석")
    out = feat.sections(text)
    assert "반도체" in out and "영업 환경" in out and "재무제표" not in out and "회사 소개" not in out


def test_Lazy_Prices_전년_대비_변화_유지_만료() -> None:
    sessions = np.array(pd.bdate_range("2022-01-03", "2024-12-31").date, dtype="datetime64[D]")
    texts = {
        "p21": REPORT.format(body="반도체 메모리 수요 증가", mdna="이익 증가"),
        "p22": REPORT.format(body="반도체 메모리 수요 증가", mdna="이익 증가"),           # 그대로 → 0
        "p23": REPORT.format(body="소송 손상 차입 부실 위험 확대", mdna="손실 지속"),     # 바뀜 → > 0
    }
    periodic = pd.DataFrame({
        "entity_id": "KR:1", "doc_id": ["p21", "p22", "p23"],
        "valid_from": [date(2022, 3, 21), date(2023, 3, 20), date(2024, 3, 18)],
        "title": ["사업보고서 (2021.12)", "사업보고서 (2022.12)", "[기재정정]사업보고서 (2023.12)"],
        "raw_path": ["p21", "p22", "p23"]})
    out = feat.lazy_prices(periodic, sessions, lambda p: texts[str(p)], log=lambda _s: None).set_index("session")
    assert date(2022, 3, 22) not in out.index                   # 첫해는 비교 대상이 없다
    assert out.loc[date(2023, 3, 21), "tx_change"] == pytest.approx(0.0, abs=1e-6)
    assert date(2023, 3, 20) not in out.index                   # 접수 당일은 못 본다
    assert out.loc[date(2024, 3, 19), "tx_change"] > 0.3
    assert out.loc[date(2024, 3, 15), "tx_change"] == pytest.approx(0.0, abs=1e-6)  # 다음 보고서 전까지 유지
    assert out.index.max() <= date(2024, 3, 18) + timedelta(days=feat.CHANGE_MAX_DAYS)


def test_등록이_초안이면_적합은_rc4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = tmp_path / "p.md"
    draft.write_text("> **초안 2026-10-02** — 승인 전\n")
    monkeypatch.setattr(feat, "PROTOCOL", draft)
    assert feat.main(["--root", str(tmp_path / "data"), "--stage", "fit"]) == 4
    draft.write_text("> **고정 2026-10-11** — 승인\n")
    assert feat.registration_fixed(draft)
