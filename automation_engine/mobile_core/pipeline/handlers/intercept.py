"""
话题搜索评论截流 Pipeline 自定义 Handler 集合。

本模块提供 ``config/pipelines/intercept_comment.yaml`` 中所有
``recognition.handler`` 和 ``action.handler`` 引用的函数实现。

Handler 分为两类:
    **识别 (Recognition)** — 被 CustomProvider 调用
        签名: ``fn(screen, spec, anchors) -> RecognitionResult | bool``
        注意: spec.params 中携带 YAML 中配置的额外参数

    **动作 (Action)** — 被 CustomAction 调用
        签名: ``fn(driver, spec, reco_result, anchors, **params) -> bool``
        注意: spec.params 已被解构为 **params 传入

设计原则:
    - 所有函数均为 **模块级函数** (非类方法)
    - 重型依赖 (cv2, requests, numpy) 在函数内部惰性导入
    - 不依赖全局状态，所有上下文通过 anchors / params 传递
    - 异常不向上抛出 (自动化不能崩)，仅通过返回值表达成功/失败
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mobile_core.logger import get_logger

if TYPE_CHECKING:
    import numpy as np
    from mobile_core.pipeline.models import (
        ActionSpec,
        AnchorStore,
        RecognitionResult,
        RecognitionSpec,
    )

logger = get_logger("pipeline.handlers.intercept")


# ============================================================
#  内部工具函数
# ============================================================

def _ocr_screen(screen, endpoint: str = "http://localhost:8001/ocr"):
    """
    直接调用 OCR 微服务，不依赖 OCRClient 实例。

    对截图 numpy array 进行 OCR 识别。
    自动缩放超大图像以提升速度，结果坐标回映射到原始分辨率。

    Args:
        screen: BGR numpy array (OpenCV 格式截图)
        endpoint: OCR 微服务地址

    Returns:
        list[dict]: 每个元素 = {"text": str, "confidence": float,
                    "box": [[x,y], ...], "center": (cx, cy)}
        失败时返回空列表。
    """
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

        resp = requests.post(
            endpoint,
            json={"image_base64": b64},
            timeout=30,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "success":
            logger.warning(f"OCR 服务返回非 success: {data.get('message')}")
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
                elif isinstance(txt_info, (list, tuple)) and len(txt_info) >= 1:
                    text, conf = str(txt_info[0]), 0.0
                elif isinstance(txt_info, str):
                    text, conf = txt_info, 0.0
                else:
                    continue

                # 坐标回映射到原始分辨率
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
        logger.error(f"_ocr_screen 调用失败: {e}")
        return []


def _detect_card_contours(screen, min_area_ratio: float = 0.03):
    """
    使用 OpenCV 边缘+轮廓检测瀑布流卡片区域。

    移植自 VisionEngine.detect_cards_waterfall() 逻辑，
    但作为无状态函数直接在 screen 上操作。

    Args:
        screen: BGR numpy array
        min_area_ratio: 卡片最小面积 / 屏幕面积 比值

    Returns:
        list[dict]: 每个元素 = {"id": int, "x": cx, "y": cy, "w": w, "h": h}
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    kernel = np.ones((5, 5), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(
        dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    h_screen, w_screen = screen.shape[:2]
    min_area = h_screen * w_screen * min_area_ratio

    cards = []
    for idx, cnt in enumerate(contours):
        x, y, w, h = cv2.boundingRect(cnt)
        if w * h > min_area:
            cards.append({
                "id": idx,
                "x": x + w // 2,
                "y": y + h // 2,
                "w": w,
                "h": h,
            })

    # 从上到下、从左到右排序
    cards.sort(key=lambda c: (c["y"] // 100, c["x"]))
    return cards


def _get_ocr_endpoint(anchors) -> str:
    """从 anchors 中解析 OCR 端点，支持多种注入方式。"""
    # 优先从 _config 对象获取
    config = anchors.get("_config")
    if config is not None:
        try:
            return config.ocr.endpoint
        except AttributeError:
            pass

    # 兜底默认值
    return "http://localhost:8001/ocr"


# ============================================================
#  识别 Handler (Recognition) — 被 CustomProvider 调用
#  签名: fn(screen, spec, anchors) -> RecognitionResult | bool
# ============================================================

def detect_search_results_loaded(screen, spec, anchors):
    """
    检测搜索结果是否已加载 — 瀑布流卡片计数。

    识别策略 (多层降级):
        1. OpenCV 轮廓检测卡片区域 (快速、无网络依赖)
        2. 全屏 OCR 检测帖子标题文本模式 (兜底)

    YAML params:
        min_cards (int): 最少检测到多少张卡片算加载完成, 默认 2
        card_min_area_ratio (float): 卡片最小面积比, 默认 0.03

    Returns:
        RecognitionResult(matched=True) 当检测到足够多的卡片
    """
    from mobile_core.pipeline.models import RecognitionResult

    params = spec.params or {}
    min_cards = int(params.get("min_cards", 2))
    area_ratio = float(params.get("card_min_area_ratio", 0.03))

    try:
        # 方案 1: 轮廓检测
        cards = _detect_card_contours(screen, min_area_ratio=area_ratio)
        if len(cards) >= min_cards:
            logger.info(
                f"搜索结果已加载: 检测到 {len(cards)} 张卡片 (>= {min_cards})"
            )
            return RecognitionResult(
                matched=True,
                confidence=min(1.0, len(cards) / max(1, min_cards)),
                raw={"cards": cards, "count": len(cards)},
            )

        # 方案 2: OCR 兜底 — 检测多条长文本 (帖子标题通常 > 4 字)
        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(screen, endpoint=endpoint)
        title_like = [
            r for r in ocr_results
            if r["confidence"] > 0.5 and len(r["text"]) > 4
        ]
        if len(title_like) >= min_cards:
            logger.info(
                f"搜索结果已加载 (OCR 兜底): {len(title_like)} 条标题文本"
            )
            return RecognitionResult(
                matched=True,
                confidence=0.8,
                raw={"ocr_titles": len(title_like)},
            )

        logger.debug(
            f"搜索结果未加载: 卡片={len(cards)}, OCR标题={len(title_like)}"
        )
        return RecognitionResult(matched=False)

    except Exception as e:
        logger.error(f"detect_search_results_loaded 异常: {e}")
        return RecognitionResult(matched=False)


def check_targets_not_empty(screen, spec, anchors):
    """
    检查 anchors 中目标帖子列表是否非空。

    纯数据检查，不依赖屏幕图像。用于 Pipeline 条件分支。

    YAML params:
        anchor_key (str): anchors 中存储目标列表的键名, 默认 "target_posts"

    Returns:
        RecognitionResult(matched=True) 当目标列表非空
    """
    from mobile_core.pipeline.models import RecognitionResult

    params = spec.params or {}
    anchor_key = str(params.get("anchor_key", "target_posts"))

    targets = anchors.get(anchor_key)
    if targets and isinstance(targets, list) and len(targets) > 0:
        logger.info(f"目标列表非空: {anchor_key} 含 {len(targets)} 个目标")
        return RecognitionResult(
            matched=True,
            confidence=1.0,
            raw={"count": len(targets)},
        )

    logger.info(f"目标列表为空: {anchor_key}")
    return RecognitionResult(matched=False)


def verify_post_identity(screen, spec, anchors):
    """
    帖子身份校验 — OCR 验证目标帖子标题锚字符是否出现在当前页面。

    防止点击到广告或错位帖子。
    移植自 intercept_flow.py 的 Identity Mismatch 校验逻辑。

    YAML params:
        anchor_key (str): 存储当前目标帖子信息的 anchor 键, 默认 "current_target"
        anchor_chars (int): 从标题取前 N 字作为锚字符, 默认 4
        title_field (str): 帖子 dict 中标题字段名, 默认 "title"

    Returns:
        RecognitionResult(matched=True) 当锚字符在屏幕 OCR 文本中被找到
    """
    from mobile_core.pipeline.models import RecognitionResult

    params = spec.params or {}
    anchor_key = str(params.get("anchor_key", "current_target"))
    anchor_chars = int(params.get("anchor_chars", 4))
    title_field = str(params.get("title_field", "title"))

    try:
        # 获取目标帖子标题
        target = anchors.get(anchor_key)
        if not target:
            logger.warning(f"verify_post_identity: anchor '{anchor_key}' 不存在")
            return RecognitionResult(matched=False)

        if isinstance(target, dict):
            title = str(target.get(title_field, "")).strip()
        else:
            title = str(target).strip()

        if not title:
            logger.warning("verify_post_identity: 目标标题为空，跳过校验")
            # 无标题则视为通过 (允许流程继续)
            return RecognitionResult(matched=True, confidence=0.5)

        # 提取锚字符 (取前 N 字)
        anchor = title[:anchor_chars] if len(title) > anchor_chars else title

        # OCR 全屏
        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(screen, endpoint=endpoint)
        all_text = " ".join(r["text"] for r in ocr_results)

        if anchor in all_text:
            logger.info(f"[Identity Verified] 锚字符 '{anchor}' 匹配成功")
            return RecognitionResult(
                matched=True,
                confidence=1.0,
                text=anchor,
            )

        logger.warning(
            f"[Identity Mismatch] 锚字符 '{anchor}' 未在页面中找到"
        )
        return RecognitionResult(matched=False, text=anchor)

    except Exception as e:
        logger.error(f"verify_post_identity 异常: {e}")
        return RecognitionResult(matched=False)


def verify_comment_posted(screen, spec, anchors):
    """
    评论发送校验 — OCR 验证评论内容前 N 字是否出现在屏幕上。

    移植自 commenter.py 的 _verify_comment() 逻辑。
    用于确认评论是否成功"上墙" (未被影子封禁)。

    YAML params:
        anchor_key (str): 存储评论文本的 anchor 键, 默认 "comment_text"
        verify_chars (int): 取评论前 N 字做校验, 默认 4
        search_roi (list): 可选的搜索区域 [x, y, w, h] (百分比), 默认全屏

    Returns:
        RecognitionResult(matched=True) 当评论文本片段在屏幕中被找到
    """
    from mobile_core.pipeline.models import RecognitionResult

    params = spec.params or {}
    anchor_key = str(params.get("anchor_key", "comment_text"))
    verify_chars = int(params.get("verify_chars", 4))

    try:
        comment_text = anchors.get(anchor_key)
        if not comment_text or not isinstance(comment_text, str):
            logger.warning(
                f"verify_comment_posted: anchor '{anchor_key}' 为空或类型错误"
            )
            return RecognitionResult(matched=False)

        check_str = comment_text[:verify_chars]
        if not check_str:
            logger.warning("verify_comment_posted: 评论文本太短，无法校验")
            return RecognitionResult(matched=False)

        # ROI 裁剪 (可选)
        import cv2
        roi_screen = screen
        search_roi = params.get("search_roi")
        if search_roi and isinstance(search_roi, (list, tuple)) and len(search_roi) >= 4:
            h_s, w_s = screen.shape[:2]
            rx = int(search_roi[0] * w_s if search_roi[0] <= 1.0 else search_roi[0])
            ry = int(search_roi[1] * h_s if search_roi[1] <= 1.0 else search_roi[1])
            rw = int(search_roi[2] * w_s if search_roi[2] <= 1.0 else search_roi[2])
            rh = int(search_roi[3] * h_s if search_roi[3] <= 1.0 else search_roi[3])
            rx, ry = max(0, rx), max(0, ry)
            rw = max(1, min(rw, w_s - rx))
            rh = max(1, min(rh, h_s - ry))
            roi_screen = screen[ry:ry + rh, rx:rx + rw]

        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(roi_screen, endpoint=endpoint)
        all_text = " ".join(r["text"] for r in ocr_results)

        if check_str in all_text:
            logger.info(
                f"评论校验通过: '{check_str}' 已出现在屏幕上"
            )
            
            # 记录到去重文件
            try:
                current_keyword = anchors.get("current_keyword", "unknown")
                # 尝试从 target_posts 获取当前目标，或者从 anchors 中取
                target_posts = anchors.get("target_posts")
                if target_posts and len(target_posts) > 0:
                    t = target_posts[0]
                    import hashlib
                    title_hash = hashlib.md5(t["title"].encode('utf-8')).hexdigest()[:8]
                    post_id = f"{current_keyword}_{title_hash}"
                    
                    config = anchors.get("_config")
                    if config is not None:
                        dedup_file = getattr(config.intercept, "dedup_record_file", None)
                        if dedup_file:
                            import os
                            import json
                            os.makedirs(os.path.dirname(dedup_file), exist_ok=True)
                            records = set()
                            if os.path.exists(dedup_file):
                                with open(dedup_file, "r") as f:
                                    records = set(json.load(f))
                            records.add(post_id)
                            with open(dedup_file, "w") as f:
                                json.dump(list(records), f)
                            logger.info(f"已将 {post_id} 写入去重记录")
            except Exception as e:
                logger.error(f"写入去重记录异常: {e}")
                
            return RecognitionResult(matched=True, confidence=1.0, text=check_str)

        logger.warning(
            f"评论校验失败: '{check_str}' 未在屏幕上找到 (可能被影子封禁)"
        )
        return RecognitionResult(matched=False, text=check_str)

    except Exception as e:
        logger.error(f"verify_comment_posted 异常: {e}")
        return RecognitionResult(matched=False)


# ============================================================
#  动作 Handler (Action) — 被 CustomAction 调用
#  签名: fn(driver, spec, reco_result, anchors, **params) -> bool
# ============================================================

def detect_popup(screen, spec, anchors):
    """
    弹窗检测识别 — OCR 扫描屏幕，判断是否存在弹窗/风控关键词。

    配合 PopupHandler 节点的 JumpBack 模式使用。
    此函数仅做识别 (是否有弹窗)，实际关闭动作由 dismiss_popup 完成。

    YAML params:
        auto_dismiss (bool): 是否自动检测 (目前固定 True)
        dismiss_keywords (list): 弹窗关键词, 默认内置列表
        risk_keywords (list): 风控关键词, 默认内置列表

    Returns:
        RecognitionResult(matched=True) 当检测到弹窗关键词
    """
    from mobile_core.pipeline.models import RecognitionResult

    params = spec.params or {}
    dismiss_kws = params.get("dismiss_keywords", [
        "我知道了", "确定", "取消", "关闭", "稍后再说", "暂不", "允许",
        "跳过", "以后再说", "暂不升级",
    ])
    risk_kws = params.get("risk_keywords", [
        "账号异常", "操作频繁", "验证", "安全", "账号冻结",
        "滑块验证", "绑定手机", "身份验证",
    ])
    all_keywords = dismiss_kws + risk_kws

    try:
        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(screen, endpoint=endpoint)

        if not ocr_results:
            return RecognitionResult(matched=False)

        all_text = " ".join(r["text"] for r in ocr_results)

        for kw in all_keywords:
            if kw in all_text:
                logger.warning(f"检测到弹窗关键词: '{kw}'")
                # 记录弹窗位置供 dismiss_popup 使用
                for r in ocr_results:
                    if kw in r["text"]:
                        return RecognitionResult(
                            matched=True,
                            confidence=1.0,
                            text=kw,
                            position=r["center"],
                            raw={"keyword": kw, "ocr_results": ocr_results},
                        )

        return RecognitionResult(matched=False)

    except Exception as e:
        logger.error(f"detect_popup 异常: {e}")
        return RecognitionResult(matched=False)



def extract_and_filter_results(driver, spec, reco_result, anchors, **params):
    """
    提取并过滤搜索结果 — OCR 提取可见帖子标题，按关键词过滤。

    移植自 searcher._extract_search_results() + filter_by_keywords() 逻辑。
    结果存入 anchors[output_anchor] 供后续节点使用。

    YAML params (通过 **params):
        output_anchor (str): 过滤后的目标列表存储键名, 默认 "target_posts"
        title_filter_keywords (list): 标题过滤关键词列表
        min_title_length (int): 标题最小字数, 默认 4
        min_confidence (float): OCR 最低置信度, 默认 0.5

    Returns:
        True 表示执行成功 (无论是否找到目标)
    """
    output_anchor = str(params.get("output_anchor", "target_posts"))
    min_title_len = int(params.get("min_title_length", 4))
    min_conf = float(params.get("min_confidence", 0.5))

    # 从 params 或 anchors 获取过滤关键词
    filter_keywords = params.get("title_filter_keywords")
    if not filter_keywords:
        # 尝试从 config 获取
        config = anchors.get("_config")
        if config is not None:
            try:
                filter_keywords = config.intercept.title_filter_keywords
            except AttributeError:
                pass
    if not filter_keywords:
        filter_keywords = []

    try:
        # 截图 + OCR
        screen = driver.screenshot()
        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(screen, endpoint=endpoint)

        # 提取疑似帖子标题 (长度 > min_title_len, 置信度 > min_conf)
        raw_posts = []
        for r in ocr_results:
            text = r["text"].strip()
            if len(text) >= min_title_len and r["confidence"] >= min_conf:
                cx, cy = r["center"]
                raw_posts.append({
                    "title": text,
                    "position": [cx, cy],
                    "x": cx,
                    "y": cy,
                    "confidence": r["confidence"],
                })

        logger.info(f"OCR 提取到 {len(raw_posts)} 条疑似帖子标题")

        # 按关键词过滤
        if filter_keywords:
            targets = []
            for post in raw_posts:
                title = post["title"]
                if any(kw in title for kw in filter_keywords):
                    targets.append(post)
                    logger.info(f"  ✓ 匹配: '{title}'")
                else:
                    logger.debug(f"  ✗ 跳过: '{title}'")
            logger.info(
                f"关键词过滤: {len(targets)}/{len(raw_posts)} 条匹配"
            )
        else:
            # 无过滤关键词时全部保留
            targets = raw_posts
            logger.info("无过滤关键词，保留全部结果")

        # 去重 (基于标题去重)
        seen_titles = set()
        unique_targets = []
        for t in targets:
            if t["title"] not in seen_titles:
                seen_titles.add(t["title"])
                unique_targets.append(t)
                
        # 基于持久化记录去重 (防重复评论)
        import hashlib
        import json
        import os
        
        current_keyword = anchors.get("current_keyword", "unknown")
        dedup_file = None
        if config is not None:
            try:
                dedup_file = config.intercept.dedup_record_file
            except AttributeError:
                pass
                
        if dedup_file and os.path.exists(dedup_file):
            try:
                with open(dedup_file, "r") as f:
                    commented_set = set(json.load(f))
            except Exception:
                commented_set = set()
        else:
            commented_set = set()
            
        final_targets = []
        for t in unique_targets:
            title_hash = hashlib.md5(t["title"].encode('utf-8')).hexdigest()[:8]
            post_id = f"{current_keyword}_{title_hash}"
            if post_id in commented_set:
                logger.info(f"去重: 帖子 '{t['title']}' 已评论过，跳过 ({post_id})")
            else:
                final_targets.append(t)

        # 存入 anchors
        anchors.set(output_anchor, final_targets)
        logger.info(
            f"已将 {len(final_targets)} 个目标存入 anchors['{output_anchor}'] (原始: {len(unique_targets)})"
        )
        return True

    except Exception as e:
        logger.error(f"extract_and_filter_results 异常: {e}")
        anchors.set(output_anchor, [])
        return False


def camouflage_browse(driver, spec, reco_result, anchors, **params):
    """
    伪装浏览 — 随机浏览帖子制造自然行为模式。

    移植自 farmer._browse_feed() + intercept_flow._camouflage_browse() 逻辑。
    在信息流中随机滑动、偶尔点入帖子阅读后返回。

    YAML params (通过 **params):
        browse_min (int): 最少浏览次数, 默认 2
        browse_max (int): 最多浏览次数, 默认 5
        enter_probability (float): 进入帖子的概率, 默认 0.2
        read_duration_mu (float): 阅读时长均值(秒), 默认 5.0
        read_duration_sigma (float): 阅读时长标准差(秒), 默认 2.0
        screen_width (int): 屏幕宽度, 默认 1080
        screen_height (int): 屏幕高度, 默认 1920

    Returns:
        True 表示伪装浏览完成
    """
    import random
    import time

    browse_min = int(params.get("browse_min", 2))
    browse_max = int(params.get("browse_max", 5))
    enter_prob = float(params.get("enter_probability", 0.2))
    read_mu = float(params.get("read_duration_mu", 5.0))
    read_sigma = float(params.get("read_duration_sigma", 2.0))

    # 获取屏幕尺寸
    screen_w = int(params.get("screen_width", 1080))
    screen_h = int(params.get("screen_height", 1920))
    config = anchors.get("_config")
    if config is not None:
        try:
            screen_w = config.device.screen_width
            screen_h = config.device.screen_height
        except AttributeError:
            pass

    browse_count = random.randint(browse_min, browse_max)
    logger.info(f"开始伪装浏览: 计划浏览 {browse_count} 次")

    try:
        for i in range(browse_count):
            # 人性化下滑
            driver.human_swipe("down")
            driver.human_sleep(2.0, 1.0)

            # 概率性点入帖子
            if random.random() < enter_prob:
                # 随机点击屏幕中部某个位置 (模拟点击卡片)
                x = random.choice([int(screen_w * 0.25), int(screen_w * 0.75)])
                y = random.randint(int(screen_h * 0.3), int(screen_h * 0.7))

                logger.info(
                    f"伪装浏览 [{i + 1}/{browse_count}]: "
                    f"进入帖子 ({x}, {y})"
                )
                driver.physical_tap(x, y)

                # W7: 使用 driver.human_sleep (对数正态) 替代 random.gauss
                driver.human_sleep(read_mu, read_sigma)

                # 返回
                driver.press_back()
                driver.human_sleep(1.5, 0.5)
            else:
                logger.debug(f"伪装浏览 [{i + 1}/{browse_count}]: 仅滑动")

                # 偶尔停顿 (模拟真人扫视)
                if random.random() < 0.3:
                    driver.human_sleep(3.0, 1.5)

        logger.info(f"伪装浏览完成: 实际浏览 {browse_count} 次")
        return True

    except Exception as e:
        logger.error(f"camouflage_browse 异常: {e}")
        return True  # 伪装浏览失败不应阻断主流程


def dismiss_popup(driver, spec, reco_result, anchors, **params):
    """
    弹窗检测与处理 — OCR 识别弹窗关键词并自动关闭。

    移植自 watchdog.py 的 _check_screen_via_ocr() 逻辑。
    支持两级检测:
        1. 风控关键词 (致命) → 记录 anchor 标记 + 返回 False 阻断流程
        2. 普通弹窗关键词 (轻微) → 自动点击关闭 + 返回 True 继续流程

    YAML params (通过 **params):
        dismiss_keywords (list): 可自动关闭的弹窗按钮文字列表
        risk_keywords (list): 风控告警关键词列表 (检测到则立即中止)

    Returns:
        True  — 无弹窗 或 弹窗已自动处理
        False — 检测到风控告警 (流程应中止)
    """
    # 默认关键词 (与 watchdog.py 保持一致)
    dismiss_kws = params.get("dismiss_keywords", [
        "我知道了", "跳过", "以后再说", "暂不升级", "取消", "关闭",
    ])
    risk_kws = params.get("risk_keywords", [
        "安全验证", "滑块验证", "账号冻结", "绑定手机",
        "操作频繁", "账号异常", "身份验证",
    ])

    try:
        screen = driver.screenshot()
        endpoint = _get_ocr_endpoint(anchors)
        ocr_results = _ocr_screen(screen, endpoint=endpoint)

        if not ocr_results:
            return True  # 无 OCR 结果 → 无弹窗

        all_text = " ".join(r["text"] for r in ocr_results)

        # 第一层: 风控关键词检测 (致命)
        for kw in risk_kws:
            if kw in all_text:
                logger.critical(
                    f"🚨 风控告警: 检测到关键词 '{kw}'! 流程应立即中止!"
                )
                anchors.set("_risk_control_triggered", True)
                anchors.set("_risk_keyword", kw)
                return False  # 返回 False 触发 on_error 流程

        # 第二层: 普通弹窗关键词 (自动关闭)
        for kw in dismiss_kws:
            if kw in all_text:
                # 找到关键词位置并点击
                for r in ocr_results:
                    if kw in r["text"]:
                        cx, cy = r["center"]
                        logger.warning(
                            f"检测到弹窗 '{kw}'，自动点击关闭 ({cx}, {cy})"
                        )
                        driver.physical_tap(cx, cy)
                        driver.human_sleep(2.0, 1.0)
                        return True

        # 无弹窗
        return True

    except Exception as e:
        logger.error(f"dismiss_popup 异常: {e}")
        return True  # 异常时不阻断主流程


def force_restart_app(driver, spec, reco_result, anchors, **params):
    """
    强制重启应用 — 用于错误恢复。

    通过 ADB force-stop 杀死应用进程，等待后重新启动。
    移植自 navigator.ensure_app_foreground() 逻辑。

    YAML params (通过 **params):
        package (str): 应用包名, 默认 "com.xingin.xhs"
        wait_after_restart (int): 重启后等待毫秒数, 默认 5000

    Returns:
        True — 重启成功
        False — 重启失败
    """
    import subprocess
    import time
    import random

    package = str(params.get("package", "com.xingin.xhs"))
    wait_ms = int(params.get("wait_after_restart", 5000))
    wait_sec = wait_ms / 1000.0

    try:
        # 获取 ADB 前缀
        adb_prefix = getattr(driver, "adb_prefix", ["adb"])

        # 1. 强制停止应用
        logger.info(f"正在强制停止应用: {package}")
        stop_cmd = list(adb_prefix) + ["shell", "am", "force-stop", package]
        subprocess.run(stop_cmd, timeout=10, capture_output=True)
        # W8: 随机延迟替代固定 2.0 秒
        time.sleep(random.uniform(1.5, 5.0))

        # 2. 重新启动应用 (W4: 优先 am start, 避免 Monkey logcat)
        logger.info(f"正在重新启动应用: {package}")
        if hasattr(driver, "ensure_app_foreground"):
            driver.ensure_app_foreground(package_name=package)
        else:
            # W4: 优先使用 am start
            launch_cmd = list(adb_prefix) + [
                "shell", "am", "start", "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER",
                package + "/.index.v2.IndexActivityV2",
            ]
            result = subprocess.run(launch_cmd, timeout=10, capture_output=True, text=True)
            if result.returncode != 0:
                # 回退到 monkey
                launch_cmd = list(adb_prefix) + [
                    "shell", "monkey", "-p", package,
                    "-c", "android.intent.category.LAUNCHER", "1",
                ]
                subprocess.run(launch_cmd, timeout=10, capture_output=True)

        # 3. 等待应用启动完成
        logger.info(f"等待应用启动: {wait_sec:.1f}s")
        time.sleep(wait_sec)

        logger.info(f"应用 {package} 重启完成")
        return True

    except Exception as e:
        logger.error(f"force_restart_app 异常: {e}")
        return False
