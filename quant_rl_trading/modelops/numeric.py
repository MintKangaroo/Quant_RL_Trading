"""numpy 만으로 필요한 수치 도구 — 특수함수·초기화·Adam.

## 왜 직접 쓰는가

오라클 카나리(`rl-training.md §0`)는 **학습 배선이 살아 있는지**만 본다.
그걸 확인하려고 torch 를 깔면, 판정이 나기도 전에 무거운 의존성이 먼저
들어온다. Dirichlet 의 log_prob·엔트로피에 필요한 것은 lgamma·digamma·
trigamma 셋뿐이라 여기서 만든다.

scipy 는 이 레포에 없다. `pyproject.toml` 을 늘리지 않는다.

## 작은 인자를 견뎌야 한다

concentration 의 하한은 `softplus(logits) + 1e-3` 이라 0.001 짜리 인자가
실제로 들어온다. digamma/trigamma 를 점근전개만으로 재면 그 구간에서 통째로
틀리고, **틀린 log_prob 은 NaN 이 아니라 조용히 편향된 그래디언트**로 나타난다.
그래서 재귀식으로 인자를 8 만큼 밀어 올린 뒤 점근전개를 쓴다 — 재귀식은
근사가 아니라 항등식이라 작은 인자에서도 정확하다.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

#: 점근전개를 쓰기 전에 인자를 이만큼 밀어 올린다. x+8 이면 오차가 1e-12 아래다.
_SHIFT = 8

_LANCZOS_G = 7.0
_LANCZOS_COEF = np.array(
    [
        0.99999999999980993,
        676.5203681218851,
        -1259.1392167224028,
        771.32342877765313,
        -176.61502916214059,
        12.507343278686905,
        -0.13857109526572012,
        9.9843695780195716e-6,
        1.5056327351493116e-7,
    ]
)


def lgamma(x: Array) -> Array:
    """log Γ(x), x > 0. Lanczos 근사(g=7, n=9) — 상대오차 1e-13 규모."""
    x = np.asarray(x, dtype=np.float64)
    z = x - 1.0
    series = np.full_like(z, _LANCZOS_COEF[0])
    for index in range(1, _LANCZOS_COEF.size):
        series = series + _LANCZOS_COEF[index] / (z + index)
    t = z + _LANCZOS_G + 0.5
    out = 0.5 * np.log(2.0 * np.pi) + (z + 0.5) * np.log(t) - t + np.log(series)
    return np.asarray(out, dtype=np.float64)


def digamma(x: Array) -> Array:
    """ψ(x), x > 0."""
    x = np.asarray(x, dtype=np.float64)
    shifted = x + _SHIFT
    # ψ(x) = ψ(x+m) - Σ 1/(x+k). 항등식이라 x 가 아무리 작아도 정확하다.
    correction = np.zeros_like(x)
    for step in range(_SHIFT):
        correction = correction + 1.0 / (x + step)

    inv = 1.0 / shifted
    inv2 = inv * inv
    asymptotic = (
        np.log(shifted)
        - 0.5 * inv
        - inv2 * (1.0 / 12.0 - inv2 * (1.0 / 120.0 - inv2 * (1.0 / 252.0 - inv2 / 240.0)))
    )
    return asymptotic - correction


def trigamma(x: Array) -> Array:
    """ψ'(x), x > 0. Dirichlet 엔트로피의 그래디언트에 필요하다."""
    x = np.asarray(x, dtype=np.float64)
    shifted = x + _SHIFT
    correction = np.zeros_like(x)
    for step in range(_SHIFT):
        correction = correction + 1.0 / ((x + step) ** 2)

    inv = 1.0 / shifted
    inv2 = inv * inv
    asymptotic = inv * (
        1.0
        + 0.5 * inv
        + inv2 * (1.0 / 6.0 - inv2 * (1.0 / 30.0 - inv2 * (1.0 / 42.0 - inv2 / 30.0)))
    )
    return asymptotic + correction


def softplus(x: Array) -> Array:
    """log(1+e^x). 큰 x 에서 overflow 하지 않는 형태로 쓴다."""
    return np.logaddexp(np.zeros_like(x), x)


def sigmoid(x: Array) -> Array:
    """softplus 의 도함수."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def orthogonal(
    rng: np.random.Generator, shape: tuple[int, int], *, gain: float = 1.0
) -> Array:
    """직교 초기화 (`rl-training.md §2`).

    정책 마지막 층은 gain 0.01 로 부른다 — 초기 정책이 거의 균등해야 탐색이
    산다. 큰 gain 으로 시작하면 첫 업데이트 전에 이미 한 종목에 몰린다.
    """
    rows, cols = shape
    flat = rng.standard_normal((max(rows, cols), min(rows, cols)))
    q, r = np.linalg.qr(flat)
    # QR 의 부호 모호성을 없앤다. 안 없애면 시드가 같아도 판본마다 부호가 갈린다.
    q = q * np.sign(np.diag(r))
    if rows < cols:
        q = q.T
    return gain * q[:rows, :cols]


class Adam:
    """파라미터 dict 하나를 맡는 Adam. 파라미터별 학습률을 받는다.

    정책과 가치가 인코더를 공유하는데 lr 이 2~3배 달라야 하므로
    (`rl-training.md §4`), 옵티마이저가 이름별 lr 을 알아야 한다.
    """

    def __init__(
        self,
        params: dict[str, Array],
        *,
        lr: dict[str, float],
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
    ) -> None:
        self.params = params
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.step_count = 0
        self.m = {name: np.zeros_like(value) for name, value in params.items()}
        self.v = {name: np.zeros_like(value) for name, value in params.items()}

    def step(self, grads: dict[str, Array], *, lr_scale: float = 1.0) -> None:
        """``lr_scale`` 은 선형 감쇠용. 0 이 되면 학습이 멈춘다."""
        self.step_count += 1
        bias1 = 1.0 - self.beta1**self.step_count
        bias2 = 1.0 - self.beta2**self.step_count
        for name, grad in grads.items():
            self.m[name] = self.beta1 * self.m[name] + (1.0 - self.beta1) * grad
            self.v[name] = self.beta2 * self.v[name] + (1.0 - self.beta2) * grad * grad
            m_hat = self.m[name] / bias1
            v_hat = self.v[name] / bias2
            lr = self.lr.get(name, self.lr["default"]) * lr_scale
            self.params[name] -= lr * m_hat / (np.sqrt(v_hat) + self.eps)


def global_norm(grads: dict[str, Array]) -> float:
    return float(np.sqrt(sum(float(np.sum(value * value)) for value in grads.values())))


def clip_grads(grads: dict[str, Array], *, max_norm: float) -> float:
    """전역 노름 클리핑. 자른 뒤가 아니라 **자르기 전 노름**을 돌려준다 —
    로그에 찍어야 할 것은 폭주했는지 여부지, 자른 결과가 아니다 (§10)."""
    norm = global_norm(grads)
    if norm > max_norm and norm > 0.0:
        scale = max_norm / norm
        for name in grads:
            grads[name] = grads[name] * scale
    return norm
