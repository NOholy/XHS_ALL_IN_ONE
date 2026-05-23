"""
工业级养号器
从 start_mobile_driver_v2.py 的 state_farming_loop 重构增强，
增加点赞、收藏、搜索、个人主页浏览等多维度养号行为。
"""
import random
import time
from .logger import get_logger

logger = get_logger("farmer")


class AccountFarmer:
    """
    工业级养号器。
    行为漏斗: 浏览100 : 点入30 : 点赞10 : 收藏3 : 评论1
    所有概率参数均从 config.farm 读取，支持运行时调整。
    """

    def __init__(self, driver, vision, ocr, navigator, reader, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.navigator = navigator
        self.reader = reader
        self.config = config
        # 会话统计
        self.stats = {
            "scrolls": 0, "posts_entered": 0,
            "likes": 0, "collects": 0, "searches": 0,
            "profile_visits": 0,
        }

    def run_session(self, duration_minutes: int = None):
        """执行一次养号会话"""
        if duration_minutes is None:
            duration_minutes = self.config.farm.session_duration_minutes

        logger.info(f"Starting farming session ({duration_minutes} min)")
        self.navigator.ensure_app_foreground()
        self.navigator.go_home()

        start_time = time.time()
        end_time = start_time + duration_minutes * 60
        step = 0

        while time.time() < end_time and step < self.config.farm.farming_steps:
            step += 1
            logger.info(f"Farm step {step}, elapsed: {int(time.time()-start_time)}s")

            # 核心行为分支（基于配置的概率）
            roll = random.random()

            if roll < self.config.farm.random_search_probability:
                self._random_search()
            elif roll < (self.config.farm.random_search_probability +
                         self.config.farm.visit_profile_probability):
                self._visit_profile()
            elif roll < (self.config.farm.random_search_probability +
                         self.config.farm.visit_profile_probability +
                         self.config.farm.enter_post_probability):
                self._enter_and_interact()
            else:
                self._browse_feed()

        self._log_session_summary(time.time() - start_time)

    def _browse_feed(self):
        """信息流浏览 + 随机停顿"""
        self.driver.human_swipe("down")
        self.stats["scrolls"] += 1

        # 偶尔停下来"看"几秒（模拟真人扫视）
        if random.random() < 0.2:
            self.driver.human_sleep(3.0, 1.5)
        else:
            self.driver.human_sleep(
                self.config.risk_control.human_sleep_mu,
                self.config.risk_control.human_sleep_sigma
            )

    def _enter_and_interact(self):
        """进入帖子 + 概率性互动（点赞/收藏）"""
        img = self.driver.screenshot()
        cards = self.vision.detect_cards_waterfall(img)

        if cards:
            card = random.choice(cards)
            x, y = card['x'], card['y']
        else:
            w = self.config.device.screen_width
            h = self.config.device.screen_height
            x = random.choice([int(w * 0.25), int(w * 0.75)])
            y = random.randint(int(h * 0.3), int(h * 0.8))

        logger.info(f"Entering post at ({x}, {y})")
        self.driver.physical_tap(x, y)
        self.stats["posts_entered"] += 1

        # 模拟阅读
        self.reader.simulate_reading()

        # 概率性点赞
        if random.random() < self.config.farm.like_probability:
            self._try_like()

        # 概率性收藏
        if random.random() < self.config.farm.collect_probability:
            self._try_collect()

        # 概率性查看评论
        if random.random() < self.config.farm.scroll_comments_probability:
            logger.info("Farming: scrolling to view comments")
            self.driver.human_swipe("down")
            self.driver.human_sleep(5.0, 2.0)

        # 退出帖子
        self.navigator.go_back()
        self.driver.human_sleep(1.5, 0.5)

    def _try_like(self):
        """尝试点赞"""
        if self.stats["likes"] >= self.config.risk_control.max_daily_likes:
            logger.info("Daily like quota reached, skipping")
            return

        img = self.driver.screenshot()
        like_btn = self.vision.find_template(img, "like_button", threshold=0.7)
        if like_btn:
            logger.info(f"Liking post at ({like_btn['x']}, {like_btn['y']})")
            self.driver.physical_tap(like_btn['x'], like_btn['y'])
            self.stats["likes"] += 1
            cooldown = random.randint(
                self.config.risk_control.like_cooldown_min,
                self.config.risk_control.like_cooldown_max
            )
            self.driver.human_sleep(float(cooldown), 1.0)

    def _try_collect(self):
        """尝试收藏"""
        if self.stats["collects"] >= self.config.risk_control.max_daily_collects:
            logger.info("Daily collect quota reached, skipping")
            return

        img = self.driver.screenshot()
        collect_btn = self.vision.find_template(img, "collect_button", threshold=0.7)
        if collect_btn:
            logger.info(f"Collecting post at ({collect_btn['x']}, {collect_btn['y']})")
            self.driver.physical_tap(collect_btn['x'], collect_btn['y'])
            self.stats["collects"] += 1
            self.driver.human_sleep(2.0, 1.0)

    def _random_search(self):
        """随机搜索热门词汇（模拟真人搜索习惯）"""
        if self.stats["searches"] >= self.config.risk_control.max_daily_searches:
            return

        keyword = random.choice(self.config.farm.hot_keywords)
        logger.info(f"Farm random search: '{keyword}'")

        self.navigator.go_search()
        self.driver.human_sleep(2.0, 1.0)

        # 简化搜索：只输入和浏览，不提取结果
        img = self.driver.screenshot()
        search_input = self.vision.find_template(img, "search_input", threshold=0.65)
        if search_input:
            self.driver.physical_tap(search_input['x'], search_input['y'])
            self.driver.human_sleep(1.0, 0.5)

            if self.config.device.typing_mode == "clipboard" and hasattr(self.driver, "d"):
                self.driver.d.set_clipboard(keyword)
                self.driver.d.send_keys(keyword, clear=True)
            # 简单浏览一下搜索结果
            self.driver.human_sleep(3.0, 1.0)
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)

        self.stats["searches"] += 1
        self.navigator.go_home()

    def _visit_profile(self):
        """浏览个人主页"""
        logger.info("Visiting own profile")
        self.navigator.go_profile()
        self.driver.human_sleep(3.0, 1.5)

        # 随机滑动浏览自己的内容
        if random.random() < 0.5:
            self.driver.human_swipe("down")
            self.driver.human_sleep(2.0, 1.0)

        self.stats["profile_visits"] += 1
        self.navigator.go_home()

    def _log_session_summary(self, elapsed: float):
        """输出养号会话统计"""
        logger.info(
            f"Farming session complete ({int(elapsed)}s). "
            f"Stats: scrolls={self.stats['scrolls']}, "
            f"entered={self.stats['posts_entered']}, "
            f"likes={self.stats['likes']}, "
            f"collects={self.stats['collects']}, "
            f"searches={self.stats['searches']}, "
            f"profile_visits={self.stats['profile_visits']}"
        )
