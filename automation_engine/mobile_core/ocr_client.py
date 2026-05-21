import requests
import cv2
import numpy as np
import base64
from .exceptions import OCRServiceError
from .logger import get_logger

logger = get_logger("ocr_client")

class OCRClient:
    def __init__(self, endpoint="http://localhost:8001/ocr", timeout=10):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False

    def ocr_image(self, image_np):
        """Send an OpenCV image numpy array to the OCR microservice."""
        try:
            _, buffer = cv2.imencode('.jpg', image_np)
            img_b64 = base64.b64encode(buffer).decode('utf-8')
            
            response = self.session.post(
                self.endpoint,
                json={"image_base64": img_b64},
                timeout=60,
                proxies={"http": None, "https": None}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return data.get("results", [])
            else:
                raise OCRServiceError(f"OCR Server returned error: {data.get('message')}")
        except requests.RequestException as e:
            logger.error("OCR API request failed", extra={"error": str(e)})
            raise OCRServiceError(f"OCR Request Failed: {e}")
            
    def find_text(self, image_np, target_text, conf_threshold=0.7):
        """Find specific text in image and return its bounding box and confidence."""
        results = self.ocr_image(image_np)
        matches = []
        for line in results:
            box, (text, conf) = line
            if target_text in text and conf >= conf_threshold:
                # box is a list of 4 points: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
                # return center coordinates
                x_center = int(sum([p[0] for p in box]) / 4)
                y_center = int(sum([p[1] for p in box]) / 4)
                matches.append({"text": text, "x": x_center, "y": y_center, "conf": conf, "box": box})
        return matches
