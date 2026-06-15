"""
Phase 3: Agentless Driver — Industrial Grade
Replaces minitouch/uiautomator2 with app_process TouchInjector.
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
from .logger import get_logger

class TouchInjectorError(Exception):
    pass

class PreconditionError(Exception):
    pass

logger = get_logger("agentless_driver")

# Touch injector prebuilt directory (relative to project root)
_INJECTOR_DEX_PATH = os.path.join(
    os.path.dirname(__file__), "injector", "touch_injector.dex"
)

class AgentlessMinitouchDriver:
    """
    Phase 3: Agentless Driver.
    Uses 'app_process' TouchInjector via ADB port forwarding for high-speed, 
    undetectable touch emulation, and 'adb exec-out screencap' for vision.
    """

    def __init__(self, serial=None):
        self.serial = serial
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]
        logger.info("Initializing Agentless Driver...", extra={"serial": serial})
        self._check_connection()

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

        # Sensor simulation config
        self._sensor_mode = "always_on"  # Default; overridden by config
        self._sensor_strategy = "none"   # Reported by TouchInjector
        self._sensor_active = False

        # Stealth IME client for text input
        from .stealth_ime_client import StealthIMEClient
        self._ime_client = StealthIMEClient(serial=serial)

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
        Backward compatibility wrapper. Ensures TouchInjector is running.
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
        """Set sensor simulation mode. Takes effect on next TouchInjector restart."""
        if mode not in ("off", "coupled", "always_on"):
            raise ValueError(f"Invalid sensor mode: {mode}. Must be 'off', 'coupled', or 'always_on'")
        self._sensor_mode = mode
        logger.info(f"Sensor simulation mode set to: {mode}")

    def _push_touch_injector(self) -> bool:
        """Push the touch_injector.dex to device."""
        if not os.path.exists(_INJECTOR_DEX_PATH):
            logger.error(f"TouchInjector dex not found locally at: {_INJECTOR_DEX_PATH}")
            return False

        logger.info("Pushing TouchInjector dex to device...")
        try:
            result = subprocess.run(
                self.adb_prefix + ["push", _INJECTOR_DEX_PATH, "/data/local/tmp/touch_injector.dex"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                logger.error(f"ADB push failed: {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"TouchInjector push failed: {e}")
            return False

    def _start_touch_injector(self) -> bool:
        """Start touch injector daemon on device via app_process and connect via TCP socket."""
        try:
            # Check if dex exists on device
            check_res = subprocess.run(
                self.adb_prefix + ["shell", "ls", "/data/local/tmp/touch_injector.dex"],
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

            self._touch_port = self._find_free_port()

            subprocess.run(
                self.adb_prefix + ["forward", f"tcp:{self._touch_port}", "tcp:1111"],
                capture_output=True, timeout=5
            )

            # Start Java daemon in background
            cmd = self.adb_prefix + [
                "shell", 
                f"export CLASSPATH=/data/local/tmp/touch_injector.dex; exec app_process /system/bin TouchInjector {self._screen_w} {self._screen_h} 1111 {self._sensor_mode}"
            ]
            self._touch_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            time.sleep(1.5)

            if self._touch_process.poll() is not None:
                stderr = self._touch_process.stderr.read().decode("utf-8", errors="ignore")
                logger.warning(f"TouchInjector exited immediately. stderr: {stderr[:200]}")
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
                    f"TouchInjector connected! max_x={self._touch_max_x}, "
                    f"max_y={self._touch_max_y}, max_pressure={self._touch_max_pressure}, "
                    f"sensor={self._sensor_strategy}({'active' if self._sensor_active else 'inactive'})"
                )
                return True
            else:
                logger.warning(f"TouchInjector banner parse failed: {banner_text}")
                self._cleanup_touch_injector()
                return False

        except Exception as e:
            logger.warning(f"TouchInjector start failed: {e}")
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
            logger.warning(f"TouchInjector socket error: {e}. Attempting recovery.")
            self._ensure_touch_injector()
            try:
                self._touch_socket.send((cmd + "\n").encode())
            except Exception as e2:
                raise TouchInjectorError(f"Failed to send touch command after recovery: {e2}")

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return f"package:{package_name}" in result.stdout

    def is_screen_on(self) -> bool:
        cmd = self.adb_prefix + ["shell", "dumpsys", "power"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return "mWakefulness=Awake" in result.stdout or "mScreenOn=true" in result.stdout

    def _ensure_touch_injector(self):
        if self._touch_available and self._touch_socket:
            return True
        
        logger.info("Attempting to initialize/recover TouchInjector...")
        for _ in range(3):
            self._cleanup_touch_injector()
            if self._start_touch_injector():
                return True
            time.sleep(1.0)
            
        raise TouchInjectorError("Failed to initialize or recover TouchInjector after 3 attempts.")

    def check_ready(self, package_name="com.xingin.xhs"):
        if not self.is_app_installed(package_name):
            raise PreconditionError(f"Precondition failed: App {package_name} is NOT installed.")
        if not self.is_screen_on():
            raise PreconditionError("Precondition failed: Device screen is OFF.")
        self._ensure_touch_injector()

    def screenshot(self):
        """High-speed raw screenshot via adb exec-out into OpenCV format."""
        try:
            cmd = self.adb_prefix + ["exec-out", "screencap", "-p"]
            process = subprocess.run(cmd, capture_output=True, timeout=10)
            if process.returncode != 0:
                raise RuntimeError("Screencap failed")

            nparr = np.frombuffer(process.stdout, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError("Failed to decode screenshot")
            return img
        except Exception as e:
            logger.error("Screenshot failed", extra={"error": str(e)})
            raise

    def clean_screenshot(self):
        """
        Temporarily disable pointer_location and show_touches to take a clean screenshot,
        then restore their original state. Useful for template collection.
        """
        try:
            pl_res = subprocess.run(self.adb_prefix + ["shell", "settings", "get", "system", "pointer_location"], capture_output=True, text=True, timeout=3)
            st_res = subprocess.run(self.adb_prefix + ["shell", "settings", "get", "system", "show_touches"], capture_output=True, text=True, timeout=3)
            pl_orig = pl_res.stdout.strip()
            st_orig = st_res.stdout.strip()
            
            pl_on = (pl_orig == "1")
            st_on = (st_orig == "1")
            
            if pl_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "pointer_location", "0"], timeout=3)
            if st_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "show_touches", "0"], timeout=3)
                
            img = self.screenshot()
            
            if pl_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "pointer_location", "1"], timeout=3)
            if st_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "show_touches", "1"], timeout=3)
                
            return img
        except Exception as e:
            logger.error("Clean screenshot failed, falling back to standard screenshot", extra={"error": str(e)})
            return self.screenshot()

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        if not self.is_app_installed(package_name):
            raise PreconditionError(f"Cannot launch {package_name}: App is not installed.")
        logger.info(f"Using ADB monkey to launch app {package_name} stealthily.")
        cmd = self.adb_prefix + ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.human_sleep(5.0, 2.0)

    def human_sleep(self, mu=5.0, sigma=2.0):
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay)
        time.sleep(sleep_time)

    def physical_tap(self, x, y):
        """Physical tap with Fitts's law inspired noise. Uses TouchInjector exclusively."""
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))

        self.check_ready()
        mt_x, mt_y = self._scale_coords(nx, ny)
        pressure = random.randint(40, 80)
        touch_duration = random.uniform(0.04, 0.12)

        logger.info(f"TouchInjector tap at ({nx}, {ny}) → mt({mt_x}, {mt_y})")
        self._touch_send(f"d 0 {mt_x} {mt_y} {pressure}")
        self._touch_send("c")
        time.sleep(touch_duration)
        self._touch_send("u 0")
        self._touch_send("c")

    def physical_double_tap(self, x, y):
        """Physical double tap. Uses TouchInjector exclusively. Extremely useful for liking posts."""
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))

        self.check_ready()
        mt_x, mt_y = self._scale_coords(nx, ny)

        logger.info(f"TouchInjector double tap at ({nx}, {ny}) → mt({mt_x}, {mt_y})")
        
        # First tap
        self._touch_send(f"d 0 {mt_x} {mt_y} 50")
        self._touch_send("c")
        time.sleep(0.05)
        self._touch_send("u 0")
        self._touch_send("c")
        
        time.sleep(0.08) # Short delay between taps
        
        # Second tap
        self._touch_send(f"d 0 {mt_x} {mt_y} 60")
        self._touch_send("c")
        time.sleep(0.05)
        self._touch_send("u 0")
        self._touch_send("c")

    def physical_swipe(self, sx, sy, ex, ey):
        """Cubic Bezier curve physical swipe with Ease-Out inertia. Uses TouchInjector exclusively."""
        self.check_ready()
        num_points = random.randint(25, 40)
        points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
        pressure = random.randint(40, 80)

        logger.info(f"TouchInjector Cubic Bezier swipe from ({sx},{sy}) to ({ex},{ey}), {num_points} points")
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
            jitter_x = random.uniform(-2, 2)
            jitter_y = random.uniform(-2, 2)
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
        
        logger.info(f"Micro swipe (attention simulation): dist={distance:.1f}px")
        
        self.check_ready()
        num_points = random.randint(8, 15)
        points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
        pressure = random.randint(30, 60)

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

    def human_swipe(self, direction="down"):
        w = self._screen_w or 540
        h = self._screen_h or 1170

        if direction == "down" and random.random() < 0.10:
            logger.info("Hesitation swipe (scrolling back up)...")
            self.physical_swipe(w / 2, h * 0.3, w / 2, h * 0.7)
            self.human_sleep(2.0, 1.0)
            return

        sx = w / 2 + random.uniform(-60, 60)
        sy = h * random.uniform(0.7, 0.85) if direction == "down" else h * random.uniform(0.15, 0.3)
        ex = w / 2 + random.uniform(-60, 60)
        ey = h * random.uniform(0.15, 0.3) if direction == "down" else h * random.uniform(0.7, 0.85)

        logger.info(f"Human swipe {direction}")
        self.physical_swipe(sx, sy, ex, ey)
        self.human_sleep(2.0, 1.0)

    def press_back(self):
        logger.info("Agentless Back key event")
        cmd = self.adb_prefix + ["shell", "input", "keyevent", "4"]
        subprocess.run(cmd)
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

    def __del__(self):
        """Cleanup touch injector on driver destruction."""
        self._cleanup_touch_injector()
