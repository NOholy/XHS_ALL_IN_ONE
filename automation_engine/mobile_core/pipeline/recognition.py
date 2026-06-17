"""
Unified Recognition Provider — Pipeline 统一识别分发系统。

设计模式: Strategy Pattern
    - RecognitionProvider (抽象基类) 定义统一接口
    - 每个 RecognitionType 对应一个具体 Provider
    - RecognitionRegistry 自动注册 + 按 type 分发

集成组件:
    - VisionEngine.find_template()      → TemplateMatchProvider
    - OCRClient.find_text()             → OCRTextProvider
    - LightPageDetector.detect_page_fast() → ActivityDetectProvider
    - VisionEngine.compute_screen_mse() → ScreenDiffProvider
    - farmer._verify_color_shift 逻辑   → ColorShiftProvider (移植)

ROI 机制:
    - 支持百分比 [0.0~1.0] 或像素值
    - 自动裁剪屏幕图像到 ROI 区域
    - 识别结果坐标会回映射到原始屏幕坐标系
"""

from __future__ import annotations

import importlib
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from mobile_core.logger import get_logger
from .models import (
    AnchorStore,
    RecognitionResult,
    RecognitionSpec,
    RecognitionType,
)

if TYPE_CHECKING:
    from mobile_core.vision import VisionEngine
    from mobile_core.ocr_client import OCRClient
    from mobile_core.page_detector import LightPageDetector
    from mobile_core.agentless_driver import AgentlessTouchDriver

logger = get_logger("recognition")


# ============================================================
#  ROI 辅助工具
# ============================================================

def apply_roi(
    screen: np.ndarray,
    roi: Optional[List[float]],
) -> Tuple[np.ndarray, int, int]:
    """
    将百分比/像素 ROI 裁剪应用到屏幕图像。

    Args:
        screen: 原始屏幕截图 (BGR numpy array, shape HxWxC).
        roi: [x, y, w, h] — 每个值若 <= 1.0 则视为屏幕百分比,
             否则视为像素值。None 表示全屏。

    Returns:
        (cropped_image, offset_x, offset_y)
        offset_x/y 是 ROI 左上角在原图中的像素偏移，
        用于将裁剪区域中的坐标回映射到全屏坐标系。
    """
    if roi is None or len(roi) < 4:
        return screen, 0, 0

    h_screen, w_screen = screen.shape[:2]
    raw_x, raw_y, raw_w, raw_h = roi[0], roi[1], roi[2], roi[3]

    # 百分比 → 像素转换: 0.0~1.0 范围视为百分比
    def _to_px(val: float, full: int) -> int:
        if 0.0 <= val <= 1.0:
            return int(val * full)
        return int(val)

    x = _to_px(raw_x, w_screen)
    y = _to_px(raw_y, h_screen)
    w = _to_px(raw_w, w_screen)
    h = _to_px(raw_h, h_screen)

    # 边界裁剪保护
    x = max(0, min(x, w_screen - 1))
    y = max(0, min(y, h_screen - 1))
    w = max(1, min(w, w_screen - x))
    h = max(1, min(h, h_screen - y))

    cropped = screen[y:y + h, x:x + w]
    return cropped, x, y


def _offset_result(
    result: RecognitionResult,
    offset_x: int,
    offset_y: int,
) -> RecognitionResult:
    """
    将 ROI 区域内的坐标偏移回原始屏幕坐标系。

    Args:
        result: 在裁剪区域内识别得到的结果。
        offset_x: ROI 左上角的 x 偏移。
        offset_y: ROI 左上角的 y 偏移。
    """
    if result is None or not result.matched:
        return result

    if result.position is not None:
        px, py = result.position
        result.position = (px + offset_x, py + offset_y)

    if result.box is not None and len(result.box) >= 4:
        bx, by, bw, bh = result.box[:4]
        result.box = [bx + offset_x, by + offset_y, bw, bh]

    return result


# ============================================================
#  抽象基类: RecognitionProvider
# ============================================================

class RecognitionProvider(ABC):
    """
    识别提供者抽象基类。

    每个 RecognitionType 对应一个具体实现。
    所有 Provider 共享相同的 recognize() 签名。
    """

    @abstractmethod
    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        """
        在屏幕截图上执行识别。

        Args:
            screen: BGR numpy array — 已经过 ROI 裁剪的图像
                   (或全屏图像，取决于调用链)。
            spec: 识别配置规格。
            anchors: 当前 Pipeline 的 Anchor 存储，
                     可用于读取上下文或写入中间变量。

        Returns:
            RecognitionResult — 命中则 matched=True，否则 None。
        """
        ...


# ============================================================
#  具体 Provider 实现
# ============================================================

class DirectHitProvider(RecognitionProvider):
    """
    DirectHit — 无条件命中。

    用于纯顺序执行节点，不依赖屏幕状态。
    返回 matched=True，position 设为屏幕中心。
    """

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        h, w = screen.shape[:2]
        return RecognitionResult(
            matched=True,
            position=(w // 2, h // 2),
            box=[0, 0, w, h],
            confidence=1.0,
        )


class TemplateMatchProvider(RecognitionProvider):
    """
    模板匹配 — 封装 VisionEngine.find_template()。

    使用 spec.template 指定模板名，spec.threshold 控制匹配阈值。
    """

    def __init__(self, vision: VisionEngine):
        self._vision = vision

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        if not spec.template:
            logger.warning("TemplateMatchProvider: spec.template is empty")
            return None

        threshold = spec.threshold if spec.threshold > 0 else 0.75

        # VisionEngine.find_template(screen_img, template_name, threshold)
        # Returns: {"x": cx, "y": cy, "conf": max_val} or None
        match = self._vision.find_template(screen, spec.template, threshold=threshold)

        if match is None:
            return None

        # 获取模板尺寸用于 box 计算
        tpl = self._vision.templates.get(spec.template)
        if tpl is not None:
            th, tw = tpl.shape[:2]
        else:
            tw, th = 0, 0

        return RecognitionResult(
            matched=True,
            position=(match["x"], match["y"]),
            box=[
                match["x"] - tw // 2,
                match["y"] - th // 2,
                tw,
                th,
            ],
            confidence=match["conf"],
        )


class OCRTextProvider(RecognitionProvider):
    """
    OCR 文字识别 — 封装 OCRClient.find_text() + 正则匹配。

    使用 spec.expected 作为正则表达式 (多个用 | 分隔)，
    spec.threshold 控制 OCR 置信度阈值。
    """

    def __init__(self, ocr: OCRClient):
        self._ocr = ocr

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        expected = anchors.resolve(spec.expected) if spec.expected else None
        if not expected:
            logger.warning("OCRTextProvider: spec.expected resolved to empty or is missing")
            return None
        
        expected = str(expected)
        threshold = spec.threshold if spec.threshold > 0 else 0.6

        # 先检查缓存
        ocr_cache = anchors.get("_ocr_cache")
        if ocr_cache is not None and "raw_results" in ocr_cache:
            raw_results = ocr_cache["raw_results"]
            parsed = ocr_cache["parsed"]
            logger.debug("OCRTextProvider: used cached OCR results")
        else:
            # 缓存未命中，执行全量 OCR
            try:
                raw_results = self._ocr.ocr_image(screen)
            except Exception as e:
                logger.error(f"OCRTextProvider: ocr_image failed: {e}")
                return None
            
            parsed = self._ocr.safe_parse_results(raw_results)
            
            # 更新缓存
            if ocr_cache is not None:
                ocr_cache["raw_results"] = raw_results
                ocr_cache["parsed"] = parsed

        # 编译正则模式 (expected 可能是 "确认|取消|跳过" 这样的多选)
        try:
            pattern = re.compile(expected)
        except re.error as e:
            logger.error(f"OCRTextProvider: invalid regex '{expected}': {e}")
            # 退化为子串匹配
            pattern = None

        # 遍历所有 OCR 结果，查找匹配
        best_match: Optional[RecognitionResult] = None
        best_conf: float = 0.0

        for box, text, conf in parsed:
            if conf < threshold:
                continue

            # 正则匹配或子串匹配
            if pattern is not None:
                if not pattern.search(text):
                    continue
            else:
                if expected not in text:
                    continue

            # 计算中心坐标 (box 是四个顶点 [[x,y], ...])
            if isinstance(box, (list, tuple)) and len(box) >= 4:
                x_center = int(sum(p[0] for p in box) / len(box))
                y_center = int(sum(p[1] for p in box) / len(box))
                # 计算 bounding box [x, y, w, h]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                bx, by = int(min(xs)), int(min(ys))
                bw, bh = int(max(xs) - min(xs)), int(max(ys) - min(ys))
            else:
                continue

            if conf > best_conf:
                best_conf = conf
                best_match = RecognitionResult(
                    matched=True,
                    position=(x_center, y_center),
                    box=[bx, by, bw, bh],
                    confidence=conf,
                    text=text,
                    raw=raw_results,
                )

        return best_match


class ColorShiftProvider(RecognitionProvider):
    """
    HSV 颜色变化检测 — 移植自 farmer._verify_color_shift() 逻辑。

    典型场景: 点赞(红色突增)、收藏(黄色突增)。

    需要两张截图: before (从 anchors 获取) 和 after (当前 screen)。
    Anchor 约定:
        - anchors.get("_screen_before") → 操作前截图
    Spec 参数:
        - spec.target_color: HSV 目标颜色 [H, S, V] (可选, 也支持 "red"/"yellow" 预设)
        - spec.color_range: HSV 容差 [dH, dS, dV] (可选)
        - spec.roi: 检测区域 (必须指定, 否则在全屏中检测)
    """

    # 预设颜色映射 (HSV 范围)
    _PRESETS: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {
        "red": [
            (np.array([0, 70, 50]), np.array([10, 255, 255])),
            (np.array([170, 70, 50]), np.array([180, 255, 255])),
        ],
        "yellow": [
            (np.array([15, 70, 50]), np.array([35, 255, 255])),
        ],
    }

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        import cv2

        img_before = anchors.get("_screen_before")
        img_after = screen

        if img_before is None:
            logger.warning("ColorShiftProvider: no '_screen_before' in anchors")
            return None

        if img_before.shape != img_after.shape:
            logger.warning("ColorShiftProvider: before/after shape mismatch")
            return None

        # 转 HSV
        hsv_before = cv2.cvtColor(img_before, cv2.COLOR_BGR2HSV)
        hsv_after = cv2.cvtColor(img_after, cv2.COLOR_BGR2HSV)

        # 确定颜色范围
        ranges = self._resolve_color_ranges(spec)
        if not ranges:
            logger.warning("ColorShiftProvider: no valid color ranges resolved")
            return None

        # 生成掩码 (支持多段 HSV 范围, 如红色跨 0/180 边界)
        mask_before = np.zeros(hsv_before.shape[:2], dtype=np.uint8)
        mask_after = np.zeros(hsv_after.shape[:2], dtype=np.uint8)

        for lower, upper in ranges:
            mask_before = cv2.bitwise_or(
                mask_before,
                cv2.inRange(hsv_before, lower, upper),
            )
            mask_after = cv2.bitwise_or(
                mask_after,
                cv2.inRange(hsv_after, lower, upper),
            )

        pixels_before = cv2.countNonZero(mask_before)
        pixels_after = cv2.countNonZero(mask_after)

        h_img, w_img = img_after.shape[:2]
        area = h_img * w_img

        # 突增判定: 目标颜色像素显著增加 (至少 3% 面积)
        threshold_pixels = area * 0.03
        shifted = pixels_after > (pixels_before + threshold_pixels)

        # 绝对值判定: 如果已经是目标颜色 (如已点过赞)
        is_already_target = pixels_after > (area * 0.15)

        matched = shifted or is_already_target

        logger.debug(
            f"ColorShift: before={pixels_before}, after={pixels_after}, "
            f"shifted={shifted}, already={is_already_target}, matched={matched}"
        )

        if matched:
            return RecognitionResult(
                matched=True,
                position=(w_img // 2, h_img // 2),
                box=[0, 0, w_img, h_img],
                confidence=1.0 if shifted else 0.8,
                raw={
                    "pixels_before": pixels_before,
                    "pixels_after": pixels_after,
                    "shifted": shifted,
                    "is_already_target": is_already_target,
                },
            )
        return None

    def _resolve_color_ranges(
        self, spec: RecognitionSpec
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        解析颜色范围配置。

        优先使用 spec.target_color + spec.color_range (自定义 HSV),
        其次查找预设 ("red", "yellow") 通过 spec.expected 指定。
        """
        # 方式 1: 自定义 HSV 范围
        if spec.target_color and spec.color_range:
            h, s, v = spec.target_color[:3]
            dh, ds, dv = spec.color_range[:3]
            lower = np.array([max(0, h - dh), max(0, s - ds), max(0, v - dv)])
            upper = np.array([min(180, h + dh), min(255, s + ds), min(255, v + dv)])
            return [(lower, upper)]

        # 方式 2: 预设颜色名 (通过 expected 字段)
        if spec.expected and spec.expected.lower() in self._PRESETS:
            return self._PRESETS[spec.expected.lower()]

        # 方式 3: target_color 有值但没有 color_range, 使用默认容差
        if spec.target_color:
            h, s, v = spec.target_color[:3]
            lower = np.array([max(0, h - 10), max(0, s - 50), max(0, v - 50)])
            upper = np.array([min(180, h + 10), min(255, s + 50), min(255, v + 50)])
            return [(lower, upper)]

        return []


class ActivityDetectProvider(RecognitionProvider):
    """
    Activity 页面检测 — 封装 LightPageDetector。

    通过 spec.expected 指定期望匹配的页面名:
        - "home_feed", "post_detail", "search_results" 等 → detect_page_fast()
        - "keyboard_visible" → is_keyboard_visible()

    matched=True 当检测到的页面包含 expected 字符串。
    """

    def __init__(self, page_detector: LightPageDetector):
        self._detector = page_detector

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        expected = anchors.resolve(spec.expected) if spec.expected else None
        if not expected:
            logger.warning("ActivityDetectProvider: spec.expected resolved to empty or is missing")
            return None

        expected_str = str(expected).strip().lower()

        # 特殊处理: 键盘可见性检测
        if expected_str in ("keyboard_visible", "keyboard", "is_keyboard_visible"):
            is_visible = self._detector.is_keyboard_visible()
            if is_visible:
                h, w = screen.shape[:2]
                return RecognitionResult(
                    matched=True,
                    position=(w // 2, h // 2),
                    confidence=1.0,
                    text="keyboard_visible",
                )
            return None

        # 通用页面检测: detect_page_fast()
        current_page = self._detector.detect_page_fast()

        # 支持正则匹配或子串匹配
        try:
            pattern = re.compile(expected_str)
            matched = pattern.search(current_page) is not None
        except re.error:
            matched = expected_str in current_page

        if matched:
            h, w = screen.shape[:2]
            return RecognitionResult(
                matched=True,
                position=(w // 2, h // 2),
                confidence=1.0,
                text=current_page,
            )

        logger.debug(
            f"ActivityDetect: expected='{expected_str}', "
            f"current='{current_page}', matched=False"
        )
        return None


class ScreenDiffProvider(RecognitionProvider):
    """
    屏幕变化量检测 — 封装 VisionEngine.compute_screen_mse()。

    反向逻辑: matched=True 当 MSE < threshold (屏幕无变化 = 卡死)。
    用于检测页面卡死、加载超时等场景。

    Anchor 约定:
        - anchors.get("_screen_before") → 上一帧截图
    Spec 参数:
        - spec.mse_threshold: MSE 阈值 (默认 1.0)
    """

    def __init__(self, vision: VisionEngine):
        self._vision = vision

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        img_before = anchors.get("_screen_before")
        img_after = screen

        if img_before is None:
            logger.debug("ScreenDiffProvider: no '_screen_before', skipping")
            return None

        # compute_screen_mse(img_a, img_b, roi=None)
        # Returns float MSE. < 1.0 = identical/no change.
        mse = self._vision.compute_screen_mse(img_before, img_after)

        threshold = spec.mse_threshold if spec.mse_threshold > 0 else 1.0

        # 反向逻辑: MSE 低于阈值 → 屏幕没变化 → matched (卡死)
        is_stuck = mse < threshold

        logger.debug(
            f"ScreenDiff: mse={mse:.4f}, threshold={threshold}, "
            f"stuck={is_stuck}"
        )

        if is_stuck:
            h, w = screen.shape[:2]
            return RecognitionResult(
                matched=True,
                position=(w // 2, h // 2),
                confidence=1.0 - min(1.0, mse / threshold),
                raw={"mse": mse, "threshold": threshold},
            )
        return None


class AndProvider(RecognitionProvider):
    """
    组合识别: 所有子规格必须全部命中。

    使用 spec.all_of 列表中的每个 dict 构造子 RecognitionSpec,
    递归调用 RecognitionRegistry.recognize() 解析。

    返回最后一个子规格的识别结果。
    """

    def __init__(self, registry: RecognitionRegistry):
        self._registry = registry

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        if not spec.all_of:
            logger.warning("AndProvider: spec.all_of is empty")
            return None

        last_result: Optional[RecognitionResult] = None

        for i, sub_dict in enumerate(spec.all_of):
            sub_spec = _dict_to_spec(sub_dict)
            result = self._registry.recognize(screen, sub_spec, anchors)

            if result is None or not result.matched:
                logger.debug(f"AndProvider: sub-spec [{i}] failed, aborting")
                return None

            last_result = result

        return last_result


class OrProvider(RecognitionProvider):
    """
    组合识别: 任一子规格命中即返回。

    使用 spec.any_of 列表中的每个 dict 构造子 RecognitionSpec,
    递归调用 RecognitionRegistry.recognize() 解析。

    返回首个命中的识别结果。
    """

    def __init__(self, registry: RecognitionRegistry):
        self._registry = registry

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        if not spec.any_of:
            logger.warning("OrProvider: spec.any_of is empty")
            return None

        for i, sub_dict in enumerate(spec.any_of):
            sub_spec = _dict_to_spec(sub_dict)
            result = self._registry.recognize(screen, sub_spec, anchors)

            if result is not None and result.matched:
                logger.debug(f"OrProvider: sub-spec [{i}] matched")
                return result

        return None


class CustomProvider(RecognitionProvider):
    """
    自定义识别 — 动态导入并调用 spec.handler 指定的 Python 函数。

    handler 格式: "module.path.function_name"
    函数签名: fn(screen: np.ndarray, spec: RecognitionSpec,
                  anchors: AnchorStore) -> Optional[RecognitionResult]
    """

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        if not spec.handler:
            logger.warning("CustomProvider: spec.handler is empty")
            return None

        try:
            func = _import_handler(spec.handler)
        except Exception as e:
            logger.error(f"CustomProvider: failed to import '{spec.handler}': {e}")
            return None

        try:
            result = func(screen, spec, anchors)
            if isinstance(result, RecognitionResult):
                return result
            # 如果返回 bool, 自动包装
            if isinstance(result, bool) and result:
                h, w = screen.shape[:2]
                return RecognitionResult(
                    matched=True,
                    position=(w // 2, h // 2),
                    confidence=1.0,
                )
            return None
        except Exception as e:
            logger.error(f"CustomProvider: handler '{spec.handler}' raised: {e}")
            return None


class YoloDetectProvider(RecognitionProvider):
    """
    YOLO 目标检测 — 支持:
      1. 纯 YOLO 目标定位 (例如找 点赞/收藏 图标)
      2. YOLO + OCR 二次验证 (用于动态文本按钮，定位后裁剪检测框进行 OCR 确认)
      3. Anchor 锚点兜底定位 (当目标不存在时，以另一个目标作为锚点偏移定位)
    """

    def __init__(
        self,
        driver: Optional[AgentlessTouchDriver] = None,
        ocr: Optional[OCRClient] = None,
    ):
        self._driver = driver
        self._ocr = ocr

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        if self._driver is None:
            raise RuntimeError("🚨 CRITICAL: YoloDetectProvider: driver is None. YOLO_DETECT cannot run.")

        # 变量解析
        yolo_class = anchors.resolve(spec.yolo_class) if spec.yolo_class else None
        if not yolo_class:
            raise ValueError("🚨 CRITICAL: YoloDetectProvider: 'yolo_class' must be specified in recognition spec.")

        yolo_class = str(yolo_class)
        fallback_anchor = anchors.resolve(spec.fallback_anchor) if spec.fallback_anchor else None
        ocr_text = anchors.resolve(spec.ocr_text) if spec.ocr_text else None
        
        offset_x = spec.safe_offset[0] if spec.safe_offset and len(spec.safe_offset) >= 1 else 0
        offset_y = spec.safe_offset[1] if spec.safe_offset and len(spec.safe_offset) >= 2 else 0

        # 1. 尝试直接 YOLO 检测主目标
        box, conf = self._driver.yolo_detect(yolo_class, screen_image=screen, conf_threshold=0.6)
        
        if box:
            logger.info(f"YoloDetectProvider: YOLO primary target '{yolo_class}' found at {box} (conf={conf:.2f}).")
            
            # 如果配置了 OCR 二次验证，则进行裁剪和 OCR 识别
            if ocr_text:
                if self._ocr is None:
                    raise RuntimeError("🚨 CRITICAL: YoloDetectProvider: OCR client not provided, cannot verify ocr_text")
                
                # 裁剪检测到的 YOLO 区域 (增加 10px 边距以确保边缘字符完整包含)
                x1, y1, x2, y2 = box
                h_img, w_img = screen.shape[:2]
                padding = 10
                x1 = max(0, min(x1 - padding, w_img - 1))
                y1 = max(0, min(y1 - padding, h_img - 1))
                x2 = max(1, min(x2 + padding, w_img))
                y2 = max(1, min(y2 + padding, h_img))
                
                cropped_btn = screen[y1:y2, x1:x2]
                
                try:
                    raw_ocr = self._ocr.ocr_image(cropped_btn)
                    parsed_ocr = self._ocr.safe_parse_results(raw_ocr)
                    # 拼接所有 OCR 文本
                    combined_text = "".join([text for _, text, _ in parsed_ocr]).strip()
                    logger.debug(f"YoloDetectProvider: OCR verifying cropped region: expected='{ocr_text}', got='{combined_text}'")
                    
                    # 匹配判断 (优先进行严格正则/子串匹配，若不匹配且非正则元字符，则尝试在清洗空格/标点后做子串匹配)
                    matched = False
                    try:
                        pattern = re.compile(ocr_text)
                        if pattern.search(combined_text):
                            matched = True
                    except Exception as e:
                        logger.debug(f"YoloDetectProvider: regex compile error: {e}")
                    
                    if not matched:
                        is_regex = any(char in ocr_text for char in "^$*+?|{}[]()\\")
                        if not is_regex:
                            cleaned_expected = re.sub(r'[^\w\u4e00-\u9fa5]', '', ocr_text).lower()
                            cleaned_combined = re.sub(r'[^\w\u4e00-\u9fa5]', '', combined_text).lower()
                            if cleaned_expected and cleaned_expected in cleaned_combined:
                                matched = True
                                
                    if not matched:
                        logger.info(f"YoloDetectProvider: OCR verification failed for '{ocr_text}' in '{combined_text}'")
                        return None
                    
                    logger.info(f"YoloDetectProvider: OCR verification success for '{ocr_text}'")
                except Exception as e:
                    logger.error(f"YoloDetectProvider: OCR verification error: {e}")
                    return None

            # 返回坐标 (x_center, y_center) 和 bounding box
            x1, y1, x2, y2 = box
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            return RecognitionResult(
                matched=True,
                position=(cx, cy),
                box=[x1, y1, x2 - x1, y2 - y1],
                confidence=conf,
            )

        # 2. 如果主目标未找到且配置了 anchor 兜底，则检测 anchor
        elif fallback_anchor:
            fallback_anchor = str(fallback_anchor)
            anchor_box, anchor_conf = self._driver.yolo_detect(fallback_anchor, screen_image=screen, conf_threshold=0.6)
            if anchor_box:
                ax1, ay1, ax2, ay2 = anchor_box
                acx = (ax1 + ax2) // 2
                acy = (ay1 + ay2) // 2
                
                est_cx = acx + offset_x
                est_cy = acy + offset_y
                
                logger.info(f"YoloDetectProvider: Primary failed. Using anchor '{fallback_anchor}' with offset. Est target center: ({est_cx}, {est_cy})")
                
                return RecognitionResult(
                    matched=True,
                    position=(est_cx, est_cy),
                    box=[est_cx - 30, est_cy - 30, 60, 60],
                    confidence=anchor_conf,
                )

        logger.debug(f"YoloDetectProvider: target '{yolo_class}' not found.")
        return None


# ============================================================
#  RecognitionRegistry — 统一分发注册中心
# ============================================================

class RecognitionRegistry:
    """
    识别注册中心 — 统一分发入口。

    职责:
        1. 持有所有底层组件引用 (VisionEngine, OCRClient, PageDetector)
        2. 自动注册每个 RecognitionType → Provider 的映射
        3. 提供 recognize(screen, spec, anchors) 统一入口
        4. 自动处理 ROI 裁剪和坐标回映射

    使用方式:
        registry = RecognitionRegistry(vision, ocr, page_detector, config)
        result = registry.recognize(screen, spec, anchors)
    """

    def __init__(
        self,
        vision: Optional[VisionEngine] = None,
        ocr: Optional[OCRClient] = None,
        page_detector: Optional[LightPageDetector] = None,
        driver: Optional[AgentlessTouchDriver] = None,
        config: Any = None,
    ):
        self._vision = vision
        self._ocr = ocr
        self._page_detector = page_detector
        self._driver = driver
        self._config = config

        # 注册所有 Provider
        self._providers: Dict[RecognitionType, RecognitionProvider] = {}
        self._register_all()

    def _register_all(self) -> None:
        """按 RecognitionType 自动注册所有内置 Provider。"""

        # 无依赖的 Provider
        self._providers[RecognitionType.DIRECT_HIT] = DirectHitProvider()
        self._providers[RecognitionType.COLOR_SHIFT] = ColorShiftProvider()
        self._providers[RecognitionType.CUSTOM] = CustomProvider()
        
        # YOLO 目标检测 (依赖 driver 和 ocr 进行二次匹配)
        self._providers[RecognitionType.YOLO_DETECT] = YoloDetectProvider(
            driver=self._driver,
            ocr=self._ocr,
        )

        # 依赖 VisionEngine
        if self._vision is not None:
            self._providers[RecognitionType.TEMPLATE_MATCH] = TemplateMatchProvider(
                self._vision
            )
            self._providers[RecognitionType.SCREEN_DIFF] = ScreenDiffProvider(
                self._vision
            )
        else:
            logger.debug("VisionEngine not provided; TemplateMatch/ScreenDiff disabled")

        # 依赖 OCRClient
        if self._ocr is not None:
            self._providers[RecognitionType.OCR_TEXT] = OCRTextProvider(self._ocr)
        else:
            logger.debug("OCRClient not provided; OCRText disabled")

        # 依赖 PageDetector
        if self._page_detector is not None:
            self._providers[RecognitionType.ACTIVITY_DETECT] = ActivityDetectProvider(
                self._page_detector
            )
        else:
            logger.debug("PageDetector not provided; ActivityDetect disabled")

        # 组合 Provider (需要 registry 自身引用)
        self._providers[RecognitionType.AND] = AndProvider(self)
        self._providers[RecognitionType.OR] = OrProvider(self)

    def recognize(
        self,
        screen: np.ndarray,
        spec: RecognitionSpec,
        anchors: AnchorStore,
    ) -> Optional[RecognitionResult]:
        """
        统一识别入口。

        流程:
            1. 查找 spec.type 对应的 Provider
            2. 如果 spec.roi 存在, 裁剪屏幕图像
            3. 调用 Provider.recognize()
            4. 将 ROI 区域内的坐标偏移回全屏坐标系

        Args:
            screen: 原始全屏截图 (BGR numpy array)。
            spec: 识别配置规格。
            anchors: 当前 Anchor 存储。

        Returns:
            RecognitionResult 或 None (未命中)。
        """
        reco_type = spec.type

        provider = self._providers.get(reco_type)
        if provider is None:
            logger.error(
                f"RecognitionRegistry: no provider for type '{reco_type.value}'"
            )
            return None

        # ROI 裁剪
        cropped, off_x, off_y = apply_roi(screen, spec.roi)

        try:
            result = provider.recognize(cropped, spec, anchors)
        except Exception as e:
            logger.error(
                f"RecognitionRegistry: provider '{reco_type.value}' raised: {e}",
                exc_info=True,
            )
            return None

        # ROI 坐标回映射
        if result is not None and (off_x != 0 or off_y != 0):
            result = _offset_result(result, off_x, off_y)

        return result

    def get_provider(self, reco_type: RecognitionType) -> Optional[RecognitionProvider]:
        """获取指定类型的 Provider 实例 (调试/扩展用)。"""
        return self._providers.get(reco_type)

    def register_provider(
        self,
        reco_type: RecognitionType,
        provider: RecognitionProvider,
    ) -> None:
        """注册或覆盖自定义 Provider。"""
        self._providers[reco_type] = provider
        logger.info(f"Registered custom provider for '{reco_type.value}'")


# ============================================================
#  工具函数
# ============================================================

def _dict_to_spec(d: dict) -> RecognitionSpec:
    """
    将 YAML 字典转换为 RecognitionSpec。

    自动处理 type 字段的字符串 → 枚举转换。
    未知字段会被安全忽略。
    """
    if not isinstance(d, dict):
        return RecognitionSpec()

    # 拷贝一份避免修改原始数据
    data = dict(d)

    # type 字段: str → RecognitionType
    raw_type = data.pop("type", "direct_hit")
    try:
        reco_type = RecognitionType(raw_type)
    except ValueError:
        logger.warning(f"Unknown recognition type '{raw_type}', defaulting to DIRECT_HIT")
        reco_type = RecognitionType.DIRECT_HIT

    # 过滤出 RecognitionSpec 已知字段
    import dataclasses
    known_fields = {f.name for f in dataclasses.fields(RecognitionSpec)}
    filtered = {k: v for k, v in data.items() if k in known_fields}

    return RecognitionSpec(type=reco_type, **filtered)


def _import_handler(dotted_path: str):
    """
    动态导入 Python 函数。

    Args:
        dotted_path: "module.submodule.function_name" 格式。

    Returns:
        可调用的函数对象。

    Raises:
        ImportError / AttributeError — 如果路径无效。
    """
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ImportError(
            f"Invalid handler path '{dotted_path}': "
            f"expected 'module.function' format"
        )

    module_path, func_name = parts
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)

    if not callable(func):
        raise TypeError(
            f"Handler '{dotted_path}' resolved to {type(func)}, "
            f"expected a callable"
        )

    return func
