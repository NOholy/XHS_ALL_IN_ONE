import subprocess
import time
from .logger import get_logger

logger = get_logger("device_optimizer")

class DeviceOptimizer:
    def __init__(self, serial=None):
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]

    def disable_all_animations(self):
        """
        关闭安卓系统的所有过渡动画。
        极大提升 OCR 和视觉匹配的准确率，防止截取到半透明的动画中间态。
        """
        logger.info("Disabling Android system animations for robust vision matching...")
        settings = [
            ("window_animation_scale", "0.0"),
            ("transition_animation_scale", "0.0"),
            ("animator_duration_scale", "0.0")
        ]
        for key, val in settings:
            subprocess.run(self.adb_prefix + ["shell", "settings", "put", "global", key, val])
        logger.info("[+] All system animations disabled.")

    def toggle_airplane_mode(self, delay_seconds=5):
        """
        开关飞行模式，用于重置 4G/5G 网络的公网 IP。
        注意：需要设备有 ROOT 权限或通过 ADB 赋权。
        """
        logger.info("Toggling airplane mode for IP rotation via KeyEvents (no root required)...")
        # 打开快速设置面板
        subprocess.run(self.adb_prefix + ["shell", "cmd", "statusbar", "expand-settings"])
        time.sleep(1)
        
        # 假设飞行模式在快速设置的第一排，这里用通用的 ADB 方案：
        # 对于没有 Root 权限且 Android 版本较新的设备，
        # am broadcast 被阻挡。可以通过 svc wifi disable/enable 结合 svc data disable/enable 来模拟换 IP 的效果。
        logger.info("Disabling Cellular Data and WiFi...")
        subprocess.run(self.adb_prefix + ["shell", "svc", "data", "disable"])
        subprocess.run(self.adb_prefix + ["shell", "svc", "wifi", "disable"])
        
        logger.info(f"Network OFF. Waiting {delay_seconds}s for connection drop...")
        time.sleep(delay_seconds)
        
        logger.info("Enabling Cellular Data and WiFi...")
        subprocess.run(self.adb_prefix + ["shell", "svc", "data", "enable"])
        subprocess.run(self.adb_prefix + ["shell", "svc", "wifi", "enable"])
        
        subprocess.run(self.adb_prefix + ["shell", "cmd", "statusbar", "collapse"])
        
        logger.info("Network ON. Waiting 10s for new IP allocation...")
        time.sleep(10)

    def keep_screen_on(self):
        """
        防止测试中途屏幕休眠。
        """
        logger.info("Setting screen timeout to maximum to prevent sleep...")
        subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "screen_off_timeout", "1800000"]) # 30 mins
        subprocess.run(self.adb_prefix + ["shell", "svc", "power", "stayon", "true"])
        
    def clear_app_data(self, package_name="com.xingin.xhs"):
        """
        切换账号时，彻底清理沙盒数据，防止本地 UUID 缓存追踪。
        """
        logger.info(f"Clearing sandbox data for {package_name}...")
        subprocess.run(self.adb_prefix + ["shell", "pm", "clear", package_name])
