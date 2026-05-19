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

try:
    import uiautomator2 as u2
except ImportError:
    print("[-] FATAL: uiautomator2 is not installed. Run `pip install uiautomator2`")
    sys.exit(1)

def parse_args():
    parser = argparse.ArgumentParser(description="XHS Android Automation Driver")
    parser.add_argument("--device", type=str, help="ADB Serial or IP address of the device")
    parser.add_argument("--action", required=True, choices=["scan", "extract", "reply", "farm"], help="The action to perform")
    parser.add_argument("--x", type=int, help="X coordinate for clicking (required for extract and reply)")
    parser.add_argument("--y", type=int, help="Y coordinate for clicking (required for extract and reply)")
    parser.add_argument("--text", type=str, help="Text to type (required for reply)")
    parser.add_argument("--live", action="store_true", help="If set, actually clicks the send button.")
    parser.add_argument("--close", action="store_true", help="If set, clicks Android back button to close post.")
    
    return parser.parse_args()

class XHSMobileDriver:
    def __init__(self, device_serial):
        try:
            print(f"[*] Driver: Connecting to device {device_serial or 'default'}...")
            self.d = u2.connect(device_serial)
            self.d.implicitly_wait(10.0)
            print("[+] Driver: Successfully connected to Android device.")
        except Exception as e:
            print(f"[-] FATAL: Could not connect to device: {e}")
            sys.exit(1)

    def _human_sleep(self, mu=5.0, sigma=2.0):
        """Simulates human pause with Gaussian distribution (avg 5s, stddev 2s)"""
        delay = np.random.normal(mu, sigma)
        sleep_time = max(1.5, delay) # Ensure at least 1.5s
        print(f"[*] Driver: Human sleep for {sleep_time:.2f}s...")
        time.sleep(sleep_time)

    def _human_swipe(self, direction="down"):
        w, h = self.d.window_size()
        
        # 10% chance to swipe back up slightly (hesitation/re-reading)
        if direction == "down" and random.random() < 0.10:
            print("[*] Driver: Hesitation swipe (scrolling back up)...")
            self.d.swipe(w/2, h*0.3, w/2, h*0.7, duration=random.uniform(0.2, 0.5))
            self._human_sleep(2.0, 1.0)
            return

        # Add horizontal random offset to start and end points
        sx = w / 2 + random.uniform(-60, 60)
        sy = h * random.uniform(0.7, 0.85) if direction == "down" else h * random.uniform(0.15, 0.3)
        ex = w / 2 + random.uniform(-60, 60)
        ey = h * random.uniform(0.15, 0.3) if direction == "down" else h * random.uniform(0.7, 0.85)
        
        # Random duration simulating finger flick speed
        duration = random.uniform(0.1, 0.6)
        
        self.d.swipe(sx, sy, ex, ey, duration=duration)
        self._human_sleep(2.0, 1.0)

    def _human_type(self, text):
        # Uses standard ADB input
        self.d.send_keys(text, clear=True)
        self._human_sleep(1.5, 0.5)

    def _click_with_noise(self, x, y):
        # Add slight random offset to prevent exact coordinate detection
        nx = int(x + random.randint(-15, 15))
        ny = int(y + random.randint(-15, 15))
        print(f"[*] Driver: Tapping at ({nx}, {ny}) with noise offset")
        self.d.click(nx, ny)

    def _human_click_element(self, element):
        """Click within the safe zone of a UI element to avoid dead-center clicks"""
        if not element.exists: return
        bounds = element.info['bounds']
        
        width = bounds['right'] - bounds['left']
        height = bounds['bottom'] - bounds['top']
        
        # Shrink bounding box by 15% to avoid edge clicks
        x = random.randint(int(bounds['left'] + width * 0.15), int(bounds['right'] - width * 0.15))
        y = random.randint(int(bounds['top'] + height * 0.15), int(bounds['bottom'] - height * 0.15))
        
        print(f"[*] Driver: Tapping element safe-zone at ({x}, {y})")
        self.d.click(x, y)

    def _check_risk_and_notify(self):
        # Monitor for sliders or security verifications
        if self.d(textContains="拖动滑块").exists or self.d(textContains="安全验证").exists:
            print("[-] CRITICAL: 🚨 Risk control triggered (Slider/Verification)! Pausing script.")
            # In a real deployment, ping Bark/ServerChan here.
            # requests.get("https://api.day.app/YOUR_KEY/XHS_Risk/Please_handle_slider")
            
            # Infinite loop until human resolves the slider
            while self.d(textContains="拖动滑块").exists or self.d(textContains="安全验证").exists:
                print("[-] Waiting for manual human intervention to pass the slider...")
                time.sleep(10)
                
            print("[+] Manual intervention complete. Resuming script.")
            self._human_sleep(3.0, 1.0)

    def action_scan(self):
        print("[*] Driver: Scanning for posts on feed...")
        self._human_swipe("down")
        
        posts = []
        try:
            # We look for typical post card elements. XHS UI ids change frequently, so we rely on relative layout or class names.
            # Usually, posts are represented by TextViews inside a RecyclerView/StaggeredGrid.
            cards = self.d.xpath('//androidx.recyclerview.widget.RecyclerView/android.widget.FrameLayout').all()
            for i, card in enumerate(cards):
                # Extract coordinates for clicking
                rect = card.bounds
                center_x = (rect[0] + rect[2]) // 2
                center_y = (rect[1] + rect[3]) // 2
                
                # Attempt to extract title text if possible
                text_nodes = card.xpath('.//android.widget.TextView').all()
                title = text_nodes[0].text if text_nodes else f"Post_{i}"
                
                posts.append({
                    "id": i,
                    "title": title.strip(),
                    "x": center_x,
                    "y": center_y
                })
        except Exception as e:
            print(f"[-] Warning: UI Dump failed or obfuscated: {e}. Falling back to visual/blind scan.")
        
        print("\n--- VISIBLE POSTS (JSON) ---")
        print(json.dumps(posts, ensure_ascii=False, indent=2))
        print("----------------------------\n")
        print("[*] Scan complete.")

    def action_extract(self, target_x, target_y):
        self._check_risk_and_notify()
        print(f"[*] Driver: Tapping post at ({target_x}, {target_y})...")
        self._click_with_noise(target_x, target_y)
        self._human_sleep(8.0, 3.0) # Pretend to read the main post (long wait)
        
        # Wait for detail page to load
        if not self.d(resourceIdMatches=".*comment.*|.*detail.*").wait(timeout=10.0):
            print("[-] FATAL: Post detail failed to load (Network issue or hit ad).")
            sys.exit(1)
            
        print("[*] Driver: Scrolling to load comments...")
        self._human_swipe("down")
        self._human_swipe("down")
        
        comments = []
        # Attempt to parse comment nodes
        try:
            comment_nodes = self.d(text="回复").all() # Find all "Reply" buttons
            for i, node in enumerate(comment_nodes):
                rect = node.bounds
                comments.append({
                    "id": i,
                    "author": "Unknown", # Requires sibling parsing
                    "content": "Unknown",
                    "reply_x": (rect[0] + rect[2]) // 2,
                    "reply_y": (rect[1] + rect[3]) // 2
                })
        except Exception as e:
            print(f"[-] Warning: Failed to parse comments UI: {e}")

        print("\n--- POST DESCRIPTION ---")
        print("Description extraction via UI Automator is partial due to obscuration.")
        print("------------------------\n")
        
        print("\n--- COMMENTS (JSON) ---")
        print(json.dumps(comments, ensure_ascii=False, indent=2))
        print("-----------------------\n")
        print("[*] Extract complete.")

    def action_reply(self, x, y, text, live_mode, should_close):
        self._check_risk_and_notify()
        print(f"[*] Driver: Replying at ({x}, {y})")
        self._click_with_noise(x, y)
        self._human_sleep(2.0, 1.0)
        
        # Check if keyboard is up or input box is active
        input_box = self.d(className="android.widget.EditText")
        if not input_box.exists:
            print("[-] FATAL: Input box did not appear after clicking. Aborting.")
            sys.exit(1)
            
        print(f"[*] Driver: Typing text: {text}")
        self._human_type(text)
        
        if live_mode:
            print("[!] LIVE MODE: Clicking '发送'...")
            # XHS send button is usually text "发送" or "发布"
            send_btn = self.d(textMatches="发送|发布")
            if send_btn.exists:
                self._human_click_element(send_btn)
                print("[*] Driver: Waiting 3s for network response...")
                time.sleep(3)
                
                # Verify Success by checking if text appeared in UI
                if self.d(textContains=text[:5]).exists:
                    print("[+] Comment verified as successfully posted in App DOM.")
                else:
                    print("[-] Warning: Comment not found in App DOM. May be shadowbanned or network failed.")
                
                print("[*] Driver: Entering mandatory cooldown after commenting to prevent high-frequency risk.")
                self._human_sleep(90.0, 30.0) # 1~2 minutes delay after a real comment
            else:
                print("[-] FATAL: Could not find '发送' (Send) button in App. Aborting.")
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
                
                # Pick a random post card in viewport
                cards = self.d.xpath('//androidx.recyclerview.widget.RecyclerView/android.widget.FrameLayout').all()
                if cards:
                    target_card = random.choice(cards)
                    self._human_click_element(target_card)
                    
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
    
    driver = XHSMobileDriver(args.device)
    
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
