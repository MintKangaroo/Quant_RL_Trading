"""10/13 대격변 보고표 — 두 판정 결과 파일을 읽어 후보별 × 기준별 O/X 표와 '10/13 쓰임 규칙 적용 결과' 를 만든다(사용자 10/10 지시).

    .venv/bin/python tools/switch_report.py                         # logs/vault-early-judge-result.txt + logs/past-vault-judge-result.txt
    .venv/bin/python tools/switch_report.py --early A --past B      # 다른 파일

판정을 다시 하지 않는다 — 판정 도구가 찍은 ○/× 와 판정 줄만 옮긴다. 쓰임 규칙은 등록 문서 그대로:
- `vault-early-additions-2026-10.md`: TF·TB·TC 둘 이상 통과면 원리 t 가 가장 강한 하나.
- `past-vault-2026-10.md`: TF·TB = early 통과 **또는** (과거 통과 + early 원리 평균 < 0 + early 포트 ≥ 대조 − 1%p) · TG = 과거 통과 · 하한 자리 하나(과거 원리 t 최강).
- `vault-early-usage-2026-10.md`: BE2 채택 → 10/15 모의계좌.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EARLY = Path("logs/vault-early-judge-result.txt")
PAST = Path("logs/past-vault-judge-result.txt")
MARK = re.compile(r"([①②③④⑤⑥])(.*?)(○|×)")
NUMS = "①②③④⑤⑥"
FLOORS = ("TF", "TB", "TC", "TG")


@dataclass
class Trial:
    name: str
    rows: dict[str, dict[str, bool]] = field(default_factory=dict)     # 행 이름(시장·변형) → {①: True}
    verdict: str = ""
    lines: list[str] = field(default_factory=list)


def criteria(line: str) -> tuple[str, dict[str, bool]]:
    """한 줄의 ①…○/× 들과 행 이름(첫 ① 앞이 'X:' 면 X)."""
    head = line.split("①", 1)[0].strip() if "①" in line else ""
    label = head[:-1].strip() if head.endswith(":") else ""
    return label, {m.group(1): m.group(3) == "○" for m in MARK.finditer(line)}


def parse_early(text: str) -> dict[str, Trial]:
    out: dict[str, Trial] = {}
    cur: Trial | None = None
    for line in text.splitlines():
        m = re.match(r"=== 시행 (\S+) —", line)
        if m:
            cur = out.setdefault(m.group(1), Trial(m.group(1)))
            continue
        if cur is None:
            continue
        if line.startswith("판정: "):
            cur.verdict = line[len("판정: "):].strip()
            cur = None
            continue
        if any(n in line for n in NUMS) and MARK.search(line):
            label, marks = criteria(line)
            cur.rows.setdefault(label or cur.name, {}).update(marks)
            cur.lines.append(line)
    return out


def parse_past(text: str) -> dict[str, Trial]:
    out: dict[str, Trial] = {}
    cur: Trial | None = None
    for line in text.splitlines():
        m = re.match(r"(TF|TB|TG): ①", line)
        if m:
            cur = out.setdefault(m.group(1), Trial(m.group(1)))
        v = re.match(r"\s+→ (TF|TB|TG): (.+)", line)
        if v:
            out.setdefault(v.group(1), Trial(v.group(1))).verdict = v.group(2).strip()
            cur = None
            continue
        if cur is not None and MARK.search(line):
            cur.rows.setdefault(cur.name, {}).update(criteria(line)[1])
            cur.lines.append(line)
    return out


def mech_t(trial: Trial | None) -> float | None:
    if trial is None:
        return None
    for line in trial.lines:
        m = re.search(r"NW t ([+−-]?[0-9.]+)", line.replace("−", "-"))
        if m and "①" in line:
            return float(m.group(1))
    return None


def early_not_reversed(trial: Trial | None) -> bool:
    """early 에서 뒤집히지 않음: 원리 평균 < 0 그리고 포트 연(시드 평균) ≥ 대조 − 1%p."""
    if trial is None:
        return False
    text = " ".join(trial.lines).replace("−", "-")
    m1 = re.search(r"①뺀 종목 5세션 초과 ([+-]?[0-9.]+)%", text)
    m2 = re.search(r"대조 [+-]?[0-9.]+%\(([+-]?[0-9.]+)%p\)", text)
    return bool(m1 and m2 and float(m1.group(1)) < 0 and float(m2.group(1)) >= -1.0)


def passed(trial: Trial | None) -> bool:
    return trial is not None and trial.verdict.startswith(("채택", "과거 금고 통과", "확인 —"))


def table(trials: dict[str, Trial], order: list[str]) -> list[str]:
    nums = sorted({n for t in trials.values() for r in t.rows.values() for n in r}, key=NUMS.index)
    out = ["| 후보 | " + " | ".join(nums) + " | 판정 |", "|---|" + "---|" * (len(nums) + 1)]
    for name in [*order, *[k for k in trials if k not in order]]:
        t = trials.get(name)
        if t is None:
            out.append(f"| {name} | " + " | ".join("" for _ in nums) + " | (결과 없음) |")
            continue
        for label, marks in (t.rows.items() or [(name, {})]):
            shown = name if label == name else f"{name} · {label}"
            cells = ["O" if marks.get(n) else ("X" if n in marks else "—") for n in nums]
            out.append(f"| {shown} | " + " | ".join(cells) + f" | {t.verdict or '—'} |")
    return out


def decide(early: dict[str, Trial], past: dict[str, Trial]) -> list[str]:
    lines, floor = [], []
    if passed(early.get("BE2")):
        lines.append("- **BE2** 채택 → 10/15 모의계좌(점수 모델 교체). 11/23 확인에서 뒤집히면 뺀다(`vault-early-usage`).")
    elif early.get("BE2") and early["BE2"].verdict.startswith("정보 효과"):
        lines.append("- BE2 '정보 효과'(①~⑤만) — 모델 주장 기각. C1 을 후보로 올릴지는 사용자 결정.")
    for k in ("AQ", "P1B2"):
        if passed(early.get(k)):
            lines.append(f"- **{k}** 채택 후보 — `apply_switch` 구성 요소 {'AQ' if k == 'AQ' else 'P1B2 / P1B2-B2(변형 이름 보고 고름)'}.")
    if passed(early.get("AQ")) and passed(early.get("P1B2")):
        lines.append("  - AQ·P1B2 는 같은 키를 다르게 바꾼다 — 하나만(사용자 결정).")
    for k in ("TF", "TB"):
        e, p = early.get(k), past.get(k)
        if passed(e):
            floor.append((k, mech_t(p) if passed(p) else mech_t(e), "early 통과"))
        elif passed(p) and early_not_reversed(e):
            floor.append((k, mech_t(p), "과거 통과 + early 안 뒤집힘"))
    if passed(early.get("TC")):
        floor.append(("TC", mech_t(early.get("TC")), "early 통과(과거 금고 없음)"))
    if passed(past.get("TG")):
        floor.append(("TG", mech_t(past.get("TG")), "과거 통과"))
    if floor:
        lines.append("- 하한 후보: " + " · ".join(f"{k}({why}, 원리 t {t:+.2f})" if t is not None else f"{k}({why})" for k, t, why in floor))
        with_t = [f for f in floor if f[1] is not None]
        best = min(with_t, key=lambda f: f[1]) if with_t else floor[0]
        mixed = len({why.startswith("early 통과(과거") for _, _, why in floor}) > 1
        lines.append(f"  - 하한 자리는 하나 → **{best[0]}**(원리 t 가 가장 강함)"
                     + (" — 단 TC 는 과거 금고 t 가 없어 t 를 직접 견줄 수 없다: 사용자 확인" if mixed else ""))
    else:
        lines.append("- 하한 후보 없음 — TTM·TA 하한은 넣지 않는다(IX-T·TG 장부는 계속).")
    for k in ("AR", "AS"):
        if passed(early.get(k)):
            lines.append(f"- {k} 채택 후보 — 랭커 재학습 배선이 따로 필요(바로 적용 불가).")
    if early.get("BD"):
        lines.append(f"- BD(미장): {early['BD'].verdict} — 국장 모의계좌와 무관.")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--early", type=Path, default=EARLY)
    ap.add_argument("--past", type=Path, default=PAST)
    args = ap.parse_args(argv)
    early = parse_early(args.early.read_text()) if args.early.exists() else {}
    past = parse_past(args.past.read_text()) if args.past.exists() else {}
    if not early and not past:
        print("판정 결과 파일이 없다", file=sys.stderr)
        return 2
    print("### 표 1 · 62일 금고(7/1~9/30)\n")
    print("\n".join(table(early, ["BE2", "AQ", "P1B2", "TF", "TB", "TC", "AR", "AS", "BD"])) if early else "(결과 없음)")
    print("\n### 표 2 · 과거 금고(2011~2021-07) — ①원리 ②넓은 포트 ③대형주\n")
    print("\n".join(table(past, ["TF", "TB", "TG"])) if past else "(결과 없음)")
    print("\n### 10/13 쓰임 규칙 적용 결과(등록 문서 그대로 — 실제 반영은 사용자 설문)\n")
    print("\n".join(decide(early, past)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
