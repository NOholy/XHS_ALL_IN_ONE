import time
import cv2
import numpy as np
import sys
from paddleocr import PaddleOCR

def test():
    print("1. Initializing PaddleOCR...", flush=True)
    start = time.time()
    import os
    ocr_version = os.getenv("OCR_VERSION", "PP-OCRv4")
    ocr_engine = PaddleOCR(lang="ch", ocr_version=ocr_version)
    print(f"Init took: {time.time() - start:.4f} seconds", flush=True)

    img = np.ones((1920, 1080, 3), dtype=np.uint8) * 255
    cv2.putText(img, 'Test', (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)

    print("2. Cold Start Inference...", flush=True)
    start = time.time()
    ocr_engine.ocr(img)
    print(f"Cold Start Inference took: {time.time() - start:.4f} seconds", flush=True)

    print("3. Warm Inference (1080x1920)...", flush=True)
    start = time.time()
    ocr_engine.ocr(img)
    time1 = time.time() - start
    print(f"Warm Inference took: {time1:.4f} seconds", flush=True)

    print("4. Resized Inference (max 1600)...", flush=True)
    h, w = img.shape[:2]
    scale = 1600 / max(h, w)
    img_resized = cv2.resize(img, (int(w * scale), int(h * scale)))
    
    start = time.time()
    ocr_engine.ocr(img_resized)
    time2 = time.time() - start
    print(f"Resized Inference took: {time2:.4f} seconds", flush=True)

if __name__ == "__main__":
    test()
