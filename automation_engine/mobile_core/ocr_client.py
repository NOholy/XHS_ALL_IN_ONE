"""
OCR 微服务客户端
支持熔断器（Circuit Breaker）保护：连续失败 N 次后自动断路，冷却后半开恢复。
"""
import time
import requests
import cv2
import numpy as np
import base64
from .exceptions import OCRServiceError
from .logger import get_logger

logger = get_logger("ocr_client")


class CircuitBreaker:
    """
    简易熔断器实现。
    状态: CLOSED（正常）→ OPEN（熔断）→ HALF_OPEN（探测恢复）
    """
    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = self.STATE_CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0

    def allow_request(self) -> bool:
        """检查是否允许发起请求"""
        if self.state == self.STATE_CLOSED:
            return True
        if self.state == self.STATE_OPEN:
            # 冷却时间到了，转为半开状态，允许一次探测
            if time.time() - self.last_failure_time >= self.cooldown_seconds:
                logger.info("Circuit breaker transitioning to HALF_OPEN (probe)")
                self.state = self.STATE_HALF_OPEN
                return True
            return False
        # HALF_OPEN: 允许一次探测
        return True

    def record_success(self):
        """记录成功，重置状态"""
        if self.state != self.STATE_CLOSED:
            logger.info(f"Circuit breaker recovered: {self.state} → CLOSED")
        self.consecutive_failures = 0
        self.state = self.STATE_CLOSED

    def record_failure(self):
        """记录失败，可能触发熔断"""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        if self.state == self.STATE_HALF_OPEN:
            # 半开探测失败，重新打开熔断器
            logger.warning("Circuit breaker probe failed, re-opening")
            self.state = self.STATE_OPEN
        elif self.consecutive_failures >= self.failure_threshold:
            logger.error(
                f"Circuit breaker OPEN after {self.consecutive_failures} consecutive failures. "
                f"Cooldown: {self.cooldown_seconds}s"
            )
            self.state = self.STATE_OPEN


class OCRClient:
    def __init__(self, endpoint="http://localhost:8001/ocr", timeout=30,
                 circuit_breaker_threshold=3, circuit_breaker_cooldown=60):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self._breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            cooldown_seconds=circuit_breaker_cooldown,
        )

    def ocr_image(self, image_np):
        """Send an OpenCV image numpy array to the OCR microservice."""
        # 熔断器检查
        if not self._breaker.allow_request():
            raise OCRServiceError(
                f"OCR circuit breaker OPEN — service unavailable. "
                f"Will retry after cooldown ({self._breaker.cooldown_seconds}s)."
            )

        try:
            # Resize image if it's too large to dramatically speed up CPU OCR
            # Increased threshold to 1600 to retain precision for small UI elements (e.g. Reply button)
            h, w = image_np.shape[:2]
            scale = 1.0
            if max(h, w) > 1600:
                scale = 1600 / max(h, w)
                image_np = cv2.resize(image_np, (int(w * scale), int(h * scale)))
            
            _, buffer = cv2.imencode('.jpg', image_np, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            img_b64 = base64.b64encode(buffer).decode('utf-8')

            response = self.session.post(
                self.endpoint,
                json={"image_base64": img_b64},
                timeout=self.timeout,
                proxies={"http": None, "https": None}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                self._breaker.record_success()
                results = data.get("results", [])
                
                # Scale boxes back to original resolution
                if scale != 1.0:
                    for i in range(len(results)):
                        box, txt_info = results[i]
                        new_box = [[p[0] / scale, p[1] / scale] for p in box]
                        results[i] = [new_box, txt_info]
                        
                return results
            else:
                self._breaker.record_failure()
                raise OCRServiceError(f"OCR Server returned error: {data.get('message')}")
        except requests.RequestException as e:
            self._breaker.record_failure()
            logger.error("OCR API request failed", extra={"error": str(e)})
            # Return empty results instead of crashing the automation flow
            return []

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

    @property
    def circuit_breaker_state(self) -> str:
        """暴露熔断器状态，供外部监控"""
        return self._breaker.state
