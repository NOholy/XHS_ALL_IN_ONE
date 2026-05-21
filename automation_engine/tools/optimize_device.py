import sys
from mobile_core.device_optimizer import DeviceOptimizer
from mobile_core.logger import get_logger

logger = get_logger("optimize_runner")

def main():
    logger.info("Starting device optimization process...")
    try:
        opt = DeviceOptimizer()
        
        # 1. 关闭所有动画提升图像识别速度与稳定性
        opt.disable_all_animations()
        
        # 2. 保持屏幕常亮防熄屏
        opt.keep_screen_on()
        
        # 3. 轮换一次 IP (切换飞行模式) 
        # 注意：如果您正在跑其他任务，这会导致短暂断网
        opt.toggle_airplane_mode(delay_seconds=3)
        
        logger.info("Device optimization completed successfully!")
    except Exception as e:
        logger.error(f"Failed to optimize device: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
