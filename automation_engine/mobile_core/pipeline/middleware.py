"""
Pipeline 中间件 — Watchdog / LoopDetector / Logging。

将现有的弹窗检测和卡死检测从手动调用升级为 Pipeline 全局中间件。
每帧截屏后自动执行，确保每个节点都受到一致的安全保护。

参考:
    - MaaFramework: JumpBack 中断处理模式
    - Airtest: @logwrap 自动日志记录
    - XHS watchdog.py: 三层弹窗检测
    - XHS loop_detector.py: 指纹级卡死检测
"""

import json
import time
from typing import Optional

from mobile_core.logger import get_logger

from .engine import PipelineMiddleware, PipelineExecutor
from .models import PipelineNode, RecognitionResult

logger = get_logger("pipeline_middleware")


class WatchdogMiddleware(PipelineMiddleware):
    """
    弹窗 / 风控检测中间件。

    包装现有 PopupWatchdog，在每帧截屏后自动检查:
    1. Activity 层: 系统弹窗检测 (dumpsys)
    2. OCR 层: 风控关键词扫描 (安全验证/滑块验证/账号冻结)
    3. 自动关闭: 普通弹窗 (我知道了/跳过/取消)

    当检测到弹窗并自动处理后，返回 True 让引擎重新截屏。
    当检测到严重风控，抛出 RiskControlTriggered 停止 Pipeline。

    解决的问题:
        当前 intercept_flow.py 手动调用 4 次 _safe_screen_check()，
        但 farmer.py 576 行代码中 0 次调用 watchdog。
        中间件方式确保 100% 覆盖。
    """

    def __init__(self, watchdog, check_interval_ms: int = 3000):
        """
        Args:
            watchdog: PopupWatchdog 实例
            check_interval_ms: 检查间隔 (毫秒)。不是每帧都检查，
                              以减少 OCR 开销。
        """
        self.watchdog = watchdog
        self.check_interval_s = check_interval_ms / 1000.0
        self._last_check_time = 0.0
        self._popup_count = 0

    def on_screen_captured(self, screen, engine: PipelineExecutor) -> bool:
        """每帧截屏后: 按间隔执行弹窗检查。"""
        now = time.time()
        if now - self._last_check_time < self.check_interval_s:
            return False

        self._last_check_time = now

        if self.watchdog is None:
            return False

        try:
            from mobile_core.exceptions import PopupIntercepted, RiskControlTriggered

            self.watchdog.check_screen(screen)
            return False  # 无弹窗

        except PopupIntercepted as e:
            self._popup_count += 1
            logger.warning(
                f"[Watchdog] Popup intercepted and dismissed: {e} "
                f"(total: {self._popup_count})"
            )
            return True  # 弹窗已处理，重新截屏

        except RiskControlTriggered:
            logger.critical(
                "🚨 [Watchdog] RISK CONTROL TRIGGERED — stopping pipeline!"
            )
            engine.stop()
            raise  # 让 engine.run() 捕获并终止

        except Exception as e:
            logger.error(f"[Watchdog] Unexpected error: {e}")
            return False


class LoopDetectorMiddleware(PipelineMiddleware):
    """
    卡死检测中间件。

    包装现有 LoopDetector (MD5 屏幕指纹滑窗检测)。
    当检测到连续多帧画面不变 + 动作无效时，触发恢复操作:
    1. 按返回键
    2. 或导航到首页

    解决的问题:
        loop_detector 当前仅在 farmer.py 中使用。
        中间件方式让截流、养号、初始化都享受防卡死保护。
    """

    def __init__(self, loop_detector, max_stuck_count: int = 3):
        """
        Args:
            loop_detector: LoopDetector 实例
            max_stuck_count: 连续卡死 N 次后尝试恢复
        """
        self.loop_detector = loop_detector
        self.max_stuck_count = max_stuck_count
        self._stuck_counter = 0

    def on_screen_captured(self, screen, engine: PipelineExecutor) -> bool:
        """每帧截屏后: 更新屏幕指纹。"""
        if self.loop_detector is None:
            return False

        self.loop_detector.update_screen(screen)
        return False

    def on_action_executed(self, node: PipelineNode, success: bool,
                           engine: PipelineExecutor):
        """动作执行后: 记录并检测卡死。"""
        if self.loop_detector is None:
            return

        self.loop_detector.record_action(node.action.type.value, node.name)

        if self.loop_detector.is_stuck():
            self._stuck_counter += 1
            suggestion = self.loop_detector.get_suggestion()
            logger.warning(
                f"[LoopDetector] Screen stuck detected! "
                f"Count: {self._stuck_counter}/{self.max_stuck_count}. "
                f"Suggestion: {suggestion}"
            )

            if self._stuck_counter >= self.max_stuck_count:
                logger.warning("[LoopDetector] Max stuck count reached, pressing back")
                try:
                    engine.driver.press_back()
                    time.sleep(1.0)
                except Exception as e:
                    logger.error(f"[LoopDetector] Recovery failed: {e}")
                self._stuck_counter = 0
                self.loop_detector.clear()
        else:
            self._stuck_counter = 0


class LoggingMiddleware(PipelineMiddleware):
    """
    操作日志中间件。

    参考 Airtest @logwrap 模式:
    记录每个节点的命中、动作执行、错误到结构化 JSON 日志。
    后期可用 Jinja2 渲染为 HTML 报告 (带截图)。

    日志格式:
    {
        "timestamp": "2026-06-11T22:30:00",
        "event": "node_hit|action_done|action_fail|error",
        "node": "Find_CommentInput",
        "confidence": 0.85,
        "position": [270, 1050],
        "duration_ms": 1234
    }
    """

    def __init__(self, log_file: Optional[str] = None,
                 save_screenshots: bool = False,
                 screenshot_dir: Optional[str] = None):
        """
        Args:
            log_file: JSON-line 日志文件路径 (None = 仅 logger 输出)
            save_screenshots: 是否保存每步截图
            screenshot_dir: 截图保存目录
        """
        self.log_file = log_file
        self.save_screenshots = save_screenshots
        self.screenshot_dir = screenshot_dir
        self._log_entries = []
        self._step_start_time = 0.0

    def on_node_hit(self, node: PipelineNode, result: RecognitionResult,
                    engine: PipelineExecutor):
        """记录节点命中。"""
        self._step_start_time = time.time()

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "node_hit",
            "node": node.name,
            "description": node.description,
            "confidence": round(result.confidence, 3) if result else 0,
            "position": list(result.position) if result and result.position else None,
            "text": result.text if result else None,
        }
        self._append_log(entry)

        # 保存截图
        if self.save_screenshots and self.screenshot_dir:
            self._save_screenshot(engine, node.name, "hit")

    def on_action_executed(self, node: PipelineNode, success: bool,
                           engine: PipelineExecutor):
        """记录动作执行。"""
        duration_ms = int((time.time() - self._step_start_time) * 1000)

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "action_done" if success else "action_fail",
            "node": node.name,
            "action_type": node.action.type.value,
            "success": success,
            "duration_ms": duration_ms,
        }
        self._append_log(entry)

    def on_error(self, node: Optional[PipelineNode], error: Exception,
                 engine: PipelineExecutor):
        """记录错误。"""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "error",
            "node": node.name if node else "unknown",
            "error": str(error),
            "error_type": type(error).__name__,
        }
        self._append_log(entry)

        # 错误时总是保存截图
        if self.screenshot_dir:
            self._save_screenshot(engine, node.name if node else "error", "error")

    def get_log_entries(self) -> list:
        """获取所有日志条目 (用于报告生成)。"""
        return self._log_entries.copy()

    def _append_log(self, entry: dict):
        """追加日志条目。"""
        self._log_entries.append(entry)

        # 写入文件
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(f"Failed to write log entry: {e}")

    def _save_screenshot(self, engine: PipelineExecutor,
                         node_name: str, suffix: str):
        """保存当前截图。"""
        try:
            import os
            import cv2

            os.makedirs(self.screenshot_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{node_name}_{suffix}.png"
            filepath = os.path.join(self.screenshot_dir, filename)

            screen = engine.driver.screenshot()
            if screen is not None:
                cv2.imwrite(filepath, screen)
        except Exception as e:
            logger.error(f"Failed to save screenshot: {e}")
