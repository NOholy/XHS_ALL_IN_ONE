"""
指纹型死循环检测器
借鉴 ApkClaw DefaultAgentService 的 (screenHash, toolCall) 滑动窗口方案。
比纯 MSE 更精准：不仅看屏幕变没变，还看"做了什么 + 结果是什么"。
"""
from collections import deque
import hashlib
import cv2
from .logger import get_logger

logger = get_logger("loop_detector")


class LoopDetector:
    """
    指纹型死循环检测器。
    每次操作后记录 (屏幕指纹, 操作描述) 二元组到滑动窗口，
    如果窗口内所有指纹完全相同 → 判定为死循环。

    设计借鉴 ApkClaw 的 RoundFingerprint(screenHash, toolCall) 机制，
    比当前项目中分散使用的 MSE 比对更鲁棒（对视频/动画场景免疫）。
    """
    WINDOW_SIZE = 4

    def __init__(self):
        self._history = deque(maxlen=self.WINDOW_SIZE)
        self._screen_hash = ""

    def update_screen(self, screen_img):
        """
        每次截图后更新屏幕指纹。
        使用极低分辨率的灰度 hash，过滤动画/视频帧变化噪声。
        """
        if screen_img is None:
            return
        try:
            small = cv2.resize(screen_img, (32, 32), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            self._screen_hash = hashlib.md5(gray.tobytes()).hexdigest()[:8]
        except Exception:
            pass

    def record_action(self, action: str, params: str = ""):
        """每次执行动作后记录指纹"""
        fingerprint = (self._screen_hash, f"{action}:{params}")
        self._history.append(fingerprint)

    def is_stuck(self) -> bool:
        """检测是否陷入死循环（滑动窗口内所有指纹完全相同）"""
        if len(self._history) < self.WINDOW_SIZE:
            return False
        return len(set(self._history)) == 1

    def clear(self):
        """重置检测窗口（在成功恢复后调用）"""
        self._history.clear()

    def get_suggestion(self) -> str:
        """卡死时的恢复建议"""
        return ("检测到连续相同操作且屏幕无变化。建议: "
                "1) 按返回键退出当前页面 "
                "2) 滑动屏幕寻找新目标 "
                "3) 回到首页重新开始")
