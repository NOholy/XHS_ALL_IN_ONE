import time
import cv2
import numpy as np
from paddleocr import PaddleOCR

def create_synthetic_image(width=1080, height=1920):
    img = np.ones((height, width, 3), dtype=np.uint8) * 255
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, 'Test OCR Performance', (100, 200), font, 2, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, 'XHS Automation Engine', (100, 400), font, 1.5, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, 'Like, Comment, Follow', (100, 600), font, 1, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, '1234567890', (100, 800), font, 1, (0, 0, 0), 2, cv2.LINE_AA)
    return img

def test_performance():
    print("1. Initializing PaddleOCR (Cold Start)...")
    start = time.time()
    ocr_engine = PaddleOCR(lang="ch")
    print(f"   Init took: {time.time() - start:.4f} seconds")

    print("\n2. Generating Synthetic UI Image (1080x1920)...")
    img = create_synthetic_image()

    print("\n3. Testing Cold Start Inference...")
    start = time.time()
    result = ocr_engine.ocr(img)
    print(f"   First inference took: {time.time() - start:.4f} seconds")

    print("\n4. Testing Warm Inference (10 iterations)...")
    times = []
    for i in range(10):
        start = time.time()
        _ = ocr_engine.ocr(img)
        times.append(time.time() - start)
    
    avg_time = sum(times) / len(times)
    fps = 1.0 / avg_time
    print(f"   Avg inference time: {avg_time:.4f} seconds")
    print(f"   FPS: {fps:.2f}")

    print("\n5. Testing Warm Inference with Resized Image (1600 max, client optimization)...")
    h, w = img.shape[:2]
    scale = 1600 / max(h, w)
    img_resized = cv2.resize(img, (int(w * scale), int(h * scale)))
    times_resized = []
    for i in range(10):
        start = time.time()
        _ = ocr_engine.ocr(img_resized)
        times_resized.append(time.time() - start)
    
    avg_time_resized = sum(times_resized) / len(times_resized)
    fps_resized = 1.0 / avg_time_resized
    print(f"   Avg inference time (resized): {avg_time_resized:.4f} seconds")
    print(f"   FPS (resized): {fps_resized:.2f}")

if __name__ == "__main__":
    test_performance()
