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
        # 1. Check for critical risk control (Slider, Security Verification)
        slider = self.vision.find_template(screen_img, "slider_puzzle", threshold=0.8)
        verify = self.vision.find_template(screen_img, "security_verification", threshold=0.8)
        
        if slider or verify:
            logger.critical("🚨 Risk control triggered (Slider/Verification)!")
            raise RiskControlTriggered("Risk control detected on screen.")

        # 2. Check for generic popups (I know, Skip, Update Later)
        # Assuming we have templates like 'btn_iknow', 'btn_skip'
        minor_popups = ["btn_iknow", "btn_skip", "btn_update_later"]
        for popup in minor_popups:
            match = self.vision.find_template(screen_img, popup, threshold=0.8)
            if match:
                logger.warning(f"Intercepted minor popup: {popup}. Auto-dismissing.")
                self.driver.physical_tap(match['x'], match['y'])
                self.driver.human_sleep(2.0, 1.0)
                raise PopupIntercepted(f"Handled popup {popup}, state needs refresh.")
