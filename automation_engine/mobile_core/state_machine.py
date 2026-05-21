from .logger import get_logger
from .exceptions import RiskControlTriggered, PopupIntercepted

logger = get_logger("state_machine")

class StateMachineExecutor:
    """
    Executes tasks using a robust state machine approach.
    Recovers from PopupIntercepted by re-evaluating the current state.
    """
    def __init__(self, driver, watchdog):
        self.driver = driver
        self.watchdog = watchdog
        self.current_state = None
        self.max_retries = 3

    def execute(self, start_state_func, *args, **kwargs):
        """
        Run a state function and handle state transitions and interruptions.
        """
        next_state = start_state_func
        retries = 0
        
        while next_state and retries < self.max_retries:
            try:
                # Always check screen before executing the state logic
                img = self.driver.screenshot()
                self.watchdog.check_screen(img)
                
                logger.info(f"Transitioning to state: {next_state.__name__}")
                next_state = next_state(*args, **kwargs)
                retries = 0 # reset on success
                
            except PopupIntercepted as e:
                logger.warning(f"Flow interrupted by popup: {e}. Retrying current state.")
                retries += 1
                continue
            except RiskControlTriggered as e:
                logger.error("FATAL: Risk control triggered. Manual intervention required. Suspending task.")
                # In industrial setup, we'd notify a human and suspend this device's queue.
                break
            except Exception as e:
                logger.error(f"State execution failed: {e}")
                retries += 1
                self.driver.human_sleep(3.0, 1.0)
                
        if retries >= self.max_retries:
            logger.error(f"Max retries exceeded in state machine.")
