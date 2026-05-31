"""
智能评论器
从 start_mobile_driver_v2.py 的 state_post_reply 提取重构，
增加模板匹配、LLM生成、配额管控、去重等工业级能力。
"""
import json
import os
import random
import re
import time
import requests
from .logger import get_logger
from .ocr_client import OCRClient

logger = get_logger("commenter")


class SmartCommenter:
    """
    智能评论器 - 支持三种评论生成模式：
    1. template: 纯模板随机
    2. contextual: 基于帖子标题关键词匹配最相关模板
    3. llm: 调用 LLM API 根据帖子内容动态生成
    """

    def __init__(self, driver, vision, ocr, keyboard, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.keyboard = keyboard
        self.config = config
        self.daily_comment_count = 0
        self._commented_posts = set()
        self._load_dedup_records()
        # 会话级评论历史（借鉴 ApkClaw 的消息列表累积 + 上下文压缩）
        self._llm_history = []

    # --- 评论生成 ---

    def compose_comment(self, post_context: dict = None, keyword: str = "", mode_override: str = None, prompt_override: str = None) -> str:
        """
        根据配置的 comment_mode 生成评论文本。支持 override 用于非截流场景（如养号废话）。
        """
        mode = mode_override or self.config.intercept.comment_mode
        templates = self.config.intercept.comment_templates

        if mode == "llm" and self.config.intercept.llm_api_key:
            return self._generate_llm_comment(post_context, keyword, prompt_override)
        elif mode == "contextual" and post_context:
            tpl = self._generate_contextual_comment(post_context, templates)
            return self._parse_spintax(tpl)
        else:
            tpl = random.choice(templates)
            return self._parse_spintax(tpl)

    def _parse_spintax(self, text: str) -> str:
        """解析 Spintax 格式，例如 '{你好|哈喽}，{想问下|请问}'"""
        pattern = re.compile(r'\{([^{}]+)\}')
        while pattern.search(text):
            text = pattern.sub(lambda m: random.choice(m.group(1).split('|')), text, count=1)
        return text

    def _generate_contextual_comment(self, post_context: dict, templates: list) -> str:
        """基于帖子内容关键词匹配最相关的模板"""
        description = " ".join(post_context.get("description", []))

        # 简单的关键词匹配评分
        scored = []
        for tpl in templates:
            # 按4字符滑窗匹配（中文无空格分词，取短语片段匹配）
            score = sum(1 for i in range(0, max(1, len(tpl) - 3), 2) if tpl[i:i+4] in description)
            scored.append((score, tpl))

        scored.sort(key=lambda x: -x[0])
        # 从 Top 3 中随机选择（避免每次都选最匹配的）
        top_n = min(3, len(scored))
        return random.choice([s[1] for s in scored[:top_n]])

    def _generate_llm_comment(self, post_context: dict, keyword: str, prompt_override: str = None) -> str:
        """
        调用 LLM API 生成评论。
        借鉴 ApkClaw 的多轮对话 + 上下文压缩：
        - System prompt 中注入本次会话已发过的评论摘要，防止重复
        - 会话历史超过阈值时自动压缩（只保留最近 N 条摘要）
        """
        cfg = self.config.intercept
        content = " ".join(post_context.get("description", []))[:200] if post_context else ""

        # 构建带历史的消息列表（借鉴 ApkClaw DefaultAgentService 的 messages 列表）
        history_hint = ""
        if self._llm_history:
            recent = self._llm_history[-5:]  # 只展示最近5条
            history_hint = f"\n你本次会话已经发过的评论：{recent}\n请确保新评论与以上内容完全不同。"

        system_content = (
            "你是一个真实的小红书用户。"
            "规则：1) 每条评论必须与之前的评论完全不同 "
            "2) 15-30字 3) 使用口语化表达 4) 不要使用emoji "
            "5) 自然地融入对帖子内容的理解"
            f"{history_hint}"
        )

        template_str = prompt_override or cfg.llm_prompt_template
        user_content = template_str.format(
            keyword=keyword,
            content=content
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        try:
            endpoint = cfg.llm_endpoint or "https://api.openai.com/v1/chat/completions"
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {cfg.llm_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": cfg.llm_model,
                    "messages": messages,
                    "max_tokens": 60,
                    "temperature": 0.9,
                },
                timeout=15
            )
            response.raise_for_status()
            result = response.json()
            comment = result["choices"][0]["message"]["content"].strip()
            logger.info(f"LLM generated comment: '{comment}'")

            # 记录到会话历史（借鉴 ApkClaw 的上下文压缩：只记摘要前15字）
            self._llm_history.append(comment[:15])
            if len(self._llm_history) > 20:
                self._llm_history = self._llm_history[-10:]  # 滑动窗口压缩

            return comment
        except Exception as e:
            logger.error(f"LLM generation failed, falling back to template: {e}")
            return random.choice(cfg.comment_templates)

    # --- 评论执行 ---

    def post_comment(self, reply_x: int, reply_y: int, text: str,
                     live: bool = None) -> bool:
        """
        执行评论：点击回复框 → 输入文本 → 发送 → OCR验证。
        live 参数如果不传，使用 config.intercept.live_mode。
        返回是否成功。
        """
        if live is None:
            live = self.config.intercept.live_mode

        img_before_click = self.driver.screenshot()
        logger.info(f"Tapping reply box at ({reply_x}, {reply_y})")
        self.driver.physical_tap(reply_x, reply_y)
        self.driver.human_sleep(2.0, 1.0)
        
        # Watchdog: 校验键盘是否真的弹起（使用绝对的 OCR 语义特征，无视视频干扰）
        img_after_click = self.driver.screenshot()
        matches_send = self.ocr.find_text(img_after_click, "发送", conf_threshold=0.6)
        matches_publish = self.ocr.find_text(img_after_click, "发布", conf_threshold=0.6)
        if not matches_send and not matches_publish:
            logger.error("Keyboard 'Send/Publish' button not found after clicking reply box. Aborting comment.")
            return False

        # 输入文本
        logger.info(f"Typing: '{text}' (mode: {self.config.device.typing_mode})")
        if self.config.device.typing_mode == "clipboard":
            # Agentless clipboard hack via ADB broadcast (requires Clipper) or standard text
            logger.info("Using ADB text input fallback for clipboard mode")
            # Encode base64 to avoid shell escaping issues if needed, or use simple input text
            # Note: adb shell input text doesn't support Chinese natively without ADBKeyboard.
            # We fallback to pure vision keyboard or ADBKeyboard broadcast.
            import subprocess
            subprocess.run(self.driver.adb_prefix + ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text], timeout=10)
            self.driver.human_sleep(1.5, 0.5)
        else:
            self.keyboard.type_chinese(text)

        if not live:
            logger.info("DRY RUN: Cancelling comment...")
            self.driver.press_back()
            self.driver.human_sleep(1.0, 0.5)
            self.driver.press_back()
            return True

        # 真实发送
        logger.info("LIVE MODE: Clicking Send...")
        img = self.driver.screenshot()
        send_btn = self.vision.find_template(img, "send_button", threshold=0.75)
        if not send_btn:
            # OCR Fallback
            matches = self.ocr.find_text(img, "发送", conf_threshold=0.7)
            if not matches:
                matches = self.ocr.find_text(img, "发布", conf_threshold=0.7)
            if matches:
                send_btn = matches[0]

        if not send_btn:
            logger.error("Could not find send button!")
            self.driver.press_back()
            return False

        self.driver.physical_tap(send_btn['x'], send_btn['y'])
        self.driver.human_sleep(4.0, 1.0)

        # OCR 验证评论是否上墙
        success = self._verify_comment(text)

        if success:
            self.daily_comment_count += 1
            logger.info(f"Comment posted! Daily count: {self.daily_comment_count}")
        else:
            logger.warning("Comment may not have posted (shadowban or network issue)")

        # 强制冷却
        cooldown = random.randint(
            self.config.risk_control.comment_cooldown_min,
            self.config.risk_control.comment_cooldown_max
        )
        logger.info(f"Mandatory cooldown: {cooldown}s")
        time.sleep(cooldown)

        return success

    def _verify_comment(self, text: str) -> bool:
        """OCR 验证刚发送的评论是否出现在屏幕上"""
        img = self.driver.screenshot()
        try:
            ocr_results = self.ocr.ocr_image(img)
            check_str = text[:4]  # 检查前4个字符
            for _, ocr_text, conf in OCRClient.safe_parse_results(ocr_results):
                if check_str in ocr_text:
                    return True
        except Exception as e:
            logger.error(f"Comment verification OCR failed: {e}")
        return False

    # --- 配额与去重 ---

    def check_quota(self) -> bool:
        """检查今日评论配额是否充足"""
        remaining = self.config.risk_control.max_daily_comments - self.daily_comment_count
        if remaining <= 0:
            logger.warning("Daily comment quota exhausted!")
            return False
        logger.info(f"Comment quota remaining: {remaining}")
        return True

    def check_duplicate(self, post_id: str) -> bool:
        """检查是否已评论过该帖子"""
        if not self.config.intercept.enable_dedup:
            return False
        return post_id in self._commented_posts

    def record_commented(self, post_id: str):
        """记录已评论帖子"""
        self._commented_posts.add(post_id)
        self._save_dedup_records()

    def _load_dedup_records(self):
        """从磁盘加载去重记录"""
        path = self.config.intercept.dedup_record_file
        if path and os.path.exists(path):
            try:
                with open(path, "r") as f:
                    self._commented_posts = set(json.load(f))
                logger.info(f"Loaded {len(self._commented_posts)} dedup records")
            except Exception:
                self._commented_posts = set()

    def _save_dedup_records(self):
        """持久化去重记录到磁盘"""
        path = self.config.intercept.dedup_record_file
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(list(self._commented_posts), f)
