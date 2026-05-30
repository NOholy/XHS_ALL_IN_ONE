"""
帖子内容阅读器
从 start_mobile_driver_v2.py 的 state_post_extract 中提取重构为独立模块。
"""
from .logger import get_logger

logger = get_logger("reader")


class PostReader:
    """
    帖子内容提取器。
    进入帖子后提取描述文本、评论列表、作者信息。
    """

    def __init__(self, driver, vision, ocr, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.config = config

    def extract_current_post(self) -> dict:
        """
        提取当前已经进入的帖子全部内容。
        返回: {"description": [str], "comments": [dict], "author": str}
        """
        logger.info(f"Extracting content from current post...")

        result = {
            "description": [],
            "comments": [],
            "author": "",
        }

        # 1. 提取帖子描述
        result["description"] = self._extract_description()

        # 2. 提取作者
        result["author"] = self._extract_author()

        # 3. 滚动加载评论
        logger.info("Scrolling to load comments...")
        self.driver.human_swipe("down")
        self.driver.human_swipe("down")
        self.driver.human_sleep(2.0, 1.0)

        # 4. 提取评论
        result["comments"] = self.extract_comments()

        logger.info(f"Extracted {len(result['description'])} desc lines, "
                    f"{len(result['comments'])} comments")
        return result

    def _extract_description(self) -> list:
        """OCR 提取帖子正文描述"""
        img = self.driver.screenshot()
        try:
            ocr_results = self.ocr.ocr_image(img)
            lines = []
            for line in ocr_results:
                _, (text, conf) = line
                if conf > 0.6 and len(text) > 2:
                    lines.append(text)
            return lines
        except Exception as e:
            logger.error(f"Description OCR failed: {e}")
            return []

    def _extract_author(self) -> str:
        """尝试 OCR 提取帖子作者名"""
        img = self.driver.screenshot()
        try:
            ocr_results = self.ocr.ocr_image(img)
            # 作者名通常在帖子顶部、字数较短
            for line in ocr_results:
                box, (text, conf) = line
                y_pos = box[0][1]  # 文字的 Y 坐标
                if y_pos < self.config.device.screen_height * 0.15 and \
                   2 < len(text) < 15 and conf > 0.7:
                    return text
        except Exception:
            pass
        return "unknown"

    def extract_comments(self) -> list:
        """
        提取当前可见评论列表（含回复按钮坐标）。
        返回: [{"id": int, "content": str, "reply_x": int, "reply_y": int}, ...]
        """
        img = self.driver.screenshot()
        comments = []

        try:
            ocr_results = self.ocr.ocr_image(img)
            reply_btn = self.vision.find_template(img, "reply_button", threshold=0.8)

            for i, line in enumerate(ocr_results):
                box, (text, conf) = line
                if conf > 0.6 and len(text) > 2 and "回复" not in text:
                    comments.append({
                        "id": i,
                        "content": text,
                        "reply_x": reply_btn["x"] if reply_btn else 0,
                        "reply_y": reply_btn["y"] if reply_btn else 0,
                        "conf": conf,
                    })
        except Exception as e:
            logger.error(f"Comments OCR failed: {e}")

        return comments

    def scroll_comments(self, pages: int = 2) -> list:
        """下滑加载并收集更多评论"""
        all_comments = []
        for p in range(pages):
            comments = self.extract_comments()
            all_comments.extend(comments)
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)
        return all_comments

    def simulate_reading(self):
        """模拟真人阅读行为（用于养号）"""
        mu = self.config.farm.read_duration_mu
        sigma = self.config.farm.read_duration_sigma
        self.driver.human_sleep(mu, sigma)
