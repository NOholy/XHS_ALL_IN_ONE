"""
真机一键初始化编排器 (Industrial Grade)
将 DeviceOptimizer + Minitouch 部署 + Auto-Crop + App检测 + 登录校验串联为完整 Pipeline。
支持多机型、多分辨率、多 CPU 架构的自动适配。
"""
import subprocess
import os
import time
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

        # ═══════════════════════════════════════════
        # Initialize Shared Components
        # 避免在不同步骤中重复创建 driver，导致 minitouch 无法复用
        # ═══════════════════════════════════════════
        from mobile_core.vision import VisionEngine
        from mobile_core.ocr_client import OCRClient
        from mobile_core.watchdog import PopupWatchdog

        if self.config.device.use_agentless:
            from mobile_core.agentless_driver import AgentlessMinitouchDriver
            driver = AgentlessMinitouchDriver(serial)
        else:
            from mobile_core.device_driver import DeviceDriver
            driver = DeviceDriver(serial)

        ocr = OCRClient(self.config.ocr.endpoint)
        vision = VisionEngine(self.config.vision.templates_dir)
        watchdog = PopupWatchdog(vision, driver)

        # ═══════════════════════════════════════════
        # Step 0: Pre-flight checks (OCR health)
        # ═══════════════════════════════════════════
        logger.info("[0/10] Pre-flight checks (OCR service)...")
        if not self._check_ocr_health():
            report["success"] = False
            report["steps"]["pre_flight"] = "OCR_FAILED"
            return report
        report["steps"]["pre_flight"] = "OK"

        # ═══════════════════════════════════════════
        # Step 1: ADB 连接校验
        # ═══════════════════════════════════════════
        logger.info("[1/10] Verifying ADB connection...")
        if not self._check_adb(adb_prefix):
            report["success"] = False
            report["steps"]["adb_check"] = "FAILED"
            logger.error("ADB connection failed. Aborting.")
            return report
        report["steps"]["adb_check"] = "OK"

        # ═══════════════════════════════════════════
        # Step 2: 动态检测屏幕分辨率
        # ═══════════════════════════════════════════
        logger.info("[2/10] Detecting screen resolution...")
        w, h = optimizer.detect_screen_resolution()
        if w > 0 and h > 0:
            self.config.device.screen_width = w
            self.config.device.screen_height = h
            logger.info(f"Screen resolution: {w}x{h}")
            report["steps"]["screen_resolution"] = f"{w}x{h}"
            
            # Update vision templates dir for resolution to enable watchdog during init
            res_dir = f"{w}x{h}"
            if not self.config.vision.templates_dir.endswith(res_dir):
                self.config.vision.templates_dir = os.path.join(self.config.vision.templates_dir, res_dir)
                os.makedirs(self.config.vision.templates_dir, exist_ok=True)
                vision.templates_dir = self.config.vision.templates_dir
        else:
            logger.warning("Could not detect resolution. Using config defaults.")
            report["steps"]["screen_resolution"] = "FALLBACK"

        # ═══════════════════════════════════════════
        # Step 3: 清理历史 U2 自动化残留
        # ═══════════════════════════════════════════
        logger.info("[3/10] Cleaning up legacy u2 agent residue...")
        subprocess.run(adb_prefix + ["shell", "pm", "uninstall", "com.github.nicekeyboard"], capture_output=True)
        subprocess.run(adb_prefix + ["shell", "rm", "-f", "/data/local/tmp/u2.jar"], capture_output=True)
        report["steps"]["cleanup_u2"] = "OK"

        # ═══════════════════════════════════════════
        # Step 4: Minitouch 部署（多机型自动适配）
        # ═══════════════════════════════════════════
        logger.info("[4/10] Deploying minitouch (multi-ABI auto-detection)...")
        mt_result = self._deploy_minitouch(driver)
        report["steps"]["minitouch"] = mt_result

        # ═══════════════════════════════════════════
        # Step 5: 关闭动画
        # ═══════════════════════════════════════════
        if self.config.device.auto_disable_animations:
            logger.info("[5/10] Disabling system animations...")
            optimizer.disable_all_animations()
            report["steps"]["disable_animations"] = "OK"
        else:
            logger.info("[5/10] Animations: Keeping enabled (stealth mode).")
            report["steps"]["disable_animations"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 6: 屏幕常亮
        # ═══════════════════════════════════════════
        if self.config.device.auto_keep_screen_on:
            logger.info("[6/10] Setting screen always-on...")
            optimizer.keep_screen_on()
            report["steps"]["keep_screen_on"] = "OK"
        else:
            report["steps"]["keep_screen_on"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 7: 检测 XHS App
        # ═══════════════════════════════════════════
        if self.config.device.check_xhs_installed:
            logger.info("[7/10] Checking XHS app installation...")
            if self._check_app_installed(adb_prefix):
                report["steps"]["xhs_installed"] = "OK"
            else:
                report["steps"]["xhs_installed"] = "NOT_INSTALLED"
                report["success"] = False
                logger.error("XHS app not installed! Please install manually.")
                return report
        else:
            report["steps"]["xhs_installed"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 8: 登录状态检测（高效单次 OCR）
        # ═══════════════════════════════════════════
        if self.config.device.check_login_status:
            logger.info("[8/10] Checking login status (efficient single-OCR)...")
            login_ok = self._check_login_status(driver, ocr, adb_prefix)
            report["steps"]["login_status"] = "LOGGED_IN" if login_ok else "NOT_LOGGED_IN"
            if not login_ok:
                logger.warning("XHS not logged in! Please login manually before running tasks.")
        else:
            report["steps"]["login_status"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 9: IP 轮换测试
        # ═══════════════════════════════════════════
        if self.config.device.auto_rotate_ip_on_init:
            logger.info("[9/10] Testing IP rotation...")
            optimizer.toggle_airplane_mode()
            report["steps"]["ip_rotation"] = "OK"
        else:
            report["steps"]["ip_rotation"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 10: UI 模板采集
        # ═══════════════════════════════════════════
        if self.config.device.auto_crop_templates_on_init:
            logger.info("[10/10] Running auto template cropper...")
            try:
                from tools.auto_crop_templates import automated_setup_pipeline
                automated_setup_pipeline(driver, ocr, watchdog=watchdog)
                report["steps"]["template_crop"] = "OK"
            except Exception as e:
                logger.error(f"Template cropping failed: {e}")
                report["steps"]["template_crop"] = f"FAILED: {e}"
        else:
            report["steps"]["template_crop"] = "SKIPPED"

        # 输出报告
        logger.info(f"Initialization complete. Report: {report}")
        return report

    # ─────────── Private Methods ───────────

    def _check_ocr_health(self) -> bool:
        """检查 OCR 微服务是否可用。使用 trust_env=False 绕过系统代理。"""
        try:
            import requests
            session = requests.Session()
            session.trust_env = False  # 关键：绕过系统 SOCKS 代理

            health_url = self.config.ocr.endpoint.replace("/ocr", "/docs")
            res = session.get(health_url, timeout=5)
            if res.status_code == 200:
                logger.info("OCR microservice is healthy.")
                return True
            else:
                logger.error(f"OCR microservice returned HTTP {res.status_code}. Please check ocr_server.py.")
                return False
        except Exception as e:
            logger.error(f"OCR microservice unreachable. Please start ocr_server.py first! Error: {e}")
            return False

    def _check_adb(self, adb_prefix) -> bool:
        """检查 ADB 连接"""
        try:
            result = subprocess.run(
                adb_prefix + ["get-state"],
                capture_output=True, text=True, timeout=10
            )
            if "device" in result.stdout:
                return True
            # if we get here, it might be unauthorized or multiple devices
            devices_res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            if "unauthorized" in devices_res.stdout:
                logger.error("ADB device is unauthorized. Please accept the RSA key prompt on the device screen.")
            elif "multiple" in result.stderr or "more than one" in result.stderr:
                logger.error("Multiple ADB devices connected. Please specify serial in config or disconnect others.")
            else:
                logger.error(f"ADB get-state failed: {result.stderr.strip() or result.stdout.strip()}")
            return False
        except Exception as e:
            logger.error(f"ADB check exception: {e}")
            return False

    def _deploy_minitouch(self, driver) -> str:
        """
        检测设备 CPU 架构，自动部署对应的 minitouch 二进制文件。
        支持所有主流 ABI: arm64-v8a, armeabi-v7a, armeabi, x86_64, x86, mips64, mips
        """
        try:
            if hasattr(driver, "ensure_minitouch"):
                if driver.ensure_minitouch():
                    abi = driver._get_device_abi()
                    return f"OK (ABI: {abi}, minitouch socket connected)"
                else:
                    return "FALLBACK (minitouch unavailable, using adb input tap)"
            return "SKIPPED (Not using Agentless Driver)"
        except Exception as e:
            logger.warning(f"Minitouch deployment failed: {e}")
            return f"FALLBACK: {e}"

    def _check_app_installed(self, adb_prefix, package="com.xingin.xhs") -> bool:
        """检查 XHS App 是否已安装，并记录版本号"""
        try:
            result = subprocess.run(
                adb_prefix + ["shell", "dumpsys", "package", package],
                capture_output=True, text=True, timeout=10
            )
            if "versionName=" in result.stdout:
                version = result.stdout.split("versionName=")[1].split("\n")[0]
                logger.info(f"XHS app found, version: {version}")
                return True
            return False
        except Exception:
            return False

    def _check_login_status(self, driver, ocr, adb_prefix, package="com.xingin.xhs") -> bool:
        """
        启动 App 并通过 OCR 检测是否已登录。
        优化：每轮只截图 + OCR 一次，用返回的全部文本做字符串匹配。
        避免原来每个指标独立调用 OCR 导致的请求爆炸。
        """
        try:
            # 启动 App
            subprocess.run(
                adb_prefix + ["shell", "monkey", "-p", package,
                              "-c", "android.intent.category.LAUNCHER", "1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            login_indicators = ["登录", "注册", "手机号", "验证码登录", "密码登录"]
            feed_indicators = ["首页", "发现", "购物", "消息", "关注", "推荐"]

            # 最多 3 轮探测（每轮一次 OCR），总耗时 ≤ 30s
            for attempt in range(3):
                time.sleep(3)
                img = driver.screenshot()

                # 单次 OCR 提取全部文本
                try:
                    results = ocr.ocr_image(img)
                except Exception as e:
                    logger.warning(f"Login check OCR attempt {attempt + 1} failed: {e}")
                    continue

                all_text = " ".join([text for _, (text, _) in results]) if results else ""

                if any(ind in all_text for ind in login_indicators):
                    logger.warning(f"Detected login indicator in screen text: {all_text[:100]}...")
                    return False
                if any(ind in all_text for ind in feed_indicators):
                    logger.info(f"Feed loaded. Login confirmed. Screen text: {all_text[:80]}...")
                    return True

            logger.warning("Could not definitively determine login status after 3 attempts. Assuming logged in.")
            return True
        except Exception as e:
            logger.error(f"Login check failed: {e}")
            return True  # 检测失败时不阻断流程
