"""
养号 Pipeline 自定义 Handler 集合。

提供 config/pipelines/farm_session.yaml 中使用的自定义识别和动作。
包含概率分支、视觉颜色强校验、注意力阅读模拟等高级策略。
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from mobile_core.logger import get_logger

if TYPE_CHECKING:
    from mobile_core.pipeline.models import RecognitionResult

logger = get_logger("pipeline.handlers.farm")


# ============================================================
#  内部工具函数
# ============================================================

def _get_ocr_endpoint(anchors) -> str:
    config = anchors.get("_config")
    if config is not None:
        try:
            return config.ocr.endpoint
        except AttributeError:
            pass
    return "http://localhost:8001/ocr"

def _ocr_screen(screen, endpoint: str):
    import cv2
    import requests
    import base64

    try:
        h, w = screen.shape[:2]
        scale = 1.0
        img = screen
        if max(h, w) > 1600:
            scale = 1600 / max(h, w)
            img = cv2.resize(screen, (int(w * scale), int(h * scale)))

        _, buf = cv2.imencode('.png', img)
        b64 = base64.b64encode(buf).decode()

        import time
        for attempt in range(3):
            try:
                session = requests.Session()
                session.trust_env = False
                resp = session.post(
                    endpoint,
                    json={"image_base64": b64},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "success":
                    break
            except requests.exceptions.ConnectionError as e:
                if attempt == 2:
                    logger.critical(f"_ocr_screen 连接失败: {e}。OCR 服务不在线。自动终止脚本。")
                    import os
                    os._exit(1)
                else:
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"_ocr_screen error: {e}")
                return []
        else:
            return []

        results = []
        for item in data.get("results", []):
            try:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                box = item[0]
                txt_info = item[1]
                if isinstance(txt_info, (list, tuple)) and len(txt_info) >= 2:
                    text, conf = str(txt_info[0]), float(txt_info[1])
                elif isinstance(txt_info, str):
                    text, conf = txt_info, 0.0
                else:
                    continue

                if scale != 1.0:
                    box = [[p[0] / scale, p[1] / scale] for p in box]

                cx = int(sum(p[0] for p in box) / len(box))
                cy = int(sum(p[1] for p in box) / len(box))
                results.append({
                    "text": text,
                    "confidence": conf,
                    "box": box,
                    "center": (cx, cy),
                })
            except Exception:
                continue
        return results
    except Exception as e:
        logger.error(f"_ocr_screen error: {e}")
        return []


def _save_farm_record(action_type: str):
    """持久化记录养号行为"""
    import os
    import json
    from datetime import datetime
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    record_file = os.path.join(base_dir, "..", "..", "..", "data", "farmed_actions.json")
    os.makedirs(os.path.dirname(record_file), exist_ok=True)
    if not os.path.exists(record_file):
        with open(record_file, "w", encoding="utf-8") as f:
            json.dump({"history": []}, f)
            
    today_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M:%S")
    try:
        import fcntl
        with open(record_file, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = json.load(f)
                today_record = next((item for item in data.get("history", []) if item["date"] == today_str), None)
                if not today_record:
                    today_record = {"date": today_str, "actions": {"like": 0, "collect": 0, "comment": 0, "follow": 0}, "details": []}
                    data.setdefault("history", []).append(today_record)
                if action_type in today_record["actions"]:
                    today_record["actions"][action_type] += 1
                today_record.setdefault("details", []).append({"time": time_str, "action": action_type})
                f.seek(0)
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.truncate()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        logger.error(f"Failed to save farm record: {e}")


# ============================================================
#  识别 Handler (Recognition)
# ============================================================

def verify_color_shift(screen, spec, anchors):
    """
    视觉颜色强校验 (点赞/收藏状态验证)。
    对比 anchors 中存储的前一帧，验证指定区域的颜色分布。
    """
    from mobile_core.pipeline.models import RecognitionResult
    import cv2
    import numpy as np

    params = spec.params or {}
    target_color = str(params.get("color", "red"))
    anchor_key = str(params.get("before_img_anchor", "farm_img_before"))
    box = params.get("box", [0, 0, screen.shape[1], screen.shape[0]]) # [x, y, w, h]
    
    img_before = anchors.get(anchor_key)
    if img_before is None:
        logger.warning("verify_color_shift: no before_img found")
        return RecognitionResult(matched=False)

    try:
        x, y, bw, bh = box
        h, w = img_before.shape[:2]
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        bw = max(1, min(bw, w - x))
        bh = max(1, min(bh, h - y))
        
        roi_b = img_before[y:y+bh, x:x+bw]
        roi_a = screen[y:y+bh, x:x+bw]
        
        if roi_b.shape != roi_a.shape or roi_b.size == 0:
            return RecognitionResult(matched=False)
            
        hsv_b = cv2.cvtColor(roi_b, cv2.COLOR_BGR2HSV)
        hsv_a = cv2.cvtColor(roi_a, cv2.COLOR_BGR2HSV)
        
        if target_color == "red":
            mask_b1 = cv2.inRange(hsv_b, np.array([0, 70, 50]), np.array([10, 255, 255]))
            mask_b2 = cv2.inRange(hsv_b, np.array([170, 70, 50]), np.array([180, 255, 255]))
            mask_b = cv2.bitwise_or(mask_b1, mask_b2)
            mask_a1 = cv2.inRange(hsv_a, np.array([0, 70, 50]), np.array([10, 255, 255]))
            mask_a2 = cv2.inRange(hsv_a, np.array([170, 70, 50]), np.array([180, 255, 255]))
            mask_a = cv2.bitwise_or(mask_a1, mask_a2)
        elif target_color == "yellow":
            mask_b = cv2.inRange(hsv_b, np.array([15, 70, 50]), np.array([35, 255, 255]))
            mask_a = cv2.inRange(hsv_a, np.array([15, 70, 50]), np.array([35, 255, 255]))
        else:
            return RecognitionResult(matched=False)
            
        pixels_b = cv2.countNonZero(mask_b)
        pixels_a = cv2.countNonZero(mask_a)
        
        threshold = (bw * bh) * 0.03
        shifted = pixels_a > (pixels_b + threshold)
        is_already_target = pixels_a > (bw * bh * 0.15)
        
        if shifted or is_already_target:
            _save_farm_record(target_color) # simplistic tracking
            return RecognitionResult(matched=True, confidence=1.0)
            
        return RecognitionResult(matched=False)
    except Exception as e:
        logger.error(f"verify_color_shift error: {e}")
        return RecognitionResult(matched=False)


# ============================================================
#  动作 Handler (Action)
# ============================================================

def farm_random_branch(driver, spec, reco_result, anchors, **params):
    """
    基于概率决定养号会话的下一跳 (Random Routing)。
    将决定存入 anchor `farm_next_branch`。
    """
    config = anchors.get("_config")
    mood_manager = anchors.get("_mood_manager")
    
    search_prob = 0.05
    profile_prob = 0.05
    enter_prob = 0.2
    
    if config:
        try:
            search_prob = config.farm.random_search_probability
            profile_prob = config.farm.visit_profile_probability
            enter_prob = config.farm.enter_post_probability
        except AttributeError:
            pass

    # 应用情绪倍率
    if mood_manager:
        mood_manager.update()
        multiplier = mood_manager.get_multiplier()
        state_name = mood_manager.get_state().name
        
        # 在 BORED 状态下，大幅削弱进贴、主页和搜索的概率，增加 browse（纯滑动）概率
        # 在 EXCITED 状态下，大幅增加进贴概率
        enter_prob = min(0.9, enter_prob * multiplier)
        search_prob = min(0.5, search_prob * multiplier)
        profile_prob = min(0.5, profile_prob * multiplier)
        logger.debug(f"Farm random branch probabilities adjusted by Mood {state_name} ({multiplier}x)")

    roll = random.random()
    if roll < search_prob:
        branch = "search"
    elif roll < search_prob + profile_prob:
        branch = "profile"
    elif roll < search_prob + profile_prob + enter_prob:
        branch = "enter_post"
    else:
        branch = "browse"
        
    logger.info(f"养号随机分支决定为: {branch}")
    anchors.set("farm_next_branch", branch)
    return True


def check_branch(screen, spec, anchors):
    """用于 Recognition，根据 farm_next_branch 选择路径"""
    from mobile_core.pipeline.models import RecognitionResult
    params = spec.params or {}
    expected = params.get("expected")
    current = anchors.get("farm_next_branch")
    return RecognitionResult(matched=(current == expected))


def farm_extract_anchor(driver, spec, reco_result, anchors, **params):
    """
    进入帖子前，截取封面文字作为身份锚点。
    """
    screen = driver.screenshot()
    
    # Simple extraction of center screen text
    h, w = screen.shape[:2]
    cx, cy = w//2, h//2
    # crop center
    crop = screen[max(0, cy-200):min(h, cy+200), max(0, cx-200):min(w, cx+200)]
    
    endpoint = _get_ocr_endpoint(anchors)
    ocr_results = _ocr_screen(crop, endpoint)
    
    anchor_text = ""
    for r in ocr_results:
        text = r["text"]
        if len(text) >= 2:
            anchor_text = text[:6] if len(text) > 6 else text
            break
            
    anchors.set("farm_post_anchor", anchor_text)
    logger.info(f"养号提取的锚点: '{anchor_text}'")
    return True


def farm_attention_read(driver, spec, reco_result, anchors, **params):
    """
    模拟注意力阅读：按正态分布总时长，分段等待，概率触发真实的滑动浏览和微滑动。
    """
    mu = params.get("mu", 5.0)
    sigma = params.get("sigma", 2.0)
    
    import numpy as np
    total_sleep_time = max(3.0, np.random.normal(mu, sigma))
    logger.info(f"养号注意力阅读中... 时长 {total_sleep_time:.1f}秒")
    
    elapsed = 0.0
    while elapsed < total_sleep_time:
        # 切片等待时间改小，让循环次数增多
        chunk = random.uniform(1.0, 2.5)
        if elapsed + chunk > total_sleep_time:
            chunk = total_sleep_time - elapsed
        time.sleep(chunk)
        elapsed += chunk
        
        # 不再阻断最后一轮的判定，60% 概率产生一次交互行为
        if random.random() < 0.6:
            action_roll = random.random()
            if action_roll < 0.4:
                # 40% 概率：真人大滑动向下（看长图或刷评论）
                if hasattr(driver, "human_swipe"):
                    logger.info("注意力阅读: 向下大滑动 (浏览更多)")
                    driver.human_swipe("down")
            elif action_roll < 0.5:
                # 10% 概率：真人回滑向上（返回看前面的图）
                if hasattr(driver, "human_swipe"):
                    logger.info("注意力阅读: 向上大滑动 (回看)")
                    driver.human_swipe("up")
            else:
                # 50% 概率：微弱抖动（盯屏模拟注意力）
                if hasattr(driver, "micro_swipe"):
                    driver.micro_swipe()
                
    return True

def save_before_image(driver, spec, reco_result, anchors, **params):
    anchors.set("farm_img_before", driver.screenshot())
    return True

def farm_like_collect_probability(screen, spec, anchors):
    """根据疲劳度和 Persona 决定是否触发点赞/收藏"""
    from mobile_core.pipeline.models import RecognitionResult
    params = spec.params or {}
    action = params.get("action", "like")
    
    config = anchors.get("_config")
    base_prob = 0.1 if action == "like" else 0.03
    
    if config:
        try:
            if action == "like": base_prob = config.farm.like_probability
            elif action == "collect": base_prob = config.farm.collect_probability
            elif action == "comment": base_prob = config.farm.comment_probability
        except AttributeError:
            pass
            
    # Simply use random check here
    roll = random.random()
    if roll < base_prob:
        return RecognitionResult(matched=True)
    return RecognitionResult(matched=False)
