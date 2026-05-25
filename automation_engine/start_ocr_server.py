from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import base64
import cv2
import numpy as np
import logging
from paddleocr import PaddleOCR

# Configure simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr_server")
logging.getLogger("ppocr").setLevel(logging.ERROR)

app = FastAPI(title="Industrial OCR Microservice")

# Initialize PaddleOCR ONCE during startup.
# This saves memory across hundreds of mobile driver instances.
logger.info("Initializing PaddleOCR Engine...")
ocr_engine = PaddleOCR(lang="ch")
logger.info("PaddleOCR Engine loaded successfully.")

# Detect which API to use (predict vs ocr)
_USE_PREDICT = hasattr(ocr_engine, 'predict')
logger.info(f"PaddleOCR API: {'predict (v3.5+)' if _USE_PREDICT else 'ocr (legacy)'}")


class ImageRequest(BaseModel):
    image_base64: str


def _run_ocr(img):
    """
    Run OCR and return results in unified format:
    [  [box, (text, confidence)], ... ]
    where box = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    
    Handles both PaddleOCR v3.5+ predict() and legacy ocr() APIs transparently.
    """
    if _USE_PREDICT:
        # predict() returns: [{"rec_texts": [...], "rec_scores": [...], "dt_polys": [...], ...}]
        results = ocr_engine.predict(img)
        formatted = []
        if results:
            page = results[0]
            texts = page.get("rec_texts", [])
            scores = page.get("rec_scores", [])
            polys = page.get("dt_polys", [])

            for i in range(len(texts)):
                if i < len(polys) and i < len(scores):
                    # dt_polys[i] is a numpy array of shape (N, 2), convert to list of [x, y]
                    poly = polys[i]
                    if hasattr(poly, 'tolist'):
                        poly = poly.tolist()
                    # Ensure we have exactly 4 corner points (take corners of bounding rect)
                    if len(poly) == 4:
                        box = poly
                    else:
                        # For polygons with more points, extract the 4 corners
                        xs = [p[0] for p in poly]
                        ys = [p[1] for p in poly]
                        box = [
                            [min(xs), min(ys)],
                            [max(xs), min(ys)],
                            [max(xs), max(ys)],
                            [min(xs), max(ys)]
                        ]
                    # Convert numpy floats to Python floats for JSON serialization
                    score = float(scores[i]) if hasattr(scores[i], 'item') else scores[i]
                    formatted.append([box, (texts[i], score)])
        return formatted
    else:
        # Legacy ocr() API: returns [[  [box, (text, conf)], ...  ]]
        results = ocr_engine.ocr(img)
        formatted = []
        if results and results[0]:
            for line in results[0]:
                formatted.append(line)
        return formatted


@app.post("/ocr")
async def process_ocr(request: ImageRequest):
    try:
        img_data = base64.b64decode(request.image_base64)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Invalid image data")

        formatted_results = _run_ocr(img)
                
        return {"status": "success", "results": formatted_results}
    except Exception as e:
        logger.error(f"OCR processing failed: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
