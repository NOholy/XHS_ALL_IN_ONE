"""
XHS UI Template Auto-Cropper V2 (Industrial)
Uses decoupled OCR microservice and DeviceDriver abstraction.
"""
import os
import cv2
import time
import sys
from mobile_core.logger import get_logger
from mobile_core.device_driver import DeviceDriver
from mobile_core.ocr_client import OCRClient
from mobile_core.exceptions import OCRServiceError

logger = get_logger("auto_cropper")

def crop_and_save_via_ocr(driver, ocr_client, text_query, filename):
    img = driver.screenshot()
    
    try:
        results = ocr_client.ocr_image(img)
    except OCRServiceError as e:
        logger.error(f"OCR Server Error: {e}")
        return False

    for line in results:
        box, (text, conf) = line
        if text_query in text:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            left, right = int(min(xs)), int(max(xs))
            top, bottom = int(min(ys)), int(max(ys))
            
            # Add padding to capture button background
            padding_x, padding_y = 15, 10
            left = max(0, left - padding_x)
            right = min(img.shape[1], right + padding_x)
            top = max(0, top - padding_y)
            bottom = min(img.shape[0], bottom + padding_y)
            
            logger.info(f"OCR Found '{text_query}' -> '{text}' | Conf: {conf:.2f}")
            cropped = img[top:bottom, left:right]
            
            out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{filename}.png")
            
            cv2.imwrite(out_path, cropped)
            logger.info(f"Saved {filename}.png to {out_path}")
            return True
            
    logger.warning(f"OCR could not find '{text_query}' on screen.")
    return False

def automated_setup_pipeline(driver, ocr_client):
    logger.info("Industrial Pipeline: Initiating automated XHS UI template extraction...")
    
    driver.ensure_app_foreground()
    time.sleep(6) # Wait for splash screen
    
    logger.info("Entering a post to find UI elements...")
    # Fallback blind click (Top-Left grid)
    driver.physical_tap(300, 500) # Generic coordinates for testing
    
    time.sleep(5) # Wait for detail page
    
    # 1. Look for '回复' button
    logger.info("Searching for '回复' (Reply) button via OCR...")
    crop_and_save_via_ocr(driver, ocr_client, "回复", "reply_button")
    
    # 2. Trigger the keyboard
    logger.info("Triggering keyboard to reveal '发送' (Send) button...")
    driver.physical_tap(200, 1800) # Bottom left area approximation
    time.sleep(2) # Wait for keyboard
    
    logger.info("Searching for '发送'/'发布' (Send) button via OCR...")
    found = crop_and_save_via_ocr(driver, ocr_client, "发送", "send_button")
    if not found:
        crop_and_save_via_ocr(driver, ocr_client, "发布", "send_button")
    
    logger.info("Cleaning up and returning to feed...")
    driver.press_back()
    driver.press_back()

def create_mock_templates_for_testing():
    """Generates dummy images to bypass missing template errors during testing."""
    import numpy as np
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates")
    os.makedirs(out_dir, exist_ok=True)
    
    templates = ["reply_button", "send_button", "slider_puzzle", "security_verification"]
    for t in templates:
        path = os.path.join(out_dir, f"{t}.png")
        if not os.path.exists(path):
            img = np.zeros((50, 150, 3), dtype=np.uint8)
            img[:] = (200, 100, 100) # Blueish mock button
            cv2.putText(img, t[:5], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
            cv2.imwrite(path, img)
            logger.info(f"Created mock template: {path}")

def main():
    logger.info("XHS UI Template Auto-Cropper V2 (PP-OCRv5 Industrial)")
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Generate mock templates instead of using real device")
    args = parser.parse_args()

    if args.mock:
        logger.info("Running in mock mode. Generating dummy templates.")
        create_mock_templates_for_testing()
        return

    try:
        driver = DeviceDriver()
    except Exception as e:
        logger.error(f"FATAL: Could not connect to device: {e}")
        logger.info("Tip: Run with --mock to generate test templates without a device.")
        return
        
    ocr_client = OCRClient()
    automated_setup_pipeline(driver, ocr_client)

if __name__ == "__main__":
    main()
