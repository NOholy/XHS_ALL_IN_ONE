import cv2
import os
import numpy as np
from .logger import get_logger

logger = get_logger("vision")

class VisionEngine:
    def __init__(self, templates_dir, shared_templates_dir=None):
        self.templates_dir = templates_dir
        self.shared_templates_dir = shared_templates_dir
        self.templates = {}
        self._load_templates()

    def _load_templates(self):
        """Load templates from device-specific dir, then fill gaps from shared dir."""
        # 1. Load from device-specific directory
        if os.path.exists(self.templates_dir):
            for file in os.listdir(self.templates_dir):
                if file.endswith('.png'):
                    name = file.replace('.png', '')
                    path = os.path.join(self.templates_dir, file)
                    tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                    if tpl is not None:
                        self.templates[name] = tpl
        else:
            logger.warning(f"Templates directory {self.templates_dir} does not exist.")
        
        # 2. Fill gaps from shared/fallback directory
        if self.shared_templates_dir and os.path.exists(self.shared_templates_dir):
            for file in os.listdir(self.shared_templates_dir):
                if file.endswith('.png'):
                    name = file.replace('.png', '')
                    if name not in self.templates:  # Don't override device-specific
                        path = os.path.join(self.shared_templates_dir, file)
                        tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                        if tpl is not None:
                            self.templates[name] = tpl
                            logger.info(f"Loaded shared fallback template: {name}")

    def find_template(self, screen_img, template_name, threshold=0.75):
        """Find a template in the screen image using OpenCV."""
        if template_name not in self.templates:
            logger.debug(f"Template '{template_name}' not loaded.")
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

    def find_all_templates(self, screen_img, template_name, threshold=0.75):
        """Find all occurrences of a template in the screen image."""
        if template_name not in self.templates:
            return []

        template = self.templates[template_name]
        gray_screen = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        
        res = cv2.matchTemplate(gray_screen, template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        
        matches = []
        h, w = template.shape
        for pt in zip(*loc[::-1]):  # Switch columns and rows
            center_x = pt[0] + w // 2
            center_y = pt[1] + h // 2
            matches.append({"x": int(center_x), "y": int(center_y), "conf": float(res[pt[1]][pt[0]])})
            
        # Basic Non-Maximum Suppression (NMS) to avoid overlapping bounding boxes
        unique_matches = []
        for m in sorted(matches, key=lambda x: x['conf'], reverse=True):
            if not any(abs(m['x'] - u['x']) < w//2 and abs(m['y'] - u['y']) < h//2 for u in unique_matches):
                unique_matches.append(m)
                
        return unique_matches

    def compute_screen_mse(self, img_a, img_b, roi=None):
        """
        Compute Mean Squared Error (MSE) between two screen images.
        roi is an optional tuple (x, y, w, h).
        Returns float MSE. Less than 1.0 usually means identical/no change.
        """
        if img_a is None or img_b is None:
            return float('inf')
            
        if roi:
            x, y, w, h = roi
            img_a = img_a[y:y+h, x:x+w]
            img_b = img_b[y:y+h, x:x+w]
            
        if img_a.shape != img_b.shape or img_a.size == 0:
            return float('inf')
            
        err = np.sum((img_a.astype("float") - img_b.astype("float")) ** 2)
        err /= float(img_a.shape[0] * img_a.shape[1] * img_a.shape[2])
        return err

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
