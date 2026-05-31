import time
import random
import numpy as np
import sys
from .logger import get_logger

logger = get_logger("device_driver")

try:
    import uiautomator2 as u2
    _U2_AVAILABLE = True
except ImportError:
    _U2_AVAILABLE = False
    logger.warning("uiautomator2 not installed. DeviceDriver unavailable (use AgentlessMinitouchDriver instead).")

class DeviceDriver:
    """
    Abstracted physical interaction layer.
    Currently wraps uiautomator2, but designed to be replaceable by Scrcpy/Minitouch in Phase 3.
    """
    def __init__(self, serial=None):
        if not _U2_AVAILABLE:
            raise ImportError("uiautomator2 is not installed. Run `pip install uiautomator2` or use AgentlessMinitouchDriver.")
        self.serial = serial
        try:
            logger.info("Connecting to device...", extra={"device_serial": serial})
            self.d = u2.connect(serial)
            self.d.implicitly_wait(10.0)
            logger.info("Successfully connected to Android device.")
        except Exception as e:
            logger.error("Could not connect to device", extra={"error": str(e)})
            raise

    def screenshot(self):
        """Returns screenshot as numpy array (OpenCV format BGR)."""
        return self.d.screenshot(format='opencv')

    def ensure_app_foreground(self, package_name="com.xingin.xhs"):
        current_app = self.d.app_current()
        if current_app['package'] != package_name:
            logger.info(f"App {package_name} not in foreground. Launching...", extra={"current_app": current_app['package']})
            self.d.app_start(package_name)
            self.human_sleep(5.0, 2.0)
        else:
            logger.info(f"App {package_name} is in foreground.")

    def human_sleep(self, mu=5.0, sigma=2.0):
        """Gaussian distributed sleep."""
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay)
        logger.info(f"Human sleep for {sleep_time:.2f}s...")
        time.sleep(sleep_time)

    def _generate_bezier_curve(self, start_x, start_y, end_x, end_y, num_points=20):
        ctrl_x = start_x + (end_x - start_x) / 2 + random.uniform(-150, 150)
        ctrl_y = start_y + (end_y - start_y) / 2 + random.uniform(-150, 150)
        
        points = []
        for t in np.linspace(0, 1, num_points):
            x = (1 - t)**2 * start_x + 2 * (1 - t) * t * ctrl_x + t**2 * end_x
            y = (1 - t)**2 * start_y + 2 * (1 - t) * t * ctrl_y + t**2 * end_y
            points.append((int(x), int(y)))
        return points

    def physical_swipe(self, sx, sy, ex, ey):
        """Bezier curve physical swipe with non-uniform velocity."""
        num_points = random.randint(18, 28)
        points = self._generate_bezier_curve(sx, sy, ex, ey, num_points)
        
        self.d.touch.down(points[0][0], points[0][1])
        time.sleep(random.uniform(0.02, 0.05))
        
        for i, (x, y) in enumerate(points[1:]):
            self.d.touch.move(x, y)
            if i < num_points * 0.2 or i > num_points * 0.8:
                time.sleep(random.uniform(0.015, 0.025))
            else:
                time.sleep(random.uniform(0.005, 0.01))
                
        self.d.touch.up(points[-1][0], points[-1][1])

    def human_swipe(self, direction="down"):
        w, h = self.d.window_size()
        
        if direction == "down" and random.random() < 0.10:
            logger.info("Hesitation swipe (scrolling back up)...")
            self.physical_swipe(w/2, h*0.3, w/2, h*0.7)
            self.human_sleep(2.0, 1.0)
            return

        sx = w / 2 + random.uniform(-60, 60)
        sy = h * random.uniform(0.7, 0.85) if direction == "down" else h * random.uniform(0.15, 0.3)
        ex = w / 2 + random.uniform(-60, 60)
        ey = h * random.uniform(0.15, 0.3) if direction == "down" else h * random.uniform(0.7, 0.85)
        
        logger.info("Executing physical Bezier swipe trajectory...")
        self.physical_swipe(sx, sy, ex, ey)
        self.human_sleep(2.0, 1.0)

    def physical_tap(self, x, y):
        """Physical tap with Fitts's law inspired noise."""
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))
        logger.info(f"Physical tap at ({nx}, {ny})")
        self.d.touch.down(nx, ny)
        time.sleep(random.uniform(0.04, 0.12))
        self.d.touch.up(nx, ny)

    def press_back(self):
        logger.info("Pressing physical back button.")
        self.d.press("back")
        self.human_sleep(1.0, 0.5)
