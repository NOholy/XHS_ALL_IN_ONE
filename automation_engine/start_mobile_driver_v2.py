"""
XHS Mobile Driver V2 - Industrial Grade CLI Entry Point
支持: 真机初始化 | 养号 | 话题截流 | 全自动(养号+截流) | 扫描 | 提取 | 回复

所有参数均通过 config.yaml + 环境变量配置，CLI仅提供 action 选择和必要覆盖。
"""
import argparse
import copy
import sys
import os
import json
from datetime import datetime

# 确保 automation_engine 为 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from config import load_config
from mobile_core.logger import get_logger

logger = get_logger("main")


def _build_driver(config):
    """根据配置构建设备驱动"""
    if config.device.use_agentless:
        from mobile_core.agentless_driver import AgentlessMinitouchDriver
        return AgentlessMinitouchDriver(config.device.serial)
    else:
        from mobile_core.device_driver import DeviceDriver
        return DeviceDriver(config.device.serial)


def _build_components(config):
    """构建所有核心组件"""
    driver = _build_driver(config)

    from mobile_core.vision import VisionEngine
    from mobile_core.ocr_client import OCRClient
    from mobile_core.keyboard_vision import KeyboardVisionTyping
    from mobile_core.watchdog import PopupWatchdog
    from mobile_core.navigator import XHSNavigator
    from mobile_core.searcher import XHSSearcher
    from mobile_core.reader import PostReader
    from mobile_core.commenter import SmartCommenter
    from mobile_core.farmer import AccountFarmer

    # Update template dir to be resolution-aware and device-isolated
    try:
        img = driver.screenshot()
        h, w = img.shape[:2]
        screenshot_res = f"{w}x{h}"
        
        # 优先读取 device_profile 获取更准确的分辨率缓存
        serial = config.device.serial
        profile_path = os.path.join(os.path.dirname(__file__), "data", "device_profiles", f"{serial}.json")
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
                if profile.get("screenshot_resolution"):
                    screenshot_res = profile["screenshot_resolution"]
        
        base_templates = config.vision.templates_dir
        if base_templates.endswith(screenshot_res):
            base_templates = os.path.dirname(base_templates)
            if serial and base_templates.endswith(serial):
                base_templates = os.path.dirname(base_templates)
                
        if serial:
            device_templates_dir = os.path.join(base_templates, serial, screenshot_res)
        else:
            device_templates_dir = os.path.join(base_templates, screenshot_res)
            
        shared_templates_dir = os.path.join(base_templates, screenshot_res)
        os.makedirs(device_templates_dir, exist_ok=True)
        config.vision.templates_dir = device_templates_dir
        config.vision.shared_templates_dir = shared_templates_dir
    except Exception as e:
        logger.warning(f"Could not determine resolution for template dir: {e}")
        config.vision.shared_templates_dir = None

    vision = VisionEngine(config.vision.templates_dir, getattr(config.vision, 'shared_templates_dir', None))
    ocr = OCRClient(
        endpoint=config.ocr.endpoint,
        timeout=config.ocr.timeout,
        circuit_breaker_threshold=config.ocr.circuit_breaker_threshold,
        circuit_breaker_cooldown=config.ocr.circuit_breaker_cooldown,
    )
    keyboard = KeyboardVisionTyping(driver, vision, ocr)
    watchdog = PopupWatchdog(vision, driver, ocr_client=ocr)
    navigator = XHSNavigator(driver, vision, ocr, config)
    searcher = XHSSearcher(driver, vision, ocr, keyboard, navigator, config)
    reader = PostReader(driver, vision, ocr, config)
    commenter = SmartCommenter(driver, vision, ocr, keyboard, config)
    farmer = AccountFarmer(driver, vision, ocr, navigator, reader, config)

    return {
        "driver": driver, "vision": vision, "ocr": ocr,
        "keyboard": keyboard, "watchdog": watchdog,
        "navigator": navigator, "searcher": searcher,
        "reader": reader, "commenter": commenter, "farmer": farmer,
    }


def action_init(config, force=False):
    """真机初始化 — 不预构建组件，避免提前安装 u2 agent"""
    from flows.init_flow import InitOrchestrator

    orchestrator = InitOrchestrator(config)
    report = orchestrator.run(config.device.serial, force=force)
    print("\n--- INIT REPORT ---")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("-------------------\n")
    return report


def action_farm(config):
    """养号模式"""
    components = _build_components(config)
    from flows.farm_flow import FarmOrchestrator
    orchestrator = FarmOrchestrator(components["farmer"], config)
    orchestrator.run()


def action_intercept(config):
    """话题搜索评论截流"""
    components = _build_components(config)
    from flows.intercept_flow import InterceptOrchestrator
    orchestrator = InterceptOrchestrator(
        navigator=components["navigator"],
        searcher=components["searcher"],
        reader=components["reader"],
        commenter=components["commenter"],
        farmer=components["farmer"],
        driver=components["driver"],
        watchdog=components["watchdog"],
        config=config,
    )
    orchestrator.run()


def _check_active_hours(config) -> bool:
    """检查当前时间是否在配置的活跃时段内"""
    if not config.schedule.enabled:
        return True  # 调度未启用时不做限制
    now_hour = datetime.now().hour
    start = config.schedule.active_hours_start
    end = config.schedule.active_hours_end
    if start <= end:
        return start <= now_hour < end
    else:
        # 跨午夜场景（如 22:00 - 06:00）
        return now_hour >= start or now_hour < end


def action_auto(config):
    """全自动模式: 根据 schedule.run_mode 配置执行"""
    mode = config.schedule.run_mode
    logger.info(f"Auto mode: {mode}")

    # 活跃时段校验
    if not _check_active_hours(config):
        now_hour = datetime.now().hour
        logger.info(
            f"Outside active hours ({config.schedule.active_hours_start}:00-"
            f"{config.schedule.active_hours_end}:00). Current hour: {now_hour}. Skipping."
        )
        return

    if mode == "farm_only":
        action_farm(config)
    elif mode == "intercept_only":
        action_intercept(config)
    elif mode == "farm_then_intercept":
        logger.info(f"Warming up with {config.schedule.warmup_farm_minutes} min farming...")
        # 先养号热身
        components = _build_components(config)
        from flows.farm_flow import FarmOrchestrator
        farm_orch = FarmOrchestrator(components["farmer"], config)
        farm_orch.run(config.schedule.warmup_farm_minutes)

        # 再截流
        from flows.intercept_flow import InterceptOrchestrator
        intercept_orch = InterceptOrchestrator(
            navigator=components["navigator"],
            searcher=components["searcher"],
            reader=components["reader"],
            commenter=components["commenter"],
            farmer=components["farmer"],
            driver=components["driver"],
            watchdog=components["watchdog"],
            config=config,
        )
        intercept_orch.run()
    elif mode == "mixed":
        # 交替执行：养号一轮 → 截流一个关键词 → 养号 → ...
        components = _build_components(config)
        from flows.intercept_flow import InterceptOrchestrator

        # 保存原始关键词列表，避免修改共享引用
        all_keywords = list(config.intercept.keywords)
        for keyword in all_keywords:
            # 养号热身
            components["farmer"].run_session(duration_minutes=10)
            # 截流单个关键词（deepcopy 避免污染原始 config）
            single_config = copy.deepcopy(config)
            single_config.intercept.keywords = [keyword]
            intercept_orch = InterceptOrchestrator(
                navigator=components["navigator"],
                searcher=components["searcher"],
                reader=components["reader"],
                commenter=components["commenter"],
                farmer=components["farmer"],
                driver=components["driver"],
                watchdog=components["watchdog"],
                config=single_config,
            )
            intercept_orch.run()
    else:
        logger.error(f"Unknown run_mode: {mode}")


def action_scan(config):
    """信息流扫描（保留原始功能）"""
    components = _build_components(config)
    driver = components["driver"]
    vision = components["vision"]

    driver.ensure_app_foreground()
    logger.info("Scanning feed...")
    driver.human_swipe("down")

    img = driver.screenshot()
    cards = vision.detect_cards_waterfall(img)

    if not cards:
        w, h = config.device.screen_width, config.device.screen_height
        cards = [
            {"id": 0, "title": "Grid_TopLeft", "x": int(w*0.25), "y": int(h*0.35)},
            {"id": 1, "title": "Grid_TopRight", "x": int(w*0.75), "y": int(h*0.35)},
            {"id": 2, "title": "Grid_BotLeft", "x": int(w*0.25), "y": int(h*0.75)},
            {"id": 3, "title": "Grid_BotRight", "x": int(w*0.75), "y": int(h*0.75)},
        ]

    print("\n--- VISIBLE POSTS ---")
    print(json.dumps(cards, ensure_ascii=False, indent=2))
    print("---------------------\n")


def action_extract(config, x, y):
    """提取帖子内容"""
    components = _build_components(config)
    result = components["reader"].enter_and_extract(x, y)
    print("\n--- EXTRACTED DATA ---")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("----------------------\n")


def action_reply(config, x, y, text, live):
    """回复评论"""
    components = _build_components(config)
    components["commenter"].post_comment(x, y, text, live=live)


def parse_args():
    parser = argparse.ArgumentParser(
        description="XHS Android Automation Driver V2 - Industrial Grade",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--action", required=True,
        choices=["init", "farm", "intercept", "auto", "scan", "extract", "reply"],
        help=(
            "init       - 真机一键初始化（关闭动画/采集模板/检测登录）\n"
            "farm       - 自动养号（浏览/点赞/收藏/搜索）\n"
            "intercept  - 话题搜索评论截流\n"
            "auto       - 全自动（根据 schedule.run_mode 配置执行）\n"
            "scan       - 信息流扫描\n"
            "extract    - 提取指定帖子内容\n"
            "reply      - 回复指定坐标"
        )
    )
    # CLI 覆盖参数（均为可选，不传则使用 config.yaml）
    parser.add_argument("--device", type=str, help="覆盖 ADB 设备序列号")
    parser.add_argument("--agentless", action="store_true", default=None,
                        help="强制使用无代理模式")
    parser.add_argument("--typing-mode", choices=["clipboard", "opencv"],
                        help="覆盖打字模式")
    parser.add_argument("--live", action="store_true", default=None,
                        help="覆盖为真实发送模式")
    parser.add_argument("--keywords", nargs="+", help="覆盖截流关键词")
    parser.add_argument("--comment-mode", choices=["template", "contextual", "llm"],
                        help="覆盖评论生成模式")
    parser.add_argument("--run-mode",
                        choices=["farm_then_intercept", "intercept_only",
                                 "farm_only", "mixed"],
                        help="覆盖 auto 模式的运行策略")
    parser.add_argument("--farm-duration", type=int, help="覆盖养号时长(分钟)")
    
    # 传统参数（extract/reply 专用）
    parser.add_argument("--force", action="store_true", default=False,
                        help="强制重新初始化（跳过幂等检测，全量执行所有步骤）")
    # 传统参数（extract/reply 专用）
    parser.add_argument("--x", type=int, help="X坐标")
    parser.add_argument("--y", type=int, help="Y坐标")
    parser.add_argument("--text", type=str, help="评论文本")

    return parser.parse_args()


def main():
    args = parse_args()

    # 加载配置
    config = load_config()

    # CLI 参数覆盖 config
    if args.device:
        config.device.serial = args.device
    if args.agentless is not None:
        config.device.use_agentless = args.agentless
    if args.typing_mode:
        config.device.typing_mode = args.typing_mode
    if args.live is not None:
        config.intercept.live_mode = args.live
    if args.keywords:
        config.intercept.keywords = args.keywords
    if args.comment_mode:
        config.intercept.comment_mode = args.comment_mode
    if args.run_mode:
        config.schedule.run_mode = args.run_mode
    if args.farm_duration:
        config.farm.session_duration_minutes = args.farm_duration

    logger.info(f"Starting XHS Driver V2",
                extra={"action": args.action, "device": config.device.serial})

    # 路由到对应 action
    if args.action == "init":
        action_init(config, force=args.force)
    elif args.action == "farm":
        action_farm(config)
    elif args.action == "intercept":
        action_intercept(config)
    elif args.action == "auto":
        action_auto(config)
    elif args.action == "scan":
        action_scan(config)
    elif args.action == "extract":
        if args.x is None or args.y is None:
            logger.error("--x and --y required for extract")
            sys.exit(1)
        action_extract(config, args.x, args.y)
    elif args.action == "reply":
        if args.x is None or args.y is None or not args.text:
            logger.error("--x, --y, and --text required for reply")
            sys.exit(1)
        action_reply(config, args.x, args.y, args.text,
                     args.live or config.intercept.live_mode)


if __name__ == "__main__":
    main()
