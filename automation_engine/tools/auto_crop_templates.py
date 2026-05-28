import os
import time
import cv2
import numpy as np
import logging
from mobile_core.ocr_client import OCRServiceError

logger = logging.getLogger(__name__)

def _parse_ocr_results(results):
    parsed = []
    for res in results:
        try:
            box, (text, conf) = res
            parsed.append((box, text, conf))
        except:
            pass
    return parsed

def _validate_template(cropped_img):
    if cropped_img.size == 0:
        return False, "EMPTY_CROP"
    variance = np.var(cropped_img)
    if variance < 50:
        return False, "LOW_VARIANCE"
    return True, "OK"

def crop_and_save_via_ocr(driver, ocr_client, text_query, filename, serial=None,
                          fallback_box=None, validate=True, y_min_ratio=0.0, x_min_ratio=0.0):
    img = driver.screenshot()
    h_screen, w_screen = img.shape[:2]
    resolution_dir = f"{w_screen}x{h_screen}"
    if serial:
        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution_dir)
    else:
        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", resolution_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{filename}.png")

    try:
        results = ocr_client.ocr_image(img)
    except OCRServiceError as e:
        logger.error(f"OCR Server Error: {e}")
        return False, "OCR_ERROR"
    parsed = _parse_ocr_results(results)

    for box, text, conf in parsed:
        if text_query in text:
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
                    continue

            logger.info(f"OCR Found '{text_query}' -> '{text}' | Conf: {conf:.2f}")
            cv2.imwrite(out_path, cropped)
            return True, "OCR"

    if fallback_box:
        left, top, right, bottom = fallback_box
        left, top = max(0, left), max(0, top)
        right, bottom = min(w_screen, right), min(h_screen, bottom)
        cropped = img[top:bottom, left:right]

        if validate:
            is_valid, reason = _validate_template(cropped)
            if not is_valid:
                return False, f"FALLBACK_INVALID ({reason})"

        cv2.imwrite(out_path, cropped)
        return True, "FALLBACK_BOX"
    return False, "NOT_FOUND"

def _get_all_screen_text(ocr_client, img):
    try:
        results = ocr_client.ocr_image(img)
        parsed = _parse_ocr_results(results)
        return " ".join([text for _, text, _ in parsed])
    except:
        return ""

def automated_setup_pipeline(driver, ocr_client, serial=None, watchdog=None,
                               reply_keywords=None, send_keywords=None, input_placeholder_keywords=None):
    report = {"templates": {}, "success": True}
    logger.info("Industrial Pipeline: Initiating automated XHS UI template extraction...")
    
    reply_search_terms = reply_keywords or ["回复", "Reply", "reply"]
    send_search_terms = send_keywords or ["发送", "发布", "Send"]
    input_box_indicators = input_placeholder_keywords or ["说点什么", "留下你的", "发弹幕", "写评论", "友好评论", "善意评论", "有话要说", "快来评论"]

    driver.ensure_app_foreground()
    time.sleep(6)

    img = driver.screenshot()
    h, w = img.shape[:2]

    post_target = None
    try:
        results = ocr_client.ocr_image(img)
        parsed = _parse_ocr_results(results)
        for box, text, conf in parsed:
            y_center = sum(p[1] for p in box) / len(box)
            x_center = sum(p[0] for p in box) / len(box)
            if h * 0.2 < y_center < h * 0.6 and len(text) > 3:
                post_target = (int(x_center), int(y_center))
                break
    except:
        pass

    if post_target:
        driver.physical_tap(post_target[0], post_target[1])
    else:
        driver.physical_tap(int(w * 0.3), int(h * 0.3))

    time.sleep(5)
    if watchdog: watchdog.check_and_handle()

    found_reply = False
    for attempt in range(2):
        if attempt > 0: time.sleep(2)
        for term in reply_search_terms:
            success, method = crop_and_save_via_ocr(
                driver, ocr_client, term, "reply_button", serial=serial, y_min_ratio=0.5
            )
            if success:
                report["templates"]["reply_button"] = method
                found_reply = True
                break
        if found_reply: break
        driver.human_swipe("up")
        time.sleep(2)

    clicked_input = False
    img_for_input = driver.screenshot()
    try:
        results = ocr_client.ocr_image(img_for_input)
        parsed = _parse_ocr_results(results)
        for box, text, conf in parsed:
            if any(ind in text for ind in input_box_indicators) and conf > 0.5:
                x_center = int(sum([p[0] for p in box]) / 4)
                y_center = int(sum([p[1] for p in box]) / 4)
                if y_center < h * 0.4:
                    continue
                driver.physical_tap(x_center, y_center)
                clicked_input = True
                break
    except:
        pass

    if not clicked_input:
        driver.physical_tap(int(w * 0.3), int(h * 0.96))
        
    time.sleep(5)

    try:
        driver.physical_tap(int(w * 0.5), int(h * 0.8))
        time.sleep(1.5)
    except:
        pass

    if watchdog: watchdog.check_and_handle()

    found_send = False
    for attempt in range(2):
        if attempt > 0: time.sleep(3)
        for term in send_search_terms:
            success, method = crop_and_save_via_ocr(
                driver, ocr_client, term, "send_button", serial=serial, y_min_ratio=0.4, x_min_ratio=0.5
            )
            if success:
                report["templates"]["send_button"] = method
                found_send = True
                break
        if found_send: break

    driver.press_back()
    time.sleep(1)
    driver.press_back()
    time.sleep(1)
    driver.press_back()
    return report

def create_mock_templates_for_testing(w=1080, h=1920, serial=None):
    pass

def main():
    pass

if __name__ == "__main__":
    main()
