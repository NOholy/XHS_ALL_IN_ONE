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
        """Detect real device screen resolution via ADB."""
        try:
            result = subprocess.run(
                self.adb_prefix + ["shell", "wm", "size"],
                capture_output=True, text=True, timeout=5
            )
            if "Physical size:" in result.stdout:
                wh = result.stdout.split("Physical size:")[1].strip()
                w, h = wh.split("x")
                self._screen_w = int(w)
                self._screen_h = int(h)
                logger.info(f"Device screen size: {self._screen_w}x{self._screen_h}")
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

    def physical_swipe(self, sx, sy, ex, ey):
        """Bezier curve physical swipe. Uses minitouch if available."""
        if self._mt_available:
            num_points = random.randint(18, 28)
            points = self._generate_bezier_curve(sx, sy, ex, ey, num_points)
            pressure = random.randint(40, 80)

            logger.info(f"Minitouch Bezier swipe from ({sx},{sy}) to ({ex},{ey}), {num_points} points")
            mt_x, mt_y = self._scale_coords(points[0][0], points[0][1])
            self._mt_send(f"d 0 {mt_x} {mt_y} {pressure}")
            self._mt_send("c")
            time.sleep(random.uniform(0.02, 0.05))

            for i, (px, py) in enumerate(points[1:]):
                mt_x, mt_y = self._scale_coords(px, py)
                self._mt_send(f"m 0 {mt_x} {mt_y} {pressure}")
                self._mt_send("c")
                # Non-linear velocity: slow at start/end, fast in middle
                if i < num_points * 0.2 or i > num_points * 0.8:
                    time.sleep(random.uniform(0.012, 0.022))
                else:
                    time.sleep(random.uniform(0.004, 0.009))

            self._mt_send("u 0")
            self._mt_send("c")
        else:
            duration = random.randint(400, 800)
            logger.info(f"ADB fallback swipe from ({sx},{sy}) to ({ex},{ey})")
            cmd = self.adb_prefix + ["shell", "input", "swipe",
                                     str(int(sx)), str(int(sy)), str(int(ex)), str(int(ey)), str(duration)]
            subprocess.run(cmd)

    def _generate_bezier_curve(self, start_x, start_y, end_x, end_y, num_points=20):
        """Generate quadratic Bezier curve points with random control point."""
        ctrl_x = start_x + (end_x - start_x) / 2 + random.uniform(-150, 150)
        ctrl_y = start_y + (end_y - start_y) / 2 + random.uniform(-150, 150)

        points = []
        for t in np.linspace(0, 1, num_points):
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y
            points.append((int(x), int(y)))
        return points

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
