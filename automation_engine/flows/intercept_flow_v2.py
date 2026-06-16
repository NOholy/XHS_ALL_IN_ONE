"""
话题搜索评论截流编排器 V2 (自然浏览式)
核心 Pipeline: 
搜索阶段：搜索话题 → 在结果页逐个下滑浏览 → 遇目标则评论，遇非目标则正常浏览(点赞/收藏)。不离开结果页。
首页推荐阶段：回首页 → 浏览系统基于刚刚搜索行为推送的帖子 → 养号+捕获漏网之鱼。
"""
import random
import time
import hashlib
import re
from mobile_core.logger import get_logger
from mobile_core.exceptions import RiskControlTriggered, PopupIntercepted

logger = get_logger("intercept_flow_v2")


class InterceptV2Orchestrator:
    """
    自然浏览式截流 V2。
    核心理念：浏览就是主流程，评论是浏览过程中的自然分支。
    """

    def __init__(self, navigator, searcher, reader, commenter, farmer,
                 driver, config, watchdog=None):
        self.navigator = navigator
        self.searcher = searcher
        self.reader = reader
        self.commenter = commenter
        self.farmer = farmer
        self.driver = driver
        self.config = config
        self.watchdog = watchdog

        self.last_comment_time = 0

    def _safe_screen_check(self) -> bool:
        if not self.watchdog:
            return True
        try:
            img = self.driver.screenshot()
            self.watchdog.check_screen(img)
            return True
        except PopupIntercepted as e:
            logger.warning(f"Popup intercepted and dismissed: {e}")
            return False

    def run(self):
        """执行自然浏览式截流"""
        cfg = self.config.intercept
        keywords = cfg.keywords
        
        logger.info("=== Starting Intercept V2 (Natural Browsing) ===")
        self.navigator.ensure_app_foreground()
        
        for keyword in keywords:
            # 检查配额
            if not self.commenter.check_quota():
                logger.info("Daily quota reached. Stopping intercept flow.")
                break

            logger.info(f"=== Phase 1: Search & Browse for '{keyword}' ===")
            self._phase_search_and_browse(keyword, max_pages=cfg.max_search_pages)
            
            # Phase 2: Home Feed Harvest (Farming + Opportunistic Intercept)
            if getattr(cfg, "v2_home_harvest_enabled", True):
                duration_mins = getattr(cfg, "v2_home_harvest_duration_minutes", 15)
                logger.info(f"=== Phase 2: Home Feed Harvest ({duration_mins} mins) ===")
                self._phase_home_feed_harvest(duration_mins, keyword)
                
        logger.info("=== Intercept V2 Flow Completed ===")

    def _wait_for_cooldown(self):
        """消化剩余的评论冷却时间"""
        if self.last_comment_time == 0:
            return
            
        cfg = self.config.risk_control
        cooldown_target = random.randint(cfg.comment_cooldown_min, cfg.comment_cooldown_max)
        elapsed = time.time() - self.last_comment_time
        remaining = cooldown_target - elapsed
        
        if remaining > 0:
            logger.info(f"Waiting {remaining:.1f}s to fulfill comment cooldown...")
            time.sleep(remaining)

    def _phase_search_and_browse(self, keyword: str, max_pages: int):
        """在搜索结果页内逐个浏览帖子"""
        # 搜索
        self.searcher.search_keyword(keyword)
        
        for page in range(max_pages):
            logger.info(f"Scanning search results page {page + 1}/{max_pages}")
            self._safe_screen_check()
            
            cards = self.searcher._extract_search_results()
            cards = self.searcher._deduplicate_results(cards)
            
            # 从上到下排序卡片
            cards.sort(key=lambda c: c['y'])
            
            for card in cards:
                if not self.commenter.check_quota():
                    return
                
                is_target = self._is_target_post(card, self.config.intercept.title_filter_keywords)
                
                if is_target:
                    # 检查冷却
                    self._wait_for_cooldown()
                    self._enter_read_comment(card, keyword)
                    self.last_comment_time = time.time()
                    
                    # IP轮换判断 (放在评论后)
                    if self.commenter.daily_comment_count > 0 and \
                       self.commenter.daily_comment_count % self.config.risk_control.ip_rotate_every_n_comments == 0:
                        self._rotate_ip()
                else:
                    # 非目标帖子，概率性浏览
                    browse_prob = getattr(self.config.intercept, "v2_non_target_browse_probability", 0.30)
                    if random.random() < browse_prob:
                        self._enter_read_browse(card)
                        
            # 下滑一页
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)
            
    def _phase_home_feed_harvest(self, duration_minutes: int, last_keyword: str):
        """
        在首页推荐流中捕获目标帖子。
        核心目的其实是"养号"，顺便截流推荐系统推出来的相关帖子。
        """
        self.navigator.go_home()
        self.driver.human_sleep(3.0, 1.0)
        
        end_time = time.time() + duration_minutes * 60
        
        while time.time() < end_time:
            if not self.commenter.check_quota():
                logger.info("Quota reached during home harvest.")
                break
                
            self._safe_screen_check()
            
            cards = self.searcher._extract_search_results()
            cards = self.searcher._deduplicate_results(cards)
            cards.sort(key=lambda c: c['y'])
            
            for card in cards:
                # 遇到合适的疑似目标
                if self._is_target_post(card, self.config.intercept.title_filter_keywords):
                    self._wait_for_cooldown()
                    self._enter_read_comment(card, keyword=last_keyword)
                    self.last_comment_time = time.time()
                else:
                    # 正常养号浏览
                    # 我们复用 farmer 的逻辑，但是限制只对当前卡片操作
                    browse_prob = getattr(self.config.intercept, "v2_non_target_browse_probability", 0.30)
                    if random.random() < browse_prob:
                        self._enter_read_browse(card)
            
            # 下滑推荐流
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)

    def _is_target_post(self, card: dict, filter_keywords: list) -> bool:
        """检查卡片是否命中目标关键词"""
        title = card.get('title', '')
        if not title:
            return False
            
        for k in filter_keywords:
            if k in title:
                return True
        return False

    def _enter_read_comment(self, card: dict, keyword: str):
        """进入目标帖子，阅读后评论"""
        title = card.get('title', 'Unknown')
        
        # 1. 进贴前的模糊去重检查（基于卡片OCR的标题）
        if self.commenter.check_duplicate_fuzzy(title):
            logger.info(f"Target fuzzy duplicated, skipping before click: {title}")
            return
            
        logger.info(f"[Target] Entering target post: {title}")
        self.driver.physical_tap(card['x'], card['y'])
        self.driver.human_sleep(3.0, 1.0)
        
        current_page = self.navigator.detect_current_page()
        if current_page != "post_detail":
            logger.warning(f"Failed to enter post (state is {current_page}). Skipping.")
            return

        post_data = self.reader.extract_current_post()
        
        # W1: 强校验：通过点进帖子后的正文再判断一次，解决卡片识别误差
        desc = " ".join(post_data.get('description', []))
        is_real_target = False
        for k in self.config.intercept.title_filter_keywords:
            if k in title or k in desc:
                is_real_target = True
                break
                
        if not is_real_target:
            logger.info("After entering post, content does not match target. Skipping.")
            self.navigator.go_back()
            return
            
        # 2. 进贴后的精准去重检查（基于作者和正文前15字）
        author = post_data.get('author', 'unknown')
        norm_desc = re.sub(r'[^\w\u4e00-\u9fa5]', '', desc)
        
        if author != 'unknown' and len(norm_desc) > 5:
            post_id = f"{author}_{norm_desc[:15]}"
        else:
            title_hash = hashlib.md5(title.encode('utf-8')).hexdigest()[:8]
            post_id = f"{keyword}_{title_hash}"
            
        if self.commenter.check_duplicate(post_id):
            logger.info(f"Target exactly duplicated (post_id: {post_id}), skipping.")
            self.navigator.go_back()
            return
        
        # 模拟阅读
        desc_len = sum(len(line) for line in post_data.get("description", []))
        read_time_boost = 1.0 + (desc_len / 100.0)
        total_sleep_time = max(2.0, random.normalvariate(
            self.config.farm.read_duration_mu * read_time_boost, 
            self.config.farm.read_duration_sigma
        ))
        logger.info(f"Reading target post for {total_sleep_time:.1f}s")
        time.sleep(total_sleep_time)
        
        # 评论
        target_x, target_y = self._find_reply_target(post_data)
        if target_x == 0 and target_y == 0:
            logger.warning("No reply input found, skipping comment.")
        else:
            comment_text = self.commenter.compose_comment(
                keyword=keyword,
                post_context=post_data,
                mode_override=self.config.intercept.comment_mode,
                prompt_override=self.config.intercept.llm_prompt_template
            )
            logger.info(f"Composed comment: {comment_text}")
            
            if not self.config.intercept.live_mode:
                logger.info(f"[DRY-RUN] Would post: {comment_text}")
                self.commenter.record_commented(post_id, title=title)
            else:
                success = self.commenter.post_comment(target_x, target_y, comment_text)
                if success:
                    self.commenter.record_commented(post_id, title=title)
                    
        # 退出帖子
        self.navigator.go_back()
        self.driver.human_sleep(1.5, 0.5)

    def _enter_read_browse(self, card: dict):
        """进入非目标帖子，正常浏览（复用 farmer 逻辑思想）"""
        logger.info(f"[Camouflage] Entering non-target post: {card.get('title', '')}")
        self.driver.physical_tap(card['x'], card['y'])
        self.driver.human_sleep(3.0, 1.0)
        
        if self.navigator.detect_current_page() != "post_detail":
            return
            
        post_context = self.reader.extract_current_post()
        
        # 模拟阅读
        desc_len = sum(len(line) for line in post_context.get("description", []))
        read_time_boost = 1.0 + (desc_len / 100.0)
        total_sleep_time = max(2.0, random.normalvariate(
            self.config.farm.read_duration_mu * read_time_boost, 
            self.config.farm.read_duration_sigma
        ))
        logger.info(f"Reading camouflage post for {total_sleep_time:.1f}s")
        time.sleep(total_sleep_time)
        
        # 概率点赞收藏
        cfg = self.config.intercept
        like_prob = getattr(cfg, "v2_non_target_like_probability", 0.10)
        collect_prob = getattr(cfg, "v2_non_target_collect_probability", 0.03)
        
        if random.random() < like_prob:
            self.farmer._try_like()
        if random.random() < collect_prob:
            self.farmer._try_collect()
            
        self.navigator.go_back()
        self.driver.human_sleep(1.5, 0.5)

    def _find_reply_target(self, post_data: dict) -> tuple:
        """从帖子数据中找到回复按钮坐标（优先主评论框，修复旧版逻辑）"""
        # 优先: OCR 查找底部主评论框
        img = self.driver.screenshot()
        for hint in ["说点什么", "写评论", "友好评论"]:
            matches = self.commenter.ocr.find_text(img, hint, conf_threshold=0.5)
            if matches:
                return matches[0]["x"], matches[0]["y"]

        # Fallback: 使用评论列表中的回复坐标
        comments = post_data.get("comments", [])
        if comments and comments[0].get("reply_x"):
            return comments[0]["reply_x"], comments[0]["reply_y"]

        return 0, 0

    def _rotate_ip(self):
        """执行 IP 轮换"""
        delay = self.config.risk_control.ip_rotate_delay
        from mobile_core.device_optimizer import DeviceOptimizer
        opt = DeviceOptimizer()
        if hasattr(opt, "rotate_ip_stealthy"):
            opt.rotate_ip_stealthy(delay_seconds=delay)
        else:
            opt.toggle_airplane_mode(delay_seconds=delay)
