import re

with open('tools/auto_crop_templates.py', 'r') as f:
    content = f.read()

# 1. Update crop_and_save_via_ocr signature
content = content.replace(
    'fallback_box=None, validate=True):',
    'fallback_box=None, validate=True, y_min_ratio=0.0, x_min_ratio=0.0):'
)

# 2. Update region filters
filter_logic = """            left, right = int(min(xs)), int(max(xs))
            top, bottom = int(min(ys)), int(max(ys))
            y_center = (top + bottom) / 2
            x_center = (left + right) / 2
            if y_center < h_screen * y_min_ratio:
                continue
            if x_center < w_screen * x_min_ratio:
                continue"""
content = content.replace(
    '            left, right = int(min(xs)), int(max(xs))\n            top, bottom = int(min(ys)), int(max(ys))',
    filter_logic
)

# 3. Update automated_setup_pipeline signature and vars
pipeline_old = """def automated_setup_pipeline(driver, ocr_client, serial=None, watchdog=None):
    \"\"\"
    Industrial template extraction pipeline.
    Returns a structured report dict with per-template results.
    \"\"\"
    report = {"templates": {}, "success": True}

    import logging
    logger = logging.getLogger(__name__) # Ensure logger exists or is global, in this file it's global
    logger.info("Industrial Pipeline: Initiating automated XHS UI template extraction...")"""

# Let's just use regex to replace the function def and first lines
content = re.sub(
    r'def automated_setup_pipeline\(driver,\s*ocr_client,\s*serial=None,\s*watchdog=None\):\s*"""[\s\S]*?"""\s*report\s*=\s*{"templates":\s*{},\s*"success":\s*True}\s*logger\.info\("Industrial Pipeline: Initiating automated XHS UI template extraction..."\)',
    '''def automated_setup_pipeline(driver, ocr_client, serial=None, watchdog=None,
                               reply_keywords=None, send_keywords=None, input_placeholder_keywords=None):
    """
    Industrial template extraction pipeline.
    Returns a structured report dict with per-template results.
    """
    report = {"templates": {}, "success": True}

    logger.info("Industrial Pipeline: Initiating automated XHS UI template extraction...")
    
    reply_search_terms = reply_keywords or ["回复", "Reply", "reply"]
    send_search_terms = send_keywords or ["发送", "发布", "Send"]
    input_box_indicators = input_placeholder_keywords or ["说点什么", "留下你的", "发弹幕", "写评论", "友好评论", "善意评论", "有话要说", "快来评论"]''',
    content
)

# 4. Remove old hardcoded lists
content = content.replace('    reply_search_terms = ["回复", "Reply", "reply"]\n', '')
content = content.replace('    send_search_terms = ["发送", "发布", "Send"]\n', '')
content = content.replace('    input_box_indicators = ["说点什么", "留下你的", "发弹幕", "写评论", "友好评论", "善意评论", "有话要说", "快来评论"]\n', '')

# 5. Add y_min_ratio=0.4 to input box detection (since we don't use crop_and_save here, we just check y_center)
input_check_old = """            if any(ind in text for ind in input_box_indicators) and conf > 0.5:
                x_center = int(sum([p[0] for p in box]) / 4)
                y_center = int(sum([p[1] for p in box]) / 4)
                logger.info(f"Found input box placeholder '{text}', clicking at ({x_center}, {y_center})")"""
input_check_new = """            if any(ind in text for ind in input_box_indicators) and conf > 0.5:
                x_center = int(sum([p[0] for p in box]) / 4)
                y_center = int(sum([p[1] for p in box]) / 4)
                if y_center < h * 0.4:
                    continue  # Ignore input box matches in the top half (likely in posts)
                logger.info(f"Found input box placeholder '{text}', clicking at ({x_center}, {y_center})")"""
content = content.replace(input_check_old, input_check_new)

# 6. Add y_min_ratio to crop_and_save_via_ocr calls
content = re.sub(
    r'crop_and_save_via_ocr\(\s*driver,\s*ocr_client,\s*term,\s*"reply_button",\s*serial=serial\s*\)',
    'crop_and_save_via_ocr(driver, ocr_client, term, "reply_button", serial=serial, y_min_ratio=0.5)',
    content
)

content = re.sub(
    r'crop_and_save_via_ocr\(\s*driver,\s*ocr_client,\s*term,\s*"send_button",\s*serial=serial\s*\)',
    'crop_and_save_via_ocr(driver, ocr_client, term, "send_button", serial=serial, y_min_ratio=0.4, x_min_ratio=0.5)',
    content
)

with open('tools/auto_crop_templates.py', 'w') as f:
    f.write(content)

print("Patched successfully!")
