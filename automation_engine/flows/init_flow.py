"""
真机一键初始化编排器 (Industrial Grade)
将 DeviceOptimizer + Minitouch 部署 + Auto-Crop + App检测 + 登录校验串联为完整 Pipeline。
支持多机型、多分辨率、多 CPU 架构的自动适配。
"""
import subprocess
import os
import time
import glob
import cv2
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

    def run(self, device_serial: str = None, force: bool = False):
        """
        执行完整初始化流程。
        每一步均可通过 config.device.auto_* 开关控制是否执行。
        支持幂等：重复执行时自动跳过高开销步骤（IP轮换、模板采集），除非 force=True。
        """
        serial = device_serial or self.config.device.serial
        
        # ─── 修正 1：在所有对象初始化前探测 Serial ───
        if not serial:
            try:
                devices_result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
                lines = [l for l in devices_result.stdout.strip().split('\n')[1:] if '\tdevice' in l]
                if len(lines) == 1:
                    serial = lines[0].split('\t')[0]
                    logger.info(f"Auto-detected device serial: {serial}")
                elif len(lines) > 1:
                    logger.warning(f"Multiple devices detected. Using first: {lines[0].split(chr(9))[0]}")
                    serial = lines[0].split('\t')[0]
            except Exception as e:
                logger.warning(f"Serial auto-detection failed: {e}")

        logger.info(f"Starting device initialization for: {serial or 'default'} (force={force})")

        report = {
            "serial": serial,
            "steps": {},
            "success": True,
        }

        # ─── 幂等屏障：serial 已确定，加载上次的初始化档案 ───
        existing_profile = None
        if not force:
            existing_profile = self._load_device_profile(serial)
            if existing_profile and existing_profile.get("success"):
                logger.info(f"Found existing profile for {serial}, last init: {existing_profile.get('last_init_time', 'unknown')}. Costly steps will be skipped.")
            else:
                existing_profile = None  # Treat as first-time init

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
            
            # Use screenshot actual pixel size for template dir (may differ from wm size due to DPI/navbar)
            try:
                test_img = driver.screenshot()
                ss_h, ss_w = test_img.shape[:2]
                screenshot_res = f"{ss_w}x{ss_h}"
                logger.info(f"Screenshot resolution: {screenshot_res} (wm size: {w}x{h})")
            except Exception:
                screenshot_res = f"{w}x{h}"
                ss_w, ss_h = w, h
                logger.warning(f"Screenshot failed, using wm size for templates: {screenshot_res}")
            
            # Build device-specific template path: data/ui_templates/{serial}/{screenshot_res}/
            base_templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates")
            if serial:
                device_templates_dir = os.path.join(base_templates_dir, serial, screenshot_res)
            else:
                device_templates_dir = os.path.join(base_templates_dir, screenshot_res)
            os.makedirs(device_templates_dir, exist_ok=True)
            self.config.vision.templates_dir = device_templates_dir
            vision.templates_dir = device_templates_dir
            vision._load_templates() # 修正 3：重新加载专属模板，让 Watchdog 立即生效
            self._screenshot_res = screenshot_res
            self._base_templates_dir = base_templates_dir
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
            try:
                # 修正 6：先处理开屏弹窗，避免 OCR 被广告挡住
                watchdog.check_and_handle()
            except Exception as e:
                pass
            login_ok = self._check_login_status(driver, ocr, adb_prefix)
            report["steps"]["login_status"] = "LOGGED_IN" if login_ok else "NOT_LOGGED_IN"
            if not login_ok:
                logger.warning("XHS not logged in! Please login manually before running tasks.")
        else:
            report["steps"]["login_status"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 9: IP 轮换测试（幂等：已初始化过则跳过）
        # ═══════════════════════════════════════════
        if self.config.device.auto_rotate_ip_on_init:
            if existing_profile and existing_profile.get("steps", {}).get("ip_rotation") == "OK":
                logger.info("[9/10] IP rotation: SKIPPED (already verified in previous init, use --force to redo)")
                report["steps"]["ip_rotation"] = "SKIPPED_IDEMPOTENT"
            else:
                logger.info("[9/10] Testing IP rotation...")
                optimizer.toggle_airplane_mode()
                report["steps"]["ip_rotation"] = "OK"
        else:
            report["steps"]["ip_rotation"] = "SKIPPED"

        # ═══════════════════════════════════════════
        # Step 10: UI 模板采集
        # ═══════════════════════════════════════════
        # Ensure fallback values for base_templates_dir / screenshot_res if Step 2 didn't set them
        base_templates_dir = getattr(self, '_base_templates_dir', os.path.join(os.path.dirname(__file__), '..', 'data', 'ui_templates'))
        screenshot_res = getattr(self, '_screenshot_res', f"{self.config.device.screen_width}x{self.config.device.screen_height}")
        ss_w = self.config.device.screen_width
        ss_h = self.config.device.screen_height
        # Try to get actual screenshot dimensions if they were set in Step 2
        try:
            _sr = screenshot_res.split('x')
            ss_w, ss_h = int(_sr[0]), int(_sr[1])
        except Exception:
            pass

        if self.config.device.auto_crop_templates_on_init:
            # 幂等检测：模板是否已完整
            if existing_profile and self._templates_complete(serial, screenshot_res, base_templates_dir):
                logger.info("[10/10] Template crop: SKIPPED (all templates already present, use --force to redo)")
                report["steps"]["template_crop"] = "SKIPPED_IDEMPOTENT"
            else:
                logger.info("[10/10] Running auto template cropper...")
                try:
                    from tools.auto_crop_templates import automated_setup_pipeline
                    automated_setup_pipeline(driver, ocr, serial=serial, watchdog=watchdog)
                    report["steps"]["template_crop"] = "OK"

                    # Generate scaled watchdog templates from best available source
                    self._generate_watchdog_templates(base_templates_dir, serial, screenshot_res, ss_w, ss_h)
                except Exception as e:
                    logger.error(f"Template cropping failed: {e}")
                    report["steps"]["template_crop"] = f"FAILED: {e}"
        else:
            report["steps"]["template_crop"] = "SKIPPED"

        # Persist device profile for future reference
        import json
        from datetime import datetime, timezone
        profiles_dir = os.path.join(os.path.dirname(__file__), "..", "data", "device_profiles")
        os.makedirs(profiles_dir, exist_ok=True)
        
        # 修正 7：增加完整的设备指纹信息
        profile = {
            **report,
            "last_init_time": datetime.now(timezone.utc).isoformat(),
            "screenshot_resolution": getattr(self, '_screenshot_res', None),
            "physical_resolution": report["steps"].get("screen_resolution"),
            "android_version": self._get_android_version(adb_prefix),
            "xhs_version": getattr(self, '_xhs_version', None),
            "abi": getattr(self, '_device_abi', None)
        }

        profile_serial = serial or "unknown"
        profile_path = os.path.join(profiles_dir, f"{profile_serial}.json")
        try:
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            logger.info(f"Device profile saved to {profile_path}")
        except Exception as e:
            logger.warning(f"Failed to save device profile: {e}")
    
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
                    self._device_abi = abi
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
                self._xhs_version = version
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

    def _generate_watchdog_templates(self, base_templates_dir, serial, target_res, target_w, target_h):
        """Generate watchdog popup templates by scaling from best available source resolution."""
        watchdog_templates = [
            "slider_puzzle", "security_verification", "account_frozen",
            "phone_bind", "frequent_operation", "btn_iknow",
            "btn_skip", "btn_update_later", "btn_cancel", "btn_close"
        ]

        # Target directory
        if serial:
            target_dir = os.path.join(base_templates_dir, serial, target_res)
        else:
            target_dir = os.path.join(base_templates_dir, target_res)
        os.makedirs(target_dir, exist_ok=True)

        # Check which templates are already present
        missing = [t for t in watchdog_templates if not os.path.exists(os.path.join(target_dir, f"{t}.png"))]
        if not missing:
            logger.info("All watchdog templates already present.")
            return

        # Find best source directory (the one with most watchdog templates)
        best_source = None
        best_count = 0

        # Search all resolution dirs (skip device-specific subdirs, look for direct resolution dirs and inside device dirs)
        for res_dir in glob.glob(os.path.join(base_templates_dir, "*", "*")) + glob.glob(os.path.join(base_templates_dir, "*")):
            if not os.path.isdir(res_dir) or res_dir == target_dir:
                continue
            count = sum(1 for t in watchdog_templates if os.path.exists(os.path.join(res_dir, f"{t}.png")))
            if count > best_count:
                best_count = count
                best_source = res_dir

        if not best_source or best_count == 0:
            logger.warning("No source watchdog templates found to scale from.")
            return

        # Parse source resolution from dir name
        source_dirname = os.path.basename(best_source)
        try:
            src_w, src_h = map(int, source_dirname.split("x"))
        except ValueError:
            logger.warning(f"Cannot parse resolution from source dir: {source_dirname}")
            return

        scaled_count = 0
        for template_name in missing:
            src_path = os.path.join(best_source, f"{template_name}.png")
            if not os.path.exists(src_path):
                continue

            src_img = cv2.imread(src_path)
            if src_img is None:
                continue

            # Scale proportionally
            scale_x = target_w / src_w
            scale_y = target_h / src_h
            new_w = max(1, int(src_img.shape[1] * scale_x))
            new_h = max(1, int(src_img.shape[0] * scale_y))
            scaled_img = cv2.resize(src_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

            dst_path = os.path.join(target_dir, f"{template_name}.png")
            cv2.imwrite(dst_path, scaled_img)
            scaled_count += 1

        logger.info(f"Generated {scaled_count}/{len(missing)} watchdog templates by scaling from {source_dirname}")

    def _load_device_profile(self, serial) -> dict:
        """加载设备的上一次初始化档案，用于幂等判断。"""
        if not serial:
            return None
        import json
        profiles_dir = os.path.join(os.path.dirname(__file__), "..", "data", "device_profiles")
        profile_path = os.path.join(profiles_dir, f"{serial}.json")
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load device profile: {e}")
        return None

    def _templates_complete(self, serial, screenshot_res, base_templates_dir) -> bool:
        """检查该设备的模板文件是否已完整（包括 watchdog 模板）。"""
        required_templates = [
            "send_button",
            "slider_puzzle", "security_verification", "account_frozen",
            "phone_bind", "frequent_operation", "btn_iknow",
            "btn_skip", "btn_update_later", "btn_cancel", "btn_close"
        ]
        if serial:
            target_dir = os.path.join(base_templates_dir, serial, screenshot_res)
        else:
            target_dir = os.path.join(base_templates_dir, screenshot_res)
        
        for t in required_templates:
            if not os.path.exists(os.path.join(target_dir, f"{t}.png")):
                return False
        return True

    def _get_android_version(self, adb_prefix) -> str:
        """获取安卓版本号"""
        try:
            result = subprocess.run(
                adb_prefix + ["shell", "getprop", "ro.build.version.release"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"
