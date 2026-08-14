"""
本模块提供桌面宠物的纯逻辑行为状态机，不直接创建窗口或访问文件。

职责范围：
- 定义待机、跑步、坐下、睡觉、互动表情和拖动状态；
- 维护亲密度、精力、无聊度与饱食度四个轻量数值；
- 根据是否允许跑动选择下一生活状态及持续时间；
- 计算到达屏幕边界后的新位置和行走方向。

Agent 快速定位：
- 状态枚举位于 PetState；
- 自主状态选择位于 BehaviorModel.next_autonomous_state()；
- 水平边界计算位于 BehaviorModel.advance_horizontal()。

输入为当前状态、配置时长、坐标和边界，输出为新的状态、时长、坐标或方向。
模块只依赖 Python 标准库，不执行文件写入、网络请求或 GUI 操作，便于单元测试。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .config import PetSettings


class PetState(str, Enum):
    """桌面宠物可显示的行为状态。"""

    IDLE = "idle"
    WALK = "walk"
    SIT = "sit"
    SLEEP = "sleep"
    WAVE = "wave"
    HAPPY = "happy"
    SHY = "shy"
    SURPRISED = "surprised"
    ANNOYED = "annoyed"
    SLEEPY = "sleepy"
    CURIOUS = "curious"
    SELFIE = "selfie"
    DRAG = "drag"


@dataclass(frozen=True)
class StateDecision:
    """一次自主状态选择的结果。"""

    state: PetState
    duration_ms: int


@dataclass(frozen=True)
class CompanionBehaviorDecision:
    """工作状态驱动的陪伴动作选择结果。"""

    activity: str
    reason: str


class CompanionBehaviorController:
    """将计时、时间、音乐和互动状态收口为自动陪伴决策。

    这里不直接操作窗口或动画；PetWindow 只需把 activity 交给
    AnimationManager/现有动画入口，用户无需再维护“陪伴动作”菜单。
    """

    def decide(
        self,
        *,
        now_hour: int,
        working: bool,
        session_seconds: int,
        today_seconds: int,
        resting: bool = False,
        music_playing: bool = False,
        idle_seconds: int = 0,
    ) -> CompanionBehaviorDecision:
        if resting:
            return CompanionBehaviorDecision("sleep" if session_seconds > 0 else "sit", "rest")
        if working:
            if session_seconds >= 4 * 3600:
                return CompanionBehaviorDecision("sleepy", "extreme_fatigue")
            if session_seconds >= 2 * 3600:
                return CompanionBehaviorDecision("thermos", "long_session")
            if now_hour >= 22:
                return CompanionBehaviorDecision("sit", "overtime")
            if session_seconds >= 45 * 60:
                return CompanionBehaviorDecision("book", "steady_work")
            if music_playing:
                return CompanionBehaviorDecision("headphones", "music_support")
            return CompanionBehaviorDecision("sit", "work_companion")
        if idle_seconds >= 30 * 60:
            return CompanionBehaviorDecision("pointing", "missing_user")
        if today_seconds >= 3 * 3600 and now_hour >= 20:
            return CompanionBehaviorDecision("sleepy", "late_day_fatigue")
        return CompanionBehaviorDecision("idle", "free_time")


@dataclass
class PetMood:
    """保存并约束宠物的亲密度、精力、无聊度和饱食度。"""

    affinity: int = 50
    energy: int = 75
    boredom: int = 10
    fullness: int = 55

    def _clamp(self) -> None:
        """把四个会话状态数值限制在 0 到 100。"""

        self.affinity = min(100, max(0, self.affinity))
        self.energy = min(100, max(0, self.energy))
        self.boredom = min(100, max(0, self.boredom))
        self.fullness = min(100, max(0, self.fullness))

    def receive_food(
        self,
        fullness_gain: int,
        energy_gain: int,
        affinity_gain: int = 3,
    ) -> None:
        """记录一次喂食带来的饱食、精力和亲密度变化。"""

        self.fullness += fullness_gain
        self.energy += energy_gain
        self.affinity += affinity_gain
        self.boredom -= 6
        self._clamp()

    def receive_affection(self) -> None:
        """记录摸头或友好点击带来的亲密反馈。"""

        self.affinity += 5
        self.energy -= 1
        self.boredom -= 18
        self._clamp()

    def receive_poke(self, repeated: bool) -> None:
        """记录身体戳击；连续戳击会轻微降低亲密度。"""

        self.affinity -= 2 if repeated else 0
        self.energy -= 2
        self.boredom -= 8
        self._clamp()

    def receive_drag(self) -> None:
        """记录一次拖拽带来的精力消耗和解闷效果。"""

        self.energy -= 3
        self.boredom -= 12
        self._clamp()

    def receive_focus_reward(self, blocks: int = 1) -> None:
        """每完成一个十分钟专注块增加默契，同时产生轻微精力消耗。"""

        safe_blocks = max(0, int(blocks))
        self.affinity += safe_blocks
        self.energy -= safe_blocks
        self.boredom -= safe_blocks * 2
        self._clamp()

    def pass_time(self, state: PetState) -> None:
        """在自主状态到期时更新精力、无聊度和轻微饥饿。"""

        if state is PetState.SLEEP:
            self.energy += 12
            self.boredom -= 5
        elif state is PetState.SIT:
            self.energy += 3
            self.boredom += 2
        else:
            self.energy -= 1
            self.boredom += 4
        self.fullness -= 1
        self._clamp()


class BehaviorModel:
    """封装可注入随机源的桌面宠物行为决策。"""

    def __init__(
        self,
        settings: PetSettings,
        random_source: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.random = random_source or random.Random()

    def initial_idle(self) -> StateDecision:
        """返回初始待机状态和随机持续时间。"""

        return StateDecision(
            PetState.IDLE,
            self.random.randint(
                self.settings.idle_min_ms,
                self.settings.idle_max_ms,
            ),
        )

    def next_autonomous_state(
        self,
        current: PetState,
        allow_walk: bool = False,
    ) -> StateDecision:
        """按当前状态和跑动开关选择下一生活状态及持续时间。"""

        if current is PetState.WALK:
            state = self.random.choices(
                (PetState.IDLE, PetState.SIT, PetState.SELFIE),
                weights=(0.42, 0.33, 0.25),
                k=1,
            )[0]
            return self._action_decision(state)
        if current is not PetState.IDLE:
            return self.initial_idle()
        if allow_walk:
            states = (PetState.WALK, PetState.SIT, PetState.SELFIE)
            weights = (0.56, 0.27, 0.17)
        else:
            states = (PetState.IDLE, PetState.SIT, PetState.SELFIE, PetState.SLEEP)
            weights = (0.32, 0.30, 0.23, 0.15)
        state = self.random.choices(states, weights=weights, k=1)[0]
        return self._action_decision(state)

    def _action_decision(self, state: PetState) -> StateDecision:
        """为选中的生活状态附加统一范围内的随机持续时间。"""

        return StateDecision(
            state,
            self.random.randint(
                self.settings.action_min_ms,
                self.settings.action_max_ms,
            ),
        )

    @staticmethod
    def advance_horizontal(
        x: int,
        direction: int,
        step: int,
        minimum: int,
        maximum: int,
    ) -> tuple[int, int]:
        """移动一步并在边界处夹紧位置、反转方向。"""

        normalized_direction = 1 if direction >= 0 else -1
        next_x = x + normalized_direction * step
        if next_x <= minimum:
            return minimum, 1
        if next_x >= maximum:
            return maximum, -1
        return next_x, normalized_direction
