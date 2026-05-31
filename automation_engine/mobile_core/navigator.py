"""
XHS App 纯视觉页面导航器
封装所有页面跳转逻辑，通过 OCR + 模板匹配判断当前页面并执行导航。
"""
from .logger import get_logger
import numpy as np

logger = get_logger("navigator")

# 页面类型常量
PAGE_HOME_FEED = "home_feed"
PAGE_SEARCH = "search_page"
PAGE_SEARCH_RESULTS = "search_results"
PAGE_POST_DETAIL = "post_detail"
PAGE_PROFILE = "profile"
PAGE_COMMENT_PANEL = "comment_panel"
PAGE_UNKNOWN = "unknown"


class XHSNavigator:
    """
    纯视觉导航器 - 封装 XHS App 内所有页面切换。
    不依赖任何 UI 树或 Accessibility 服务。
    """

    def __init__(self, driver, vision, ocr, config, page_detector=None):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.config = config
        self.page_detector = page_detector

    def detect_current_page(self) -> str:
        """
        通过 OCR + 模板匹配判断当前处于哪个页面。
        返回页面类型常量。
        """
        # Fast path: Activity 级别检测（~50ms，零风控风险）
        if self.page_detector:
            fast_result = self.page_detector.detect_page_fast()
            if fast_result != "unknown":
                logger.debug(f"Fast page detection via Activity: {fast_result}")
                return fast_result

        # Slow path: 截图 + 视觉匹配 + OCR（原有逻辑，作为降级）
        img = self.driver.screenshot()

        # 1. 检测搜索结果页（有搜索框+结果列表）
        search_results_indicators = self.ocr.find_text(img, "搜索", conf_threshold=0.6)
        note_cards = self.vision.detect_cards_waterfall(img)

        # 2. 检测帖子详情页（有评论输入框）
        comment_input = self.vision.find_template(img, "comment_input", threshold=0.7)
        reply_btn = self.vision.find_template(img, "reply_button", threshold=0.7)

        # 3. 检测底部Tab来判断首页/个人主页
        tab_home = self.vision.find_template(img, "tab_home", threshold=0.7)
        tab_profile = self.vision.find_template(img, "tab_profile", threshold=0.7)

        # 4. 搜索页（有搜索输入框但无结果）
        search_input = self.vision.find_template(img, "search_input", threshold=0.7)

        # 判断逻辑
        if comment_input or reply_btn:
            return PAGE_POST_DETAIL
        if search_input and not note_cards:
            return PAGE_SEARCH
        if search_results_indicators and note_cards:
            return PAGE_SEARCH_RESULTS

        # OCR 检测个人主页标志
        profile_indicators = self.ocr.find_text(img, "编辑资料", conf_threshold=0.6)
        if profile_indicators:
            return PAGE_PROFILE

        if tab_home and note_cards:
            return PAGE_HOME_FEED

        return PAGE_UNKNOWN

    def go_home(self):
        """确保回到首页推荐流"""
        current = self.detect_current_page()
        if current == PAGE_HOME_FEED:
            logger.info("Already on home feed.")
            return True

        logger.info("Not on home feed. Starting smart backtrack to home...")
        # 智能回退：连续按返回键，直到状态变成首页
        for _ in range(5):
            self.go_back()
            self.driver.human_sleep(1.0, 0.5)
            
            current = self.detect_current_page()
            if current == PAGE_HOME_FEED:
                logger.info("Successfully returned to home feed.")
                return True
                
            if current == PAGE_UNKNOWN:
                logger.debug("Current page unknown, continuing to press back...")

        # 假如连续返回 5 次还没到首页，可能是卡在某个特殊的根页面。
        # 此时尝试点击底部的首页 Tab 作为最终兜底
        logger.warning("Backtrack loop failed to reach home. Trying bottom tab fallback.")
        w_screen, h_screen = self.driver.get_screen_size()
        tab_y = int(h_screen * 0.96)  # Tab 栏中心 y 坐标
        tab_x = int(w_screen * 0.1)   # 首页 Tab (第 1 个)
        self.driver.physical_tap(tab_x, tab_y)
        self.driver.human_sleep(2.0, 1.0)
        
        return self.detect_current_page() == PAGE_HOME_FEED

    def go_search(self):
        """从首页进入搜索页"""
        self.go_home()
        self.driver.human_sleep(1.0, 0.5)

        img = self.driver.screenshot()
        # 1. 尝试视觉匹配搜索图标
        search_icon = self.vision.find_template(img, "search_icon", threshold=0.7)
        if search_icon:
            logger.info(f"Clicking search icon at ({search_icon['x']}, {search_icon['y']})")
            self.driver.physical_tap(search_icon['x'], search_icon['y'])
            self.driver.human_sleep(2.0, 1.0)
            return self.detect_current_page() == PAGE_SEARCH

        # 2. Fallback: OCR 查找 "搜索" 文字
        matches = self.ocr.find_text(img, "搜索", conf_threshold=0.7)
        if matches:
            target = matches[0]
            logger.info(f"OCR found '搜索' at ({target['x']}, {target['y']})")
            self.driver.physical_tap(target['x'], target['y'])
            self.driver.human_sleep(2.0, 1.0)
            return self.detect_current_page() == PAGE_SEARCH

        # 3. Fallback: 点击顶部右侧区域（搜索图标的常见位置）
        w = self.config.device.screen_width
        h = self.config.device.screen_height
        logger.info(f"Fallback: tapping search area at ({int(w * 0.85)}, {int(h * 0.05)})")
        self.driver.physical_tap(int(w * 0.85), int(h * 0.05))
        self.driver.human_sleep(2.0, 1.0)
        
        return self.detect_current_page() == PAGE_SEARCH

    def go_profile(self):
        """进入个人主页"""
        # 必须确保在首页，否则底部 Tab 坐标是错的
        self.go_home()
        self.driver.human_sleep(1.0, 0.5)

        # 强制使用底部固定坐标点击“我”Tab
        w_screen, h_screen = self.driver.get_screen_size()
        tab_y = int(h_screen * 0.96)
        tab_x = int(w_screen * 0.9)  # Center of the 5th tab (out of 5)
        
        logger.info(f"Clicking profile tab at fixed coordinate ({tab_x}, {tab_y})")
        self.driver.physical_tap(tab_x, tab_y)
        self.driver.human_sleep(2.0, 1.0)
        
        return self.detect_current_page() == PAGE_PROFILE

    def go_back(self):
        """智能返回：先尝试视觉关闭按钮，再用物理Back键（带键盘吸附防御）"""
        img_before = self.driver.screenshot()

        # 尝试点击关闭按钮
        close_btn = self.vision.find_template(img_before, "close_button", threshold=0.7)
        if close_btn:
            logger.info(f"Clicking close button at ({close_btn['x']}, {close_btn['y']})")
            self.driver.physical_tap(close_btn['x'], close_btn['y'])
            self.driver.human_sleep(1.5, 0.5)
            return

        # Fallback: 物理返回键
        self.driver.press_back()
        self.driver.human_sleep(1.0, 0.5)
        
        # Watchdog: 键盘吸附/卡死校验
        img_after = self.driver.screenshot()
        if hasattr(self.vision, 'compute_screen_mse'):
            err = self.vision.compute_screen_mse(img_before, img_after)
            if err < 1.0:
                logger.warning(f"Back key absorbed (MSE={err:.2f}). Triggering double-back.")
                self.driver.press_back()
                self.driver.human_sleep(1.0, 0.5)

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        """确保 XHS App 在前台"""
        self.driver.ensure_app_foreground(package_name)
