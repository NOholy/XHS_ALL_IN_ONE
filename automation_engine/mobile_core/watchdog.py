from .logger import get_logger
from .exceptions import RiskControlTriggered, PopupIntercepted

logger = get_logger("watchdog")


class PopupWatchdog:
    """
    Monitors the screen for unexpected popups (system updates, risk sliders, etc.)
    and intercepts the execution flow.

    V2: Hybrid detection — tries template matching first (fast), then OCR fallback
    for more reliable popup detection when templates are unavailable or synthetic.
    """

    # OCR-based detection keywords
    CRITICAL_RISK_KEYWORDS = ["安全验证", "滑块验证", "账号冻结", "绑定手机", "操作频繁", "账号异常", "身份验证"]
    MINOR_POPUP_KEYWORDS = {
        "我知道了": "btn_iknow",
        "跳过": "btn_skip",
        "以后再说": "btn_update_later",
        "暂不升级": "btn_update_later",
        "取消": "btn_cancel",
        "关闭": "btn_close",
    }

    def __init__(self, vision_engine, device_driver, ocr_client=None, page_detector=None):
        self.vision = vision_engine
        self.driver = device_driver
        self.ocr = ocr_client  # Optional: enables OCR-based fallback
        self.page_detector = page_detector  # Optional: enables Activity-level detection

    def check_screen(self, screen_img):
        """
        三层递进检测：
        1. Activity 级别（~50ms，零风控风险）— 检测系统弹窗和 App 切走
        2. OCR 关键词扫描（~1s）— 检测 App 内风控弹窗
        """
        # 第一层：Activity 快速检测（无需截图，无需 OCR）
        if self.page_detector:
            self._check_via_activity()

        # 第二层：OCR 关键词检测（原有逻辑）
        if self.ocr is not None:
            self._check_screen_via_ocr(screen_img)
        elif self.page_detector is None:
            logger.warning("Watchdog: No detection backend available — popup/risk detection is DISABLED.")

    def _check_via_activity(self):
        """通过 Activity 名称检测系统级弹窗和 App 切走"""
        try:
            # 检测系统弹窗
            if self.page_detector.is_system_dialog():
                activity = self.page_detector.get_current_activity()
                logger.warning(f"System dialog detected via Activity: {activity}")
                self.driver.press_back()
                self.driver.human_sleep(1.5, 0.5)
                raise PopupIntercepted(f"System activity dismissed: {activity}")

            # 检测是否离开了 XHS
            if not self.page_detector.is_xhs_foreground():
                pkg = self.page_detector.get_current_package()
                if pkg:  # 空字符串表示检测失败，不做处理
                    logger.warning(f"Left XHS! Current foreground: {pkg}")
                    self.driver.ensure_app_foreground()
                    self.driver.human_sleep(2.0, 1.0)
                    raise PopupIntercepted(f"Returned to XHS from {pkg}")
        except PopupIntercepted:
            raise
        except Exception as e:
            logger.debug(f"Activity-level detection failed (non-critical): {e}")

    def _check_screen_via_ocr(self, screen_img):
        """OCR-based popup detection fallback for when templates are unavailable."""
        try:
            results = self.ocr.ocr_image(screen_img)
            if not results:
                return

            # Defensive parsing
            texts_with_boxes = []
            for item in results:
                try:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        box = item[0]
                        txt_info = item[1]
                        if isinstance(txt_info, (list, tuple)) and len(txt_info) >= 1:
                            texts_with_boxes.append((box, str(txt_info[0])))
                        elif isinstance(txt_info, str):
                            texts_with_boxes.append((box, txt_info))
                except Exception:
                    continue

            all_text = " ".join([t for _, t in texts_with_boxes])

            # Check for critical risk keywords
            for keyword in self.CRITICAL_RISK_KEYWORDS:
                if keyword in all_text:
                    logger.critical(f"🚨 Risk control triggered (OCR): '{keyword}'!")
                    raise RiskControlTriggered(f"Risk control detected via OCR: {keyword}")

            # Check for minor popup keywords and auto-dismiss
            for keyword, popup_name in self.MINOR_POPUP_KEYWORDS.items():
                if keyword in all_text:
                    # Find the keyword's box and tap it
                    for box, text in texts_with_boxes:
                        if keyword in text:
                            x_center = int(sum(p[0] for p in box) / len(box))
                            y_center = int(sum(p[1] for p in box) / len(box))
                            logger.warning(f"Intercepted minor popup (OCR): '{keyword}'. Auto-dismissing at ({x_center}, {y_center}).")
                            self.driver.physical_tap(x_center, y_center)
                            self.driver.human_sleep(2.0, 1.0)
                            raise PopupIntercepted(f"Handled popup '{keyword}' via OCR, state needs refresh.")

        except (RiskControlTriggered, PopupIntercepted):
            raise  # Re-raise detection exceptions
        except Exception as e:
            logger.debug(f"OCR watchdog check failed (non-critical): {e}")

    def check_and_handle(self):
        """
        Takes a screenshot and checks for popups.
        Catches PopupIntercepted to allow the flow to continue.
        """
        img = self.driver.screenshot()
        try:
            self.check_screen(img)
        except PopupIntercepted as e:
            logger.info(f"Watchdog auto-handled popup: {e}")
