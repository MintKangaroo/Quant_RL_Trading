#!/usr/bin/env bash
# 시행 TX 준비 러너 — ① 수시공시 임베딩(시간 관문·이어받기) → ② 다 됐고 등록이 고정됐으면 얼림(fit) → 굽기(bake) → 커버리지.
# 등록: docs/protocols/tx-filing-text-2026-10.md. 크론은 리드가 건다(10/10 원문 백필 끝난 뒤, 제안: `10 16 * * *` 와 `10 8 * * 0,6`).
# 관문: DART 원문 백필 00:40~07:35 · 평일 장 중 08:50~15:40 엔 임베딩이 저장하고 멈춘다 — 다음 회차가 잇는다.
# fit 은 등록 문서 머리줄이 '> **초안' 이면 rc 4 로 거절한다(라벨을 보지 않는다). 수익·IC 는 이 러너 어디에서도 계산하지 않는다
# (fit 의 알파 선택은 컷오프 2023-01-31 이전 OOF IC 만 — 등록 초안 'tx 머리').
# rc: 임베딩 rc 가 0 이 아니면 그것 · 임베딩이 남았으면 0(다음 회차) · 그 뒤 단계의 rc.
set -u
cd /home/mintkangaroo/Project/Quant_RL_Trading || exit 1
LOG="logs/tx-prepare-$(date +%Y%m).log"
exec 9> data/_locks/tx-prepare.lock
if ! flock -n 9; then
    echo "$(date '+%F %T') 앞 회차가 아직 돈다 — 건너뛴다" >> "${LOG}"
    exit 0
fi
export QUANT_RL_DUCKDB_MEMORY_LIMIT=600MB QUANT_RL_DUCKDB_THREADS=2 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PY=".venv/bin/python -u"
run() {
    echo "=== $(date '+%F %T') TX 준비 ==="
    nice -n 10 ${PY} tools/tx_embed.py --threads 8
    rc=$?
    echo "임베딩 rc=${rc}"
    if [ "${rc}" -ne 0 ]; then return "${rc}"; fi
    ${PY} tools/tx_embed.py --status | tail -1
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then echo "임베딩이 남았다 — 다음 회차"; return 0; fi
    if ! ls data/models/tx/frozen-*.npz > /dev/null 2>&1; then
        nice -n 10 ${PY} tools/tx_features.py --stage fit
        rc=$?
        echo "얼림 rc=${rc}"
        if [ "${rc}" -ne 0 ]; then return "${rc}"; fi
    fi
    nice -n 10 ${PY} tools/tx_features.py --stage bake
    rc=$?
    echo "굽기 rc=${rc}"
    if [ "${rc}" -ne 0 ]; then return "${rc}"; fi
    ${PY} tools/tx_features.py --stage coverage
}
run >> "${LOG}" 2>&1
RC=$?
echo "$(date '+%F %T') rc=${RC}" >> "${LOG}"
exit "${RC}"
