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


def _record_crop_timestamp(out_dir, template_name):
    """记录模板采集时间戳到 metadata.json"""
    import json
    from datetime import datetime
    meta_path = os.path.join(out_dir, "metadata.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta[template_name] = {"cropped_at": datetime.now().isoformat()}
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def crop_and_save_via_ocr(driver, ocr_client, text_query, filename, serial=None, 
                          validate=True, y_min_ratio=0.0, x_min_ratio=0.0, exact_match=False):
    """
    Screenshot -> OCR -> find text_query -> crop region -> validate -> save.
    Returns (success: bool, path: str).
    """
    img = driver.clean_screenshot()
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
            _record_crop_timestamp(out_dir, filename)
            return True, out_path

    return False, None

def verify_template_action(driver, ocr_client, template_name, serial, resolution, assert_keywords):
    """
    Load the template via VisionEngine, find it on screen, tap it,
    then use OCR to assert the new screen state contains one of the assert_keywords.
    WARNING: This is DESTRUCTIVE - it actually taps the button. Do NOT use for
    buttons with irreversible side effects (e.g. send_button).
    """
    serial = serial or _auto_detect_serial()
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution)
    vision = VisionEngine(templates_dir)
    img = driver.clean_screenshot()
    match = vision.find_template(img, template_name, threshold=0.7)
    if not match:
        logger.error(f"Verification failed: newly cropped template '{template_name}' could not be matched!")
        return False
    
    logger.info(f"Verification matching successful for '{template_name}' at {match['x']}, {match['y']}. Tapping...")
    driver.physical_tap(match['x'], match['y'])
    driver.human_sleep(4.0, 1.0) # Wait longer for page to load
    
    new_img = driver.clean_screenshot()
    all_text = _get_all_screen_text(ocr_client, new_img)
    
    for kw in assert_keywords:
        if kw in all_text:
            logger.info(f"Verification passed for '{template_name}': Assert keyword '{kw}' found.")
            return True
            
    logger.error(f"Verification failed for '{template_name}': None of assert keywords {assert_keywords} found in new screen state.")
    return False


def verify_template_cross_check(driver, ocr_client, template_name, serial, resolution, 
                                 ocr_text, valid_region=None, max_offset=50):
    """
    Non-destructive multi-signal cross-verification.
    Does NOT tap the button. Instead, uses 3 independent signals:
      1. Template matching: VisionEngine finds the cropped image on screen.
      2. OCR text matching: OCR independently locates the same text at a nearby position.
      3. Region sanity: The matched position falls within a valid screen region.
    All 3 must agree for the template to be considered verified.
    """
    serial = serial or _auto_detect_serial()
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution)
    vision = VisionEngine(templates_dir)
    img = driver.clean_screenshot()
    
    # Signal 1: Template matching
    match = vision.find_template(img, template_name, threshold=0.7)
    if not match:
        logger.error(f"Cross-check failed for '{template_name}': template not matched on screen.")
        return False
    tmpl_x, tmpl_y = match['x'], match['y']
    logger.info(f"Signal 1 (Template): '{template_name}' matched at ({tmpl_x}, {tmpl_y})")
    
    # Signal 2: OCR text position cross-validation
    ocr_matches = ocr_client.find_text(img, ocr_text, conf_threshold=0.5)
    if not ocr_matches:
        logger.error(f"Cross-check failed for '{template_name}': OCR cannot find '{ocr_text}' on screen.")
        return False
    
    # Find the OCR match closest to the template match position
    best_ocr = min(ocr_matches, key=lambda m: abs(m['x'] - tmpl_x) + abs(m['y'] - tmpl_y))
    offset = abs(best_ocr['x'] - tmpl_x) + abs(best_ocr['y'] - tmpl_y)
    
    if offset > max_offset:
        logger.error(f"Cross-check failed for '{template_name}': Template at ({tmpl_x},{tmpl_y}) but OCR '{ocr_text}' at ({best_ocr['x']},{best_ocr['y']}), offset={offset} > {max_offset}")
        return False
    logger.info(f"Signal 2 (OCR): '{ocr_text}' found at ({best_ocr['x']}, {best_ocr['y']}), offset={offset}px")
    
    # Signal 3: Region sanity check
    if valid_region:
        rx_min, ry_min, rx_max, ry_max = valid_region
        if not (rx_min <= tmpl_x <= rx_max and ry_min <= tmpl_y <= ry_max):
            logger.error(f"Cross-check failed for '{template_name}': position ({tmpl_x},{tmpl_y}) outside valid region {valid_region}")
            return False
        logger.info(f"Signal 3 (Region): position within valid region {valid_region}")
    else:
        logger.info(f"Signal 3 (Region): skipped (no region constraint)")
    
    logger.info(f"Cross-check PASSED for '{template_name}': all 3 signals agree.")
    return True

def crop_fixed_region(driver, filename, serial, box, validate=True):
    """Crop a fixed region of the screen and save it."""
    img = driver.clean_screenshot()
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
    _record_crop_timestamp(out_dir, filename)
    return True, out_path

# ─────────── Pipeline Core ───────────

def automated_setup_pipeline(driver, ocr_client, serial=None, watchdog=None, **kwargs):
    """
    Closed-loop UI template cropping pipeline (Upgraded with full-link verification).
    """
    logger.info("=== STARTING FULL AUTO-CROP AND VALIDATION PIPELINE ===")
    
    import subprocess
    adb_cmd = ["adb"] if not serial else ["adb", "-s", serial]
    
    from mobile_core.navigator import Navigator
    from mobile_core.watchdog import PopupWatchdog
    from mobile_core.config import Config
    from mobile_core.vision import VisionEngine
    
    config = Config.load()
    base_templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial or _auto_detect_serial())
    vision = VisionEngine(base_templates_dir)
    navigator = Navigator(driver, vision, ocr_client, config)
    if watchdog is None:
        watchdog = PopupWatchdog(driver, vision, ocr_client, config)
    
    # Force stop app to ensure we start at the home feed
    subprocess.run(adb_cmd + ["shell", "am", "force-stop", "com.xingin.xhs"], timeout=10)
    time.sleep(2)
    
    subprocess.run(adb_cmd + ["shell", "input", "keyevent", "KEYCODE_HOME"], timeout=10)
    time.sleep(1)
    
    # Launch app
    subprocess.run(adb_cmd + ["shell", "monkey", "-p", "com.xingin.xhs", "-c", "android.intent.category.LAUNCHER", "1"], timeout=10)
    driver.human_sleep(5.0) 
    
    # Wait until app is truly loaded (OCR sees '推荐' or '发现' or '首页')
    logger.info("Waiting for app to load to home feed...")
    app_loaded = False
    for _ in range(10):
        img_check = driver.clean_screenshot()
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

    img = driver.clean_screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    report = {"templates": {}, "success": True}
    
    def attempt_crop_and_verify(template_name, ocr_query, y_min_ratio, assert_keywords, 
                                exact_match=False, fixed_box=None, recovery_action=None,
                                destructive=True, cross_check_text=None, valid_region=None):
        """
        destructive=True: tap and assert (default, for navigation buttons)
        destructive=False: cross-check only (for send/reply, no side effects)
        """
        logger.info(f"\n--- Harvesting: {template_name} (mode={'destructive' if destructive else 'cross-check'}) ---")
        for attempt in range(3):
            watchdog.check_and_handle()  # 拦截任意弹窗
            
            if fixed_box:
                success, path = crop_fixed_region(driver, template_name, serial, fixed_box)
            else:
                success, path = crop_and_save_via_ocr(
                    driver, ocr_client, ocr_query, template_name, serial, 
                    y_min_ratio=y_min_ratio, exact_match=exact_match
                )
            if success:
                driver.human_sleep(1.0)
                
                if destructive:
                    verified = verify_template_action(driver, ocr_client, template_name, serial, resolution_dir, assert_keywords)
                else:
                    verified = verify_template_cross_check(
                        driver, ocr_client, template_name, serial, resolution_dir,
                        ocr_text=cross_check_text or ocr_query,
                        valid_region=valid_region
                    )
                    
                if verified:
                    report["templates"][template_name] = "VERIFIED" if destructive else "CROSS-CHECKED"
                    return True
                else:
                    logger.warning(f"Template '{template_name}' failed verification. Removing and retrying...")
                    if os.path.exists(path):
                        os.remove(path)
            
            if recovery_action:
                recovery_action()
            else:
                navigator.go_back()
            driver.human_sleep(2.0)
            
        report["templates"][template_name] = "FAILED"
        report["success"] = False
        return False

    # --- Phase A & B: Fixed-coordinate templates ---
    # Read coordinates from TEMPLATE_REGISTRY (single source of truth)
    # Lazy import to avoid circular dependency (assisted_crop imports auto_crop_templates)
    from tools.assisted_crop import TEMPLATE_REGISTRY as _REGISTRY

    def _registry_box(name):
        """从 TEMPLATE_REGISTRY 获取固定坐标 box (像素值)"""
        region = _REGISTRY[name][3]
        if isinstance(region, tuple):
            return [int(w_screen * region[0]), int(h_screen * region[1]),
                    int(w_screen * region[2]), int(h_screen * region[3])]
        return None

    # Capture 'tab_profile' (5th tab)
    attempt_crop_and_verify("tab_profile", "我", 0.88, ["编辑资料", "粉丝", "赞与收藏"], fixed_box=_registry_box("tab_profile"))

    # Capture 'tab_message' (4th tab)
    attempt_crop_and_verify("tab_message", "消息", 0.88, ["发现群聊", "赞和收藏", "新增关注"], fixed_box=_registry_box("tab_message"))

    # Capture 'tab_home' (1st tab)
    attempt_crop_and_verify("tab_home", "首页", 0.88, ["搜索", "发现", "推荐"], fixed_box=_registry_box("tab_home"))

    # --- Phase B: Search Input ---
    attempt_crop_and_verify("search_input", "搜索", 0.0, ["历史记录", "搜索发现", "猜你想搜", "搜索"], fixed_box=_registry_box("search_input"))
    navigator.go_back()  # 第一次：收起键盘或退出搜索页
    driver.human_sleep(1.0)
    # 校验是否已经回到首页，防止"退过头"
    img_check = driver.clean_screenshot()
    screen_text = _get_all_screen_text(ocr_client, img_check)
    if not ("推荐" in screen_text or "发现" in screen_text or "首页" in screen_text):
        navigator.go_back()  # 第二次：确实还在搜索页，再退一次
    driver.human_sleep(1.0)

    # --- Phase C, D, E: Comment & Post Templates ---
    logger.info("Searching for a valid post to harvest comment templates...")
    post_valid = False
    
    def post_recovery():
        # Simple back to close keyboard, or retry inside post
        navigator.go_back()
        driver.human_sleep(1.0)
        
    for attempt in range(10):
        logger.info(f"Post discovery attempt {attempt + 1}/10")
        
        # Enter the post
        driver.physical_tap(int(w_screen/2), int(h_screen/2))
        driver.human_sleep(4.0)
        
        # Force open the comment sheet by blindly tapping the comment icon
        # The comment icon is usually at the bottom right, around x=85%, y=96%
        comment_icon_x = int(w_screen * 0.85)
        comment_icon_y = int(h_screen * 0.96)
        logger.info(f"Blind tapping comment icon at ({comment_icon_x}, {comment_icon_y})...")
        driver.physical_tap(comment_icon_x, comment_icon_y)
        driver.human_sleep(2.0)
        
        # Check if the white comment sheet is open (high contrast, easy OCR)
        img = driver.clean_screenshot()
        try:
            res = ocr_client.ocr_image(img)
            text_str = " ".join([t for _, t, _ in _parse_ocr_results(res)])
        except Exception:
            text_str = ""
            
        if "说点什么" in text_str:
            post_valid = True
            logger.info("Comment sheet successfully opened ('说点什么' found)! Proceeding with harvest.")
            
            # Phase C: Comment Input (on the white background)
            c_success = attempt_crop_and_verify("comment_input", "说点什么", 0.5, ["发送", "发送给", "@"], recovery_action=post_recovery)
            
            if c_success:
                # Type text so that '发送' button appears
                logger.info("Checking keyboard focus before typing...")
                driver.human_sleep(2.0)
                # Watchdog: OCR 校验键盘是否弹出
                img_kb = driver.clean_screenshot()
                if ocr_client.find_text(img_kb, "发送", conf_threshold=0.6) or ocr_client.find_text(img_kb, "发布", conf_threshold=0.6):
                    logger.info("Keyboard opened successfully. Typing text to reveal send button...")
                    subprocess.run(adb_cmd + ["shell", "input", "text", "hello"], timeout=10)
                    driver.human_sleep(2.0)
    
                    # Phase D: Send Button (非破坏性交叉校验，绝不真的点击发送)
                    send_region = (int(w_screen * 0.6), 0, w_screen, int(h_screen * 0.6))
                    attempt_crop_and_verify("send_button", "发送", 0.5, [],
                                            recovery_action=post_recovery,
                                            destructive=False, cross_check_text="发送",
                                            valid_region=send_region)
                    
                    # 清除已输入的文字，绝不留下痕迹
                    logger.info("Clearing typed text to avoid accidental send...")
                    subprocess.run(adb_cmd + ["shell", "input", "keyevent", "KEYCODE_MOVE_END"], timeout=10)
                    for _ in range(10):
                        subprocess.run(adb_cmd + ["shell", "input", "keyevent", "KEYCODE_DEL"], timeout=10)
                else:
                    logger.warning("Keyboard did not open. Aborting send_button harvest for this post.")
                
                # Phase E: Reply Button
                # Ensure keyboard is closed first
                navigator.go_back()
                driver.human_sleep(1.0)
                
                # Scroll down the comment sheet slightly to find comments
                img_before_swipe = driver.clean_screenshot()
                driver.physical_swipe(w_screen//2, int(h_screen*0.8), w_screen//2, int(h_screen*0.4))
                driver.human_sleep(2.0)
                
                # Watchdog: 防卡死校验
                img_after_swipe = driver.clean_screenshot()
                if img_before_swipe is not None and img_after_swipe is not None:
                    roi_b = img_before_swipe[int(h_screen*0.3):int(h_screen*0.7), int(w_screen*0.2):int(w_screen*0.8)]
                    roi_a = img_after_swipe[int(h_screen*0.3):int(h_screen*0.7), int(w_screen*0.2):int(w_screen*0.8)]
                    if roi_b.shape == roi_a.shape and roi_b.size > 0:
                        err = np.sum((roi_b.astype("float") - roi_a.astype("float")) ** 2) / float(roi_b.size)
                        if err < 1.0:
                            logger.warning("Failed to scroll comment section (MSE < 1.0). Skipping reply_button harvest.")
                            c_success = False # Force break
                            
                if c_success:
                    # 回复按钮也用非破坏性交叉校验
                    reply_region = (0, int(h_screen * 0.2), w_screen, int(h_screen * 0.9))
                    attempt_crop_and_verify("reply_button", "回复", 0.3, [],
                                            recovery_action=post_recovery,
                                            destructive=False, cross_check_text="回复",
                                            valid_region=reply_region)
            
            break # Exit post discovery loop
            
        # If comment sheet didn't open or empty, back out and try next post
        logger.warning("Failed to open comment sheet or empty. Closing post and skipping...")
        navigator.go_back() # Close sheet if it partially opened
        driver.human_sleep(1.0)
        navigator.go_back() # Exit post
        driver.human_sleep(1.0)
        driver.physical_swipe(w_screen//2, int(h_screen*0.8), w_screen//2, int(h_screen*0.2))
        driver.human_sleep(2.0)
        
    if not post_valid:
        logger.error("Failed to find a valid post after 10 attempts.")

    # Finish and return to home
    navigator.go_home()

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
