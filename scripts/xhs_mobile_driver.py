"""
XHS Mobile Driver - The Android UI Automation Adapter.
Uses uiautomator2 for UI tree traversal and ADB commands, with OpenCV fallback.
"""
import time
import random
import argparse
import json
import sys
import cv2
import numpy as np
import requests
import math
import os
import logging
from pypinyin import pinyin, Style
from paddleocr import PaddleOCR

# Suppress debug logs from paddleocr
logging.getLogger("ppocr").setLevel(logging.ERROR)

try:
    import uiautomator2 as u2
except ImportError:
    print("[-] FATAL: uiautomator2 is not installed. Run `pip install uiautomator2`")
    sys.exit(1)

def parse_args():
    parser = argparse.ArgumentParser(description="XHS Android Automation Driver")
    parser.add_argument("--device", type=str, help="ADB Serial or IP address of the device")
    parser.add_argument("--action", required=True, choices=["scan", "extract", "reply", "farm"], help="The action to perform")
    parser.add_argument("--typing-mode", choices=["clipboard", "opencv"], default="clipboard", help="How to input text")
    parser.add_argument("--x", type=int, help="X coordinate for clicking (required for extract and reply)")
    parser.add_argument("--y", type=int, help="Y coordinate for clicking (required for extract and reply)")
    parser.add_argument("--text", type=str, help="Text to type (required for reply)")
    parser.add_argument("--live", action="store_true", help="If set, actually clicks the send button.")
    parser.add_argument("--close", action="store_true", help="If set, clicks Android back button to close post.")
    
    return parser.parse_args()

class XHSMobileDriver:
    def __init__(self, device_serial, typing_mode="clipboard"):
        self.typing_mode = typing_mode
        try:
            print(f"[*] Driver: Connecting to device {device_serial or 'default'}...")
            self.d = u2.connect(device_serial)
            self.d.implicitly_wait(10.0)
            print("[+] Driver: Successfully connected to Android device.")
            print("[*] Driver: Initializing PaddleOCR Engine...")
            self.ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
        except Exception as e:
            print(f"[-] FATAL: Could not connect to device: {e}")
            sys.exit(1)

    def _ensure_app_foreground(self):
        """Ensure Xiaohongshu is the active application."""
        current_app = self.d.app_current()
        if current_app['package'] != 'com.xingin.xhs':
            print(f"[*] Driver: XHS is not in foreground (Current: {current_app['package']}). Launching XHS...")
            self.d.app_start('com.xingin.xhs')
            self._human_sleep(5.0, 2.0)
            print("[*] Driver: Waiting for app to settle...")
            self._human_sleep(3.0, 1.0)
        else:
            print("[+] Driver: XHS is already in foreground.")

    def _validate_templates(self, required_templates):
        """Verify that required OpenCV templates exist before starting a task."""
        template_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates")
        missing = []
        for t in required_templates:
            if not os.path.exists(os.path.join(template_dir, f"{t}.png")):
                missing.append(t)
                
        if missing:
            print(f"[-] FATAL PRECONDITION: Missing required UI templates: {missing}")
            print(f"    Please run `python scripts/auto_crop_templates.py` or manually place them in {template_dir}")
            sys.exit(1)
        print("[+] Precondition check passed: All required UI templates found.")

    def _human_sleep(self, mu=5.0, sigma=2.0):
        """Simulates human pause with Gaussian distribution (avg 5s, stddev 2s)"""
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay) # Ensure at least 1.5s
        print(f"[*] Driver: Human sleep for {sleep_time:.2f}s...")
        time.sleep(sleep_time)

    def _generate_bezier_curve(self, start_x, start_y, end_x, end_y, num_points=20):
        """Generates a 2nd order Bezier curve path for human-like swiping."""
        # Random control point to create an arc
        ctrl_x = start_x + (end_x - start_x) / 2 + random.uniform(-150, 150)
        ctrl_y = start_y + (end_y - start_y) / 2 + random.uniform(-150, 150)
        
        points = []
        for t in np.linspace(0, 1, num_points):
            x = (1 - t)**2 * start_x + 2 * (1 - t) * t * ctrl_x + t**2 * end_x
            y = (1 - t)**2 * start_y + 2 * (1 - t) * t * ctrl_y + t**2 * end_y
            points.append((int(x), int(y)))
        return points

    def _physical_swipe(self, sx, sy, ex, ey):
        """Injects a physical Bezier curve swipe via touch down/move/up events."""
        num_points = random.randint(18, 28)
        points = self._generate_bezier_curve(sx, sy, ex, ey, num_points)
        
        # Start touch
        self.d.touch.down(points[0][0], points[0][1])
        time.sleep(random.uniform(0.02, 0.05))
        
        # Move along curve with non-uniform delays
        for i, (x, y) in enumerate(points[1:]):
            self.d.touch.move(x, y)
            # Simulate human speed: slow at ends, fast in middle
            if i < num_points * 0.2 or i > num_points * 0.8:
                time.sleep(random.uniform(0.015, 0.025))
            else:
                time.sleep(random.uniform(0.005, 0.01))
                
        # Release touch
        self.d.touch.up(points[-1][0], points[-1][1])

    def _human_swipe(self, direction="down"):
        w, h = self.d.window_size()
        
        # 10% chance to swipe back up slightly (hesitation/re-reading)
        if direction == "down" and random.random() < 0.10:
            print("[*] Driver: Hesitation swipe (scrolling back up)...")
            self._physical_swipe(w/2, h*0.3, w/2, h*0.7)
            self._human_sleep(2.0, 1.0)
            return

        # Add horizontal random offset to start and end points
        sx = w / 2 + random.uniform(-60, 60)
        sy = h * random.uniform(0.7, 0.85) if direction == "down" else h * random.uniform(0.15, 0.3)
        ex = w / 2 + random.uniform(-60, 60)
        ey = h * random.uniform(0.15, 0.3) if direction == "down" else h * random.uniform(0.7, 0.85)
        
        print("[*] Driver: Executing physical Bezier swipe trajectory...")
        self._physical_swipe(sx, sy, ex, ey)
        self._human_sleep(2.0, 1.0)

    def _human_type(self, text):
        if self.typing_mode == "clipboard":
            print("[*] Driver: Injecting text via clipboard to bypass input monitoring...")
            self.d.set_clipboard(text)
            self._human_sleep(1.0, 0.5)
            # Send keys relies on U2's native input/IME, which is safer than raw ADB text but not physical.
            self.d.send_keys(text, clear=True)
            self._human_sleep(1.5, 0.5)
        elif self.typing_mode == "opencv":
            self._opencv_physical_typing(text)

    def _opencv_physical_typing(self, text):
        """Uses OpenCV to find soft keyboard keys and physical tap them letter by letter."""
        print(f"[*] Driver: OpenCV physical typing mode for text: '{text}'")
        
        # 1. Convert Chinese to Pinyin without tones
        py_list = pinyin(text, style=Style.NORMAL)
        keys_to_type = "".join([item[0] for item in py_list]).lower()
        print(f"[*] Driver: Translated to Pinyin keys: '{keys_to_type}'")
        
        # 2. Capture screen
        print("[*] Driver: Capturing screen for OpenCV template matching...")
        screen_img = self.d.screenshot(format='opencv')
        gray_screen = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        
        template_dir = os.path.join(os.path.dirname(__file__), "..", "data", "keyboard")
        os.makedirs(template_dir, exist_ok=True)
        
        for char in keys_to_type:
            if not char.isalpha():
                continue # Skip non-letters for now
                
            template_path = os.path.join(template_dir, f"{char}.png")
            if not os.path.exists(template_path):
                print(f"[-] Warning: OpenCV Template for '{char}' not found at {template_path}. Please crop and save the key image.")
                continue
                
            template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
            if template is None: continue
                
            h, w = template.shape
            res = cv2.matchTemplate(gray_screen, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            
            if max_val > 0.75: # Confidence threshold
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                print(f"[*] OpenCV: Found '{char}' at ({center_x}, {center_y}) with confidence {max_val:.2f}")
                
                # Physical click on the key
                self._click_with_noise(center_x, center_y)
                
                # Sleep simulating human typing speed
                time.sleep(random.uniform(0.15, 0.45))
            else:
                print(f"[-] Warning: Could not locate '{char}' on screen. (Confidence: {max_val:.2f})")
                
        # Send/Space/Select candidate logic would be added here depending on the specific input method
        self._human_sleep(1.0, 0.5)

    def _click_with_noise(self, x, y):
        # Add slight random offset to prevent exact coordinate detection
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))
        print(f"[*] Driver: Physical tap at ({nx}, {ny}) with noise offset")
        # Use down -> sleep -> up to simulate genuine physical tap duration
        self.d.touch.down(nx, ny)
        time.sleep(random.uniform(0.04, 0.12))
        self.d.touch.up(nx, ny)

    def _find_template(self, template_name, threshold=0.75, screen_img=None):
        """Find a UI element by OpenCV template matching, returning (x, y) or None."""
        template_path = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", f"{template_name}.png")
        if not os.path.exists(template_path):
            print(f"[-] Warning: UI Template '{template_name}.png' missing. Please provide it for CV-based interaction.")
            return None
            
        if screen_img is None:
            screen_img = self.d.screenshot(format='opencv')
            
        gray_screen = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        
        if template is None: return None
        h, w = template.shape
        
        res = cv2.matchTemplate(gray_screen, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
        if max_val >= threshold:
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            print(f"[*] CV Found: '{template_name}' at ({center_x}, {center_y}) | Conf: {max_val:.2f}")
            return (center_x, center_y)
        return None

    def _check_risk_and_notify(self):
        # Take ONE screenshot to check both
        img = self.d.screenshot(format='opencv')
        slider = self._find_template("slider_puzzle", threshold=0.8, screen_img=img)
        verify = self._find_template("security_verification", threshold=0.8, screen_img=img)
        
        if slider or verify:
            print("[-] CRITICAL: 🚨 Risk control triggered (Slider/Verification)! Pausing script.")
            # Infinite loop until human resolves the slider
            while True:
                time.sleep(10)
                img = self.d.screenshot(format='opencv')
                if not self._find_template("slider_puzzle", threshold=0.8, screen_img=img) and \
                   not self._find_template("security_verification", threshold=0.8, screen_img=img):
                    break
                print("[-] Waiting for manual human intervention to pass the slider...")
                
            print("[+] Manual intervention complete. Resuming script.")
            self._human_sleep(3.0, 1.0)

    def action_scan(self):
        self._ensure_app_foreground()
        print("[*] Driver: Scanning for posts on feed...")
        self._human_swipe("down")
        
        posts = []
        # Since we stripped UI Tree dumping for safety, we rely on a fixed dual-column grid heuristic.
        # XHS uses a standard waterfall layout. We map out 4 generic visible card centers.
        w, h = self.d.window_size()
        
        posts = [
            {"id": 0, "title": "CV_Post_TopLeft", "x": int(w * 0.25), "y": int(h * 0.35)},
            {"id": 1, "title": "CV_Post_TopRight", "x": int(w * 0.75), "y": int(h * 0.35)},
            {"id": 2, "title": "CV_Post_BotLeft", "x": int(w * 0.25), "y": int(h * 0.75)},
            {"id": 3, "title": "CV_Post_BotRight", "x": int(w * 0.75), "y": int(h * 0.75)}
        ]
        
        print("\n--- VISIBLE POSTS (GRID-BASED) ---")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("----------------------------\n")
        print("[*] Scan complete.")

    def action_extract(self, target_x, target_y):
        self._ensure_app_foreground()
        self._validate_templates(["reply_button"])
        self._check_risk_and_notify()
        print(f"[*] Driver: Tapping post at ({target_x}, {target_y})...")
        self._click_with_noise(target_x, target_y)
        self._human_sleep(4.0, 1.0) # Wait for detail page
        
        print("[*] Driver: Extracting post text via OCR...")
        screen_img = self.d.screenshot(format='opencv')
        ocr_res = self.ocr.ocr(screen_img, cls=False)
        
        post_desc = []
        if ocr_res and ocr_res[0]:
            for line in ocr_res[0]:
                box, (text, conf) = line
                if conf > 0.6:
                    post_desc.append(text)
            
        print("[*] Driver: Scrolling to load comments...")
        self._human_swipe("down")
        self._human_swipe("down")
        self._human_sleep(2.0, 1.0)
        
        print("[*] Driver: Extracting comments via OCR...")
        screen_img2 = self.d.screenshot(format='opencv')
        ocr_res2 = self.ocr.ocr(screen_img2, cls=False)
        
        comments = []
        reply_btn = self._find_template("reply_button", threshold=0.8, screen_img=screen_img2)
        
        if ocr_res2 and ocr_res2[0]:
            for i, line in enumerate(ocr_res2[0]):
                box, (text, conf) = line
                if conf > 0.6 and len(text) > 2 and "回复" not in text:
                    comments.append({
                        "id": i,
                        "author": "OCR_User",
                        "content": text,
                        "reply_x": reply_btn[0] if reply_btn else 0,
                        "reply_y": reply_btn[1] if reply_btn else 0
                    })

        print("\n--- POST DESCRIPTION ---")
        print("\n".join(post_desc))
        print("------------------------\n")
        
        print("\n--- COMMENTS (JSON) ---")
        print(json.dumps(comments, ensure_ascii=False, indent=2))
        print("-----------------------\n")
        print("[*] Extract complete.")

    def action_reply(self, x, y, text, live_mode, should_close):
        self._ensure_app_foreground()
        if live_mode:
            self._validate_templates(["send_button"])
            
        if self.typing_mode == "opencv":
            kb_dir = os.path.join(os.path.dirname(__file__), "..", "data", "keyboard")
            if not os.path.exists(kb_dir) or len(os.listdir(kb_dir)) < 5:
                print("[-] Warning: keyboard templates directory seems empty or incomplete. OpenCV typing may fail.")
                
        self._check_risk_and_notify()
        print(f"[*] Driver: Replying at ({x}, {y})")
        self._click_with_noise(x, y)
        self._human_sleep(2.0, 1.0)
            
        print(f"[*] Driver: Typing text: {text}")
        self._human_type(text)
        
        if live_mode:
            print("[!] LIVE MODE: Clicking '发送' (Send)...")
            send_btn = self._find_template("send_button", threshold=0.75)
            if send_btn:
                self._click_with_noise(send_btn[0], send_btn[1])
                print("[*] Driver: Waiting 4s for network response...")
                time.sleep(4)
                
                # Check success via OCR
                print("[*] Driver: Verifying comment via OCR...")
                verify_img = self.d.screenshot(format='opencv')
                verify_res = self.ocr.ocr(verify_img, cls=False)
                success = False
                if verify_res and verify_res[0]:
                    for line in verify_res[0]:
                        if text[:4] in line[1][0]: # Check if first 4 chars match
                            success = True
                            break
                            
                if success:
                    print("[+] Comment verified as successfully posted in UI!")
                else:
                    print("[-] Warning: Comment not found via OCR. May be shadowbanned or network failed.")
                
                print("[*] Driver: Entering mandatory cooldown after commenting to prevent high-frequency risk.")
                self._human_sleep(90.0, 30.0) # 1~2 minutes delay after a real comment
            else:
                print("[-] FATAL: Could not find 'send_button.png' visually in App. Aborting.")
                sys.exit(1)
        else:
            print("[!] DRY RUN: Cancelling comment...")
            # Click outside or hit back to dismiss keyboard/comment
            self.d.press("back")
            self._human_sleep(1.0, 0.5)
            self.d.press("back")

        if should_close:
            print("[*] Driver: Closing overlay (Pressing Back)...")
            self.d.press("back")
            self._human_sleep(1.5, 0.5)
        print("[*] Reply complete.")

    def action_farm(self):
        """
        Layer 2: Funnel Model (Farming & Dilution Loop)
        Implements the 100:30:10:2 probability funnel to build account trust.
        """
        print("=========================================")
        print("[*] 🌾 Starting Autonomous Farming Funnel (Layer 2)")
        print("=========================================")
        
        self._ensure_app_foreground()
        self._check_risk_and_notify()
        
        # Target: Scroll 50 times in this session
        scroll_target = 50
        print(f"[*] Driver: Will perform {scroll_target} swipes in the feed.")
        
        for i in range(scroll_target):
            print(f"\n--- 🌾 Farming Step {i+1}/{scroll_target} ---")
            self._human_swipe("down")
            self._human_sleep(4.0, 2.0) # Pause to look at covers
            
            # 30% chance to click into a post
            if random.random() < 0.30:
                print("[*] Funnel: 30% chance triggered! Clicking into a post...")
                
                # Pick a random post card via generic coordinate grid (waterfall layout)
                w, h = self.d.window_size()
                tx = random.choice([int(w * 0.25), int(w * 0.75)])
                ty = random.randint(int(h * 0.3), int(h * 0.8))
                
                self._click_with_noise(tx, ty)
                    
                print("[*] Driver: Pretending to read the post...")
                self._human_sleep(10.0, 4.0) # Long read
                
                # 30% chance * 10% chance = 3% overall chance to open comments
                if random.random() < 0.33: 
                    print("[*] Funnel: Opening comments area...")
                    self._human_swipe("down")
                    self._human_sleep(5.0, 2.0)
                    
                    # Note: We do NOT randomly comment here. Commenting (Layer 3) 
                    # is explicitly controlled by the AI brain via action_reply. 
                    # This farming mode is purely for building account trust score.
                
                print("[*] Driver: Exiting post...")
                self.d.press("back")
                self._human_sleep(1.5, 0.5)
            
            # Check for captchas periodically
            if i % 10 == 0:
                self._check_risk_and_notify()

        print("[*] 🌾 Farming session complete.")

def main():
    args = parse_args()
    print("=========================================")
    print(f"XHS Mobile Driver executing action: {args.action}")
    print("=========================================")
    
    driver = XHSMobileDriver(args.device, typing_mode=args.typing_mode)
    
    if args.action == "scan":
        driver.action_scan()
    elif args.action == "farm":
        driver.action_farm()
    elif args.action == "extract":
        if args.x is None or args.y is None:
            print("[-] Error: --x and --y required for extract")
            return
        driver.action_extract(args.x, args.y)
    elif args.action == "reply":
        if args.x is None or args.y is None or not args.text:
            print("[-] Error: --x, --y, and --text required for reply")
            return
        driver.action_reply(args.x, args.y, args.text, args.live, args.close)

if __name__ == "__main__":
    main()
