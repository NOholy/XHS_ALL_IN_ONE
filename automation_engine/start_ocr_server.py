from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import base64
import cv2
import numpy as np
import logging
import os
import threading
from abc import ABC, abstractmethod

# Configure simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr_server")

app = FastAPI(title="Industrial OCR Microservice")

class ImageRequest(BaseModel):
    image_base64: str

class ConfigRequest(BaseModel):
    engine_type: str
    lang: str = "ch"
    version: str = "PP-OCRv4"
    # Future parameters for other engines can be added here

# ---------------------------------------------------------
# Strategy Pattern: Base OCR Engine
# ---------------------------------------------------------
class BaseOCREngine(ABC):
    @abstractmethod
    def process(self, img_np) -> list:
        """
        Process the image and return standard format:
        [ [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], (text, confidence) ]
        """
        pass

# ---------------------------------------------------------
# Specific Implementation: PaddleOCR
# ---------------------------------------------------------
class PaddleOCREngine(BaseOCREngine):
    def __init__(self, lang="ch", version="PP-OCRv4"):
        from paddleocr import PaddleOCR
        logging.getLogger("ppocr").setLevel(logging.ERROR)
        self.lang = lang
        self.version = version
        logger.info(f"Initializing PaddleOCR Engine... (lang: {lang}, version: {version})")
        self.engine = PaddleOCR(lang=lang, ocr_version=version)
        self._use_predict = hasattr(self.engine, 'predict')
        logger.info(f"PaddleOCR API: {'predict (v3.5+)' if self._use_predict else 'ocr (legacy)'}")

    def process(self, img_np) -> list:
        if self._use_predict:
            results = self.engine.predict(img_np)
            formatted = []
            if results:
                page = results[0]
                texts = page.get("rec_texts", [])
                scores = page.get("rec_scores", [])
                polys = page.get("dt_polys", [])

                for i in range(len(texts)):
                    if i < len(polys) and i < len(scores):
                        poly = polys[i]
                        if hasattr(poly, 'tolist'):
                            poly = poly.tolist()
                        if len(poly) == 4:
                            box = poly
                        else:
                            xs = [p[0] for p in poly]
                            ys = [p[1] for p in poly]
                            box = [
                                [min(xs), min(ys)],
                                [max(xs), min(ys)],
                                [max(xs), max(ys)],
                                [min(xs), max(ys)]
                            ]
                        score = float(scores[i]) if hasattr(scores[i], 'item') else scores[i]
                        formatted.append([box, (texts[i], score)])
            return formatted
        else:
            results = self.engine.ocr(img_np)
            formatted = []
            if results and results[0]:
                for line in results[0]:
                    formatted.append(line)
            return formatted

# ---------------------------------------------------------
# Placeholder Implementation: MockEngine (For testing)
# ---------------------------------------------------------
class MockOCREngine(BaseOCREngine):
    def __init__(self, **kwargs):
        logger.info("Initializing MockOCREngine...")
        
    def process(self, img_np) -> list:
        # Return a fake result for testing multi-engine support
        return [
            [[[10, 10], [100, 10], [100, 50], [10, 50]], ("Mocked Text", 0.99)]
        ]

# ---------------------------------------------------------
# Thread-Safe Engine Manager
# ---------------------------------------------------------
class OCREngineManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.current_engine: BaseOCREngine = None
        
        # Initialize default engine
        default_type = os.getenv("OCR_ENGINE_TYPE", "paddle")
        default_lang = os.getenv("OCR_LANG", "ch")
        default_version = os.getenv("OCR_VERSION", "PP-OCRv4")
        self.switch_engine(default_type, default_lang, default_version)

    def switch_engine(self, engine_type: str, lang: str, version: str):
        engine_type = engine_type.lower()
        
        # Instantiate the new engine first (outside the lock) so we don't block
        # ongoing OCR requests during the heavy initialization phase.
        if engine_type == "paddle":
            new_engine = PaddleOCREngine(lang=lang, version=version)
        elif engine_type == "mock":
            new_engine = MockOCREngine()
        else:
            raise ValueError(f"Unsupported engine type: {engine_type}")
            
        with self.lock:
            self.current_engine = new_engine
            logger.info(f"Successfully switched OCR engine to: {engine_type}")

    def process_image(self, img_np):
        with self.lock:
            engine = self.current_engine
        return engine.process(img_np)

# Initialize global manager
engine_manager = OCREngineManager()

# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@app.post("/ocr")
def process_ocr(request: ImageRequest):
    try:
        img_data = base64.b64decode(request.image_base64)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Invalid image data")

        formatted_results = engine_manager.process_image(img)
                
        return {"status": "success", "results": formatted_results}
    except Exception as e:
        logger.error(f"OCR processing failed: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
def health_check():
    """健康检查端点：验证 OCR 引擎已初始化且可正常推理。"""
    try:
        engine = engine_manager.current_engine
        if engine is None:
            return {"status": "unhealthy", "engine_ready": False, "message": "No engine loaded"}

        # 用一张小空白图做轻量级推理探测，验证引擎真正可用
        test_img = np.zeros((20, 20, 3), dtype=np.uint8)
        engine.process(test_img)

        engine_type = type(engine).__name__
        return {"status": "healthy", "engine_ready": True, "engine_type": engine_type}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "engine_ready": False, "message": str(e)}

@app.post("/config")
def update_config(request: ConfigRequest):
    try:
        engine_manager.switch_engine(request.engine_type, request.lang, request.version)
        return {"status": "success", "message": f"Engine switched to {request.engine_type}"}
    except Exception as e:
        logger.error(f"Config update failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
