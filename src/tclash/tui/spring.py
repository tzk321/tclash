"""弹簧阻尼动画。

参考 lilt（chhongzh/lilt）的做法：不用固定时长的补间，而是跑一个阻尼振子

    m·x'' = -k·(x - target) - c·x'

好处是**可打断**：动画途中改目标值时速度不清零，视觉上会顺着原来的方向
拐过去，而不是先停一下再重新起步。默认参数接近临界阻尼（ζ≈0.87），
会有极轻微过冲，约 0.3 秒收敛。
"""

from __future__ import annotations

from math import ceil, sqrt

# 积分步长上限：semi-implicit Euler 每步不超过 1/240 秒，避免大 dt 炸开
_MAX_SUBSTEP = 1.0 / 240.0
_MAX_DT = 0.1


class Spring:
    """一维弹簧。

    Args:
        value: 初始位置。
        stiffness: 刚度 k，越大越急。
        damping: 阻尼 c，越大越不弹。
        mass: 质量 m。
        threshold: 收敛阈值（位置）。
    """

    __slots__ = ("stiffness", "damping", "mass", "value", "velocity", "target", "_threshold")

    def __init__(
        self,
        value: float = 0.0,
        target: float | None = None,
        *,
        stiffness: float = 300.0,
        damping: float = 30.0,
        mass: float = 1.0,
        threshold: float = 0.002,
    ) -> None:
        self.stiffness = stiffness
        self.damping = damping
        self.mass = mass
        self._threshold = threshold
        self.value = float(value)
        self.target = float(value if target is None else target)
        self.velocity = 0.0

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Spring({self.value:.3f} -> {self.target:.3f}, v={self.velocity:.2f})"

    # ------------------------------------------------------------------ 属性
    @property
    def settled(self) -> bool:
        return self.value == self.target and self.velocity == 0.0

    @property
    def zeta(self) -> float:
        """阻尼比。1 为临界阻尼，<1 会过冲。"""
        return self.damping / (2.0 * sqrt(self.stiffness * self.mass))

    # ------------------------------------------------------------------ 控制
    def to(self, target: float) -> None:
        """改目标值，保留当前速度（可打断）。"""
        self.target = float(target)

    def kick(self, velocity: float) -> None:
        self.velocity += velocity

    def snap(self, value: float) -> None:
        """直接归位，不跑动画。"""
        self.value = self.target = float(value)
        self.velocity = 0.0

    # ------------------------------------------------------------------ 积分
    def step(self, dt: float) -> float:
        """前进 dt 秒，返回新的位置。"""
        if self.settled:
            return self.value
        dt = min(max(dt, 0.0), _MAX_DT)
        if dt == 0.0:
            return self.value

        steps = max(1, ceil(dt / _MAX_SUBSTEP))
        h = dt / steps
        for _ in range(steps):
            accel = (
                -self.stiffness * (self.value - self.target) - self.damping * self.velocity
            ) / self.mass
            self.velocity += accel * h
            self.value += self.velocity * h

        if (
            abs(self.value - self.target) < self._threshold
            and abs(self.velocity) < self._threshold * 20
        ):
            self.snap(self.target)
        return self.value
