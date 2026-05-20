"""
XHS Mobile Driver V2 - Industrial Grade Refactor
Implements decoupled OCR, State Machine architecture, robust logging, and probability farming funnel.
"""
import argparse
import sys
import os
import json
import random
from mobile_core.logger import get_logger
from mobile_core.device_driver import DeviceDriver
from mobile_core.agentless_driver import AgentlessMinitouchDriver
from mobile_core.keyboard_vision import KeyboardVisionTyping
from mobile_core.vision import VisionEngine
from mobile_core.watchdog import PopupWatchdog
from mobile_core.state_machine import StateMachineExecutor
from mobile_core.ocr_client import OCRClient

logger = get_logger("main")

class XHSBusinessFlows:
    def __init__(self, device_serial, use_agentless=False, typing_mode="clipboard"):
        self.typing_mode = typing_mode
        self.use_agentless = use_agentless
        if use_agentless:
            self.driver = AgentlessMinitouchDriver(device_serial)
        else:
            self.driver = DeviceDriver(device_serial)
            
        templates_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates")
        self.vision = VisionEngine(templates_dir)
        self.watchdog = PopupWatchdog(self.vision, self.driver)
        self.fsm = StateMachineExecutor(self.driver, self.watchdog)
        self.ocr = OCRClient() # Connects to decoupled FastAPI microservice
        self.keyboard = KeyboardVisionTyping(self.driver, self.vision, self.ocr)

    # --- STATE FUNCTIONS ---
    
    def state_feed_scan(self):
        """State: Scanning the feed."""
        self.driver.ensure_app_foreground()
        logger.info("Scanning for posts on feed...")
        self.driver.human_swipe("down")
        
        img = self.driver.screenshot()
        cards = self.vision.detect_cards_waterfall(img)
        
        # Grid fallback if no cards detected dynamically
        if not cards:
            w, h = 1080, 1920
            if hasattr(self.driver, "d"):
                w, h = self.driver.d.window_size()
            cards = [
                {"id": 0, "title": "CV_Post_TopLeft", "x": int(w * 0.25), "y": int(h * 0.35)},
                {"id": 1, "title": "CV_Post_TopRight", "x": int(w * 0.75), "y": int(h * 0.35)},
                {"id": 2, "title": "CV_Post_BotLeft", "x": int(w * 0.25), "y": int(h * 0.75)},
                {"id": 3, "title": "CV_Post_BotRight", "x": int(w * 0.75), "y": int(h * 0.75)}
            ]
        
        print("\n--- VISIBLE POSTS (GRID-BASED) ---")
        print(json.dumps(cards, ensure_ascii=False, indent=2))
        print("----------------------------\n")
        
        logger.info("Feed scan complete.")
        return None # End of flow

    def state_post_extract(self, target_x, target_y):
        """State: Extracting data from a post."""
        self.driver.ensure_app_foreground()
        logger.info(f"Tapping post at ({target_x}, {target_y})")
        self.driver.physical_tap(target_x, target_y)
        self.driver.human_sleep(4.0, 1.0)
        
        # 1. OCR for post description
        img = self.driver.screenshot()
        post_desc = []
        try:
            ocr_res = self.ocr.ocr_image(img)
            post_desc = [line[1][0] for line in ocr_res if line[1][1] > 0.6]
            logger.info("Post description extracted")
        except Exception as e:
            logger.error("Post OCR Extraction failed", extra={"error": str(e)})
            
        # 2. Scroll to load comments
        logger.info("Scrolling to load comments...")
        self.driver.human_swipe("down")
        self.driver.human_swipe("down")
        self.driver.human_sleep(2.0, 1.0)
        
        # 3. OCR for comments
        img_comments = self.driver.screenshot()
        comments = []
        try:
            ocr_res2 = self.ocr.ocr_image(img_comments)
            reply_btn = self.vision.find_template(img_comments, "reply_button", threshold=0.8)
            
            for i, line in enumerate(ocr_res2):
                box, (text, conf) = line
                if conf > 0.6 and len(text) > 2 and "回复" not in text:
                    comments.append({
                        "id": i,
                        "author": "OCR_User",
                        "content": text,
                        "reply_x": reply_btn["x"] if reply_btn else 0,
                        "reply_y": reply_btn["y"] if reply_btn else 0
                    })
            logger.info("Comments extracted")
        except Exception as e:
            logger.error("Comments OCR Extraction failed", extra={"error": str(e)})

        # Output JSON result for orchestrator integration
        output_data = {
            "description": post_desc,
            "comments": comments
        }
        print("\n--- EXTRACTED DATA (JSON) ---")
        print(json.dumps(output_data, ensure_ascii=False, indent=2))
        print("-----------------------------\n")
        
        return None # End of flow
        
    def state_post_reply(self, target_x, target_y, text, live_mode=False, should_close=False):
        """State: Reply to a post with physical or clipboard typing."""
        self.driver.ensure_app_foreground()
        
        logger.info(f"Tapping reply box at ({target_x}, {target_y})")
        self.driver.physical_tap(target_x, target_y)
        self.driver.human_sleep(2.0, 1.0)
        
        logger.info(f"Typing text: '{text}' using mode: {self.typing_mode}")
        if self.typing_mode == "clipboard" and hasattr(self.driver, "d"):
            self.driver.d.set_clipboard(text)
            self.driver.human_sleep(1.0, 0.5)
            self.driver.d.send_keys(text, clear=True)
            self.driver.human_sleep(1.5, 0.5)
        else:
            self.keyboard.type_chinese(text)
            
        if live_mode:
            logger.info("LIVE MODE: Clicking '发送' (Send)...")
            img = self.driver.screenshot()
            send_btn = self.vision.find_template(img, "send_button", threshold=0.75)
            if send_btn:
                self.driver.physical_tap(send_btn['x'], send_btn['y'])
                logger.info("Waiting 4s for network response...")
                self.driver.human_sleep(4.0, 1.0)
                
                # Check success via OCR
                logger.info("Verifying comment via OCR...")
                verify_img = self.driver.screenshot()
                try:
                    ocr_res = self.ocr.ocr_image(verify_img)
                    success = False
                    for line in ocr_res:
                        text_val = line[1][0]
                        if text[:4] in text_val: # Check first 4 chars
                            success = True
                            break
                    if success:
                        logger.info("Comment verified as successfully posted in UI!")
                    else:
                        logger.warning("Comment not found via OCR. May be shadowbanned or network failed.")
                except Exception as e:
                    logger.error("OCR Verification failed", extra={"error": str(e)})
                
                logger.info("Entering mandatory cooldown after comment to prevent high-frequency risk.")
                self.driver.human_sleep(90.0, 30.0) # 1~2 minutes delay
            else:
                logger.error("Could not find send button!")
        else:
            logger.info("DRY RUN: Cancelling comment...")
            self.driver.press_back()
            self.driver.human_sleep(1.0, 0.5)
            self.driver.press_back()

        if should_close:
            logger.info("Closing overlay (Pressing Back)...")
            self.driver.press_back()
            self.driver.human_sleep(1.5, 0.5)
            
        return None # End of flow
        
    def state_farming_loop(self, current_step=0, target_steps=50):
        """State: Farm loop with probability funnel."""
        if current_step >= target_steps:
            logger.info("Farming session complete.")
            return None
            
        logger.info(f"Farming Step {current_step+1}/{target_steps}")
        self.driver.human_swipe("down")
        self.driver.human_sleep(4.0, 2.0)
        
        # 30% chance to click into a post
        if random.random() < 0.30:
            logger.info("Funnel: 30% chance triggered! Transitioning to read post...")
            return lambda: self.state_farming_read_post(current_step, target_steps)
            
        return lambda: self.state_farming_loop(current_step + 1, target_steps)

    def state_farming_read_post(self, current_step, target_steps):
        """State: Pretend to read a post during farming."""
        img = self.driver.screenshot()
        cards = self.vision.detect_cards_waterfall(img)
        if cards:
            card = random.choice(cards)
            logger.info(f"Farming: Clicking into detected post card at ({card['x']}, {card['y']})")
            self.driver.physical_tap(card['x'], card['y'])
        else:
            # Grid heuristic fallback
            w, h = 1080, 1920
            if hasattr(self.driver, "d"):
                w, h = self.driver.d.window_size()
            tx = random.choice([int(w * 0.25), int(w * 0.75)])
            ty = random.randint(int(h * 0.3), int(h * 0.8))
            logger.info(f"Farming: Fallback click at ({tx}, {ty})")
            self.driver.physical_tap(tx, ty)
            
        self.driver.human_sleep(10.0, 4.0) # Long read simulation
        
        # 33% chance to open comments
        if random.random() < 0.33:
            logger.info("Farming: Opening comments...")
            self.driver.human_swipe("down")
            self.driver.human_sleep(5.0, 2.0)
            
        logger.info("Farming: Exiting post...")
        self.driver.press_back()
        self.driver.human_sleep(1.5, 0.5)
        
        return lambda: self.state_farming_loop(current_step + 1, target_steps)

    # --- ENTRY POINTS ---
    def run_scan(self):
        self.fsm.execute(self.state_feed_scan)
        
    def run_extract(self, x, y):
        self.fsm.execute(self.state_post_extract, x, y)
        
    def run_reply(self, x, y, text, live_mode=False, should_close=False):
        self.fsm.execute(self.state_post_reply, x, y, text, live_mode, should_close)
        
    def run_farm(self):
        self.fsm.execute(self.state_farming_loop, 0, 50)

def parse_args():
    parser = argparse.ArgumentParser(description="XHS Android Automation Driver V2")
    parser.add_argument("--device", type=str, help="ADB Serial or IP address of the device")
    parser.add_argument("--action", required=True, choices=["scan", "extract", "reply", "farm"], help="Action")
    parser.add_argument("--typing-mode", choices=["clipboard", "opencv"], default="clipboard", help="How to input text")
    parser.add_argument("--x", type=int, help="X coordinate for clicking")
    parser.add_argument("--y", type=int, help="Y coordinate for clicking")
    parser.add_argument("--text", type=str, help="Text to type")
    parser.add_argument("--live", action="store_true", help="If set, actually clicks the send button.")
    parser.add_argument("--close", action="store_true", help="If set, clicks Android back button to close post.")
    parser.add_argument("--agentless", action="store_true", help="Use Phase 3 Minitouch/ADB Agentless Driver")
    return parser.parse_args()

def main():
    args = parse_args()
    logger.info(f"Starting XHS Mobile Driver V2", extra={"action": args.action, "device": args.device})
    
    app = XHSBusinessFlows(args.device, use_agentless=args.agentless, typing_mode=args.typing_mode)
    
    if args.action == "scan":
        app.run_scan()
    elif args.action == "farm":
        app.run_farm()
    elif args.action == "extract":
        if args.x is None or args.y is None:
            logger.error("--x and --y required for extract")
            sys.exit(1)
        app.run_extract(args.x, args.y)
    elif args.action == "reply":
        if args.x is None or args.y is None or not args.text:
            logger.error("--x, --y, and --text required for reply")
            sys.exit(1)
        app.run_reply(args.x, args.y, args.text, args.live, args.close)

if __name__ == "__main__":
    main()
