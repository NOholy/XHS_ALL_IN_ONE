from .logger import get_logger
from .exceptions import RiskControlTriggered, PopupIntercepted

logger = get_logger("watchdog")

class PopupWatchdog:
    """
    Monitors the screen for unexpected popups (system updates, risk sliders, etc.)
    and intercepts the execution flow.
    """
    def __init__(self, vision_engine, device_driver):
        self.vision = vision_engine
        self.driver = device_driver

    def check_screen(self, screen_img):
        """
        Scan the screen for known risks or popups.
        Raises exceptions if critical issues are found, or automatically handles minor ones.
        """
        # 1. Check for critical risk control (Slider, Security Verification, Account Frozen, Phone Bind)
        critical_risks = ["slider_puzzle", "security_verification", "account_frozen", "phone_bind", "frequent_operation"]
        for risk in critical_risks:
            if self.vision.find_template(screen_img, risk, threshold=0.8):
                logger.critical(f"🚨 Risk control triggered: {risk}!")
                raise RiskControlTriggered(f"Risk control detected: {risk}")

        # 2. Check for generic popups (I know, Skip, Update Later, Cancel, Close)
        minor_popups = ["btn_iknow", "btn_skip", "btn_update_later", "btn_cancel", "btn_close"]
        for popup in minor_popups:
            match = self.vision.find_template(screen_img, popup, threshold=0.8)
            if match:
                logger.warning(f"Intercepted minor popup: {popup}. Auto-dismissing.")
                self.driver.physical_tap(match['x'], match['y'])
                self.driver.human_sleep(2.0, 1.0)
                raise PopupIntercepted(f"Handled popup {popup}, state needs refresh.")

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

