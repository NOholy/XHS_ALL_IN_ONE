"""
轻量级页面检测器
通过 dumpsys activity top 获取当前前台 Activity 名称，实现 ~50ms 的快速页面判断。
不使用 uiautomator dump（避免进程暴露），不使用 Accessibility（避免权限检测）。
"""
import subprocess
from .logger import get_logger

logger = get_logger("page_detector")


class LightPageDetector:
    """
    轻量级页面检测器。
    使用 dumpsys 系统命令获取当前前台 Activity，实现零风险的快速页面判断。

    风控安全性:
    - dumpsys 是系统级命令，App 进程无权检测其他进程的 ADB 活动
    - 不在设备上创建任何文件
    - 不启动任何新进程（如 uiautomator）
    - 不注入任何 Java agent
    - 命令执行时间 ~50ms，不产生 UI 冻结
    """

    def __init__(self, adb_prefix):
        self.adb_prefix = adb_prefix

    def get_current_activity(self) -> str:
        """
        获取当前前台 Activity 类名。
        返回格式: "com.xingin.xhs/.index.v2.IndexActivityV2"
        失败返回空字符串。
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "dumpsys", "activity", "top"],
                capture_output=True, text=True, timeout=3
            )
            # 输出中搜索 ACTIVITY 行
            for line in result.stdout.strip().split('\n'):
                stripped = line.strip()
                if 'ACTIVITY' in stripped and '/' in stripped:
                    parts = stripped.split()
                    for part in parts:
                        if '/' in part and '.' in part:
                            return part
        except Exception as e:
            logger.debug(f"get_current_activity failed: {e}")
        return ""

    def get_current_package(self) -> str:
        """
        获取当前前台 App 包名。
        使用更轻量的 dumpsys window 命令。
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "dumpsys", "window", "windows"],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.split('\n'):
                if 'mCurrentFocus' in line or 'mFocusedApp' in line:
                    # 格式: "mCurrentFocus=Window{xxx com.xingin.xhs/...}"
                    if 'com.' in line:
                        import re
                        m = re.search(r'(com\.\S+?)(?:/|\}|\s)', line)
                        if m:
                            return m.group(1)
        except Exception as e:
            logger.debug(f"get_current_package failed: {e}")
        return ""

    def is_keyboard_visible(self) -> bool:
        """
        检测软键盘是否弹出。
        替代 MSE 比对来检测键盘吸附问题。
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "dumpsys", "input_method"],
                capture_output=True, text=True, timeout=3
            )
            return "mInputShown=true" in result.stdout
        except Exception:
            return False

    def detect_page_fast(self) -> str:
        """
        通过 Activity 名称快速判断当前页面。
        耗时 ~50ms，比 OCR 的 ~1000ms 快 20 倍。
        
        返回值与 navigator.py 的 PAGE_* 常量保持一致。
        """
        activity = self.get_current_activity()
        if not activity:
            return "unknown"

        if "com.xingin.xhs" not in activity:
            return "not_xhs"

        # XHS Activity 映射（基于 v9.x 版本，可能需要随版本更新）
        activity_lower = activity.lower()
        if any(k in activity_lower for k in ["index", "main", "home", "splash"]):
            return "home_feed"
        elif any(k in activity_lower for k in ["searchresult", "search_result"]):
            return "search_results"
        elif any(k in activity_lower for k in ["notedetail", "note_detail", "postdetail", "post_detail"]):
            return "post_detail"
        elif any(k in activity_lower for k in ["profile", "user"]):
            return "profile"
        elif any(k in activity_lower for k in ["search"]):
            return "search_page"
        elif any(k in activity_lower for k in ["comment"]):
            return "comment_panel"
        return "unknown"

    # ---- 系统级弹窗检测 ----

    # 常见的系统弹窗 Activity 特征（不依赖 OCR）
    SYSTEM_ACTIVITY_PATTERNS = [
        "PermissionController",     # 权限弹窗
        "PackageInstaller",         # 安装确认
        "DialogActivity",           # 系统对话框
        "AlertActivity",            # 系统警告
        "chooser",                  # 分享选择器
        "ResolverActivity",         # Intent 解析弹窗
    ]

    def is_system_dialog(self) -> bool:
        """检测当前前台是否为系统弹窗"""
        activity = self.get_current_activity()
        return any(p.lower() in activity.lower() for p in self.SYSTEM_ACTIVITY_PATTERNS)

    def is_xhs_foreground(self) -> bool:
        """检测 XHS 是否在前台"""
        activity = self.get_current_activity()
        return "com.xingin.xhs" in activity
