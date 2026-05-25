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

def crop_and_save_via_ocr(driver, ocr_client, text_query, filename, fallback_box=None):
    img = driver.screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", resolution_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{filename}.png")
    
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
            
            # Add dynamic padding based on screen resolution
            padding_x, padding_y = int(w_screen * 0.015), int(h_screen * 0.005)
            left = max(0, left - padding_x)
            right = min(w_screen, right + padding_x)
            top = max(0, top - padding_y)
            bottom = min(h_screen, bottom + padding_y)
            
            logger.info(f"OCR Found '{text_query}' -> '{text}' | Conf: {conf:.2f}")
            cropped = img[top:bottom, left:right]
            
            cv2.imwrite(out_path, cropped)
            logger.info(f"Saved {filename}.png to {out_path}")
            return True
            
    logger.warning(f"OCR could not find '{text_query}' on screen.")
    if fallback_box:
        logger.info(f"Using fallback physical box for {filename}: {fallback_box}")
        left, top, right, bottom = fallback_box
        cropped = img[top:bottom, left:right]
        cv2.imwrite(out_path, cropped)
        logger.info(f"Saved FALLBACK {filename}.png to {out_path}")
        return True
    return False

def automated_setup_pipeline(driver, ocr_client, watchdog=None):
    logger.info("Industrial Pipeline: Initiating automated XHS UI template extraction...")
    
    driver.ensure_app_foreground()
    time.sleep(6) # Wait for splash screen
    
    if watchdog:
        watchdog.check_and_handle()
        
    img = driver.screenshot()
    h, w = img.shape[:2]
    
    logger.info("Entering a post to find UI elements...")
    # Smarter fallback click (Relative Top-Left grid)
    driver.physical_tap(int(w * 0.3), int(h * 0.3))
    
    time.sleep(5) # Wait for detail page
    
    if watchdog:
        watchdog.check_and_handle()
        
    # 1. Look for '回复' button
    logger.info("Scrolling down to reveal comments area...")
    driver.human_swipe("up")  # swipe up to scroll down
    time.sleep(2)
    
    logger.info("Searching for '回复' (Reply) button via OCR...")
    found_reply = crop_and_save_via_ocr(driver, ocr_client, "回复", "reply_button")
    if not found_reply:
        logger.info("Scrolling down again to find '回复'...")
        driver.human_swipe("up")
        time.sleep(2)
        crop_and_save_via_ocr(driver, ocr_client, "回复", "reply_button")
    
    # 2. Trigger the keyboard
    logger.info("Triggering keyboard to reveal '发送' (Send) button...")
    
    # Find input box using OCR
    input_box_indicators = ["说点什么", "留下你的", "发弹幕"]
    clicked_input = False
    
    img_for_input = driver.screenshot()
    try:
        results = ocr_client.ocr_image(img_for_input)
        for line in results:
            box, (text, conf) = line
            if any(ind in text for ind in input_box_indicators) and conf > 0.7:
                x_center = int(sum([p[0] for p in box]) / 4)
                y_center = int(sum([p[1] for p in box]) / 4)
                logger.info(f"Found input box placeholder '{text}', clicking at ({x_center}, {y_center})")
                driver.physical_tap(x_center, y_center)
                clicked_input = True
                break
    except OCRServiceError:
        pass
        
    if not clicked_input:
        logger.warning("Could not find input box placeholder via OCR. Using fallback bottom click.")
        driver.physical_tap(int(w * 0.2), int(h * 0.96))
        
    time.sleep(2) # Wait for keyboard
    
    # Fallback box for send button (usually bottom right of keyboard or input bar)
    fallback_send_box = (int(w * 0.8), int(h * 0.9), w, h)
    
    logger.info("Searching for '发送'/'发布' (Send) button via OCR...")
    found = crop_and_save_via_ocr(driver, ocr_client, "发送", "send_button")
    if not found:
        found = crop_and_save_via_ocr(driver, ocr_client, "发布", "send_button")
    if not found:
        crop_and_save_via_ocr(driver, ocr_client, "NOT_FOUND", "send_button", fallback_box=fallback_send_box)
    
    logger.info("Cleaning up and returning to feed...")
    driver.press_back()
    driver.press_back()

def create_mock_templates_for_testing(w=1080, h=1920):
    """Generates dummy images to bypass missing template errors during testing."""
    import numpy as np
    resolution_dir = f"{w}x{h}"
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", resolution_dir)
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
