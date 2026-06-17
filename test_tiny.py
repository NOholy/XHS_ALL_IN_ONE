from paddleocr import PaddleOCR
try:
    ocr = PaddleOCR(lang='ch', text_detection_model_name='PP-OCRv6_tiny_det', text_recognition_model_name='PP-OCRv6_tiny_rec')
    print("Success loading tiny models")
except Exception as e:
    print(f"Error loading tiny models: {e}")
