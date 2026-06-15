"""
Pipeline 动作系统 — Unified Action Provider (统一动作提供者)。

设计模式: Strategy Pattern
    ActionRegistry 持有所有 ActionProvider，按 ActionType 分派。
    每个 Provider 封装一种原子操作（点击、滑动、输入、导航等）。

集成组件:
    - AgentlessMinitouchDriver: 物理触控 (tap, swipe, double_tap, press_back)
    - XHSNavigator: 页面导航 (go_home, go_search, go_profile, go_back)
    - KeyboardVisionTyping: 视觉拼音输入
    - DeviceOptimizer: 飞行模式 IP 轮换
    - EngineConfig: 全局配置

所有文本字段通过 AnchorStore.resolve() 解析 {{anchor.xxx}} / {{context.xxx}} 变量。
"""

from __future__ import annotations

import importlib
import os
import random
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from mobile_core.logger import get_logger
from mobile_core.pipeline.models import (
    ActionSpec,
    ActionType,
    AnchorStore,
    RecognitionResult,
)

if TYPE_CHECKING:
    from mobile_core.agentless_driver import AgentlessMinitouchDriver
    from mobile_core.navigator import XHSNavigator
    from mobile_core.keyboard_vision import KeyboardVisionTyping
    from automation_engine.config import EngineConfig

logger = get_logger("pipeline.actions")


# ============================================================
#  工具函数
# ============================================================

def resolve_delay(delay_spec: Any) -> float:
    """
    将延迟规格转为实际延迟秒数。

    支持格式:
        - int / float: 固定毫秒数 → 转为秒
        - [min, max]: 随机范围毫秒 → 转为秒
        - None: 返回 0.0

    Returns:
        延迟秒数 (float)。
    """
    if delay_spec is None:
        return 0.0

    if isinstance(delay_spec, (list, tuple)) and len(delay_spec) >= 2:
        ms = random.uniform(float(delay_spec[0]), float(delay_spec[1]))
    else:
        ms = float(delay_spec)

    return max(0.0, ms / 1000.0)


def resolve_target(
    spec: ActionSpec,
    reco_result: Optional[RecognitionResult],
    anchors: AnchorStore,
) -> Optional[Tuple[int, int]]:
    """
    从 ActionSpec.target 解析点击坐标 (x, y)。

    策略:
        - True (bool): 使用识别结果的 position 字段
        - [x, y] (list): 直接像素坐标
        - str: 通过 AnchorStore.resolve() 解析变量引用,
               期望返回 [x, y] 或 (x, y)
        - None: 返回 None (由调用方处理)

    偏移 (spec.offset) 在最终坐标上叠加。

    Returns:
        (x, y) 像素坐标，或 None 若无法解析。
    """
    target = spec.target
    x: Optional[int] = None
    y: Optional[int] = None

    # --- bool: 使用识别结果位置 ---
    if target is True:
        if reco_result and reco_result.position:
            x, y = reco_result.position
        elif reco_result and reco_result.box:
            # 从 bounding box 中心推算
            bx, by, bw, bh = reco_result.box
            x, y = bx + bw // 2, by + bh // 2
        else:
            logger.warning("Target=True 但识别结果无 position/box，无法定位")
            return None

    # --- list/tuple: 直接坐标 ---
    elif isinstance(target, (list, tuple)) and len(target) >= 2:
        x, y = int(target[0]), int(target[1])

    # --- str: Anchor 引用 ---
    elif isinstance(target, str):
        resolved = anchors.resolve(target)
        if isinstance(resolved, (list, tuple)) and len(resolved) >= 2:
            x, y = int(resolved[0]), int(resolved[1])
        else:
            logger.warning(f"Anchor 引用 '{target}' 解析结果不是坐标: {resolved}")
            return None

    else:
        # None 或其他类型
        return None

    # --- 叠加偏移 ---
    if spec.offset and len(spec.offset) >= 2:
        x += spec.offset[0]
        y += spec.offset[1]

    return (x, y)


# ============================================================
#  抽象基类
# ============================================================

class ActionProvider(ABC):
    """
    动作提供者基类 (Strategy 接口)。

    每种 ActionType 对应一个 ActionProvider 子类。
    execute() 返回 True 表示执行成功，False 表示失败。
    """

    @abstractmethod
    def execute(
        self,
        spec: ActionSpec,
        reco_result: Optional[RecognitionResult],
        anchors: AnchorStore,
    ) -> bool:
        """
        执行动作。

        Args:
            spec:        动作配置规格
            reco_result: 前序识别结果 (可能为 None)
            anchors:     Pipeline 动态变量存储

        Returns:
            True = 成功, False = 失败
        """
        ...


# ============================================================
#  具体 Provider 实现
# ============================================================

class DoNothingAction(ActionProvider):
    """空操作 — 仅识别、不执行 (DO_NOTHING)。"""

    def execute(self, spec, reco_result, anchors) -> bool:
        logger.debug("DoNothing: 跳过执行")
        return True


class TapAction(ActionProvider):
    """
    单击操作 (TAP)。

    解析目标坐标后调用 driver.physical_tap()。
    physical_tap 内部已包含 Fitts 定律噪声 (±15px)。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        pos = resolve_target(spec, reco_result, anchors)
        if pos is None:
            logger.error("TapAction: 无法解析点击目标")
            return False

        x, y = pos
        logger.info(f"TapAction: physical_tap({x}, {y})")
        self.driver.physical_tap(x, y)
        return True


class DoubleTapAction(ActionProvider):
    """
    双击操作 (DOUBLE_TAP) — 常用于点赞。

    调用 driver.physical_double_tap()，内部含 Fitts 噪声和两次点击间隔。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        pos = resolve_target(spec, reco_result, anchors)
        if pos is None:
            logger.error("DoubleTapAction: 无法解析点击目标")
            return False

        x, y = pos
        logger.info(f"DoubleTapAction: physical_double_tap({x}, {y})")
        self.driver.physical_double_tap(x, y)
        return True


class SwipeAction(ActionProvider):
    """
    精确滑动 (SWIPE)。

    使用 spec.begin / spec.end 作为起止坐标，
    调用 driver.physical_swipe() (Cubic Bézier + Ease-Out 惯性)。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        begin = spec.begin
        end = spec.end

        if not begin or not end or len(begin) < 2 or len(end) < 2:
            logger.error("SwipeAction: begin/end 坐标缺失或格式错误")
            return False

        sx, sy = int(begin[0]), int(begin[1])
        ex, ey = int(end[0]), int(end[1])

        logger.info(f"SwipeAction: physical_swipe({sx}, {sy} → {ex}, {ey})")
        self.driver.physical_swipe(sx, sy, ex, ey)
        return True


class HumanSwipeAction(ActionProvider):
    """
    人性化滑动 (HUMAN_SWIPE)。

    使用 spec.direction (up/down/left/right)，
    调用 driver.human_swipe()，内含犹豫回滚概率 + 贝塞尔曲线。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        direction = spec.direction or "down"
        logger.info(f"HumanSwipeAction: 真人滑动('{direction}')")
        self.driver.human_swipe(direction=direction)
        return True


class InputTextAction(ActionProvider):
    """
    文本输入 (INPUT_TEXT)。

    根据 spec.mode 选择输入方式:
        - 'vision':    调用 keyboard_vision.type_chinese() (逐字拼音 + OCR 候选)
        - 'clipboard': ADB broadcast 剪贴板粘贴 (需要 ADBKeyboard)

    文本字段支持 {{anchor.xxx}} 变量解析。
    """

    def __init__(
        self,
        driver: AgentlessMinitouchDriver,
        keyboard_vision: Optional[KeyboardVisionTyping],
    ):
        self.driver = driver
        self.keyboard_vision = keyboard_vision

    def execute(self, spec, reco_result, anchors) -> bool:
        text = anchors.resolve(spec.text) if spec.text else None
        if not text:
            logger.error("InputTextAction: text 为空")
            return False

        mode = spec.mode or "clipboard"
        logger.info(f"InputTextAction: mode='{mode}', text='{text[:30]}...'")

        if mode == "vision":
            if not self.keyboard_vision:
                logger.error("InputTextAction: keyboard_vision 未注入，无法使用 vision 模式")
                return False
            self.keyboard_vision.type_chinese(str(text))
        else:
            # clipboard 模式: ADB broadcast (需要 ADBKeyboard 或 Clipper)
            self._clipboard_input(str(text))

        return True

    def _clipboard_input(self, text: str):
        """通过 Stealth IME 输入文本（替代 ADB_INPUT_TEXT 广播）。"""
        try:
            if hasattr(self.driver, 'type_text'):
                self.driver.type_text(text, human_like=False)
            elif hasattr(self.driver, 'ime_client') and self.driver.ime_client:
                self.driver.ime_client.type_text_fast(text)
            else:
                logger.error("No stealth input channel available for clipboard input")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Stealth IME input 失败: {e}")


class ClipboardInputAction(ActionProvider):
    """
    剪贴板粘贴输入 (CLIPBOARD_INPUT)。

    专用于快速粘贴场景，支持 {{anchor.xxx}} 变量解析。
    通过 ADB broadcast 发送文本到 ADBKeyboard。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        text = anchors.resolve(spec.text) if spec.text else None
        if not text:
            logger.error("ClipboardInputAction: text 为空")
            return False

        logger.info(f"ClipboardInputAction: 粘贴文本 '{str(text)[:30]}...'")
        try:
            if hasattr(self.driver, 'type_text'):
                self.driver.type_text(str(text), human_like=False)
            elif hasattr(self.driver, 'ime_client') and self.driver.ime_client:
                self.driver.ime_client.type_text_fast(str(text))
            else:
                logger.error("No stealth input channel available")
                return False
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"ClipboardInputAction 失败: {e}")
            return False


class PressBackAction(ActionProvider):
    """
    返回键 (PRESS_BACK)。

    调用 driver.press_back()，内含 human_sleep 延迟。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        logger.info("PressBackAction: press_back()")
        self.driver.press_back()
        return True


class NavigateAction(ActionProvider):
    """
    页面导航 (NAVIGATE)。

    根据 spec.target_page 调用 navigator 的对应方法:
        - home    → navigator.go_home()
        - search  → navigator.go_search()
        - profile → navigator.go_profile()
        - back    → navigator.go_back()
    """

    def __init__(self, navigator: XHSNavigator):
        self.navigator = navigator

    def execute(self, spec, reco_result, anchors) -> bool:
        target_page = anchors.resolve(spec.target_page) if spec.target_page else None
        if not target_page:
            logger.error("NavigateAction: target_page 未指定")
            return False

        target_page = str(target_page).lower().strip()
        logger.info(f"NavigateAction: 导航到 '{target_page}'")

        if target_page == "home":
            result = self.navigator.go_home()
        elif target_page == "search":
            result = self.navigator.go_search()
        elif target_page == "profile":
            result = self.navigator.go_profile()
        elif target_page == "back":
            self.navigator.go_back()
            result = True  # go_back 无返回值
        else:
            logger.error(f"NavigateAction: 未知 target_page '{target_page}'")
            return False

        return bool(result)


class LaunchAppAction(ActionProvider):
    """
    启动 App (LAUNCH_APP)。

    调用 driver.ensure_app_foreground()，默认包名 com.xingin.xhs。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        package = spec.package or "com.xingin.xhs"
        package = str(anchors.resolve(package))
        logger.info(f"LaunchAppAction: 确保应用在前台运行('{package}')")
        self.driver.ensure_app_foreground(package_name=package)
        return True


class StopAppAction(ActionProvider):
    """
    停止 App (STOP_APP)。

    通过 ADB force-stop 强制停止应用。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        package = spec.package or "com.xingin.xhs"
        package = str(anchors.resolve(package))
        logger.info(f"StopAppAction: force-stop '{package}'")

        cmd = self.driver.adb_prefix + ["shell", "am", "force-stop", package]
        try:
            subprocess.run(cmd, timeout=10, capture_output=True)
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.error(f"StopAppAction 失败: {e}")
            return False


class IpRotateAction(ActionProvider):
    """
    飞行模式 IP 轮换 (IP_ROTATE)。

    延迟加载 DeviceOptimizer 并调用 toggle_airplane_mode()。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver
        self._optimizer = None

    def _get_optimizer(self):
        """延迟初始化 DeviceOptimizer (避免循环依赖)。"""
        if self._optimizer is None:
            from mobile_core.device_optimizer import DeviceOptimizer
            self._optimizer = DeviceOptimizer(serial=self.driver.serial)
        return self._optimizer

    def execute(self, spec, reco_result, anchors) -> bool:
        logger.info("IpRotateAction: toggle_airplane_mode()")
        try:
            optimizer = self._get_optimizer()
            optimizer.toggle_airplane_mode()
            return True
        except Exception as e:
            logger.error(f"IpRotateAction 失败: {e}")
            return False


class LlmGenerateAction(ActionProvider):
    """
    LLM 文本生成 (LLM_GENERATE)。

    调用配置中的 LLM API 生成文本 (如评论、标题)，
    将结果存入 anchors[spec.output_anchor]。

    使用 spec.prompt_template 作为 prompt 内容，
    spec.context_from 提供上下文变量。
    """

    def __init__(self, config: EngineConfig):
        self.config = config

    def execute(self, spec, reco_result, anchors) -> bool:
        import requests

        # 解析 prompt 模板和上下文
        prompt_template = anchors.resolve(spec.prompt_template) if spec.prompt_template else None
        if not prompt_template:
            logger.error("LlmGenerateAction: prompt_template 未指定")
            return False

        context_text = ""
        if spec.context_from:
            context_text = str(anchors.resolve(spec.context_from) or "")

        # 构建 prompt (将 {content} 占位符替换)
        prompt = str(prompt_template)
        if "{content}" in prompt:
            prompt = prompt.replace("{content}", context_text)
        if "{keyword}" in prompt:
            keyword = str(anchors.get("current_keyword", ""))
            prompt = prompt.replace("{keyword}", keyword)

        output_anchor = spec.output_anchor
        if not output_anchor:
            logger.error("LlmGenerateAction: output_anchor 未指定")
            return False

        # 从配置中读取 LLM 参数 (优先使用 intercept，兜底用 agent)
        cfg_intercept = self.config.intercept
        cfg_agent = self.config.agent

        endpoint = cfg_intercept.llm_endpoint or cfg_agent.llm_endpoint
        api_key = cfg_intercept.llm_api_key or cfg_agent.llm_api_key
        model = cfg_intercept.llm_model or cfg_agent.llm_model

        if not endpoint or not api_key:
            logger.error("LlmGenerateAction: LLM endpoint/api_key 未配置")
            return False

        logger.info(f"LlmGenerateAction: 调用 LLM (model={model})")

        messages = [
            {"role": "system", "content": "你是一个真实的小红书用户。请根据要求生成自然的文本。"},
            {"role": "user", "content": prompt},
        ]

        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 100,
                    "temperature": 0.9,
                },
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()
            generated = result["choices"][0]["message"]["content"].strip()

            logger.info(f"LlmGenerateAction: 生成结果 '{generated[:50]}...'")
            anchors.set(output_anchor, generated)
            return True

        except Exception as e:
            logger.error(f"LlmGenerateAction 失败: {e}")
            return False


class WaitAction(ActionProvider):
    """
    人性化等待 (WAIT)。

    支持固定延迟和 [min, max] 随机范围 (毫秒)。
    使用 driver.human_sleep 添加正态分布抖动。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        duration = spec.duration
        # 支持 anchor 引用
        if isinstance(duration, str):
            duration = anchors.resolve(duration)

        delay_sec = resolve_delay(duration)
        if delay_sec <= 0:
            logger.debug("WaitAction: duration=0，跳过")
            return True

        logger.info(f"WaitAction: 等待 {delay_sec:.2f}s")

        # 使用 human_sleep 添加轻微抖动
        # human_sleep(mu, sigma) 其中 mu 和 sigma 都是秒
        sigma = min(delay_sec * 0.15, 1.0)  # 抖动不超过 15% 且最大 1s
        self.driver.human_sleep(mu=delay_sec, sigma=sigma)
        return True


class ScreencapSaveAction(ActionProvider):
    """
    截图保存 (SCREENCAP_SAVE)。

    调用 driver.screenshot() 后保存到 spec.filename 指定路径。
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        import cv2

        filename = anchors.resolve(spec.filename) if spec.filename else None
        if not filename:
            # 自动生成文件名
            timestamp = int(time.time() * 1000)
            filename = f"screencap_{timestamp}.png"

        filename = str(filename)
        logger.info(f"ScreencapSaveAction: 保存截图到 '{filename}'")

        try:
            img = self.driver.screenshot()
            # 确保目录存在
            dir_path = os.path.dirname(filename)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            cv2.imwrite(filename, img)
            return True
        except Exception as e:
            logger.error(f"ScreencapSaveAction 失败: {e}")
            return False


class CustomAction(ActionProvider):
    """
    自定义动作 (CUSTOM)。

    动态导入并调用 spec.handler 指定的 Python 函数。
    handler 格式: 'module.path.function_name'

    函数签名:
        def handler(driver, spec, reco_result, anchors, **params) -> bool
    """

    def __init__(self, driver: AgentlessMinitouchDriver):
        self.driver = driver

    def execute(self, spec, reco_result, anchors) -> bool:
        handler_path = spec.handler
        if not handler_path:
            logger.error("CustomAction: handler 未指定")
            return False

        logger.info(f"CustomAction: 调用 '{handler_path}'")

        try:
            # 分离 module 路径和函数名
            parts = handler_path.rsplit(".", 1)
            if len(parts) != 2:
                logger.error(f"CustomAction: handler 格式错误 (期望 'module.func'): {handler_path}")
                return False

            module_path, func_name = parts
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name)

            # 调用自定义函数
            params = spec.params or {}
            result = func(
                driver=self.driver,
                spec=spec,
                reco_result=reco_result,
                anchors=anchors,
                **params,
            )
            return bool(result) if result is not None else True

        except Exception as e:
            logger.error(f"CustomAction 执行异常: {e}", exc_info=True)
            return False


# ============================================================
#  ActionRegistry — 统一调度中心
# ============================================================

class ActionRegistry:
    """
    动作注册表 — 管理所有 ActionProvider 并按 ActionType 分派。

    初始化时根据注入的组件创建所有 Provider 实例，
    execute() 自动查找对应 Provider 并执行。

    Usage:
        registry = ActionRegistry(driver, navigator, keyboard_vision, config)
        success = registry.execute(action_spec, reco_result, anchors)
    """

    def __init__(
        self,
        driver: AgentlessMinitouchDriver,
        navigator: Optional[XHSNavigator] = None,
        keyboard_vision: Optional[KeyboardVisionTyping] = None,
        config: Optional[EngineConfig] = None,
    ):
        """
        初始化动作注册表。

        Args:
            driver:          AgentlessMinitouchDriver 实例 (必需)
            navigator:       XHSNavigator 实例 (可选，NavigateAction 需要)
            keyboard_vision: KeyboardVisionTyping 实例 (可选，InputText vision 模式需要)
            config:          EngineConfig 实例 (可选，LlmGenerateAction 需要)
        """
        self.driver = driver
        self.navigator = navigator
        self.keyboard_vision = keyboard_vision
        self.config = config

        # 构建 ActionType → Provider 映射
        self._providers: dict[ActionType, ActionProvider] = {
            ActionType.DO_NOTHING: DoNothingAction(),
            ActionType.TAP: TapAction(driver),
            ActionType.DOUBLE_TAP: DoubleTapAction(driver),
            ActionType.SWIPE: SwipeAction(driver),
            ActionType.HUMAN_SWIPE: HumanSwipeAction(driver),
            ActionType.INPUT_TEXT: InputTextAction(driver, keyboard_vision),
            ActionType.CLIPBOARD_INPUT: ClipboardInputAction(driver),
            ActionType.PRESS_BACK: PressBackAction(driver),
            ActionType.LAUNCH_APP: LaunchAppAction(driver),
            ActionType.STOP_APP: StopAppAction(driver),
            ActionType.IP_ROTATE: IpRotateAction(driver),
            ActionType.WAIT: WaitAction(driver),
            ActionType.SCREENCAP_SAVE: ScreencapSaveAction(driver),
            ActionType.CUSTOM: CustomAction(driver),
        }

        # 有条件注册的 Provider
        if navigator:
            self._providers[ActionType.NAVIGATE] = NavigateAction(navigator)

        if config:
            self._providers[ActionType.LLM_GENERATE] = LlmGenerateAction(config)

        logger.info(
            f"ActionRegistry 初始化完成: {len(self._providers)} 个 Provider 已注册",
            extra={"registered_types": [t.value for t in self._providers]},
        )

    def execute(
        self,
        spec: ActionSpec,
        reco_result: Optional[RecognitionResult],
        anchors: AnchorStore,
    ) -> bool:
        """
        分派并执行动作。

        流程:
            1. 查找 spec.type 对应的 Provider
            2. 解析 spec 中的所有文本字段 ({{anchor.xxx}} 等)
            3. 调用 Provider.execute()
            4. 失败时尝试 fallback_keyevent 兜底

        Args:
            spec:        动作配置规格
            reco_result: 前序识别结果
            anchors:     Pipeline 动态变量存储

        Returns:
            True = 成功, False = 失败
        """
        action_type = spec.type
        provider = self._providers.get(action_type)

        if provider is None:
            logger.error(f"ActionRegistry: 未注册的动作类型 '{action_type.value}'")
            return False

        # 预解析文本字段 (避免每个 Provider 重复解析)
        spec = self._resolve_spec_fields(spec, anchors)

        try:
            success = provider.execute(spec, reco_result, anchors)
        except Exception as e:
            if e.__class__.__name__ == "PreconditionError":
                logger.critical(f"前置条件不满足 ({e})。自动终止脚本。")
                import os
                os._exit(1)
            logger.error(
                f"ActionRegistry: {action_type.value} 执行异常: {e}",
                exc_info=True,
            )
            success = False

        # 失败时尝试 fallback keyevent 兜底（通过 注入隧道）
        if not success and spec.fallback_keyevent is not None:
            logger.warning(
                f"ActionRegistry: 主动作失败，执行 fallback_keyevent={spec.fallback_keyevent}"
            )
            try:
                if hasattr(self.driver, 'inject_keyevent'):
                    self.driver.inject_keyevent(spec.fallback_keyevent)
                else:
                    logger.error("Driver has no inject_keyevent method, cannot execute fallback")
                success = True  # 兜底视为成功
            except Exception as e:
                logger.error(f"Fallback keyevent 也失败: {e}")

        return success

    def _resolve_spec_fields(self, spec: ActionSpec, anchors: AnchorStore) -> ActionSpec:
        """
        预解析 ActionSpec 中可能含有 {{anchor.xxx}} 引用的文本字段。

        不修改原始 spec，而是返回一个字段已解析的浅拷贝。
        只解析字符串类型的字段，非字符串字段保持原样。
        """
        import copy
        resolved = copy.copy(spec)

        # 需要解析的字符串字段列表
        str_fields = [
            "text", "prompt_template", "output_anchor",
            "context_from", "target_page", "package",
            "filename", "handler", "direction",
        ]
        for field_name in str_fields:
            val = getattr(resolved, field_name, None)
            if isinstance(val, str):
                setattr(resolved, field_name, anchors.resolve(val))

        # target 字段特殊处理 (可能是 str、bool、list)
        if isinstance(resolved.target, str):
            resolved.target = anchors.resolve(resolved.target)

        # duration 字段可能是 str 引用
        if isinstance(resolved.duration, str):
            resolved.duration = anchors.resolve(resolved.duration)

        return resolved

    def register(self, action_type: ActionType, provider: ActionProvider):
        """
        注册或替换一个 ActionProvider。

        用于扩展自定义动作类型或覆盖默认实现。

        Args:
            action_type: 动作类型枚举
            provider:    ActionProvider 实例
        """
        self._providers[action_type] = provider
        logger.info(f"ActionRegistry: 注册/替换 Provider '{action_type.value}'")

    def get_provider(self, action_type: ActionType) -> Optional[ActionProvider]:
        """获取指定类型的 Provider (用于单元测试或直接调用)。"""
        return self._providers.get(action_type)
