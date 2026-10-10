"""10/13 보고표 — 합성 판정 출력만(실제 판정 결과 아님). ○/× 옮기기 · 쓰임 규칙."""
from __future__ import annotations

from tools import switch_report as sr

EARLY = """
=== 시행 BE2 — docs/x.md (해시 a) · 금고 early 2026-07-01~2026-09-30 ===
  BE2 be2 · C0 c0 사이드카 해시·파일 지문 대조 ○ (시드 [0, 1])
①평균 +2.5%p (≥ +2%p) ○ · ②4/5 (80%) ○ · ③두 구간 +1.0%p/+0.5%p ○ · ④ΔIC +0.010 ○ · ⑤MDD ○ · ⑥모델 +1.2%p ○
판정: 채택 — ①~⑥

=== 시행 TF — docs/y.md (해시 b) · 금고 early ===
TF: ①뺀 종목 5세션 초과 -0.40%(NW t -1.50, 56세션) × · ②연 +10.0% 대 대조 +10.5%(-0.5%p) × · ③시드 2/5 × · ④두 구간 -0.5%p/+0.1%p ○ · ⑤회전 20.0 대 19.0 · MDD -10.0% 대 -10.0% ○
판정: 기각

=== 시행 TC — docs/y.md (해시 b) · 금고 early ===
TC: ①뺀 종목 5세션 초과 -0.10%(NW t -0.50, 56세션) × · ②연 +10.0% 대 대조 +10.0%(+0.0%p) ○ · ③시드 5/5 ○ · ④두 구간 ○ · ⑤회전 ○
판정: 기각

=== 시행 AR — docs/z.md ===
KR: ①평균 +0.5%p (> 0) ○ · ②시드 3/3 ○ · ③ΔIC ○
US: ①평균 -0.5%p (> 0) × · ②시드 1/3 × · ③ΔIC ○
판정: 기각

=== 요약 ===
"""
PAST = """
TF: ①원리 뺀 종목 5세션 초과 -0.60%(NW t -5.10, 2600세션) · 구간 A -0.5% / B -0.6% / C -0.7% ○
    ②넓은 포트 연 +4.0%p · IR +1.20 · 구간 A +3% / B +4% / C +5% · 회전 6.0 ○
    ③대형주 200 시총가중 연 +0.3%p · IR +0.40 · 구간 A +0% / B +0% / C +1% ○
  → TF: 과거 금고 통과(원리 t -5.10)
TG: ①원리 뺀 종목 5세션 초과 -0.50%(NW t -6.00, 2600세션) · 구간 A -0.5% / B -0.5% / C -0.5% ○
    ②넓은 포트 연 +5.0%p · IR +2.00 · 구간 A +5% / B +5% / C +5% · 회전 5.0 ○
    ③대형주 200 시총가중 연 -0.2%p · IR -0.10 · 구간 A +0% / B +0% / C +0% ×
  → TG: 기각
"""


def test_tables_copy_marks() -> None:
    e, p = sr.parse_early(EARLY), sr.parse_past(PAST)
    assert e["BE2"].rows["BE2"] == {n: True for n in "①②③④⑤⑥"} and e["BE2"].verdict.startswith("채택")
    assert e["TF"].rows["TF"]["①"] is False and e["TF"].rows["TF"]["④"] is True
    assert set(e["AR"].rows) == {"KR", "US"} and e["AR"].rows["US"]["①"] is False
    assert p["TF"].rows["TF"] == {"①": True, "②": True, "③": True} and p["TG"].rows["TG"]["③"] is False
    t = "\n".join(sr.table(e, ["BE2", "TF"]))
    assert "| BE2 | O | O | O | O | O | O |" in t and "| AR · US | X | X | O |" in t


def test_usage_rules() -> None:
    e, p = sr.parse_early(EARLY), sr.parse_past(PAST)
    assert sr.early_not_reversed(e["TF"])                       # 원리 평균 −0.40% < 0 · 포트 −0.5%p ≥ −1%p
    out = "\n".join(sr.decide(e, p))
    assert "BE2** 채택" in out
    assert "TF(과거 통과 + early 안 뒤집힘, 원리 t -5.10)" in out and "**TF**" in out
    assert "TG" not in out.split("하한 후보:")[1].split("\n")[0]   # TG 는 과거 ③ 실패
