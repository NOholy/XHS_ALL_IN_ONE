"""
XHS UI Template Auto-Cropper V4 (Closed-Loop Validation)
Uses decoupled OCR microservice and DeviceDriver abstraction.

V4 improvements:
- Removed useless mock watchdog templates
- Complete collection of navigation templates (tab_home, tab_profile, search_input, comment_input, send_button, reply_button)
- Strict Closed-Loop Validation: [Crop] -> [Vision Match] -> [Action] -> [Assert Success]
"""
import os
import cv2
import time
import numpy as np
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mobile_core.logger import get_logger
from mobile_core.exceptions import OCRServiceError


def _auto_detect_serial():
    """Auto-detect device serial from ADB when not provided."""
    import subprocess
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return "unknown"
from mobile_core.vision import VisionEngine

logger = get_logger("auto_crop_templates")


# ─────────── Helpers ───────────

def _parse_ocr_results(results):
    """Defensively parse OCR results into a list of (box, text, conf) tuples."""
    parsed = []
    for item in (results or []):
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                box = item[0]
                txt_info = item[1]
                if isinstance(txt_info, (list, tuple)) and len(txt_info) >= 2:
                    parsed.append((box, str(txt_info[0]), float(txt_info[1])))
                elif isinstance(txt_info, (list, tuple)) and len(txt_info) >= 1:
                    parsed.append((box, str(txt_info[0]), 0.0))
                elif isinstance(txt_info, str):
                    parsed.append((box, txt_info, 0.0))
        except Exception:
            continue
    return parsed


def _validate_template(img, min_variance=50, min_size=15):
    """Validate that a template image contains meaningful visual content."""
    if img is None:
        return False, "image is None"
    h, w = img.shape[:2]
    if h < min_size or w < min_size:
        return False, f"too small ({w}x{h})"

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    variance = float(np.var(gray))
    if variance < min_variance:
        return False, f"low variance ({variance:.1f}), likely blank/solid"

    return True, "OK"


def _get_all_screen_text(ocr_client, img):
    """OCR the screen and return all text as a single string."""
    try:
        results = ocr_client.ocr_image(img)
        parsed = _parse_ocr_results(results)
        return " ".join([text for _, text, _ in parsed])
    except Exception:
        return ""


def crop_and_save_via_ocr(driver, ocr_client, text_query, filename, serial=None, 
                          validate=True, y_min_ratio=0.0, x_min_ratio=0.0, exact_match=False):
    """
    Screenshot -> OCR -> find text_query -> crop region -> validate -> save.
    Returns (success: bool, path: str).
    """
    img = driver.screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    serial = serial or _auto_detect_serial()
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{filename}.png")

    try:
        results = ocr_client.ocr_image(img)
    except Exception as e:
        logger.error(f"OCR Server Error: {e}")
        return False, None
    parsed = _parse_ocr_results(results)

    for box, text, conf in parsed:
        is_match = (text_query == text.strip()) if exact_match else (text_query in text)
        if is_match:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            left, right = int(min(xs)), int(max(xs))
            top, bottom = int(min(ys)), int(max(ys))
            y_center = (top + bottom) / 2
            x_center = (left + right) / 2

            if y_center < h_screen * y_min_ratio:
                continue
            if x_center < w_screen * x_min_ratio:
                continue

            # Exact text subset matching to tighten crop box
            if len(text) > len(text_query):
                start_idx = text.find(text_query)
                if start_idx != -1:
                    char_width = (right - left) / len(text)
                    new_left = left + int(start_idx * char_width)
                    new_right = left + int((start_idx + len(text_query)) * char_width)
                    left = new_left
                    right = new_right

            padding_x, padding_y = int(w_screen * 0.015), int(h_screen * 0.005)
            left = max(0, left - padding_x)
            right = min(w_screen, right + padding_x)
            top = max(0, top - padding_y)
            bottom = min(h_screen, bottom + padding_y)

            cropped = img[top:bottom, left:right]

            if validate:
                is_valid, reason = _validate_template(cropped)
                if not is_valid:
                    logger.warning(f"Crop {filename} failed validation: {reason}")
                    continue

            logger.info(f"OCR Found '{text_query}' -> '{text}' | Conf: {conf:.2f}")
            cv2.imwrite(out_path, cropped)
            return True, out_path

    return False, None

def verify_template_action(driver, ocr_client, template_name, serial, resolution, assert_keywords):
    """
    Load the template via VisionEngine, find it on screen, tap it,
    then use OCR to assert the new screen state contains one of the assert_keywords.
    """
    serial = serial or _auto_detect_serial()
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution)
    vision = VisionEngine(templates_dir)
    img = driver.screenshot()
    match = vision.find_template(img, template_name, threshold=0.7)
    if not match:
        logger.error(f"Verification failed: newly cropped template '{template_name}' could not be matched!")
        return False
    
    logger.info(f"Verification matching successful for '{template_name}' at {match['x']}, {match['y']}. Tapping...")
    driver.physical_tap(match['x'], match['y'])
    driver.human_sleep(2.0, 1.0)
    
    new_img = driver.screenshot()
    all_text = _get_all_screen_text(ocr_client, new_img)
    
    for kw in assert_keywords:
        if kw in all_text:
            logger.info(f"Verification passed for '{template_name}': Assert keyword '{kw}' found.")
            return True
            
    logger.error(f"Verification failed for '{template_name}': None of assert keywords {assert_keywords} found in new screen state.")
    return False

def crop_fixed_region(driver, filename, serial, box, validate=True):
    """Crop a fixed region of the screen and save it."""
    img = driver.screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    serial = serial or _auto_detect_serial()
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{filename}.png")
    
    left, top, right, bottom = box
    cropped = img[top:bottom, left:right]
    
    if validate:
        is_valid, reason = _validate_template(cropped)
        if not is_valid:
            logger.warning(f"Crop {filename} failed validation: {reason}")
            return False, None
            
    cv2.imwrite(out_path, cropped)
    return True, out_path

# ─────────── Pipeline Core ───────────

def automated_setup_pipeline(driver, ocr_client, serial=None, watchdog=None, **kwargs):
    """
    Closed-loop UI template cropping pipeline.
    """
    logger.info("=== STARTING FULL AUTO-CROP AND VALIDATION PIPELINE ===")
    
    import subprocess
    adb_cmd = ["adb"] if not serial else ["adb", "-s", serial]
    
    # Force stop app to ensure we start at the home feed
    subprocess.run(adb_cmd + ["shell", "am", "force-stop", "com.xingin.xhs"])
    time.sleep(2)
    
    subprocess.run(adb_cmd + ["shell", "input", "keyevent", "KEYCODE_HOME"])
    time.sleep(1)
    
    # Launch app
    subprocess.run(adb_cmd + ["shell", "monkey", "-p", "com.xingin.xhs", "-c", "android.intent.category.LAUNCHER", "1"])
    driver.human_sleep(5.0) 
    
    # Wait until app is truly loaded (OCR sees '推荐' or '发现' or '首页')
    logger.info("Waiting for app to load to home feed...")
    app_loaded = False
    for _ in range(10):
        img_check = driver.screenshot()
        try:
            res = ocr_client.ocr_image(img_check)
            text_str = " ".join([t for _, t, _ in _parse_ocr_results(res)])
            if "推荐" in text_str or "发现" in text_str or "首页" in text_str:
                app_loaded = True
                break
        except Exception:
            pass
        driver.human_sleep(2.0)
        
    if not app_loaded:
        logger.warning("Could not definitively detect home feed, proceeding anyway.")

    img = driver.screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    report = {"templates": {}, "success": True}
    
    def attempt_crop_and_verify(template_name, ocr_query, y_min_ratio, assert_keywords, exact_match=False, fixed_box=None, recovery_action=None):
        logger.info(f"\n--- Harvesting: {template_name} ---")
        for attempt in range(3):
            if fixed_box:
                success, path = crop_fixed_region(driver, template_name, serial, fixed_box)
            else:
                success, path = crop_and_save_via_ocr(
                    driver, ocr_client, ocr_query, template_name, serial, 
                    y_min_ratio=y_min_ratio, exact_match=exact_match
                )
            if success:
                driver.human_sleep(1.0)
                verified = verify_template_action(driver, ocr_client, template_name, serial, resolution_dir, assert_keywords)
                if verified:
                    report["templates"][template_name] = "VERIFIED"
                    return True
                else:
                    logger.warning(f"Template '{template_name}' failed verification. Removing and retrying...")
                    if os.path.exists(path):
                        os.remove(path)
            
            if recovery_action:
                recovery_action()
            else:
                driver.press_back()
            driver.human_sleep(2.0)
            
        report["templates"][template_name] = "FAILED"
        report["success"] = False
        return False

    # --- Phase A: Navigation Tabs ---
    # Since OCR often misses the small tab bar text, we use fixed geometric boxes.
    # The tab bar is at the bottom. 5 tabs -> each is w_screen/5 wide.
    # Tab bar height is roughly bottom 8%.
    tab_y_start = int(h_screen * 0.92)
    tab_w = w_screen // 5
    
    # Capture 'tab_profile' (5th tab)
    profile_box = [w_screen - tab_w, tab_y_start, w_screen, h_screen]
    attempt_crop_and_verify("tab_profile", "我", 0.88, ["编辑资料", "粉丝", "赞与收藏"], fixed_box=profile_box)
    
    # Capture 'tab_home' (1st tab)
    home_box = [0, tab_y_start, tab_w, h_screen]
    attempt_crop_and_verify("tab_home", "首页", 0.88, ["搜索", "发现", "推荐"], fixed_box=home_box)
    
    # --- Phase B: Search Input ---
    # Capture 'search_input' (Top right search icon on Home Feed)
    # Usually around x: 80% to 100%, y: 5% to 15%
    search_box = [int(w_screen * 0.8), int(h_screen * 0.05), w_screen, int(h_screen * 0.15)]
    attempt_crop_and_verify("search_input", "搜索", 0.0, ["历史记录", "搜索发现", "猜你想搜", "搜索"], fixed_box=search_box)
    driver.press_back() # Leave search page
    driver.press_back() # Leave search page
    driver.human_sleep(1.0)

    # --- Phase C, D, E: Comment & Post Templates ---
    logger.info("Searching for a valid post to harvest comment templates...")
    post_valid = False
    
    def post_recovery():
        # Simple back to close keyboard, or retry inside post
        driver.press_back()
        driver.human_sleep(1.0)
        
    for attempt in range(10):
        logger.info(f"Post discovery attempt {attempt + 1}/10")
        driver.physical_tap(int(w_screen/2), int(h_screen/2))
        driver.human_sleep(4.0)
        
        # Check if '说点什么' is on screen
        img = driver.screenshot()
        try:
            res = ocr_client.ocr_image(img)
            text_str = " ".join([t for _, t, _ in _parse_ocr_results(res)])
        except Exception:
            text_str = ""
            
        if "说点什么" in text_str or "发送" in text_str or "评论" in text_str:
            post_valid = True
            logger.info("Valid post found! Proceeding with harvest.")
            
            # Phase C: Comment Input
            c_success = attempt_crop_and_verify("comment_input", "说点什么", 0.5, ["发送", "发送给", "@"], recovery_action=post_recovery)
            
            if c_success:
                # Phase D: Send Button (keyboard is open)
                attempt_crop_and_verify("send_button", "发送", 0.5, ["刚刚", "评论", "说点什么"], recovery_action=post_recovery)
                
                # Phase E: Reply Button
                driver.physical_swipe(w_screen//2, int(h_screen*0.8), w_screen//2, int(h_screen*0.4))
                driver.human_sleep(2.0)
                attempt_crop_and_verify("reply_button", "回复", 0.3, ["发送", "说点什么", "回复"], recovery_action=post_recovery)
            
            break # Exit post discovery loop
            
        # Not a valid post, press back and scroll feed
        logger.warning("No comment indicators found. Skipping post.")
        driver.press_back()
        driver.human_sleep(1.0)
        driver.physical_swipe(w_screen//2, int(h_screen*0.8), w_screen//2, int(h_screen*0.2))
        driver.human_sleep(2.0)
        
    if not post_valid:
        logger.error("Failed to find a valid post after 10 attempts.")

    # Finish and return to home
    for _ in range(4):
        driver.press_back()
        driver.human_sleep(1.0)

    logger.info(f"Pipeline finished. Final report: {report}")
    return report

if __name__ == "__main__":
    import argparse
    from mobile_core.agentless_driver import AgentlessMinitouchDriver
    from mobile_core.ocr_client import OCRClient

    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", type=str, default=None)
    args = parser.parse_args()

    driver = AgentlessMinitouchDriver(args.serial)
    ocr = OCRClient()
    
    automated_setup_pipeline(driver, ocr, serial=args.serial)
