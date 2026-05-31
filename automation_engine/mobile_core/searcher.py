"""
XHS 移动端纯视觉搜索器
通过 OCR + 物理键盘输入实现移动端搜索能力（当前代码库完全缺失此能力）。
"""
import random
from .logger import get_logger
import numpy as np
from .ocr_client import OCRClient

logger = get_logger("searcher")


class XHSSearcher:
    """
    移动端纯视觉搜索引擎。
    流程: 进入搜索页 → 输入关键词 → 点击搜索 → OCR提取结果列表
    """

    def __init__(self, driver, vision, ocr, keyboard, navigator, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.keyboard = keyboard
        self.navigator = navigator
        self.config = config

    def search_keyword(self, keyword: str) -> list:
        """
        执行完整的搜索流程。
        返回: [{"title": str, "x": int, "y": int, "index": int}, ...]
        """
        logger.info(f"Searching for keyword: '{keyword}'")

        # 1. 导航到搜索页并强制校验
        if not self.navigator.go_search():
            logger.error("Failed to reach search page! Aborting search to prevent blind text injection.")
            return []
            
        self.driver.human_sleep(1.5, 0.5)

        # 2. 点击搜索框获取焦点
        img_before_click = self.driver.screenshot()
        search_input = self.vision.find_template(img_before_click, "search_input", threshold=0.65)
        if search_input:
            self.driver.physical_tap(search_input['x'], search_input['y'])
        else:
            # Fallback: 点击顶部中央
            w = self.config.device.screen_width
            h = self.config.device.screen_height
            self.driver.physical_tap(int(w * 0.5), int(h * 0.05))

        self.driver.human_sleep(1.0, 0.5)
        
        # Watchdog: 校验键盘是否真的弹起（使用绝对的 OCR 语义特征，无视视频干扰）
        img_after_click = self.driver.screenshot()
        matches = self.ocr.find_text(img_after_click, "搜索", conf_threshold=0.6)
        if not matches:
            logger.error("Keyboard 'Search' button not found after clicking search input. Aborting search.")
            return []

        # 3. 输入关键词
        logger.info(f"Typing keyword: '{keyword}'")
        if self.config.device.typing_mode == "clipboard":
            import subprocess
            subprocess.run(self.driver.adb_prefix + ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", keyword], timeout=10)
        else:
            self.keyboard.type_chinese(keyword)

        self.driver.human_sleep(1.5, 0.5)

        # 4. 点击搜索按钮
        self._click_search_button()
        self.driver.human_sleep(3.0, 1.0)  # 等待搜索结果加载

        # 5. 提取搜索结果
        results = self._extract_search_results()
        logger.info(f"Found {len(results)} posts in search results")
        return results

    def scroll_and_collect(self, max_pages: int = None) -> list:
        """翻页收集更多搜索结果"""
        if max_pages is None:
            max_pages = self.config.intercept.max_search_pages

        all_results = []
        for page in range(max_pages):
            logger.info(f"Collecting search results page {page + 1}/{max_pages}")
            results = self._extract_search_results()
            all_results.extend(results)

            img_before_swipe = self.driver.screenshot()
            
            # 下滑加载更多
            self.driver.human_swipe("down")
            self.driver.human_sleep(
                self.config.risk_control.search_cooldown_min,
                (self.config.risk_control.search_cooldown_max -
                 self.config.risk_control.search_cooldown_min) / 3
            )
            
            # Watchdog: 校验采集是否滑到底或者卡死
            img_after_swipe = self.driver.screenshot()
            if hasattr(self.vision, 'compute_screen_mse'):
                h, w = img_before_swipe.shape[:2] if img_before_swipe is not None else (0, 0)
                roi = (int(w*0.2), int(h*0.3), int(w*0.6), int(h*0.4)) if w > 0 else None
                err = self.vision.compute_screen_mse(img_before_swipe, img_after_swipe, roi)
                if err < 1.0:
                    logger.info(f"Search feed stuck or reached end (MSE={err:.2f}). Breaking scroll loop early.")
                    break

        # 去重（基于坐标聚类）
        return self._deduplicate_results(all_results)

    def filter_by_keywords(self, posts: list, filter_keywords: list = None) -> list:
        """基于标题关键词过滤目标帖子"""
        if filter_keywords is None:
            filter_keywords = self.config.intercept.title_filter_keywords

        filtered = []
        for post in posts:
            title = post.get("title", "")
            if any(kw in title for kw in filter_keywords):
                filtered.append(post)
                logger.info(f"  ✓ Matched: '{title}'")
            else:
                logger.info(f"  ✗ Skipped: '{title}'")

        logger.info(f"Filtered {len(filtered)}/{len(posts)} posts match keywords")
        return filtered

    def _click_search_button(self):
        """点击搜索/确认按钮"""
        img = self.driver.screenshot()

        # 尝试视觉匹配搜索按钮模板
        btn = self.vision.find_template(img, "search_button", threshold=0.7)
        if btn:
            self.driver.physical_tap(btn['x'], btn['y'])
            return

        # OCR 查找 "搜索" 按钮文字
        matches = self.ocr.find_text(img, "搜索", conf_threshold=0.7)
        if matches:
            # 选择最右侧的"搜索"（通常是按钮而非输入框提示）
            rightmost = max(matches, key=lambda m: m['x'])
            self.driver.physical_tap(rightmost['x'], rightmost['y'])
            return

        # Fallback: 发送回车键事件
        logger.info("Fallback: sending Enter key to submit search")
        if hasattr(self.driver, 'adb_prefix'):
            import subprocess
            subprocess.run(self.driver.adb_prefix + ["shell", "input", "keyevent", "66"], timeout=5)
        else:
            logger.warning("No search button found. Skipping search submission.")

    def _extract_search_results(self) -> list:
        """OCR 提取当前屏幕上的搜索结果帖子列表"""
        img = self.driver.screenshot()
        results = []

        # 方案1: 视觉卡片检测
        cards = self.vision.detect_cards_waterfall(img)
        if cards:
            # 对每张卡片区域做 OCR 提取标题
            for card in cards:
                # 裁剪卡片区域做精细 OCR
                x, y, w, h = card['x'] - card.get('w', 200)//2, \
                              card['y'] - card.get('h', 200)//2, \
                              card.get('w', 200), card.get('h', 200)
                x = max(0, x)
                y = max(0, y)
                card_img = img[y:y+h, x:x+w]

                try:
                    ocr_results = self.ocr.ocr_image(card_img)
                    title_parts = []
                    for _, text, conf in OCRClient.safe_parse_results(ocr_results):
                        if conf > 0.6 and len(text) > 2:
                            title_parts.append(text)
                    card['title'] = " ".join(title_parts[:2]) if title_parts else ""
                except Exception:
                    card['title'] = ""

                results.append(card)
        else:
            # 方案2: 全屏 OCR + 网格 Fallback
            try:
                ocr_results = self.ocr.ocr_image(img)
                for i, (box, text, conf) in enumerate(OCRClient.safe_parse_results(ocr_results)):
                    if conf > 0.6 and len(text) > 4:
                        x_center = int(sum([p[0] for p in box]) / 4)
                        y_center = int(sum([p[1] for p in box]) / 4)
                        results.append({
                            "id": i, "title": text,
                            "x": x_center, "y": y_center
                        })
            except Exception as e:
                logger.error(f"OCR extraction failed: {e}")

        return results

    def _deduplicate_results(self, results: list) -> list:
        """基于标题去重"""
        seen_titles = set()
        unique = []
        for r in results:
            title = r.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique.append(r)
        return unique
