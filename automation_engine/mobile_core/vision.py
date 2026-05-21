import cv2
import os
import numpy as np
from .logger import get_logger

logger = get_logger("vision")

class VisionEngine:
    def __init__(self, templates_dir):
        self.templates_dir = templates_dir
        self.templates = {}
        self._load_templates()

    def _load_templates(self):
        if not os.path.exists(self.templates_dir):
            logger.warning(f"Templates directory {self.templates_dir} does not exist.")
            return
        for file in os.listdir(self.templates_dir):
            if file.endswith('.png'):
                name = file.replace('.png', '')
                path = os.path.join(self.templates_dir, file)
                tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if tpl is not None:
                    self.templates[name] = tpl

    def find_template(self, screen_img, template_name, threshold=0.75):
        """Find a template in the screen image using OpenCV."""
        if template_name not in self.templates:
            logger.error(f"Template '{template_name}' not loaded.")
            return None

        template = self.templates[template_name]
        gray_screen = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        
        res = cv2.matchTemplate(gray_screen, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
        if max_val >= threshold:
            h, w = template.shape
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            return {"x": center_x, "y": center_y, "conf": max_val}
        return None

    def detect_cards_waterfall(self, screen_img):
        """Dynamically detect feed cards using edge detection and contour logic."""
        # This replaces the hardcoded grid with dynamic contour detection
        gray = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        
        # Dilate to connect edges
        kernel = np.ones((5,5), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=2)
        
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        cards = []
        h_screen, w_screen = screen_img.shape[:2]
        min_area = (w_screen * 0.3) * (h_screen * 0.1) # At least 30% width, 10% height
        
        for idx, cnt in enumerate(contours):
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if area > min_area:
                cards.append({"id": idx, "x": x + w//2, "y": y + h//2, "w": w, "h": h})
                
        # Sort top to bottom, then left to right
        cards.sort(key=lambda c: (c['y'] // 100, c['x']))
        return cards
