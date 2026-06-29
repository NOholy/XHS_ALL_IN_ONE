import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from automation_engine.mobile_core.agentless_driver import AgentlessTouchDriver

def test():
    print("Testing AgentlessTouchDriver initialization...")
    try:
        driver = AgentlessTouchDriver()
        driver.check_ready("com.android.settings") # Use settings to ensure it exists
        print(f"Touch Port dynamically allocated: {driver._touch_port}")
        print("Initialization successful!")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == '__main__':
    test()
