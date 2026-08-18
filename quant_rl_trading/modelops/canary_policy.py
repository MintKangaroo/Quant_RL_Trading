"""카나리용 정책·가치망 — numpy 로 손수 미분한다.

## 진짜 정책망이 아니다

`rl-training.md §2` 의 정책망은 Transformer ×2 이고 `allocator/policy.py` 에
torch 로 만들어진다(M4-4-3). 여기 있는 것은 **그것을 대신 검증하는 물건이
아니라, 학습 루프를 돌려보기 위한 최소 정책**이다. 두 가지 성질만 진짜와
같게 맞췄다.

1. **순열 불변** — 위치 인코딩이 없다. 종목별 MLP 를 공유하고 마스크 평균으로
   모은다(DeepSets). 실제 구조인 attention 도 같은 이유로 위치 인코딩을 빼며,
   여기서 "3번 슬롯" 같은 없는 개념이 새어 들어오면 카나리가 먼저 깨진다.
2. **Dirichlet 액션** — 심플렉스 위에 직접 정의된 분포. softmax+Gaussian 은
   합=1·≥0 제약을 깨서 클리핑이 필요하고, 클리핑된 액션과 로그확률이 어긋나
   정책 그래디언트가 편향된다(§1). 카나리가 검증해야 할 배선의 일부다.

## 마스킹은 분포에서 뺀다

패딩 슬롯에 아주 작은 concentration 을 주는 방식은 쓰지 않는다. a→0 이면
log_prob 의 `(a-1)·log x` 항이 발산해서, **NaN 이 아니라 거대한 유한값**으로
나타나 어드밴티지를 통째로 오염시킨다. 대신 유효 슬롯만으로 Dirichlet 을
세우고 패딩 비중은 정확히 0 으로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from quant_rl_trading.modelops.numeric import (
    digamma,
    lgamma,
    orthogonal,
    sigmoid,
    softplus,
    trigamma,
)

Array = npt.NDArray[np.float64]

#: concentration 하한·상한 (§1). 하한은 수치 안정, 상한은 폭주 방지.
ALPHA_FLOOR = 1e-3
ALPHA_CEIL = 1e3
#: Dirichlet 표본이 정확히 0 으로 내려앉는 것을 막는 바닥. log x 가 -inf 가 된다.
SAMPLE_FLOOR = 1e-9


@dataclass
class PolicyOutput:
    """forward 결과 + backward 가 필요로 하는 중간값."""

    concentration: Array
    value: Array
    cache: dict[str, Any]


class CanaryPolicy:
    """종목축 공유 MLP → 마스크 평균 풀링 → 컨텍스트 → 헤드 3개.

    인코더는 정책·가치가 **공유**하고 헤드만 분리한다(§2). 표본이 적은
    문제라 공유가 유리하다.
    """

    def __init__(
        self,
        *,
        n_asset_features: int,
        n_portfolio_features: int,
        hidden: int = 64,
        seed: int = 0,
    ) -> None:
        rng = np.random.default_rng(seed)
        h = hidden
        self.hidden = h
        self.params: dict[str, Array] = {
            "w1": orthogonal(rng, (n_asset_features, h), gain=np.sqrt(2.0)),
            "b1": np.zeros(h),
            "w2": orthogonal(rng, (h, h), gain=np.sqrt(2.0)),
            "b2": np.zeros(h),
            "wp": orthogonal(rng, (n_portfolio_features, h), gain=np.sqrt(2.0)),
            "bp": np.zeros(h),
            "wc": orthogonal(rng, (2 * h, h), gain=np.sqrt(2.0)),
            "bc": np.zeros(h),
            "wt": orthogonal(rng, (2 * h, h), gain=np.sqrt(2.0)),
            "bt": np.zeros(h),
            # 정책 마지막 층 gain 0.01 — 초기 정책이 거의 균등해야 탐색이 산다.
            "ww": orthogonal(rng, (h, 1), gain=0.01),
            "bw": np.zeros(1),
            "wcash": orthogonal(rng, (h, 1), gain=0.01),
            "bcash": np.zeros(1),
            "wv1": orthogonal(rng, (h, h), gain=np.sqrt(2.0)),
            "bv1": np.zeros(h),
            "wv2": orthogonal(rng, (h, 1), gain=1.0),
            "bv2": np.zeros(1),
        }

    # -- forward --------------------------------------------------------------

    def forward(self, obs: dict[str, Array]) -> PolicyOutput:
        params = self.params
        assets = obs["assets"]
        portfolio = obs["portfolio"]
        mask = obs["mask"]
        batch, n_assets, _ = assets.shape
        mm = mask.astype(np.float64)[:, :, None]

        flat = assets.reshape(batch * n_assets, -1)
        h1 = np.tanh(flat @ params["w1"] + params["b1"])
        h2 = np.tanh(h1 @ params["w2"] + params["b2"]).reshape(batch, n_assets, -1)

        counts = np.maximum(mm.sum(axis=1), 1.0)
        pool = (h2 * mm).sum(axis=1) / counts

        cp = np.tanh(portfolio @ params["wp"] + params["bp"])
        ctx_in = np.concatenate([pool, cp], axis=1)
        ctx = np.tanh(ctx_in @ params["wc"] + params["bc"])

        tok_in = np.concatenate(
            [h2, np.repeat(ctx[:, None, :], n_assets, axis=1)], axis=2
        ).reshape(batch * n_assets, -1)
        tok = np.tanh(tok_in @ params["wt"] + params["bt"])

        asset_logits = (tok @ params["ww"] + params["bw"]).reshape(batch, n_assets)
        cash_logit = ctx @ params["wcash"] + params["bcash"]
        logits = np.concatenate([asset_logits, cash_logit], axis=1)

        raw = softplus(logits) + ALPHA_FLOOR
        concentration = np.minimum(raw, ALPHA_CEIL)

        v1 = np.tanh(ctx @ params["wv1"] + params["bv1"])
        value = (v1 @ params["wv2"] + params["bv2"]).reshape(batch)

        cache = {
            "assets": assets,
            "portfolio": portfolio,
            "mm": mm,
            "counts": counts,
            "h1": h1,
            "h2": h2,
            "flat": flat,
            "pool": pool,
            "cp": cp,
            "ctx_in": ctx_in,
            "ctx": ctx,
            "tok_in": tok_in,
            "tok": tok,
            "logits": logits,
            "raw": raw,
            "v1": v1,
            "shape": (batch, n_assets),
        }
        return PolicyOutput(concentration=concentration, value=value, cache=cache)

    # -- backward -------------------------------------------------------------

    def backward(
        self,
        out: PolicyOutput,
        *,
        d_concentration: Array,
        d_value: Array,
        want_input_grad: bool = False,
    ) -> tuple[dict[str, Array], Array | None]:
        """헤드 쪽 미분을 받아 파라미터 그래디언트를 돌려준다.

        ``want_input_grad`` 를 켜면 종목축 입력에 대한 미분도 함께 돌려준다 —
        `rl-training.md §0` 의 합격 조건 두 번째, **오라클 피처의 그래디언트
        기여도가 최상위인지**를 재는 데 쓴다.
        """
        params = self.params
        cache = out.cache
        batch, n_assets = cache["shape"]
        h = self.hidden
        grads = {name: np.zeros_like(value) for name, value in params.items()}

        # concentration = min(softplus(logits) + floor, ceil)
        d_logits = d_concentration * sigmoid(cache["logits"]) * (cache["raw"] < ALPHA_CEIL)
        d_asset_logits = d_logits[:, :n_assets]
        d_cash_logit = d_logits[:, n_assets:]

        # 가치 헤드
        dv2 = d_value.reshape(batch, 1)
        grads["wv2"] = cache["v1"].T @ dv2
        grads["bv2"] = dv2.sum(axis=0)
        dv1 = (dv2 @ params["wv2"].T) * (1.0 - cache["v1"] ** 2)
        grads["wv1"] = cache["ctx"].T @ dv1
        grads["bv1"] = dv1.sum(axis=0)
        d_ctx = dv1 @ params["wv1"].T

        # 현금 로짓
        grads["wcash"] = cache["ctx"].T @ d_cash_logit
        grads["bcash"] = d_cash_logit.sum(axis=0)
        d_ctx += d_cash_logit @ params["wcash"].T

        # 종목 로짓 → 토큰
        d_zl = d_asset_logits.reshape(batch * n_assets, 1)
        grads["ww"] = cache["tok"].T @ d_zl
        grads["bw"] = d_zl.sum(axis=0)
        d_tok = (d_zl @ params["ww"].T) * (1.0 - cache["tok"] ** 2)
        grads["wt"] = cache["tok_in"].T @ d_tok
        grads["bt"] = d_tok.sum(axis=0)
        d_tok_in = d_tok @ params["wt"].T
        d_h2_direct = d_tok_in[:, :h].reshape(batch, n_assets, h)
        d_ctx += d_tok_in[:, h:].reshape(batch, n_assets, h).sum(axis=1)

        # 컨텍스트
        d_ctx_pre = d_ctx * (1.0 - cache["ctx"] ** 2)
        grads["wc"] = cache["ctx_in"].T @ d_ctx_pre
        grads["bc"] = d_ctx_pre.sum(axis=0)
        d_ctx_in = d_ctx_pre @ params["wc"].T
        d_pool = d_ctx_in[:, :h]
        d_cp = d_ctx_in[:, h:] * (1.0 - cache["cp"] ** 2)
        grads["wp"] = cache["portfolio"].T @ d_cp
        grads["bp"] = d_cp.sum(axis=0)

        # 풀링 → 종목 MLP
        d_h2 = d_h2_direct + (d_pool[:, None, :] / cache["counts"][:, None]) * cache["mm"]
        d_h2_flat = d_h2.reshape(batch * n_assets, h)
        h2_flat = cache["h2"].reshape(batch * n_assets, h)
        d_a2 = d_h2_flat * (1.0 - h2_flat**2)
        grads["w2"] = cache["h1"].T @ d_a2
        grads["b2"] = d_a2.sum(axis=0)
        d_a1 = (d_a2 @ params["w2"].T) * (1.0 - cache["h1"] ** 2)
        grads["w1"] = cache["flat"].T @ d_a1
        grads["b1"] = d_a1.sum(axis=0)

        input_grad = None
        if want_input_grad:
            input_grad = (d_a1 @ params["w1"].T).reshape(batch, n_assets, -1)
        return grads, input_grad


# -- Dirichlet -----------------------------------------------------------------


def sample(rng: np.random.Generator, concentration: Array, valid: Array) -> Array:
    """유효 슬롯만으로 Dirichlet 표본을 뽑는다. 패딩 비중은 정확히 0 이다."""
    alpha = np.where(valid, concentration, 0.0)
    gammas = np.where(valid, rng.standard_gamma(np.maximum(alpha, ALPHA_FLOOR)), 0.0)
    total = np.maximum(gammas.sum(axis=1, keepdims=True), 1e-300)
    return gammas / total


def log_prob(concentration: Array, weights: Array, valid: Array) -> Array:
    """유효 차원 위 Dirichlet 의 log 밀도."""
    alpha = np.where(valid, concentration, 1.0)
    total = np.where(valid, concentration, 0.0).sum(axis=1)
    clipped = np.where(valid, np.maximum(weights, SAMPLE_FLOOR), 1.0)
    log_b = np.where(valid, lgamma(alpha), 0.0).sum(axis=1) - lgamma(total)
    return np.where(valid, (alpha - 1.0) * np.log(clipped), 0.0).sum(axis=1) - log_b


def log_prob_grad(concentration: Array, weights: Array, valid: Array) -> Array:
    """d log_prob / d a_j = ψ(Σa) - ψ(a_j) + log x_j."""
    alpha = np.where(valid, concentration, 1.0)
    total = np.where(valid, concentration, 0.0).sum(axis=1, keepdims=True)
    clipped = np.where(valid, np.maximum(weights, SAMPLE_FLOOR), 1.0)
    grad = digamma(total) - digamma(alpha) + np.log(clipped)
    return np.where(valid, grad, 0.0)


def entropy(concentration: Array, valid: Array) -> Array:
    alpha = np.where(valid, concentration, 1.0)
    total = np.where(valid, concentration, 0.0).sum(axis=1)
    k = valid.sum(axis=1).astype(np.float64)
    log_b = np.where(valid, lgamma(alpha), 0.0).sum(axis=1) - lgamma(total)
    out = (
        log_b
        + (total - k) * digamma(total)
        - np.where(valid, (alpha - 1.0) * digamma(alpha), 0.0).sum(axis=1)
    )
    return np.asarray(out, dtype=np.float64)


def entropy_grad(concentration: Array, valid: Array) -> Array:
    """d H / d a_j = (Σa - K)·ψ'(Σa) - (a_j - 1)·ψ'(a_j)."""
    alpha = np.where(valid, concentration, 1.0)
    total = np.where(valid, concentration, 0.0).sum(axis=1, keepdims=True)
    k = valid.sum(axis=1, keepdims=True).astype(np.float64)
    grad = (total - k) * trigamma(total) - (alpha - 1.0) * trigamma(alpha)
    return np.where(valid, grad, 0.0)
