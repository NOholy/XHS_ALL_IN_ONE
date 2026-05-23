"""
真机一键初始化编排器
将 DeviceOptimizer + Auto-Crop + App检测 + 登录校验串联为完整 Pipeline。
"""
import subprocess
from mobile_core.device_optimizer import DeviceOptimizer
from mobile_core.logger import get_logger

logger = get_logger("init_flow")


class InitOrchestrator:
    """
    真机一键初始化 Pipeline。
    所有步骤均通过 config.device 配置开关控制。
    """

    def __init__(self, config):
        self.config = config

    def run(self, device_serial: str = None):
        """
        执行完整初始化流程。
        每一步均可通过 config.device.auto_* 开关控制是否执行。
        """
        serial = device_serial or self.config.device.serial
        logger.info(f"Starting device initialization for: {serial or 'default'}")

        report = {
            "serial": serial,
            "steps": {},
            "success": True,
        }

        optimizer = DeviceOptimizer(serial)
        adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]

        # Step 1: ADB 连接校验
        logger.info("[1/7] Verifying ADB connection...")
        if not self._check_adb(adb_prefix):
            report["success"] = False
            report["steps"]["adb_check"] = "FAILED"
            logger.error("ADB connection failed. Aborting.")
            return report
        report["steps"]["adb_check"] = "OK"

        # Step 2: 关闭动画
        if self.config.device.auto_disable_animations:
            logger.info("[2/7] Disabling system animations...")
            optimizer.disable_all_animations()
            report["steps"]["disable_animations"] = "OK"
        else:
            report["steps"]["disable_animations"] = "SKIPPED"

        # Step 3: 屏幕常亮
        if self.config.device.auto_keep_screen_on:
            logger.info("[3/7] Setting screen always-on...")
            optimizer.keep_screen_on()
            report["steps"]["keep_screen_on"] = "OK"
        else:
            report["steps"]["keep_screen_on"] = "SKIPPED"

        # Step 4: 检测 XHS App
        if self.config.device.check_xhs_installed:
            logger.info("[4/7] Checking XHS app installation...")
            if self._check_app_installed(adb_prefix):
                report["steps"]["xhs_installed"] = "OK"
            else:
                report["steps"]["xhs_installed"] = "NOT_INSTALLED"
                report["success"] = False
                logger.error("XHS app not installed! Please install manually.")
                return report
        else:
            report["steps"]["xhs_installed"] = "SKIPPED"

        # Step 5: 登录状态检测
        if self.config.device.check_login_status:
            logger.info("[5/7] Checking login status...")
            login_ok = self._check_login_status(adb_prefix)
            report["steps"]["login_status"] = "LOGGED_IN" if login_ok else "NOT_LOGGED_IN"
            if not login_ok:
                logger.warning("XHS not logged in! Please login manually before running tasks.")
        else:
            report["steps"]["login_status"] = "SKIPPED"

        # Step 6: UI 模板采集
        if self.config.device.auto_crop_templates_on_init:
            logger.info("[6/7] Running auto template cropper...")
            try:
                from tools.auto_crop_templates import automated_setup_pipeline
                from mobile_core.device_driver import DeviceDriver
                from mobile_core.ocr_client import OCRClient
                driver = DeviceDriver(serial)
                ocr = OCRClient(self.config.ocr.endpoint)
                automated_setup_pipeline(driver, ocr)
                report["steps"]["template_crop"] = "OK"
            except Exception as e:
                logger.error(f"Template cropping failed: {e}")
                report["steps"]["template_crop"] = f"FAILED: {e}"
        else:
            report["steps"]["template_crop"] = "SKIPPED"

        # Step 7: IP 轮换测试
        if self.config.device.auto_rotate_ip_on_init:
            logger.info("[7/7] Testing IP rotation...")
            try:
                optimizer.toggle_airplane_mode(
                    delay_seconds=self.config.risk_control.ip_rotate_delay
                )
                report["steps"]["ip_rotation"] = "OK"
            except Exception as e:
                logger.error(f"IP rotation failed: {e}")
                report["steps"]["ip_rotation"] = f"FAILED: {e}"
        else:
            report["steps"]["ip_rotation"] = "SKIPPED"

        # 输出报告
        logger.info(f"Initialization complete. Report: {report}")
        return report

    def _check_adb(self, adb_prefix) -> bool:
        """检查 ADB 连接"""
        try:
            result = subprocess.run(
                adb_prefix + ["get-state"],
                capture_output=True, text=True, timeout=10
            )
            return "device" in result.stdout
        except Exception:
            return False

    def _check_app_installed(self, adb_prefix, package="com.xingin.xhs") -> bool:
        """检查 XHS App 是否已安装"""
        try:
            result = subprocess.run(
                adb_prefix + ["shell", "pm", "list", "packages", package],
                capture_output=True, text=True, timeout=10
            )
            return package in result.stdout
        except Exception:
            return False

    def _check_login_status(self, adb_prefix, package="com.xingin.xhs") -> bool:
        """
        启动 App 并通过 OCR 检测是否需要登录。
        检测屏幕上是否出现 "登录" / "注册" 等文字。
        """
        try:
            # 启动 App
            subprocess.run(
                adb_prefix + ["shell", "monkey", "-p", package,
                              "-c", "android.intent.category.LAUNCHER", "1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            import time
            time.sleep(8)  # 等待启动和闪屏

            # 截图 + OCR
            from mobile_core.agentless_driver import AgentlessMinitouchDriver
            from mobile_core.ocr_client import OCRClient

            serial = self.config.device.serial
            driver = AgentlessMinitouchDriver(serial)
            ocr = OCRClient(self.config.ocr.endpoint)

            img = driver.screenshot()
            login_indicators = ["登录", "注册", "手机号"]
            for indicator in login_indicators:
                matches = ocr.find_text(img, indicator, conf_threshold=0.6)
                if matches:
                    return False  # 检测到登录提示，说明未登录

            return True  # 未检测到登录提示，认为已登录
        except Exception as e:
            logger.error(f"Login check failed: {e}")
            return True  # 检测失败时不阻断流程
