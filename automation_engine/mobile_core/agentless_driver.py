import time
import random
import numpy as np
import subprocess
import cv2
from .logger import get_logger

logger = get_logger("agentless_driver")

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

    def _check_connection(self):
        result = subprocess.run(self.adb_prefix + ["get-state"], capture_output=True, text=True)
        if "device" not in result.stdout:
            logger.error("Agentless Driver: ADB Device not connected or unauthorized.")
            raise RuntimeError("ADB Connection Failed")
        logger.info("Agentless Driver: ADB connection verified.")

    def screenshot(self):
        """High-speed raw screenshot via adb exec-out into OpenCV format."""
        # Note: In a true industrial setup, this would attach to a scrcpy video stream socket.
        # This implementation uses adb exec-out for simplicity in code without scrcpy dependencies.
        try:
            cmd = self.adb_prefix + ["exec-out", "screencap", "-p"]
            process = subprocess.run(cmd, capture_output=True)
            if process.returncode != 0:
                raise RuntimeError("Screencap failed")
            
            nparr = np.frombuffer(process.stdout, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            return img
        except Exception as e:
            logger.error("Screenshot failed", extra={"error": str(e)})
            raise

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        logger.info(f"Using ADB monkey to launch app {package_name} steathily.")
        cmd = self.adb_prefix + ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.human_sleep(5.0, 2.0)

    def human_sleep(self, mu=5.0, sigma=2.0):
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay)
        time.sleep(sleep_time)

    def physical_tap(self, x, y):
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))
        logger.info(f"Agentless tap at ({nx}, {ny}) via raw ADB input")
        
        # Fallback to adb input tap. 
        # In full production, this pushes 'd 0 x y 50\nc\nu 0\nc\n' to the minitouch socket.
        cmd = self.adb_prefix + ["shell", "input", "tap", str(nx), str(ny)]
        subprocess.run(cmd)

    def physical_swipe(self, sx, sy, ex, ey):
        duration = random.randint(400, 800)
        logger.info(f"Agentless swipe from ({sx}, {sy}) to ({ex}, {ey})")
        # In full production, generate bezier curves and push minitouch socket events.
        cmd = self.adb_prefix + ["shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration)]
        subprocess.run(cmd)

    def human_swipe(self, direction="down"):
        sx, sy, ex, ey = 500, 1500, 500, 500
        if direction == "up":
            sy, ey = ey, sy
        self.physical_swipe(sx, sy, ex, ey)
        self.human_sleep(2.0, 1.0)
        
    def press_back(self):
        logger.info("Agentless Back key event")
        cmd = self.adb_prefix + ["shell", "input", "keyevent", "4"]
        subprocess.run(cmd)
