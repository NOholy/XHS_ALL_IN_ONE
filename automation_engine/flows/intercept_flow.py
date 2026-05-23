"""
话题搜索评论截流编排器
核心 Pipeline: 搜索话题 → 筛选帖子 → 伪装浏览 → 智能评论 → 冷却循环
"""
import random
import json
from mobile_core.logger import get_logger

logger = get_logger("intercept_flow")


class InterceptOrchestrator:
    """
    话题评论截流 - 完整 Pipeline 编排。
    所有行为参数均从 config 读取。
    """

    def __init__(self, navigator, searcher, reader, commenter, farmer,
                 driver, config):
        self.navigator = navigator
        self.searcher = searcher
        self.reader = reader
        self.commenter = commenter
        self.farmer = farmer
        self.driver = driver
        self.config = config

    def run(self):
        """
        主截流循环。
        遍历所有配置的关键词，搜索 → 过滤 → 评论。
        """
        cfg = self.config.intercept
        keywords = cfg.keywords

        if not keywords:
            logger.error("No intercept keywords configured!")
            return

        logger.info(f"Starting intercept flow with {len(keywords)} keywords: {keywords}")
        self.navigator.ensure_app_foreground()

        total_comments = 0
        comment_since_ip_rotate = 0

        for keyword in keywords:
            if not self.commenter.check_quota():
                logger.warning("Daily quota exhausted. Stopping intercept.")
                break

            logger.info(f"=== Processing keyword: '{keyword}' ===")

            # 1. 搜索
            posts = self.searcher.search_keyword(keyword)
            if not posts:
                logger.warning(f"No posts found for '{keyword}', skipping.")
                continue

            # 2. 翻页收集更多结果
            if cfg.max_search_pages > 1:
                more = self.searcher.scroll_and_collect(cfg.max_search_pages - 1)
                posts.extend(more)

            # 3. 过滤
            targets = self.searcher.filter_by_keywords(posts, cfg.title_filter_keywords)
            if not targets:
                logger.info(f"No matching posts for '{keyword}' after filtering.")
                self.navigator.go_home()
                continue

            logger.info(f"Found {len(targets)} target posts for '{keyword}'")

            # 4. 对每个目标帖子执行截流
            for i, target in enumerate(targets):
                if not self.commenter.check_quota():
                    break

                post_id = f"{keyword}_{target.get('title', '')[:20]}_{target['x']}_{target['y']}"

                # 去重检查
                if self.commenter.check_duplicate(post_id):
                    logger.info(f"Post already commented, skipping: {post_id}")
                    continue

                # 4a. 伪装浏览（每次评论前先浏览几个无关帖子）
                browse_count = random.randint(
                    cfg.browse_before_comment_min,
                    cfg.browse_before_comment_max
                )
                logger.info(f"Browsing {browse_count} posts before commenting (camouflage)...")
                self._camouflage_browse(browse_count)

                # 4b. 回到搜索结果（重新搜索同一关键词）
                if i > 0:
                    posts_refreshed = self.searcher.search_keyword(keyword)
                    targets_refreshed = self.searcher.filter_by_keywords(
                        posts_refreshed, cfg.title_filter_keywords
                    )
                    # 尝试在新结果中找到相同帖子
                    match = self._find_matching_post(target, targets_refreshed)
                    if match:
                        target = match
                    else:
                        logger.warning("Could not re-locate target post after camouflage. Skipping.")
                        continue

                # 4c. 进入帖子 + 提取内容
                post_data = self.reader.enter_and_extract(target['x'], target['y'])

                # 4d. 生成评论
                comment_text = self.commenter.compose_comment(
                    post_context=post_data,
                    keyword=keyword
                )
                logger.info(f"Generated comment: '{comment_text}'")

                # 4e. 定位回复输入框并评论
                reply_x, reply_y = self._find_reply_target(post_data)
                if reply_x == 0 and reply_y == 0:
                    logger.warning("Could not locate reply button. Skipping this post.")
                    self.navigator.go_back()
                    continue

                success = self.commenter.post_comment(reply_x, reply_y, comment_text)

                if success:
                    total_comments += 1
                    comment_since_ip_rotate += 1
                    self.commenter.record_commented(post_id)

                # 4f. 退出帖子
                self.navigator.go_back()
                self.driver.human_sleep(2.0, 1.0)

                # 4g. 概率性 IP 轮换
                if comment_since_ip_rotate >= self.config.risk_control.ip_rotate_every_n_comments:
                    logger.info("Rotating IP after N comments...")
                    self._rotate_ip()
                    comment_since_ip_rotate = 0

            # 关键词处理完毕，回首页
            self.navigator.go_home()
            self.driver.human_sleep(3.0, 1.0)

        logger.info(f"Intercept flow complete. Total comments: {total_comments}")

    def _camouflage_browse(self, count: int):
        """伪装浏览：在首页随机浏览几个帖子"""
        self.navigator.go_home()
        for _ in range(count):
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)

            # 20% 概率点进去看看
            if random.random() < 0.2:
                img = self.driver.screenshot()
                cards = self.driver.__class__.__name__  # 仅用于判断类型
                # 简单随机点击
                w = self.config.device.screen_width
                h = self.config.device.screen_height
                x = random.choice([int(w * 0.25), int(w * 0.75)])
                y = random.randint(int(h * 0.3), int(h * 0.7))
                self.driver.physical_tap(x, y)
                self.driver.human_sleep(5.0, 2.0)
                self.navigator.go_back()

    def _find_reply_target(self, post_data: dict) -> tuple:
        """从帖子数据中找到回复按钮坐标"""
        comments = post_data.get("comments", [])

        # 优先使用评论列表中的回复坐标
        if comments and comments[0].get("reply_x"):
            return comments[0]["reply_x"], comments[0]["reply_y"]

        # Fallback: OCR 查找 "说点什么" / "写评论" 输入框
        from mobile_core.ocr_client import OCRClient
        img = self.driver.screenshot()
        ocr = OCRClient(self.config.ocr.endpoint)
        for hint in ["说点什么", "写评论", "友好评论"]:
            matches = ocr.find_text(img, hint, conf_threshold=0.5)
            if matches:
                return matches[0]["x"], matches[0]["y"]

        return 0, 0

    def _find_matching_post(self, original: dict, candidates: list) -> dict:
        """在新搜索结果中找到与原始目标匹配的帖子"""
        orig_title = original.get("title", "")
        if not orig_title:
            return None

        for c in candidates:
            if c.get("title", "") == orig_title:
                return c
            # 模糊匹配（标题前10字相同）
            if orig_title[:10] and c.get("title", "")[:10] == orig_title[:10]:
                return c

        return None

    def _rotate_ip(self):
        """执行 IP 轮换"""
        try:
            from mobile_core.device_optimizer import DeviceOptimizer
            opt = DeviceOptimizer(self.config.device.serial)
            opt.toggle_airplane_mode(
                delay_seconds=self.config.risk_control.ip_rotate_delay
            )
        except Exception as e:
            logger.error(f"IP rotation failed: {e}")
