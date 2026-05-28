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

    def __init__(self, vision_engine, device_driver, ocr_client=None):
        self.vision = vision_engine
        self.driver = device_driver
        self.ocr = ocr_client  # Optional: enables OCR-based fallback

    def check_screen(self, screen_img):
        """
        Scan the screen for known risks or popups.
        Uses template matching first, then OCR fallback if available.
        Raises exceptions if critical issues are found, or automatically handles minor ones.
        """
        # ─── Phase 1: Template matching (fast) ───
        critical_risks = ["slider_puzzle", "security_verification", "account_frozen", "phone_bind", "frequent_operation"]
        for risk in critical_risks:
            if self.vision.find_template(screen_img, risk, threshold=0.8):
                logger.critical(f"🚨 Risk control triggered (template): {risk}!")
                raise RiskControlTriggered(f"Risk control detected: {risk}")

        minor_popups = ["btn_iknow", "btn_skip", "btn_update_later", "btn_cancel", "btn_close"]
        for popup in minor_popups:
            match = self.vision.find_template(screen_img, popup, threshold=0.8)
            if match:
                logger.warning(f"Intercepted minor popup (template): {popup}. Auto-dismissing.")
                self.driver.physical_tap(match['x'], match['y'])
                self.driver.human_sleep(2.0, 1.0)
                raise PopupIntercepted(f"Handled popup {popup}, state needs refresh.")

        # ─── Phase 2: OCR fallback (more reliable, slower) ───
        if self.ocr is not None:
            self._check_screen_via_ocr(screen_img)

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
