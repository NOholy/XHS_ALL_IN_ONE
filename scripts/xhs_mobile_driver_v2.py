"""
XHS Mobile Driver V2 - Industrial Grade Refactor
Implements decoupled OCR, State Machine architecture, and robust logging.
"""
import argparse
import sys
import os
import json
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
    def __init__(self, device_serial, use_agentless=False):
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
        
        logger.info("Visible Posts Detected", extra={"cards": cards})
        return None # End of flow

    def state_post_extract(self, target_x, target_y):
        """State: Extracting data from a post."""
        self.driver.ensure_app_foreground()
        logger.info(f"Tapping post at ({target_x}, {target_y})")
        self.driver.physical_tap(target_x, target_y)
        self.driver.human_sleep(4.0, 1.0)
        
        # Call decoupled OCR service
        img = self.driver.screenshot()
        try:
            results = self.ocr.ocr_image(img)
            post_desc = [line[1][0] for line in results if line[1][1] > 0.6]
            logger.info("Post description extracted", extra={"description": post_desc})
        except Exception as e:
            logger.error("OCR Extraction failed", extra={"error": str(e)})

        return None # End of flow
        
    def state_post_reply(self, target_x, target_y, text):
        """State: Reply to a post with physical typing."""
        self.driver.ensure_app_foreground()
        logger.info(f"Tapping reply box at ({target_x}, {target_y})")
        self.driver.physical_tap(target_x, target_y)
        self.driver.human_sleep(2.0, 1.0)
        
        # Phase 3 Visual Typing
        self.keyboard.type_chinese(text)
        
        # Click send visually
        img = self.driver.screenshot()
        send_btn = self.vision.find_template(img, "send_button", threshold=0.75)
        if send_btn:
            self.driver.physical_tap(send_btn['x'], send_btn['y'])
            logger.info("Comment sent.")
        else:
            logger.error("Could not find send button!")
            
        return None # End of flow
        
    def state_farming_loop(self, current_step=0, target_steps=50):
        """State: Farm loop."""
        if current_step >= target_steps:
            logger.info("Farming session complete.")
            return None
            
        logger.info(f"Farming Step {current_step+1}/{target_steps}")
        self.driver.human_swipe("down")
        self.driver.human_sleep(4.0, 2.0)
        
        return lambda: self.state_farming_loop(current_step + 1, target_steps)

    # --- ENTRY POINTS ---
    def run_scan(self):
        self.fsm.execute(self.state_feed_scan)
        
    def run_extract(self, x, y):
        self.fsm.execute(self.state_post_extract, x, y)
        
    def run_reply(self, x, y, text):
        self.fsm.execute(self.state_post_reply, x, y, text)
        
    def run_farm(self):
        self.fsm.execute(self.state_farming_loop, 0, 50)

def parse_args():
    parser = argparse.ArgumentParser(description="XHS Android Automation Driver V2")
    parser.add_argument("--device", type=str, help="ADB Serial or IP address of the device")
    parser.add_argument("--action", required=True, choices=["scan", "extract", "reply", "farm"], help="Action")
    parser.add_argument("--x", type=int, help="X coordinate for clicking")
    parser.add_argument("--y", type=int, help="Y coordinate for clicking")
    parser.add_argument("--text", type=str, help="Text to type")
    parser.add_argument("--agentless", action="store_true", help="Use Phase 3 Minitouch/ADB Agentless Driver")
    return parser.parse_args()

def main():
    args = parse_args()
    logger.info(f"Starting XHS Mobile Driver V2", extra={"action": args.action, "device": args.device})
    
    app = XHSBusinessFlows(args.device, use_agentless=args.agentless)
    
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
        app.run_reply(args.x, args.y, args.text)

if __name__ == "__main__":
    main()
