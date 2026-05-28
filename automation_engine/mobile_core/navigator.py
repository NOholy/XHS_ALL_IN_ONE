"""
XHS App 纯视觉页面导航器
封装所有页面跳转逻辑，通过 OCR + 模板匹配判断当前页面并执行导航。
"""
from .logger import get_logger

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

    def __init__(self, driver, vision, ocr, config):
        self.driver = driver
        self.vision = vision
        self.ocr = ocr
        self.config = config

    def detect_current_page(self) -> str:
        """
        通过 OCR + 模板匹配判断当前处于哪个页面。
        返回页面类型常量。
        """
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

        # 尝试通过固定的底部Tab坐标点击回到首页（代替不稳定的模板匹配）
        w_screen, h_screen = self.driver.get_screen_size()
        tab_y = int(h_screen * 0.96)  # Bottom 4% is usually the center of the tab
        tab_x = w_screen // 10        # Center of the 1st tab (out of 5)
        
        logger.info(f"Clicking home tab at fixed coordinate ({tab_x}, {tab_y})")
        self.driver.physical_tap(tab_x, tab_y)
        self.driver.human_sleep(2.0, 1.0)
        return True

        # Fallback: 连续按返回键
        for _ in range(5):
            self.driver.press_back()
            self.driver.human_sleep(1.0, 0.5)
            if self.detect_current_page() == PAGE_HOME_FEED:
                return True

        logger.warning("Failed to navigate to home feed.")
        return False

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
            return True

        # 2. Fallback: OCR 查找 "搜索" 文字
        matches = self.ocr.find_text(img, "搜索", conf_threshold=0.7)
        if matches:
            target = matches[0]
            logger.info(f"OCR found '搜索' at ({target['x']}, {target['y']})")
            self.driver.physical_tap(target['x'], target['y'])
            self.driver.human_sleep(2.0, 1.0)
            return True

        # 3. Fallback: 点击顶部右侧区域（搜索图标的常见位置）
        w = self.config.device.screen_width
        logger.info(f"Fallback: tapping search area at ({int(w * 0.85)}, 120)")
        self.driver.physical_tap(int(w * 0.85), 120)
        self.driver.human_sleep(2.0, 1.0)
        return True

    def go_profile(self):
        """进入个人主页"""
        # 强制使用底部固定坐标点击“我”Tab
        w_screen, h_screen = self.driver.get_screen_size()
        tab_y = int(h_screen * 0.96)
        tab_x = int(w_screen * 0.9)  # Center of the 5th tab (out of 5)
        
        logger.info(f"Clicking profile tab at fixed coordinate ({tab_x}, {tab_y})")
        self.driver.physical_tap(tab_x, tab_y)
        self.driver.human_sleep(2.0, 1.0)
        return True
        self.driver.human_sleep(2.0, 1.0)
        return True

    def go_back(self):
        """智能返回：先尝试视觉关闭按钮，再用物理Back键"""
        img = self.driver.screenshot()

        # 尝试点击关闭按钮
        close_btn = self.vision.find_template(img, "close_button", threshold=0.7)
        if close_btn:
            logger.info(f"Clicking close button at ({close_btn['x']}, {close_btn['y']})")
            self.driver.physical_tap(close_btn['x'], close_btn['y'])
            self.driver.human_sleep(1.5, 0.5)
            return

        # Fallback: 物理返回键
        self.driver.press_back()
        self.driver.human_sleep(1.0, 0.5)

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        """确保 XHS App 在前台"""
        self.driver.ensure_app_foreground(package_name)
