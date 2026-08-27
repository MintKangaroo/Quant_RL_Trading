"""PPO 업데이트의 분리 클리핑 — `rl-training.md §4` (2026-08-27).

한 손실로 합쳐 전역 노름을 자르면 노름이 큰 쪽이 예산을 독식한다. M4 2회차에서
가치 쪽 1,659 대 정책 쪽 19.5 로 정책의 실효 학습률이 3e-9 까지 떨어졌다.
"""

from __future__ import annotations

import torch
from torch import nn

from quant_rl_trading.allocator.train import step_separately


class _TwoHeads(nn.Module):
    """공유 인코더 하나에 정책·가치 머리. 실제 정책망의 축소판이다."""

    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Linear(4, 4, bias=False)
        self.policy_head = nn.Linear(4, 1, bias=False)
        self.value_head = nn.Linear(4, 1, bias=False)
        for p in self.parameters():
            nn.init.constant_(p, 0.1)


def _losses(model: _TwoHeads, *, value_scale: float) -> tuple[torch.Tensor, torch.Tensor]:
    x = torch.ones(8, 4)
    h = model.encoder(x)
    policy_loss = model.policy_head(h).mean()
    # 가치 손실을 일부러 수천 배 키운다 — 실측 1,659 대 19.5 를 흉내 낸다.
    value_loss = value_scale * (model.value_head(h) ** 2).mean()
    return policy_loss, value_loss


def _global_clip_step(model: _TwoHeads, lr: float, *, value_scale: float) -> torch.Tensor:
    """옛 방식(합쳐서 한 번 자르기). 비교 대조군이다."""
    opt = torch.optim.SGD(model.parameters(), lr=lr)
    before = model.policy_head.weight.detach().clone()
    p, v = _losses(model, value_scale=value_scale)
    opt.zero_grad()
    (p + v).backward()
    nn.utils.clip_grad_norm_(model.parameters(), 0.5)
    opt.step()
    return (model.policy_head.weight.detach() - before).abs().sum()


def test_가치_손실이_커도_정책_머리는_자기_예산만큼_움직인다() -> None:
    torch.manual_seed(0)
    lr = 1e-2
    old_way = _TwoHeads()
    moved_old = _global_clip_step(old_way, lr, value_scale=1e4)

    new_way = _TwoHeads()
    opt = torch.optim.SGD(new_way.parameters(), lr=lr)
    before = new_way.policy_head.weight.detach().clone()
    p, v = _losses(new_way, value_scale=1e4)
    policy_norm, value_norm = step_separately(
        new_way, opt, policy_side=p, value_side=v, max_norm=0.5
    )
    moved_new = (new_way.policy_head.weight.detach() - before).abs().sum()

    # 자르기 전 노름을 돌려준다 — 가치 쪽이 압도적으로 크다는 사실이 보여야 한다.
    assert value_norm > 100 * policy_norm
    # 옛 방식은 정책 머리가 거의 안 움직이고, 새 방식은 수백 배 더 움직인다.
    assert moved_new > 100 * moved_old


def test_분리_클리핑은_두_손실을_다_반영한다() -> None:
    """정책만 남기고 가치를 버리는 것이 아니다 — 가치 머리도 같이 배운다."""
    torch.manual_seed(0)
    model = _TwoHeads()
    opt = torch.optim.SGD(model.parameters(), lr=1e-2)
    before_value = model.value_head.weight.detach().clone()
    before_policy = model.policy_head.weight.detach().clone()
    p, v = _losses(model, value_scale=1.0)
    step_separately(model, opt, policy_side=p, value_side=v, max_norm=0.5)
    assert not torch.equal(model.value_head.weight.detach(), before_value)
    assert not torch.equal(model.policy_head.weight.detach(), before_policy)


def test_자르기_전_노름이_상한_아래면_그대로_간다() -> None:
    """작은 그래디언트는 안 건드린다 — 클리핑은 상한이지 정규화가 아니다."""
    torch.manual_seed(0)
    model = _TwoHeads()
    opt = torch.optim.SGD(model.parameters(), lr=1.0)
    p, v = _losses(model, value_scale=1e-3)
    policy_norm, value_norm = step_separately(
        model, opt, policy_side=p, value_side=v, max_norm=1e6
    )
    assert 0 < policy_norm < 1e6 and 0 < value_norm < 1e6
