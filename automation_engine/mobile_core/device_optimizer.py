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
        logger.info("Reducing Android animation scales to 0.5 (W5: avoid full-disable fingerprint)...")
        settings = [
            ("window_animation_scale", "0.5"),
            ("transition_animation_scale", "0.5"),
            ("animator_duration_scale", "0.5")
        ]
        for key, val in settings:
            res = subprocess.run(self.adb_prefix + ["shell", "settings", "put", "global", key, val], capture_output=True, text=True, timeout=10)
            if "SecurityException" in res.stderr:
                logger.error(f"Permission denied to change {key}. "
                             f"For MIUI/ColorOS/OriginOS, please manually enable 'USB debugging (Security settings)' in Developer Options.")
        logger.info("[+] Animation disable sequence completed.")

    def detect_screen_resolution(self):
        """
        动态检测真机屏幕分辨率，返回 (width, height) 元组。
        同时兼容 Override size 场景（取物理分辨率）。
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "wm", "size"],
                capture_output=True, text=True, timeout=5
            )
            # 统一使用 Override size（与 agentless_driver.py 和截图坐标空间一致）
            override_size = None
            default_size = None
            for line in result.stdout.strip().split('\n'):
                if "Override size:" in line:
                    wh = line.split("Override size:")[1].strip()
                    w, h = wh.split("x")
                    override_size = (int(w), int(h))
                elif ":" in line:
                    wh = line.strip().split(":")[-1].strip()
                    try:
                        w, h = wh.split("x")
                        default_size = (int(w), int(h))
                    except ValueError:
                        pass
            if override_size:
                return override_size
            if default_size:
                return default_size
        except Exception as e:
            logger.warning(f"Screen resolution detection failed: {e}")
        return 0, 0

    def _get_ip(self):
        """
        获取设备当前活跃网络 IP（蜂窝优先，WiFi 兜底）。
        返回 (ip_address, network_type) 元组。
        兼容各品牌的网卡命名差异（高通 rmnet / MTK ccmni / 三星 wwan / 展锐 seth）。
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "ip", "-f", "inet", "addr", "show"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return "unknown", "unknown"

            cellular_prefixes = ["rmnet", "ccmni", "wwan", "pdp", "seth", "v4-rmnet"]
            wifi_prefixes = ["wlan", "wifi"]
            
            lines = result.stdout.split('\n')
            cellular_ip = None
            wifi_ip = None
            current_iface = ""

            for line in lines:
                stripped = line.strip()
                # Interface line: "40: wlan0: <FLAGS> ..."
                if stripped and stripped[0].isdigit() and ": " in stripped:
                    current_iface = stripped.split(": ")[1].split(":")[0] if ": " in stripped else ""
                # inet line: "inet 10.x.x.x/29 ..."
                elif "inet " in stripped and not "inet6" in stripped:
                    parts = stripped.split()
                    if len(parts) >= 2:
                        ip = parts[1].split('/')[0]
                        if ip.startswith("127."):
                            continue
                        # 判断归属
                        if any(p in current_iface for p in cellular_prefixes):
                            cellular_ip = ip
                        elif any(p in current_iface for p in wifi_prefixes):
                            wifi_ip = ip

            # 蜂窝优先
            if cellular_ip:
                return cellular_ip, "cellular"
            if wifi_ip:
                return wifi_ip, "wifi"

        except Exception as e:
            logger.warning(f"IP detection failed: {e}")
        return "unknown", "unknown"

    def _execute_shell(self, *cmd_args, **kwargs):
        """
        统一的 shell 执行包装器。
        支持 PC 端 adb shell，或者手机端本地通过 Shizuku (rish) 提权执行。
        """
        use_shizuku = getattr(self, 'use_shizuku', False)
        if use_shizuku:
            # 本地运行环境，使用 rish -c 执行
            cmd_str = " ".join(cmd_args)
            return subprocess.run(["rish", "-c", cmd_str], **kwargs)
        else:
            # PC 端运行环境，使用 adb shell 执行
            return subprocess.run(self.adb_prefix + ["shell"] + list(cmd_args), **kwargs)

    def toggle_airplane_mode(self, delay_seconds=5, use_shizuku=False):
        """
        开关飞行模式，用于重置 4G/5G 网络的公网 IP。
        支持 Android 12 的 cmd connectivity 接口，并兼容 Shizuku (rish) 提权执行。
        """
        import random
        # 动态设置此次执行是否使用 shizuku
        if use_shizuku and not hasattr(self, 'use_shizuku'):
            self.use_shizuku = True
            
        jitter_delay = delay_seconds + random.uniform(1.0, 5.0)
        env_msg = "Shizuku (rish)" if getattr(self, 'use_shizuku', False) else "PC ADB"
        logger.info(f"Toggling airplane mode via {env_msg} (delay {jitter_delay:.1f}s)...")
        
        old_ip, old_net = self._get_ip()
        logger.info(f"Current IP: {old_ip} ({old_net})")

        # 1. 打开快速设置面板 (用于伪装给系统看)
        try:
            self._execute_shell("cmd", "statusbar", "expand-settings", timeout=10)
        except Exception as e:
            logger.debug(f"Failed to expand statusbar: {e}")
        
        time.sleep(1)
        
        # 2. 断开网络
        # 优先尝试 Android 12+ 更有效的飞行模式接口
        try:
            res_ap = self._execute_shell("cmd", "connectivity", "airplane-mode", "enable", capture_output=True, text=True, timeout=10)
            
            # 同时下发 svc 命令作为兜底
            res_data = self._execute_shell("svc", "data", "disable", capture_output=True, text=True, timeout=10)
            self._execute_shell("svc", "wifi", "disable", capture_output=True, text=True, timeout=10)
            
            # 错误分析日志
            if res_ap and res_ap.stderr and ("SecurityException" in res_ap.stderr or "not found" in res_ap.stderr):
                logger.debug(f"cmd connectivity fallback: {res_ap.stderr.strip()}")
            if res_data and res_data.stderr and ("Killed" in res_data.stderr or "Permission denied" in res_data.stderr):
                logger.warning("svc command failed due to missing Root/Shell permissions.")
                if not getattr(self, 'use_shizuku', False):
                    logger.error("You are on Android 12 without Shizuku local execution enabled. IP rotation might fail.")
        except Exception as e:
            logger.error(f"Error executing shell commands to disable network: {e}")
        
        logger.info(f"Network OFF. Waiting {jitter_delay:.1f}s for connection drop...")
        time.sleep(jitter_delay)
        
        # 3. 恢复网络
        logger.info("Enabling Cellular Data and WiFi...")
        try:
            self._execute_shell("cmd", "connectivity", "airplane-mode", "disable", timeout=10)
            self._execute_shell("svc", "data", "enable", timeout=10)
            self._execute_shell("svc", "wifi", "enable", timeout=10)
            self._execute_shell("cmd", "statusbar", "collapse", timeout=10)
        except Exception as e:
            logger.error(f"Error executing shell commands to enable network: {e}")
        
        logger.info("Network ON. Waiting for IP allocation...")
        
        # Retry loop for network recovery (up to 30 seconds)
        for attempt in range(15):
            time.sleep(2)
            new_ip, new_net = self._get_ip()
            if new_ip != "unknown":
                break
                
        if old_ip != "unknown" and old_ip == new_ip:
            logger.warning(f"⚠️ IP rotation may have failed. IP remains: {new_ip} ({new_net})")
        elif new_ip == "unknown":
            logger.warning("⚠️ Network not recovered yet. IP unknown.")
        else:
            logger.info(f"✅ IP rotated: {old_ip} → {new_ip} ({new_net})")

    def rotate_ip_stealthy(self, delay_seconds=5, use_shizuku=False):
        """
        W1: 隐蔽 IP 轮换 — 仅断开/重连蜂窝数据，不触发飞行模式广播。
        App 无法通过 ACTION_AIRPLANE_MODE_CHANGED 监听到此操作。
        比 toggle_airplane_mode 更安全，但仅限蜂窝网络。
        """
        import random
        if use_shizuku and not hasattr(self, 'use_shizuku'):
            self.use_shizuku = True

        jitter_delay = delay_seconds + random.uniform(2.0, 8.0)
        env_msg = "Shizuku" if getattr(self, 'use_shizuku', False) else "ADB"
        logger.info(f"W1: Stealthy IP rotation via {env_msg} (delay {jitter_delay:.1f}s)")

        old_ip, old_net = self._get_ip()
        logger.info(f"Current IP: {old_ip} ({old_net})")

        # 仅断开蜂窝数据 (不触发飞行模式广播)
        try:
            self._execute_shell("svc", "data", "disable", capture_output=True, text=True, timeout=10)
        except Exception as e:
            logger.warning(f"svc data disable failed, falling back to airplane mode: {e}")
            return self.toggle_airplane_mode(delay_seconds, use_shizuku)

        logger.info(f"Cellular data OFF. Waiting {jitter_delay:.1f}s...")
        time.sleep(jitter_delay)

        # 重连
        try:
            self._execute_shell("svc", "data", "enable", capture_output=True, text=True, timeout=10)
        except Exception as e:
            logger.error(f"svc data enable failed: {e}")

        logger.info("Cellular data ON. Waiting for IP allocation...")
        for attempt in range(15):
            time.sleep(2)
            new_ip, new_net = self._get_ip()
            if new_ip != "unknown" and new_ip != old_ip:
                break

        if old_ip != "unknown" and old_ip == new_ip:
            logger.warning(f"⚠️ Stealthy rotation failed (IP unchanged: {new_ip}). Falling back to airplane mode.")
            return self.toggle_airplane_mode(delay_seconds, use_shizuku)
        elif new_ip == "unknown":
            logger.warning("⚠️ Network not recovered. Falling back to airplane mode.")
            return self.toggle_airplane_mode(delay_seconds, use_shizuku)
        else:
            logger.info(f"✅ Stealthy IP rotated: {old_ip} → {new_ip} ({new_net})")

    def keep_screen_on(self):
        """
        防止测试中途屏幕休眠。
        注意: stayon 通常只在连接 USB 时生效，电池模式下可能不生效。
        """
        logger.info("Setting screen timeout to maximum to prevent sleep... (Note: stayon works mostly while charging)")
        subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "screen_off_timeout", "1800000"], timeout=10)  # 30 mins
        subprocess.run(self.adb_prefix + ["shell", "svc", "power", "stayon", "true"], timeout=10)
        
    def clear_app_data(self, package_name="com.xingin.xhs"):
        """
        切换账号时，彻底清理沙盒数据，防止本地 UUID 缓存追踪。
        """
        logger.info(f"Clearing sandbox data for {package_name}...")
        subprocess.run(self.adb_prefix + ["shell", "pm", "clear", package_name], timeout=15)
