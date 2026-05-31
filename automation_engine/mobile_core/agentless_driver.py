"""
Phase 3: Agentless Driver — Industrial Grade
Replaces uiautomator2 entirely. Leaves ZERO test agents on the Android device.
Uses 'minitouch' for high-speed, undetectable touch emulation,
and 'adb exec-out screencap' for vision.
"""
import time
import random
import socket
import struct
import numpy as np
import subprocess
import cv2
import os
from .logger import get_logger

logger = get_logger("agentless_driver")

# minitouch prebuilt directory (relative to project root)
_MINITOUCH_PREBUILT_BASE = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "node_modules", "minitouch-prebuilt", "prebuilt"
)

# ABI 映射：Android getprop → minitouch 预编译目录名
_ABI_MAP = {
    "arm64-v8a": "arm64-v8a",
    "armeabi-v7a": "armeabi-v7a",
    "armeabi": "armeabi",
    "x86_64": "x86_64",
    "x86": "x86",
    "mips64": "mips64",
    "mips": "mips",
}


class AgentlessMinitouchDriver:
    """
    Phase 3: Agentless Driver.
    Replaces uiautomator2 entirely. Leaves ZERO test agents on the Android device.
    Uses 'minitouch' via ADB port forwarding for high-speed, undetectable touch emulation,
    and 'adb exec-out screencap' (or scrcpy frame buffer) for vision.
    """

    def __init__(self, serial=None):
        self.serial = serial
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]
        logger.info("Initializing Agentless Driver...", extra={"serial": serial})
        self._check_connection()

        # minitouch state
        self._mt_process = None
        self._mt_socket = None
        self._mt_max_x = 0
        self._mt_max_y = 0
        self._mt_max_contacts = 0
        self._mt_max_pressure = 0
        self._mt_port = 0
        self._mt_available = False

        # Screen size cache
        self._screen_w = 0
        self._screen_h = 0
        self._detect_screen_size()

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
            # Prefer Override size (matches screenshot/OCR coordinate space)
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

    # ─────────── Minitouch Lifecycle ───────────

    def ensure_minitouch(self):
        """
        Detect device CPU ABI, check if minitouch exists on device,
        push matching binary if needed, and start minitouch daemon.
        Returns True if minitouch is ready, False if fallback needed.
        """
        if self._mt_available:
            return True

        try:
            # 1. Detect device ABI
            abi = self._get_device_abi()
            if not abi:
                logger.warning("Could not detect device ABI. Minitouch unavailable.")
                return False

            # 2. Check if minitouch already exists on device
            if not self._check_minitouch_on_device():
                # 3. Push matching binary
                if not self._push_minitouch(abi):
                    return False

            # 4. Start minitouch daemon and connect socket
            return self._start_minitouch()

        except Exception as e:
            logger.warning(f"Minitouch setup failed, using ADB input fallback: {e}")
            return False

    def _get_device_abi(self) -> str:
        """Get device CPU ABI for selecting correct minitouch binary."""
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "getprop", "ro.product.cpu.abi"],
                capture_output=True, text=True, timeout=5
            )
            abi = result.stdout.strip()
            if abi in _ABI_MAP:
                logger.info(f"Device ABI detected: {abi}")
                return abi

            # Fallback: try abilist and pick first supported
            result2 = subprocess.run(
                self.adb_prefix + ["shell", "getprop", "ro.product.cpu.abilist"],
                capture_output=True, text=True, timeout=5
            )
            for candidate in result2.stdout.strip().split(","):
                candidate = candidate.strip()
                if candidate in _ABI_MAP:
                    logger.info(f"Device ABI from abilist: {candidate}")
                    return candidate

            logger.error(f"Unsupported ABI: {abi}")
            return ""
        except Exception as e:
            logger.error(f"ABI detection failed: {e}")
            return ""

    def _check_minitouch_on_device(self) -> bool:
        """Check if minitouch binary already exists and is executable on device."""
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "ls", "-l", "/data/local/tmp/minitouch"],
                capture_output=True, text=True, timeout=5
            )
            if "No such file" in result.stdout or "No such file" in result.stderr:
                return False
            # Check it's executable (has 'x' in permissions)
            if result.stdout.startswith("-") and "x" in result.stdout[:10]:
                logger.info("Minitouch binary already present and executable on device.")
                return True
            # Exists but not executable — fix permissions
            subprocess.run(
                self.adb_prefix + ["shell", "chmod", "777", "/data/local/tmp/minitouch"],
                timeout=5
            )
            logger.info("Minitouch binary found, permissions fixed.")
            return True
        except Exception:
            return False

    def _push_minitouch(self, abi: str) -> bool:
        """Push the correct minitouch binary to device based on ABI."""
        mapped_dir = _ABI_MAP.get(abi, abi)
        local_path = os.path.join(_MINITOUCH_PREBUILT_BASE, mapped_dir, "bin", "minitouch")

        if not os.path.exists(local_path):
            logger.error(
                f"Minitouch binary not found locally for ABI '{abi}' at: {local_path}. "
                f"Run 'npm install minitouch-prebuilt' in project root."
            )
            return False

        logger.info(f"Pushing minitouch binary for ABI '{abi}' to device...")
        try:
            result = subprocess.run(
                self.adb_prefix + ["push", local_path, "/data/local/tmp/minitouch"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.error(f"ADB push failed: {result.stderr}")
                return False

            subprocess.run(
                self.adb_prefix + ["shell", "chmod", "777", "/data/local/tmp/minitouch"],
                timeout=5
            )
            logger.info("Minitouch binary pushed and permissions set.")
            return True
        except Exception as e:
            logger.error(f"Minitouch push failed: {e}")
            return False

    def _start_minitouch(self) -> bool:
        """Start minitouch daemon on device and connect via TCP socket."""
        try:
            # Kill any existing minitouch process
            subprocess.run(
                self.adb_prefix + ["shell", "killall", "minitouch"],
                capture_output=True, timeout=3
            )
            time.sleep(0.3)

            # Find a free local port
            self._mt_port = self._find_free_port()

            # Set up ADB forward
            subprocess.run(
                self.adb_prefix + ["forward", f"tcp:{self._mt_port}", "localabstract:minitouch"],
                capture_output=True, timeout=5
            )

            # Start minitouch in background
            self._mt_process = subprocess.Popen(
                self.adb_prefix + ["shell", "/data/local/tmp/minitouch"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait for minitouch to start up (varies by device speed)
            time.sleep(1.5)

            # Check if process is still alive
            if self._mt_process.poll() is not None:
                stderr = self._mt_process.stderr.read().decode("utf-8", errors="ignore")
                logger.warning(f"Minitouch exited immediately. stderr: {stderr[:200]}")
                return False

            # Connect socket
            self._mt_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._mt_socket.settimeout(3.0)
            self._mt_socket.connect(("127.0.0.1", self._mt_port))

            # Read minitouch banner lines
            # Banner format:
            #   v <version>
            #   ^ <max_contacts> <max_x> <max_y> <max_pressure>
            #   $ <pid>
            banner_data = b""
            for _ in range(10):  # max 10 read attempts
                try:
                    chunk = self._mt_socket.recv(4096)
                    if not chunk:
                        break
                    banner_data += chunk
                    # We need at least the ^ line
                    if b"^" in banner_data and b"$" in banner_data:
                        break
                except socket.timeout:
                    break
                time.sleep(0.1)

            # Parse banner: "^ <max_contacts> <max_x> <max_y> <max_pressure>"
            banner_text = banner_data.decode("utf-8", errors="ignore").strip()
            for line in banner_text.split("\n"):
                if line.startswith("^"):
                    parts = line.split()
                    if len(parts) >= 5:
                        self._mt_max_contacts = int(parts[1])
                        self._mt_max_x = int(parts[2])
                        self._mt_max_y = int(parts[3])
                        self._mt_max_pressure = int(parts[4])

            if self._mt_max_x > 0 and self._mt_max_y > 0:
                self._mt_available = True
                logger.info(
                    f"Minitouch connected! max_x={self._mt_max_x}, "
                    f"max_y={self._mt_max_y}, max_pressure={self._mt_max_pressure}"
                )
                return True
            else:
                logger.warning(f"Minitouch banner parse failed: {banner_text}")
                self._cleanup_minitouch()
                return False

        except Exception as e:
            logger.warning(f"Minitouch start failed: {e}")
            self._cleanup_minitouch()
            return False

    def _cleanup_minitouch(self):
        """Clean up minitouch resources."""
        try:
            if self._mt_socket:
                self._mt_socket.close()
                self._mt_socket = None
            if self._mt_process:
                self._mt_process.kill()
                self._mt_process = None
            if self._mt_port:
                subprocess.run(
                    self.adb_prefix + ["forward", "--remove", f"tcp:{self._mt_port}"],
                    capture_output=True, timeout=3
                )
        except Exception:
            pass
        self._mt_available = False

    def _find_free_port(self) -> int:
        """Find a free TCP port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _mt_send(self, cmd: str):
        """Send a command to minitouch socket."""
        if self._mt_socket:
            self._mt_socket.send((cmd + "\n").encode())

    def _scale_coords(self, x, y):
        """Scale screen coordinates to minitouch coordinate space."""
        if self._screen_w > 0 and self._mt_max_x > 0:
            mt_x = int(x * self._mt_max_x / self._screen_w)
            mt_y = int(y * self._mt_max_y / self._screen_h)
            return mt_x, mt_y
        return int(x), int(y)

    # ─────────── Public API ───────────

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
            # Save current state
            pl_res = subprocess.run(self.adb_prefix + ["shell", "settings", "get", "system", "pointer_location"], capture_output=True, text=True, timeout=3)
            st_res = subprocess.run(self.adb_prefix + ["shell", "settings", "get", "system", "show_touches"], capture_output=True, text=True, timeout=3)
            pl_orig = pl_res.stdout.strip()
            st_orig = st_res.stdout.strip()
            
            pl_on = (pl_orig == "1")
            st_on = (st_orig == "1")
            
            # Disable if needed
            if pl_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "pointer_location", "0"], timeout=3)
            if st_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "show_touches", "0"], timeout=3)
                
            # Take screenshot
            img = self.screenshot()
            
            # Restore state
            if pl_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "pointer_location", "1"], timeout=3)
            if st_on:
                subprocess.run(self.adb_prefix + ["shell", "settings", "put", "system", "show_touches", "1"], timeout=3)
                
            return img
        except Exception as e:
            logger.error("Clean screenshot failed, falling back to standard screenshot", extra={"error": str(e)})
            return self.screenshot()

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        logger.info(f"Using ADB monkey to launch app {package_name} stealthily.")
        cmd = self.adb_prefix + ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.human_sleep(5.0, 2.0)

    def human_sleep(self, mu=5.0, sigma=2.0):
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay)
        time.sleep(sleep_time)

    def physical_tap(self, x, y):
        """Physical tap with Fitts's law inspired noise. Uses minitouch if available."""
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))

        if self._mt_available:
            mt_x, mt_y = self._scale_coords(nx, ny)
            pressure = random.randint(40, 80)
            touch_duration = random.uniform(0.04, 0.12)

            logger.info(f"Minitouch tap at ({nx}, {ny}) → mt({mt_x}, {mt_y})")
            self._mt_send(f"d 0 {mt_x} {mt_y} {pressure}")
            self._mt_send("c")
            time.sleep(touch_duration)
            self._mt_send("u 0")
            self._mt_send("c")
        else:
            logger.info(f"ADB fallback tap at ({nx}, {ny})")
            cmd = self.adb_prefix + ["shell", "input", "tap", str(nx), str(ny)]
            subprocess.run(cmd)

    def physical_double_tap(self, x, y):
        """Physical double tap. Uses minitouch if available. Extremely useful for liking posts."""
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))

        if self._mt_available:
            mt_x, mt_y = self._scale_coords(nx, ny)
            logger.info(f"Minitouch double tap at ({nx}, {ny})")
            
            # First tap
            self._mt_send(f"d 0 {mt_x} {mt_y} {random.randint(40, 80)}")
            self._mt_send("c")
            time.sleep(random.uniform(0.04, 0.08))
            self._mt_send("u 0")
            self._mt_send("c")
            
            # Interval
            time.sleep(random.uniform(0.05, 0.12))
            
            # Second tap
            self._mt_send(f"d 0 {mt_x} {mt_y} {random.randint(40, 80)}")
            self._mt_send("c")
            time.sleep(random.uniform(0.04, 0.08))
            self._mt_send("u 0")
            self._mt_send("c")
        else:
            logger.info(f"ADB fallback double tap at ({nx}, {ny})")
            subprocess.run(self.adb_prefix + ["shell", "input", "tap", str(nx), str(ny)])
            time.sleep(random.uniform(0.05, 0.12))
            subprocess.run(self.adb_prefix + ["shell", "input", "tap", str(nx), str(ny)])

    def physical_swipe(self, sx, sy, ex, ey):
        """Cubic Bezier curve physical swipe with Ease-Out inertia."""
        if self._mt_available:
            num_points = random.randint(25, 40) # 更多的点使得滑动更细腻
            points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
            pressure = random.randint(40, 80)

            logger.info(f"Minitouch Cubic Bezier swipe from ({sx},{sy}) to ({ex},{ey}), {num_points} points")
            mt_x, mt_y = self._scale_coords(points[0][0], points[0][1])
            self._mt_send(f"d 0 {mt_x} {mt_y} {pressure}")
            self._mt_send("c")
            time.sleep(random.uniform(0.02, 0.05))

            # Total swipe duration ~ 200-400ms depending on speed
            # Using Ease-Out algorithm: starts fast (short sleep), ends slow (longer sleep)
            for i, (px, py) in enumerate(points[1:]):
                mt_x, mt_y = self._scale_coords(px, py)
                self._mt_send(f"m 0 {mt_x} {mt_y} {pressure}")
                self._mt_send("c")
                
                # Calculate progress [0, 1]
                progress = (i + 1) / (num_points - 1)
                # Ease-Out cubic inverse for time sleep (closer to 1 = longer sleep)
                # 初始速度极快(sleep小)，末端因为摩擦力极慢(sleep大)
                sleep_time = 0.003 + (progress ** 3) * 0.035
                # 加入极微小的网络抖动
                sleep_time += random.uniform(-0.001, 0.002)
                time.sleep(max(0.001, sleep_time))

            self._mt_send("u 0")
            self._mt_send("c")
        else:
            duration = random.randint(400, 800)
            logger.info(f"ADB fallback swipe from ({sx},{sy}) to ({ex},{ey})")
            cmd = self.adb_prefix + ["shell", "input", "swipe",
                                     str(int(sx)), str(int(sy)), str(int(ex)), str(int(ey)), str(duration)]
            subprocess.run(cmd)

    def _generate_cubic_bezier_curve(self, start_x, start_y, end_x, end_y, num_points=30):
        """Generate Cubic Bezier curve points simulating human thumb arc."""
        # 模拟大拇指滑动时的弧线，添加两个控制点
        # 偏右手的拇指滑动通常会向左或向右微凸
        offset_x = random.uniform(20, 100) if random.random() > 0.5 else random.uniform(-100, -20)
        
        ctrl1_x = start_x + (end_x - start_x) * 0.3 + offset_x + random.uniform(-20, 20)
        ctrl1_y = start_y + (end_y - start_y) * 0.3 + random.uniform(-50, 50)
        
        ctrl2_x = start_x + (end_x - start_x) * 0.7 + offset_x * 0.5 + random.uniform(-20, 20)
        ctrl2_y = start_y + (end_y - start_y) * 0.7 + random.uniform(-50, 50)

        points = []
        for t in np.linspace(0, 1, num_points):
            x = (1 - t)**3 * start_x + 3 * (1 - t)**2 * t * ctrl1_x + 3 * (1 - t) * t**2 * ctrl2_x + t**3 * end_x
            y = (1 - t)**3 * start_y + 3 * (1 - t)**2 * t * ctrl1_y + 3 * (1 - t) * t**2 * ctrl2_y + t**3 * end_y
            # 引入极细微的高频抖动 (Jitter)
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
        
        # 随机决定是向上看还是向下看 (位移小)
        direction = 1 if random.random() > 0.4 else -1
        distance = random.uniform(15, max_distance) * direction
        
        ex = sx + random.uniform(-10, 10)
        ey = sy - distance # 负数是向上移，也就是向下看
        
        logger.info(f"Micro swipe (attention simulation): dist={distance:.1f}px")
        
        if self._mt_available:
            num_points = random.randint(8, 15)
            points = self._generate_cubic_bezier_curve(sx, sy, ex, ey, num_points)
            pressure = random.randint(30, 60)

            mt_x, mt_y = self._scale_coords(points[0][0], points[0][1])
            self._mt_send(f"d 0 {mt_x} {mt_y} {pressure}")
            self._mt_send("c")
            time.sleep(random.uniform(0.01, 0.03))

            for px, py in points[1:]:
                mt_x, mt_y = self._scale_coords(px, py)
                self._mt_send(f"m 0 {mt_x} {mt_y} {pressure}")
                self._mt_send("c")
                # 匀速缓慢微划
                time.sleep(random.uniform(0.015, 0.035))

            self._mt_send("u 0")
            self._mt_send("c")
        else:
            duration = random.randint(200, 400)
            cmd = self.adb_prefix + ["shell", "input", "swipe",
                                     str(int(sx)), str(int(sy)), str(int(ex)), str(int(ey)), str(duration)]
            subprocess.run(cmd)

    def human_swipe(self, direction="down"):
        w = self._screen_w or 540
        h = self._screen_h or 1170

        # 10% chance hesitation swipe (scroll back)
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

    def __del__(self):
        """Cleanup minitouch on driver destruction."""
        self._cleanup_minitouch()
