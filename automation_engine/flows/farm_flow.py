"""
养号会话编排器
封装 AccountFarmer 的调度逻辑，支持配置化的时段控制。
"""
import time
from mobile_core.logger import get_logger

logger = get_logger("farm_flow")


class FarmOrchestrator:
    """养号流程编排"""

    def __init__(self, farmer, config):
        self.farmer = farmer
        self.config = config

    def run(self, duration_minutes: int = None):
        """执行养号会话"""
        if not self.config.farm.enabled:
            logger.info("Farm mode disabled in config. Skipping.")
            return

        duration = duration_minutes or self.config.farm.session_duration_minutes
        logger.info(f"Starting farm session ({duration} min)")
        self.farmer.run_session(duration)
