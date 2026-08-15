"""모의와 실전을 **판별할 수 없으므로 선언하게 한다.**

2026-08-15 에 실측으로 확인한 것:

- 모의·실전이 **같은 호스트**(``openapi.ls-sec.co.kr:8080``)를 쓴다
- 둘 다 appkey 가 ``PS`` 로 시작하고 길이가 36 으로 같다
- **모의 appkey 로도 t0424(잔고)가 정상 응답한다** — LS 가 안 막는다

코드 주석은 오랫동안 "모의면 잔고·주문을 LS 가 막는다" 고 적고 있었고 그게
틀렸다. 그 믿음 위에서 "모의니까 안전하다" 를 가정하면, 실전 키가 꽂힌 줄
모르고 주문을 낸다. **판별이 불가능하면 선언하게 하고 그 선언을 지문에 묶는
것**이 유일하게 정직한 방법이다.
"""

from __future__ import annotations

from quant_rl_trading.collectors.ls_client import LSCredentials

REAL = {
    "LS_APPKEY": "PSpDxxxxxxxxxxxxxxxxxxxxxxxxxxxx4h3i",
    "LS_APPSECRET": "secret-real",
    "LS_REST_BASE_URL": "https://openapi.ls-sec.co.kr:8080",
    "LS_ACCOUNT_KIND": "real",
}
PAPER = {
    "LS_APPKEY": "PSn6xxxxxxxxxxxxxxxxxxxxxxxxxxxxZR0X",
    "LS_APPSECRET": "secret-paper",
    # **같은 호스트다.** 엔드포인트로는 못 가른다.
    "LS_REST_BASE_URL": "https://openapi.ls-sec.co.kr:8080",
    "LS_ACCOUNT_KIND": "paper",
}


def test_선언이_없으면_빈_문자열이다() -> None:
    """**모르는 것을 paper 로 기본값 잡지 않는다.**

    기본값을 paper 로 두면 미선언 상태가 "모의" 로 읽히고, 그게 이 결함의
    원인이었다 — 실전 키가 꽂힌 채로 "모의겠거니" 하고 주문이 나간다.
    """
    creds = LSCredentials.from_env({k: v for k, v in REAL.items() if k != "LS_ACCOUNT_KIND"})

    assert creds.declared_kind == ""
    assert creds.usable(), "키는 멀쩡한데 선언만 없는 상태여야 한다"


def test_선언한_대로_읽힌다() -> None:
    assert LSCredentials.from_env(REAL).declared_kind == "real"
    assert LSCredentials.from_env(PAPER).declared_kind == "paper"


def test_엔드포인트로는_못_가른다() -> None:
    """같은 호스트를 쓴다 — 이걸로 판별하려던 코드가 있으면 지금 막는다."""
    assert LSCredentials.from_env(REAL).base_url == LSCredentials.from_env(PAPER).base_url


def test_지문이_계좌를_가른다() -> None:
    assert LSCredentials.from_env(REAL).fingerprint != LSCredentials.from_env(PAPER).fingerprint


def test_지문은_키를_되돌릴_수_없다() -> None:
    """설정 파일에 적히는 값이다. 키 조각이면 안 된다."""
    creds = LSCredentials.from_env(REAL)
    fp = creds.fingerprint

    assert len(fp) == 12
    assert fp not in creds.appkey
    # 앞뒤 어느 조각도 그대로 새어 나오면 안 된다.
    assert creds.appkey[:4] not in fp
    assert creds.appkey[-4:] not in fp


def test_같은_키는_같은_지문이다() -> None:
    """지문이 실행마다 바뀌면 설정에 고정할 수 없다."""
    assert LSCredentials.from_env(REAL).fingerprint == LSCredentials.from_env(REAL).fingerprint


def test_미장은_별도로_선언한다() -> None:
    """국장·미장은 별도 appkey 라 선언도 따로다."""
    env = {
        **REAL,
        "LS_US_APPKEY": "PSiqxxxxxxxxxxxxxxxxxxxxxxxxxxxxQjnn",
        "LS_US_APPSECRET": "secret-us",
        "LS_US_ACCOUNT_KIND": "paper",
    }

    assert LSCredentials.from_env(env).declared_kind == "real"
    assert LSCredentials.from_env(env, prefix="LS_US_").declared_kind == "paper"
