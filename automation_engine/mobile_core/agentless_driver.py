"""
Phase 3: Agentless Driver — Industrial Grade
Replaces minitouch/uiautomator2 with app_process 触控注入.
Leaves ZERO test agents on the Android device.
Uses 'app_process' for high-speed, undetectable touch emulation,
and 'adb exec-out screencap' for vision.
"""
import time
import random
import socket
import numpy as np
import subprocess
import cv2
import os
import atexit
from .logger import get_logger

class InjectorError(Exception):
    pass

class PreconditionError(Exception):
    pass

logger = get_logger("agentless_driver")

# Touch injector prebuilt directory (relative to project root)
# V4: 使用伪装名称, 避免进程名暴露
_INJECTOR_DEX_PATH = os.path.join(
    os.path.dirname(__file__), "injector", "touch_injector.dex"
)
_INJECTOR_DEX_REMOTE = "/data/local/tmp/framework-ext.dex"  # V4: 伪装为系统扩展
_INJECTOR_CLASS_NAME = "SensorHalService"  # V4: 进程名伪装为传感器服务

class AgentlessMinitouchDriver:
    """
    Phase 3: Agentless Driver.
    Uses 'app_process' 触控注入 via ADB port forwarding for high-speed, 
    undetectable touch emulation, and 'adb exec-out screencap' for vision.
    """

    def __init__(self, serial=None, yolo_model_path=None):
        self.serial = serial
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]
        logger.info("Initializing Agentless Driver...", extra={"serial": serial})
        self._check_connection()

        # YOLO Object Detection Model
        self._yolo_model = None
        if yolo_model_path:
            try:
                from ultralytics import YOLO
                self._yolo_model = YOLO(yolo_model_path)
                logger.info(f"Loaded YOLO model from {yolo_model_path}")
            except Exception as e:
                logger.error(f"Failed to load YOLO model: {e}")

        # injector state
        self._touch_process = None
        self._touch_socket = None
        self._touch_max_x = 0
        self._touch_max_y = 0
        self._touch_max_contacts = 0
        self._touch_max_pressure = 0
        self._touch_port = 0
        self._touch_available = False

        # Screen size cache
        self._screen_w = 0
        self._screen_h = 0
        self._detect_screen_size()

        # V2: Screenshot cache — 避免高频 fork screencap 进程
        self._screenshot_cache = None
        self._screenshot_cache_time = 0
        self._screenshot_cache_ttl = 0.5  # 500ms TTL

        # Sensor simulation config
        self._sensor_mode = "always_on"  # Default; overridden by config
        self._sensor_strategy = "none"   # Reported by 触控注入
        self._sensor_active = False

        # Stealth IME client for text input
        from .stealth_ime_client import StealthIMEClient
        self._ime_client = StealthIMEClient(serial=serial)
        
        # Enforce Stealth IME activation on driver init
        logger.info("Enforcing Stealth IME activation...")
        if not self._ime_client.ensure_ime_active():
            raise PreconditionError("🚨 CRITICAL: Failed to activate Stealth IME. Aborting to protect account safety.")

        # Mask battery state on startup, restore on exit
        self._mask_battery()
        atexit.register(self._restore_battery)
        atexit.register(self._cleanup_touch_injector)

    def _mask_battery(self):
        """Mask battery state to avoid 100% + USB plugged fingerprint."""
        try:
            fake_level = random.randint(45, 85)
            subprocess.run(self.adb_prefix + ["shell", "dumpsys", "battery", "unplug"], capture_output=True, timeout=3)
            subprocess.run(self.adb_prefix + ["shell", "dumpsys", "battery", "set", "level", str(fake_level)], capture_output=True, timeout=3)
            logger.info(f"Battery state spoofed to: unplugged, level {fake_level}%")
        except Exception as e:
            logger.warning(f"Failed to mask battery: {e}")

    def _restore_battery(self):
        """Restore physical battery state."""
        try:
            subprocess.run(self.adb_prefix + ["shell", "dumpsys", "battery", "reset"], capture_output=True, timeout=3)
            logger.info("Battery state restored.")
        except Exception as e:
            logger.warning(f"Failed to restore battery: {e}")

    def _check_connection(self):
        result = subprocess.run(self.adb_prefix + ["get-state"], capture_output=True, text=True)
        if "device" not in result.stdout:
            logger.error("Agentless Driver: ADB Device not connected or unauthorized.")
            raise RuntimeError("ADB Connection Failed")
        logger.info("Agentless Driver: ADB connection verified.")

    def _detect_screen_size(self):
        """Detect device screen resolution via ADB.
        
        Prioritizes 'Override size' over 'Physical size' because screenshots
        and OCR coordinates use the logical (override) resolution.
        """
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "wm", "size"],
                capture_output=True, text=True, timeout=5
            )
            output = result.stdout
            if "Override size:" in output:
                wh = output.split("Override size:")[1].strip().split('\n')[0].strip()
                w, h = wh.split("x")
                self._screen_w = int(w)
                self._screen_h = int(h)
                logger.info(f"Device screen size (override): {self._screen_w}x{self._screen_h}")
            elif "Physical size:" in output:
                wh = output.split("Physical size:")[1].strip().split('\n')[0].strip()
                w, h = wh.split("x")
                self._screen_w = int(w)
                self._screen_h = int(h)
                logger.info(f"Device screen size (physical): {self._screen_w}x{self._screen_h}")
        except Exception as e:
            logger.warning(f"Could not detect screen size: {e}")

    # ─────────── Touch Injector Lifecycle ───────────

    def ensure_minitouch(self):
        """
        Backward compatibility wrapper. Ensures 触控注入 is running.
        """
        return self._ensure_touch_injector()

    @property
    def sensor_status(self):
        """Return current sensor simulation status."""
        return {
            "mode": self._sensor_mode,
            "strategy": self._sensor_strategy,
            "active": self._sensor_active
        }

    def set_sensor_mode(self, mode: str):
        """Set sensor simulation mode. Takes effect on next 触控注入 restart."""
        if mode not in ("off", "coupled", "always_on"):
            raise ValueError(f"Invalid sensor mode: {mode}. Must be 'off', 'coupled', or 'always_on'")
        self._sensor_mode = mode
        logger.info(f"Sensor simulation mode set to: {mode}")

    def _push_touch_injector(self) -> bool:
        """Push the touch_injector.dex to device."""
        if not os.path.exists(_INJECTOR_DEX_PATH):
            logger.error(f"Injector dex not found locally at: {_INJECTOR_DEX_PATH}")
            return False

        logger.info("Pushing injector dex to device...")
        try:
            result = subprocess.run(
                self.adb_prefix + ["push", _INJECTOR_DEX_PATH, _INJECTOR_DEX_REMOTE],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                logger.error(f"ADB push failed: {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"Injector push failed: {e}")
            return False

    def _start_touch_injector(self) -> bool:
        """Start touch injector daemon on device via app_process and connect via TCP socket."""
        try:
            # Check if dex exists on device
            check_res = subprocess.run(
                self.adb_prefix + ["shell", "ls", _INJECTOR_DEX_REMOTE],
                capture_output=True, text=True, timeout=3
            )
            if "No such file" in check_res.stdout or "No such file" in check_res.stderr:
                if not self._push_touch_injector():
                    return False

            # Kill any existing injector process
            subprocess.run(
                self.adb_prefix + ["shell", "killall", "app_process"],
                capture_output=True, timeout=3
            )
            time.sleep(0.3)

            # V6: 设备端也使用随机端口
            device_port = random.randint(10000, 60000)
            self._touch_port = self._find_free_port()

            subprocess.run(
                self.adb_prefix + ["forward", f"tcp:{self._touch_port}", f"tcp:{device_port}"],
                capture_output=True, timeout=5
            )

            # V4: 使用伪装的类名和 dex 路径
            self._touch_process = subprocess.Popen(
                self.adb_prefix + [
                    "shell",
                    f"export CLASSPATH={_INJECTOR_DEX_REMOTE}; exec app_process /system/bin {_INJECTOR_CLASS_NAME} {self._screen_w} {self._screen_h} {device_port} {self._sensor_mode}"
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            time.sleep(1.5)

            if self._touch_process.poll() is not None:
                stderr = self._touch_process.stderr.read().decode("utf-8", errors="ignore")
                logger.warning(f"Injector exited immediately. stderr: {stderr[:200]}")
                return False

            self._touch_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._touch_socket.settimeout(3.0)
            self._touch_socket.connect(("127.0.0.1", self._touch_port))

            banner_data = b""
            for _ in range(10):
                try:
                    chunk = self._touch_socket.recv(4096)
                    if not chunk:
                        break
                    banner_data += chunk
                    if b"^" in banner_data and b"$" in banner_data:
                        break
                except socket.timeout:
                    break
                time.sleep(0.1)

            banner_text = banner_data.decode("utf-8", errors="ignore").strip()
            for line in banner_text.split("\n"):
                if line.startswith("^"):
                    parts = line.split()
                    if len(parts) >= 5:
                        self._touch_max_contacts = int(parts[1])
                        self._touch_max_x = int(parts[2])
                        self._touch_max_y = int(parts[3])
                        self._touch_max_pressure = int(parts[4])
                elif line.startswith("s "):
                    s_parts = line.split()
                    if len(s_parts) >= 4:
                        self._sensor_mode = s_parts[1]
                        self._sensor_strategy = s_parts[2]
                        self._sensor_active = (s_parts[3] == "active")

            if self._touch_max_x > 0 and self._touch_max_y > 0:
                self._touch_available = True
                logger.info(
                    f"Injector connected! max_x={self._touch_max_x}, "
                    f"max_y={self._touch_max_y}, max_pressure={self._touch_max_pressure}, "
                    f"sensor={self._sensor_strategy}({'active' if self._sensor_active else 'inactive'})"
                )
                
                # Enforce sensor simulation condition
                if self._sensor_mode != "off" and not self._sensor_active:
                    self._cleanup_touch_injector()
                    raise PreconditionError(
                        f"🚨 CRITICAL: Sensor simulation failed to activate (strategy={self._sensor_strategy}). "
                        f"This exposes automation to severe 'dead sensor' detection. Aborting."
                    )

                # V11: dex 已加载到内存, 立即删除文件避免残留指纹
                subprocess.run(
                    self.adb_prefix + ["shell", "rm", "-f", _INJECTOR_DEX_REMOTE],
                    capture_output=True, timeout=3
                )
                return True
            else:
                logger.warning(f"Injector banner parse failed: {banner_text}")
                self._cleanup_touch_injector()
                return False

        except Exception as e:
            logger.warning(f"Injector start failed: {e}")
            self._cleanup_touch_injector()
            return False

    def _cleanup_touch_injector(self):
        """Clean up touch injector resources."""
        try:
            if self._touch_socket:
                self._touch_socket.close()
                self._touch_socket = None
            if self._touch_process:
                self._touch_process.kill()
                self._touch_process = None
            if self._touch_port:
                subprocess.run(
                    self.adb_prefix + ["forward", "--remove", f"tcp:{self._touch_port}"],
                    capture_output=True, timeout=3
                )
        except Exception:
            pass
        finally:
            self._touch_available = False
        self._touch_available = False

    def _find_free_port(self) -> int:
        """Find a free TCP port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _touch_send(self, cmd: str):
        """Send a command to touch injector socket."""
        if not self._touch_socket:
            return
        try:
            self._touch_socket.send((cmd + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.warning(f"Injector socket error: {e}. Attempting recovery.")
            self._cleanup_touch_injector()  # Explicitly clean up to force restart
            self._ensure_touch_injector()
            try:
                self._touch_socket.send((cmd + "\n").encode())
            except Exception as e2:
                raise InjectorError(f"Failed to send touch command after recovery: {e2}")

    def _scale_coords(self, x, y):
        """Scale screen coordinates to injector coordinate space."""
        if self._screen_w > 0 and self._touch_max_x > 0:
            mt_x = int(x * self._touch_max_x / self._screen_w)
            mt_y = int(y * self._touch_max_y / self._screen_h)
            return mt_x, mt_y
        return int(x), int(y)

    # ─────────── Public API ───────────

    def is_app_installed(self, package_name: str) -> bool:
        cmd = self.adb_prefix + ["shell", "pm", "list", "packages", package_name]
        for _ in range(3):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return f"package:{package_name}" in result.stdout
                time.sleep(1.0)
            except subprocess.TimeoutExpired:
                time.sleep(1.0)
                
        if 'result' in locals() and hasattr(result, 'stderr') and result.stderr:
            logger.error(f"is_app_installed ADB Error: {result.stderr}")
        return False

    def is_screen_on(self) -> bool:
        cmd = self.adb_prefix + ["shell", "dumpsys", "power"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return "mWakefulness=Awake" in result.stdout or "mScreenOn=true" in result.stdout

    def _ensure_touch_injector(self):
        if self._touch_available and self._touch_socket:
            return True
        
        logger.info("尝试初始化或恢复触控注入器...")
        for _ in range(3):
            self._cleanup_touch_injector()
            if self._start_touch_injector():
                return True
            time.sleep(1.0)
            
        raise InjectorError("Failed to initialize or recover 触控注入 after 3 attempts.")

    def check_ready(self, package_name="com.xingin.xhs"):
        if not self.is_app_installed(package_name):
            raise PreconditionError(f"Precondition failed: App {package_name} is NOT installed.")
        if not self.is_screen_on():
            raise PreconditionError("Precondition failed: Device screen is OFF.")
        self._ensure_touch_injector()

    def screenshot(self, use_cache=True):
        """High-speed raw screenshot via adb exec-out into OpenCV format.
        
        V2: 内置 TTL 缓存层, 500ms 内的重复调用直接返回缓存,
        避免高频 fork screencap 进程暴露机刷指纹。
        """
        now = time.time()
        # V2: 检查缓存是否有效
        if use_cache and self._screenshot_cache is not None:
            if (now - self._screenshot_cache_time) < self._screenshot_cache_ttl:
                return self._screenshot_cache.copy()
        
        try:
            cmd = self.adb_prefix + ["exec-out", "screencap", "-p"]
            process = subprocess.run(cmd, capture_output=True, timeout=10)
            if process.returncode != 0:
                raise RuntimeError("Screencap failed")

            nparr = np.frombuffer(process.stdout, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError("Failed to decode screenshot")
            
            # V2: 更新缓存
            self._screenshot_cache = img
            self._screenshot_cache_time = now
            return img
        except Exception as e:
            logger.error("Screenshot failed", extra={"error": str(e)})
            raise

    def clean_screenshot(self):
        """
        Temporarily disable pointer_location and show_touches to take a clean screenshot,
        then restore their original state. Useful for template collection.
        V12: ADB 命令合并 — 减少 adb session 数量。
        """
        try:
            # V12: 合并两个 settings get 为一条命令
            result = subprocess.run(
                self.adb_prefix + ["shell", 
                    "settings get system pointer_location; settings get system show_touches"],
                capture_output=True, text=True, timeout=3
            )
            lines = result.stdout.strip().split('\n')
            pl_on = len(lines) > 0 and lines[0].strip() == "1"
            st_on = len(lines) > 1 and lines[1].strip() == "1"
            
            # V12: 合并禁用命令
            if pl_on or st_on:
                disable_cmds = []
                if pl_on:
                    disable_cmds.append("settings put system pointer_location 0")
                if st_on:
                    disable_cmds.append("settings put system show_touches 0")
                subprocess.run(
                    self.adb_prefix + ["shell", "; ".join(disable_cmds)],
                    capture_output=True, timeout=3
                )
                
            img = self.screenshot(use_cache=False)
            
            # V12: 合并恢复命令
            if pl_on or st_on:
                restore_cmds = []
                if pl_on:
                    restore_cmds.append("settings put system pointer_location 1")
                if st_on:
                    restore_cmds.append("settings put system show_touches 1")
                subprocess.run(
                    self.adb_prefix + ["shell", "; ".join(restore_cmds)],
                    capture_output=True, timeout=3
                )
                
            return img
        except Exception as e:
            logger.error("Clean screenshot failed, falling back to standard screenshot", extra={"error": str(e)})
            return self.screenshot()

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        if not self.is_app_installed(package_name):
            raise PreconditionError(f"Cannot launch {package_name}: App is not installed.")
        # Round 4: Use monkey instead of am start to avoid mLaunchSource=2 (Shell) fingerprint.
        # Monkey completes instantly, so ActivityManager.isUserAMonkey() returns false during actual app usage.
        logger.info(f"使用 monkey 隐蔽启动应用 {package_name}")
        cmd = self.adb_prefix + [
            "shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"
        ]
        subprocess.run(cmd, capture_output=True, timeout=10)
        self.human_sleep(5.0, 2.0)

    def human_sleep(self, mu=5.0, sigma=2.0):
        """V5: 对数正态分布 + 长尾停顿, 统计学上更接近真人操作间隔。"""
        # 对数正态分布：大部分操作快速, 少部分有长停顿
        delay = np.random.lognormal(np.log(mu), sigma * 0.3)
        # 5% 概率注意力漂移（切 App、看通知等）
        if random.random() < 0.05:
            delay += random.uniform(8, 35)
        # 1% 概率长中断（接电话、上厕所等）
        if random.random() < 0.01:
            delay += random.uniform(30, 90)
        time.sleep(max(0.5, delay))

    def physical_tap(self, x, y):
        """Physical tap with Fitts's law inspired noise. Uses 触控注入 exclusively."""
        # 采用二维高斯(正态)分布，sigma=5 则 99.7% 的点击落在 [-15, 15] 范围内
        nx = int(x + random.gauss(0, 5))
        ny = int(y + random.gauss(0, 5))

        self.check_ready()
        mt_x, mt_y = self._scale_coords(nx, ny)
        pressure = int(max(10, random.gauss(60, 10)))
        touch_duration = max(0.01, random.gauss(0.08, 0.02))

        logger.info(f"触控点击 ({nx}, {ny}) → mt({mt_x}, {mt_y})")
        self._touch_send(f"d 0 {mt_x} {mt_y} {pressure}")
        self._touch_send("c")
        time.sleep(touch_duration)
        self._touch_send("u 0")
        self._touch_send("c")

    def physical_double_tap(self, x, y):
        """Physical double tap. Uses 触控注入 exclusively. Extremely useful for liking posts."""
        # 采用二维高斯(正态)分布，sigma=5 则 99.7% 的点击落在 [-15, 15] 范围内
        nx = int(x + random.gauss(0, 5))
        ny = int(y + random.gauss(0, 5))

        self.check_ready()
        mt_x, mt_y = self._scale_coords(nx, ny)

        logger.info(f"触控双击 ({nx}, {ny}) → mt({mt_x}, {mt_y})")
        pressure1 = int(max(10, random.gauss(55, 10)))
        
        # First tap
        self._touch_send(f"d 0 {mt_x} {mt_y} {pressure1}")
        self._touch_send("c")
        time.sleep(random.uniform(0.03, 0.07))
        self._touch_send("u 0")
        self._touch_send("c")
        
        time.sleep(random.uniform(0.06, 0.12)) # Short delay between taps
        
        # V8: 第二击加微偏移 (模拟拇指回弹) 使用高斯分布
        mt_x2 = mt_x + int(random.gauss(0, 3))
        mt_y2 = mt_y + int(random.gauss(0, 3))
        pressure2 = pressure1 + int(random.gauss(0, 5))
        self._touch_send(f"d 0 {mt_x2} {mt_y2} {max(20, pressure2)}")
        self._touch_send("c")
        time.sleep(random.uniform(0.03, 0.07))
        self._touch_send("u 0")
        self._touch_send("c")

    def physical_swipe(self, sx, sy, ex, ey):
        """Cubic Bezier curve physical swipe with Ease-Out inertia. Uses 触控注入 exclusively."""
        self.check_ready()
        num_points = random.randint(25, 40)
        points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
        pressure = int(max(10, random.gauss(60, 10)))

        logger.info(f"贝塞尔曲线滑动: 从 ({sx},{sy}) 到 ({ex},{ey}), 共 {num_points} 个点")
        mt_x, mt_y = self._scale_coords(points[0][0], points[0][1])
        self._touch_send(f"d 0 {mt_x} {mt_y} {pressure}")
        self._touch_send("c")
        time.sleep(random.uniform(0.02, 0.05))

        for i, (px, py) in enumerate(points[1:]):
            mt_x, mt_y = self._scale_coords(px, py)
            self._touch_send(f"m 0 {mt_x} {mt_y} {pressure}")
            self._touch_send("c")
            progress = (i + 1) / (num_points - 1)
            sleep_time = 0.003 + (progress ** 3) * 0.035
            sleep_time += random.uniform(-0.001, 0.002)
            time.sleep(max(0.001, sleep_time))

        self._touch_send("u 0")
        self._touch_send("c")

    def _generate_cubic_bezier_curve(self, start_x, start_y, end_x, end_y, num_points=30):
        """Generate Cubic Bezier curve points simulating human thumb arc."""
        offset_x = random.uniform(20, 100) if random.random() > 0.5 else random.uniform(-100, -20)
        
        ctrl1_x = start_x + (end_x - start_x) * 0.3 + offset_x + random.uniform(-20, 20)
        ctrl1_y = start_y + (end_y - start_y) * 0.3 + random.uniform(-50, 50)
        
        ctrl2_x = start_x + (end_x - start_x) * 0.7 + offset_x * 0.5 + random.uniform(-20, 20)
        ctrl2_y = start_y + (end_y - start_y) * 0.7 + random.uniform(-50, 50)

        points = []
        for t in np.linspace(0, 1, num_points):
            x = (1 - t)**3 * start_x + 3 * (1 - t)**2 * t * ctrl1_x + 3 * (1 - t) * t**2 * ctrl2_x + t**3 * end_x
            y = (1 - t)**3 * start_y + 3 * (1 - t)**2 * t * ctrl1_y + 3 * (1 - t) * t**2 * ctrl2_y + t**3 * end_y
            jitter_x = random.gauss(0, 1)
            jitter_y = random.gauss(0, 1)
            points.append((int(x + jitter_x), int(y + jitter_y)))
        return points

    def micro_swipe(self, max_distance=40):
        """Micro swipe to simulate reading attention and keep connection alive."""
        w = self._screen_w or 540
        h = self._screen_h or 1170
        
        sx = w / 2 + random.uniform(-80, 80)
        sy = h * random.uniform(0.4, 0.6)
        
        direction = 1 if random.random() > 0.4 else -1
        distance = random.uniform(15, max_distance) * direction
        
        ex = sx + random.uniform(-10, 10)
        ey = sy - distance
        
        logger.info(f"微小滑动 (注意力模拟): 距离={distance:.1f}px")
        
        self.check_ready()
        num_points = random.randint(8, 15)
        points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
        pressure = int(max(10, random.gauss(45, 10)))

        mt_x, mt_y = self._scale_coords(points[0][0], points[0][1])
        self._touch_send(f"d 0 {mt_x} {mt_y} {pressure}")
        self._touch_send("c")
        time.sleep(random.uniform(0.01, 0.03))

        for px, py in points[1:]:
            mt_x, mt_y = self._scale_coords(px, py)
            self._touch_send(f"m 0 {mt_x} {mt_y} {pressure}")
            self._touch_send("c")
            time.sleep(random.uniform(0.015, 0.035))

        self._touch_send("u 0")
        self._touch_send("c")

    def inject_keyevent(self, keycode: int):
        """Inject a KeyEvent through the 触控注入 socket tunnel.
        
        This replaces 'adb shell input keyevent' which exposes a deviceId=-1
        synthetic flag detectable by risk control SDKs. The injected KeyEvent
        uses InputDevice.SOURCE_KEYBOARD through InputManager, making it
        indistinguishable from real hardware key presses.
        
        Args:
            keycode: Android KeyEvent keycode (e.g. 4=BACK, 66=ENTER, 3=HOME)
        """
        self._ensure_touch_injector()
        logger.info(f"触控 keyevent: {keycode}")
        self._touch_send(f"k {keycode}")

    def human_swipe(self, direction="down"):
        w = self._screen_w or 540
        h = self._screen_h or 1170

        if direction == "down" and random.random() < 0.10:
            logger.info("犹豫回滑 (向上滚动)...")
            self.physical_swipe(w / 2, h * 0.3, w / 2, h * 0.7)
            self.human_sleep(2.0, 1.0)
            return

        sx = w / 2 + random.uniform(-60, 60)
        sy = h * random.uniform(0.7, 0.85) if direction == "down" else h * random.uniform(0.15, 0.3)
        ex = w / 2 + random.uniform(-60, 60)
        ey = h * random.uniform(0.15, 0.3) if direction == "down" else h * random.uniform(0.7, 0.85)

        logger.info(f"真人滑动: {direction}")
        self.physical_swipe(sx, sy, ex, ey)
        self.human_sleep(2.0, 1.0)

    def press_back(self):
        """Press back key via 触控注入 (KEYCODE_BACK=4)."""
        logger.info("Agentless Back key event (via 触控注入)")
        self.inject_keyevent(4)
        self.human_sleep(1.0, 0.5)

    def get_screen_size(self):
        """Return cached screen size."""
        return self._screen_w, self._screen_h

    # ─────────── Text Input (via Stealth IME) ───────────

    @property
    def ime_client(self):
        """Access the underlying StealthIMEClient for advanced operations."""
        return self._ime_client

    def type_text(self, text: str, human_like: bool = True):
        """
        Type text using the Stealth IME.

        Args:
            text: Text to type (supports Chinese and any Unicode)
            human_like: If True, type character by character with random delays.
                       If False, commit the entire text at once.
        """
        if human_like:
            self._ime_client.type_text(text)
        else:
            self._ime_client.type_text_fast(text)

    def clear_input(self):
        """Clear the current input field via Stealth IME."""
        self._ime_client.clear_text()

    # ─────────── YOLO & Anti-Detection Safe Clicks ───────────

    def yolo_detect(self, class_name: str, screen_image: np.ndarray = None, conf_threshold: float = 0.5):
        """
        Runs YOLO object detection on the screen to find a specific class.
        
        Args:
            class_name: The name of the class to detect (e.g., 'send_btn', 'input_area').
            screen_image: Optional image. If None, uses cached screenshot.
            conf_threshold: Minimum confidence to consider a match.
            
        Returns:
            Tuple of (bounding_box, confidence) where bounding_box is (x1, y1, x2, y2).
            Returns (None, 0.0) if not found.
        """
        if self._yolo_model is None:
            logger.error("YOLO model is not initialized. Pass yolo_model_path to AgentlessMinitouchDriver.")
            return None, 0.0
            
        img = screen_image if screen_image is not None else self.screenshot()
        if img is None:
            return None, 0.0
            
        # Run inference
        results = self._yolo_model(img, verbose=False)
        if not results:
            return None, 0.0
            
        best_box = None
        best_conf = 0.0
        
        for result in results:
            boxes = result.boxes
            for box in boxes:
                cls_idx = int(box.cls[0].item())
                detected_class = result.names[cls_idx]
                conf = box.conf[0].item()
                
                if detected_class == class_name and conf >= conf_threshold:
                    if conf > best_conf:
                        best_conf = conf
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        best_box = (int(x1), int(y1), int(x2), int(y2))
                        
        if best_box:
            logger.info(f"YOLO detected '{class_name}' at {best_box} with conf {best_conf:.2f}")
        else:
            logger.debug(f"YOLO could not detect '{class_name}' above conf {conf_threshold}")
            
        return best_box, best_conf

    def safe_click_yolo(self, class_name: str, fallback_anchor_class: str = None, offset_x: int = 0, offset_y: int = 0):
        """
        Anti-detection safe click using YOLO. 
        Resistant to A/B testing visual changes, gracefully falls back to anchor points, 
        and inherently uses Gaussian tap for risk control.
        
        Returns True if clicked successfully, False if manual intervention is needed.
        """
        img = self.screenshot()
        target_box = None
        
        # 1. Try exact YOLO detection for the primary target
        box, conf = self.yolo_detect(class_name, screen_image=img, conf_threshold=0.6)
        if box:
            target_box = box
            logger.info(f"safe_click: YOLO primary target '{class_name}' found.")
            
        # 2. Try anchor fallback if primary target not found
        elif fallback_anchor_class:
            anchor_box, anchor_conf = self.yolo_detect(fallback_anchor_class, screen_image=img, conf_threshold=0.6)
            if anchor_box:
                # Calculate estimated area based on anchor center + offsets
                ax1, ay1, ax2, ay2 = anchor_box
                cx = (ax1 + ax2) // 2
                cy = (ay1 + ay2) // 2
                
                est_cx = cx + offset_x
                est_cy = cy + offset_y
                # Assume a fixed size box for the estimated target (e.g., 60x60)
                target_box = (est_cx - 30, est_cy - 30, est_cx + 30, est_cy + 30)
                logger.info(f"safe_click: Primary failed. Using anchor '{fallback_anchor_class}' with offset. Est target: {target_box}")
                
        # 3. Fused Failsafe (Risk Control Trigger)
        if not target_box:
            logger.warning(f"CRITICAL: Visual link broken for '{class_name}'. A/B test or major UI change suspected. Action aborted to prevent risk control flags.")
            return False
            
        # 4. Anti-detection Execution (Gaussian Tap inside target box)
        x1, y1, x2, y2 = target_box
        # Use center with slight randomness bounded by the box
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        
        # We rely on physical_tap's built-in Fitts's law/Gaussian distribution, 
        # but we pass the base center coordinates.
        self.physical_tap(center_x, center_y)
        return True

    def __del__(self):
        """Cleanup touch injector on driver destruction."""
        self._cleanup_touch_injector()
