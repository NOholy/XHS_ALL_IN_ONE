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

class ImageRequest(BaseModel):
    image_base64: str

@app.post("/ocr")
async def process_ocr(request: ImageRequest):
    try:
        img_data = base64.b64decode(request.image_base64)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Invalid image data")

        results = ocr_engine.ocr(img)
        
        # results structure: [[[[x,y], [x,y], [x,y], [x,y]], ('text', confidence)], ...]
        formatted_results = []
        if results and results[0]:
            for line in results[0]:
                formatted_results.append(line)
                
        return {"status": "success", "results": formatted_results}
    except Exception as e:
        logger.error(f"OCR processing failed: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
