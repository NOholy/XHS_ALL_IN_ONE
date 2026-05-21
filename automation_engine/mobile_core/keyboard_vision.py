from pypinyin import pinyin, Style
import time
from .logger import get_logger

logger = get_logger("keyboard_vision")

class KeyboardVisionTyping:
    """
    Phase 3: 100% Visual Chinese Typing without system clipboard or U2 send_keys.
    Requires a clean input method (e.g. customized Baidu IME) with known layouts.
    """
    def __init__(self, driver, vision, ocr):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr

    def type_chinese(self, text):
        logger.info(f"Starting Visual Typing for: '{text}'")
        
        for char in text:
            # 1. Get Pinyin
            py_list = pinyin(char, style=Style.NORMAL)
            pinyin_str = py_list[0][0].lower()
            
            logger.info(f"Typing pinyin for '{char}': {pinyin_str}")
            # 2. Type pinyin letters physically
            for letter in pinyin_str:
                self._click_keyboard_letter(letter)
                time.sleep(0.1) # Human inter-key typing speed
                
            # 3. OCR candidate bar to find the exact Chinese character
            self._select_candidate(char)
            
    def _click_keyboard_letter(self, letter):
        # We rely on pre-cropped letter templates in vision engine.
        img = self.driver.screenshot()
        match = self.vision.find_template(img, f"key_{letter}", threshold=0.7)
        if match:
            self.driver.physical_tap(match['x'], match['y'])
        else:
            logger.error(f"Could not visually locate key '{letter}' on keyboard!")

    def _select_candidate(self, target_char):
        """Scans the candidate bar above the keyboard for the target Chinese character."""
        time.sleep(0.5) # Wait for candidates to generate
        img = self.driver.screenshot()
        
        # Use OCR service to read candidate text
        matches = self.ocr.find_text(img, target_char, conf_threshold=0.6)
        if matches:
            # Click the first match
            target = matches[0]
            logger.info(f"Found candidate '{target_char}' at ({target['x']}, {target['y']})")
            self.driver.physical_tap(target['x'], target['y'])
        else:
            logger.error(f"Candidate '{target_char}' not found via OCR. Typing might fail.")
            # In a robust system, we would click the "expand candidates" button and search again
