import time
import random
from enum import Enum, auto
from mobile_core.logger import get_logger

logger = get_logger("mood_manager")

class MoodState(Enum):
    EXCITED = auto()
    NORMAL = auto()
    BORED = auto()

class MoodManager:
    """
    马尔可夫情绪状态机（Mood State Machine）
    用于模拟真实用户的交互爆发期（Burst）和静默潜水期（Silence）。
    """
    def __init__(self):
        self.state = MoodState.NORMAL
        self.last_transition_time = time.time()
        # 持续时间配置 (秒): min, max
        self.duration_ranges = {
            MoodState.EXCITED: (60, 180),     # 兴奋期 1-3 分钟
            MoodState.NORMAL: (300, 600),     # 正常期 5-10 分钟
            MoodState.BORED: (600, 1200),     # 无聊期 10-20 分钟
        }
        # 转移矩阵 (当前状态: [(下一个状态, 权重), ...])
        self.transition_matrix = {
            MoodState.EXCITED: [(MoodState.NORMAL, 0.8), (MoodState.BORED, 0.2)],
            MoodState.NORMAL: [(MoodState.EXCITED, 0.3), (MoodState.BORED, 0.7)],
            MoodState.BORED: [(MoodState.NORMAL, 0.9), (MoodState.EXCITED, 0.1)],
        }
        # 概率修正倍率
        self.multipliers = {
            MoodState.EXCITED: 3.0,
            MoodState.NORMAL: 1.0,
            MoodState.BORED: 0.2,
        }
        self.current_duration_target = self._generate_duration(self.state)
        logger.info(f"Initialized MoodManager: state={self.state.name}, duration={self.current_duration_target}s")

    def _generate_duration(self, state: MoodState) -> int:
        min_sec, max_sec = self.duration_ranges[state]
        return random.randint(min_sec, max_sec)

    def update(self):
        """推进时间，判断是否需要状态转移"""
        now = time.time()
        if now - self.last_transition_time >= self.current_duration_target:
            self._transition()

    def _transition(self):
        old_state = self.state
        transitions = self.transition_matrix[self.state]
        states = [t[0] for t in transitions]
        weights = [t[1] for t in transitions]
        
        # 依据权重进行随机转移
        new_state = random.choices(states, weights=weights, k=1)[0]
        self.state = new_state
        self.last_transition_time = time.time()
        self.current_duration_target = self._generate_duration(new_state)
        
        logger.info(f"Mood transition: {old_state.name} -> {self.state.name} (Multiplier: {self.get_multiplier()}x, Next transition in {self.current_duration_target}s)")

    def get_multiplier(self) -> float:
        return self.multipliers[self.state]

    def get_state(self) -> MoodState:
        return self.state
