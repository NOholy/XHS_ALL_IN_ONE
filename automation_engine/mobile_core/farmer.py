"""
工业级养号器
从 start_mobile_driver_v2.py 的 state_farming_loop 重构增强，
增加点赞、收藏、搜索、个人主页浏览等多维度养号行为。
"""
import json
import os
import random
import time
import numpy as np
from datetime import datetime
from .logger import get_logger

logger = get_logger("farmer")


class AccountFarmer:
    """
    工业级养号器。
    行为漏斗: 浏览100 : 点入30 : 点赞10 : 收藏3 : 评论1
    所有概率参数均从 config.farm 读取，支持运行时调整。
    """

    def __init__(self, driver, vision, ocr, navigator, reader, commenter, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.navigator = navigator
        self.reader = reader
        self.commenter = commenter
        self.config = config
        # 会话统计
        self.stats = {
            "scrolls": 0, "posts_entered": 0,
            "likes": 0, "collects": 0, "comments": 0, "follows": 0,
            "searches": 0, "profile_visits": 0,
        }
        
        self.session_start_time = 0
        self.total_duration_seconds = 1
        
        # Persona multipliers
        self.persona_multipliers = {"like": 1.0, "collect": 1.0, "comment": 1.0, "search": 1.0}
        p = getattr(self.config.farm, "persona", "balanced")
        if p == "liker": self.persona_multipliers["like"] = 2.0
        elif p == "collector": self.persona_multipliers["collect"] = 2.5
        elif p == "commenter": self.persona_multipliers["comment"] = 3.0
        elif p == "lurker":
            self.persona_multipliers = {"like": 0.5, "collect": 0.5, "comment": 0.2, "search": 1.5}

        
        # 记录文件路径
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.record_file = os.path.join(base_dir, "..", "..", "data", "farmed_actions.json")
        self._ensure_record_dir()

    def _ensure_record_dir(self):
        os.makedirs(os.path.dirname(self.record_file), exist_ok=True)
        if not os.path.exists(self.record_file):
            with open(self.record_file, "w", encoding="utf-8") as f:
                json.dump({"history": []}, f)

    def _save_record(self, action_type: str):
        """持久化记录行为"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.record_file, "r+", encoding="utf-8") as f:
                data = json.load(f)
                
                # 寻找今天的记录
                today_record = next((item for item in data.get("history", []) if item["date"] == today_str), None)
                if not today_record:
                    today_record = {"date": today_str, "actions": {"like": 0, "collect": 0, "comment": 0, "follow": 0}, "details": []}
                    data.setdefault("history", []).append(today_record)
                
                # 更新
                if action_type in today_record["actions"]:
                    today_record["actions"][action_type] += 1
                    
                today_record.setdefault("details", []).append({
                    "time": time_str,
                    "action": action_type
                })
                
                f.seek(0)
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.truncate()
        except Exception as e:
            logger.error(f"Failed to save farm record: {e}")

    def _get_fatigue_factor(self) -> float:
        """获取时间衰减疲劳系数"""
        if not getattr(self.config.farm, "fatigue_decay_enabled", False):
            return 1.0
        if self.total_duration_seconds <= 0:
            return 1.0
        elapsed = time.time() - self.session_start_time
        progress = min(1.0, elapsed / self.total_duration_seconds)
        # 从 1.2 衰减到 0.2
        return max(0.2, 1.2 - progress)

    def run_session(self, duration_minutes: int = None):
        """执行一次养号会话"""
        if duration_minutes is None:
            duration_minutes = self.config.farm.session_duration_minutes

        logger.info(f"Starting farming session ({duration_minutes} min)")
        self.navigator.ensure_app_foreground()
        self.navigator.go_home()

        self.session_start_time = time.time()
        self.total_duration_seconds = duration_minutes * 60
        end_time = self.session_start_time + self.total_duration_seconds
        step = 0

        while time.time() < end_time and step < self.config.farm.farming_steps:
            step += 1
            logger.info(f"Farm step {step}, elapsed: {int(time.time()-self.session_start_time)}s")

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

        self._log_session_summary(time.time() - self.session_start_time)

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

        # 提取内容
        post_context = self.reader.enter_and_extract(x, y)

        # 动态阅读时间
        desc_len = sum(len(line) for line in post_context.get("description", []))
        read_time_boost = 1.0 + (desc_len / 100.0) # 每100字多看一倍时间
        self.driver.human_sleep(self.config.farm.read_duration_mu * read_time_boost, self.config.farm.read_duration_sigma)

        fatigue = self._get_fatigue_factor()
        logger.debug(f"Farming context: fatigue={fatigue:.2f}, persona={getattr(self.config.farm, 'persona', 'balanced')}")

        like_prob = getattr(self.config.farm, "like_probability", 0.10) * fatigue * self.persona_multipliers.get("like", 1.0)
        collect_prob = getattr(self.config.farm, "collect_probability", 0.03) * fatigue * self.persona_multipliers.get("collect", 1.0)
        comment_prob = getattr(self.config.farm, "comment_probability", 0.01) * fatigue * self.persona_multipliers.get("comment", 1.0)
        follow_prob = getattr(self.config.farm, "follow_probability", 0.005) * fatigue

        liked = False
        if random.random() < like_prob:
            liked = self._try_like()

        # 连击概率加成
        if liked and getattr(self.config.farm, "combo_boost_enabled", False):
            collect_prob *= 3.0
            comment_prob *= 2.0

        if random.random() < collect_prob:
            self._try_collect()

        if random.random() < follow_prob:
            self._try_follow()

        if random.random() < comment_prob:
            self._try_comment(post_context)

        # 概率性查看评论
        if random.random() < getattr(self.config.farm, "scroll_comments_probability", 0.33):
            logger.info("Farming: scrolling to view comments")
            self.driver.human_swipe("down")
            self.driver.human_sleep(5.0, 2.0)

        # 退出帖子
        self.navigator.go_back()
        self.driver.human_sleep(1.5, 0.5)

    def _check_visual_change(self, img1, img2, box, threshold=2.0) -> bool:
        """比较两张图片的指定区域是否有显著变化 (MSE)"""
        if img1 is None or img2 is None:
            return False
        x, y, bw, bh = box
        h, w = img1.shape[:2]
        
        # Ensure box is within bounds
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        bw = max(1, min(bw, w - x))
        bh = max(1, min(bh, h - y))
        
        roi1 = img1[y:y+bh, x:x+bw]
        roi2 = img2[y:y+bh, x:x+bw]
        
        if roi1.shape != roi2.shape or roi1.size == 0:
            return False
            
        err = np.sum((roi1.astype("float") - roi2.astype("float")) ** 2)
        err /= float(roi1.shape[0] * roi1.shape[1] * roi1.shape[2])
        logger.debug(f"Visual diff MSE for box {box}: {err:.2f}")
        return err > threshold

    def _try_like(self) -> bool:
        """尝试点赞并进行视觉强校验"""
        if self.stats["likes"] >= self.config.risk_control.max_daily_likes:
            logger.info("Daily like quota reached, skipping")
            return False

        w, h = self.driver.get_screen_size()
        cx = int(w / 2)
        cy = int(h / 2)

        logger.info(f"Double tap liking post at center ({cx}, {cy})")
        
        img_before = self.driver.screenshot()
        
        if hasattr(self.driver, 'physical_double_tap'):
            self.driver.physical_double_tap(cx, cy)
        else:
            self.driver.physical_tap(cx, cy)
            time.sleep(0.1)
            self.driver.physical_tap(cx, cy)

        self.driver.human_sleep(1.0, 0.5)
        img_after = self.driver.screenshot()
        
        matches = self.ocr.find_text(img_before, "说点什么", conf_threshold=0.6)
        changed = False
        if matches:
            target = matches[0]
            like_x = int(w * 0.65)
            like_y = target['y']
            box = (max(0, like_x - 30), max(0, like_y - 30), 60, 60)
            changed = self._check_visual_change(img_before, img_after, box, threshold=2.0)
        else:
            box = (int(w * 0.8), int(h * 0.4), int(w * 0.2), int(h * 0.4))
            changed = self._check_visual_change(img_before, img_after, box, threshold=2.0)

        cooldown = random.randint(
            self.config.risk_control.like_cooldown_min,
            self.config.risk_control.like_cooldown_max
        )
        self.driver.human_sleep(float(cooldown), 1.0)
        
        if changed:
            logger.info("Like verification passed (visual diff).")
            self.stats["likes"] += 1
            self._save_record("like")
            return True
        else:
            logger.warning("Like verification failed (no visual change).")
            return False

    def _try_collect(self):
        """尝试收藏并进行视觉强校验"""
        if self.stats["collects"] >= self.config.risk_control.max_daily_collects:
            logger.info("Daily collect quota reached, skipping")
            return

        img_before = self.driver.screenshot()
        matches = self.ocr.find_text(img_before, "说点什么", conf_threshold=0.6)
        if matches:
            target = matches[0]
            w = self.config.device.screen_width
            cx = int(w * 0.8)
            cy = target['y']
            
            box = (max(0, cx - 40), max(0, cy - 40), 80, 80)
            
            logger.info(f"Collecting post at ({cx}, {cy})")
            self.driver.physical_tap(cx, cy)
            
            self.driver.human_sleep(1.5, 0.5)
            img_after = self.driver.screenshot()
            
            if self._check_visual_change(img_before, img_after, box, threshold=2.0):
                logger.info("Collect verification passed (visual diff).")
                self.stats["collects"] += 1
                self._save_record("collect")
            else:
                logger.warning("Collect verification failed (no visual change).")
                
            self.driver.human_sleep(1.0, 0.5)
        else:
            logger.warning("Farm collect failed: could not anchor bottom bar.")

    def _try_follow(self):
        """尝试关注作者"""
        img = self.driver.screenshot()
        matches = self.ocr.find_text(img, "关注", conf_threshold=0.7)
        for m in matches:
            if m['y'] < self.config.device.screen_height * 0.5: # 关注按钮通常在上半屏或者左下角
                logger.info(f"Following author at ({m['x']}, {m['y']})")
                self.driver.physical_tap(m['x'], m['y'])
                self.driver.human_sleep(2.0, 0.5)
                
                # 强校验
                img_after = self.driver.screenshot()
                if self.ocr.find_text(img_after, "已关注", conf_threshold=0.7):
                    logger.info("Follow verification passed.")
                    self.stats.setdefault("follows", 0)
                    self.stats["follows"] += 1
                    self._save_record("follow")
                else:
                    logger.warning("Follow verification ambiguous or failed.")
                return

    def _try_comment(self, post_context=None):
        """尝试养号随机评论"""
        if not self.commenter:
            return
            
        img = self.driver.screenshot()
        for hint in ["说点什么", "写评论", "友好评论"]:
            matches = self.ocr.find_text(img, hint, conf_threshold=0.6)
            if matches:
                rx, ry = matches[0]["x"], matches[0]["y"]
                
                text = ""
                if getattr(self.config.farm, "enable_llm_farm_comments", False) and post_context and post_context.get("description"):
                    try:
                        text = self.commenter.compose_comment(
                            post_context=post_context,
                            mode_override="llm",
                            prompt_override=getattr(self.config.farm, "llm_farm_prompt_template", "")
                        )
                    except Exception as e:
                        logger.error(f"Farm LLM comment generation failed: {e}")
                
                if not text:
                    farm_comments = ["绝了", "好看！", "马住", "绝绝子", "爱了爱了", "太棒了吧", "求分享", "太美了", "赞"]
                    text = random.choice(farm_comments)
                
                logger.info(f"Farm commenting: '{text}' at ({rx}, {ry})")
                if self.commenter.post_comment(rx, ry, text):
                    self.stats.setdefault("comments", 0)
                    self.stats["comments"] += 1
                    self._save_record("comment")
                return

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

            if self.config.device.typing_mode == "clipboard":
                import subprocess
                subprocess.run(self.driver.adb_prefix + ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", f"'{keyword}'"])
            else:
                self.keyboard.type_chinese(keyword)
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
