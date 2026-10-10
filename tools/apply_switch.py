"""10/13 '대격변' 적용기 — 금고 통과 후보를 모의계좌 실전 설정으로 바꾼다(사용자 결정 10/9, 메모리 switch-2026-10-13).

    .venv/bin/python tools/apply_switch.py --components TF,BE2            # 미리보기(아무것도 안 바꾼다)
    .venv/bin/python tools/apply_switch.py --components TF,BE2 --apply    # yaml·shadow 고정·창고 정정본

하는 일(순서):
1. **실험 shadow 장부를 지금 값에 고정**한다 — `data/_*_shadow/config-overrides.yaml` 에, 바뀔 키 중 그 장부가 아직 덮어쓰지 않은 것을
   **현재 실전 값**으로 적는다(파일이 없으면 만든다 — N24). 아직 안 생긴 샌드박스의 원본 `config/shadow/*.config-overrides.yaml` 도.
   값이 같아 동작은 그대로이고, 실전 전환이 대조군(IX0·N24 등)에 새지 않는다.
   `data/_paper`·`data/_shadow`(실전의 그림자)는 실전을 따른다 — 고정하지 않는다.
2. `config/quant_rl_trading.yaml` 의 해당 키를 바꾼다(줄 단위 — 주석 보존). 리셋일 `dashboard.ir_reset_date` = 2026-10-15.
3. `tools/seed_config.py --store data --apply` 로 창고에 정정본(지금 시각)을 적는다 — 첫 반영은 그 뒤 세션(10/14 장 마감 → 10/15 주문).

장 중(평일 08:20~15:50 KST)에는 --apply 를 거부한다. 구성 요소는 금고 판정 통과분만 넣는다 — 이 도구는 판정을 확인하지 않는다(사람이 한다).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
YAML = REPO_ROOT / "config" / "quant_rl_trading.yaml"
DATA = REPO_ROOT / "data"
RESET_DATE = "2026-10-15"

#: 구성 요소 → {키: 새 값(yaml 리터럴)}. 등록 문서·shadow 설정과 같은 값.
COMPONENTS: dict[str, dict[str, str]] = {
    "BE2": {"selector.weights_override": "{be2: 1.0, risk: 1.0}"},
    "AQ": {"selector.n_candidates": "72", "selector.exit_rank": "216", "risk.max_positions": "220"},
    "P1B2": {"selector.n_candidates": "100", "selector.exit_rank": "500", "selector.rebalance_every": "20",
             "selector.swap_min_z": "1.0", "selector.rebalance_anchor": '"2026-10-14"', "risk.max_positions": "110"},
    "P1B2-B2": {"selector.rebalance_every": "20", "selector.rebalance_anchor": '"2026-10-14"'},
    "TF": {"selector.extra_floor_analyst": "tsfm", "selector.extra_floor_percentile": "0.20"},
    "TB": {"selector.extra_floor_analyst": "tsfm", "selector.extra_floor_percentile": "0.20",
           "selector.ceiling_analyst": "tsfm", "selector.ceiling_percentile": "0.10"},
    "TC": {"selector.combo_floor_analyst": "tsfm", "selector.combo_floor_percentile": "0.10"},
}
EXCLUSIVE = [{"AQ", "P1B2", "P1B2-B2"}, {"TF", "TB", "TC"}]


def changes(parts: list[str]) -> dict[str, str]:
    unknown = [p for p in parts if p not in COMPONENTS]
    if unknown:
        raise SystemExit(f"모르는 구성 요소 {unknown} — 있는 것: {sorted(COMPONENTS)}")
    for group in EXCLUSIVE:
        hit = group & set(parts)
        if len(hit) > 1:
            raise SystemExit(f"같이 넣을 수 없다: {sorted(hit)} — 하나만(같은 키를 다르게 바꾼다)")
    out: dict[str, str] = {}
    for p in parts:
        out.update(COMPONENTS[p])
    out["dashboard.ir_reset_date"] = f'"{RESET_DATE}"'
    return out


def yaml_line(key: str) -> tuple[int, re.Match[str]]:
    """``a.b`` 키의 yaml 줄 — 섹션 ``a:`` 아래 들여쓴 ``b:`` 가 정확히 한 줄이어야 한다."""
    section, name = key.split(".", 1)
    lines = YAML.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{section}:")), None)
    if start is None:
        raise SystemExit(f"yaml 에 섹션 {section}: 이 없다")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "#"))), len(lines))
    pat = re.compile(rf"^(\s+){re.escape(name)}:\s*([^#]*?)(\s+#.*)?$")
    hits = [(i, m) for i in range(start + 1, end) if (m := pat.match(lines[i]))]
    if len(hits) != 1:
        raise SystemExit(f"yaml 에서 {key} 를 한 줄로 못 찾았다({len(hits)}줄)")
    return hits[0]


def current(key: str) -> str:
    return yaml_line(key)[1].group(2).strip()


#: **실전을 따라가는** 장부 — 고정하지 않는다. TG(docs/protocols/ta-floor-2026-10.md, 사용자 10/10 "제안대로"): 전환 후 실전 규칙 + TA 하한,
#: 대조 = data/_shadow(실전 그림자). 고정하면 "옛 규칙 + TA" 를 재게 된다.
FOLLOW_LIVE = frozenset({"tg", "livekr"})   # livekr = TG 의 대조(실전 그림자, 국장 전용)


def pin_targets() -> list[Path]:
    """고정할 덮어쓰기 파일 — 실험 샌드박스 전부(파일이 없으면 만든다: N24 처럼 실전을 그대로 따르던 대조군) +
    아직 안 생긴 샌드박스가 첫 실행에 복사해 갈 `config/shadow/*.config-overrides.yaml` 원본(IX·IX0·IX-T, 10/12 첫 실행).
    FOLLOW_LIVE 장부는 뺀다."""
    boxes = [b / "config-overrides.yaml" for b in sorted(DATA.glob("_*_shadow"))
             if b.is_dir() and b.name.removeprefix("_").removesuffix("_shadow") not in FOLLOW_LIVE]
    templates = [f for f in sorted((REPO_ROOT / "config" / "shadow").glob("*.config-overrides.yaml"))
                 if f.name.removesuffix(".config-overrides.yaml") not in FOLLOW_LIVE]
    return boxes + templates


def pin_shadows(keys: list[str], *, apply: bool) -> list[str]:
    notes = []
    for f in pin_targets():
        text = f.read_text() if f.exists() else ""
        have = {ln.split(":", 1)[0].strip() for ln in text.splitlines() if ":" in ln and not ln.lstrip().startswith("#")}
        add = [k for k in keys if k not in have]
        if not add:
            continue
        block = "\n# 10/13 실전 전환 전 값 고정(tools/apply_switch.py) — 실전이 바뀌어도 이 장부는 그대로다.\n" + "".join(
            f"{k}: {current(k)}\n" for k in add)
        notes.append(f"{f.relative_to(REPO_ROOT)}{'' if f.exists() else '(새로 만듦)'}: {', '.join(add)}")
        if apply:
            f.write_text(text.rstrip("\n") + "\n" + block)
    return notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--components", required=True, help="쉼표 구분 — BE2·AQ·P1B2·P1B2-B2·TF·TB·TC (금고 통과분만)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    now = datetime.now(ZoneInfo("Asia/Seoul"))  # invariant-allow: wallclock — 장 중 거부 판단
    if args.apply and now.weekday() < 5 and 820 <= now.hour * 100 + now.minute <= 1550:
        print("장 중(평일 08:20~15:50)에는 적용하지 않는다", flush=True)
        return 2
    parts = [p.strip() for p in args.components.split(",") if p.strip()]
    want = changes(parts)
    print("== 바뀌는 실전 설정", flush=True)
    for k, v in want.items():
        print(f"  {k}: {current(k)} → {v}", flush=True)
    print("== 실험 shadow 고정(지금 값)", flush=True)
    for n in pin_shadows(list(want), apply=args.apply) or ["(고정할 것 없음)"]:
        print(f"  {n}", flush=True)
    if not args.apply:
        print("\n미리보기 — --apply 로 적용", flush=True)
        return 0
    # 바꿀 키가 창고에 아직 없으면 거부한다 — 없는 키는 seed_config 가 '신규' 로 보고 바뀐 값을 2000-01-01 로 소급해 심는다
    # (과거 as_of 재현이 새 규칙을 보고, 그날 세션은 옛 키·새 키가 섞인다 — 독립 검토 10/10). 먼저 지금 yaml 로 seed 를 돌려 중립값을 심을 것.
    pre = subprocess.run([sys.executable, "tools/seed_config.py", "--store", "data"], cwd=REPO_ROOT, capture_output=True, text=True)
    fresh = [k for k in want if f"신규  {k}:" in pre.stdout]
    if fresh:
        print(f"창고에 없는 키 {fresh} — 먼저 `tools/seed_config.py --store data --apply`(지금 yaml, 중립값)를 돌린 뒤 다시", flush=True)
        return 3
    lines = YAML.read_text().splitlines()
    for k, v in want.items():
        i, m = yaml_line(k)
        lines[i] = f"{m.group(1)}{k.split('.', 1)[1]}: {v}{m.group(3) or ''}"
    YAML.write_text("\n".join(lines) + "\n")
    proc = subprocess.run([sys.executable, "tools/seed_config.py", "--store", "data", "--apply"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    print(proc.stdout[-3000:], proc.stderr[-1500:], flush=True)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
