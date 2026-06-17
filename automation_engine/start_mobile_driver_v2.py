"""
XHS Mobile Driver V2 - Industrial Grade CLI Entry Point
支持: 真机初始化 | 养号 | 话题截流 | 全自动(养号+截流) | 扫描 | 提取 | 回复 | Pipeline

所有参数均通过 config.yaml + 环境变量配置，CLI仅提供 action 选择和必要覆盖。
"""
import argparse
import copy
import sys
import os
import json
from datetime import datetime
import threading

# 确保 automation_engine 为 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from config import load_config
from mobile_core.logger import get_logger

logger = get_logger("main")


def _build_driver(config):
    """根据配置构建设备驱动"""
    from mobile_core.agentless_driver import AgentlessTouchDriver
    
    yolo_model_path = config.device.yolo_model_path
    if not yolo_model_path:
        raise ValueError("🚨 CRITICAL: config.device.yolo_model_path is not configured! YOLO model path is required.")
        
    abs_yolo_path = os.path.abspath(yolo_model_path)
    if not os.path.exists(abs_yolo_path):
        raise FileNotFoundError(f"🚨 CRITICAL: YOLO model file not found at: {abs_yolo_path}")

    driver = AgentlessTouchDriver(config.device.serial, yolo_model_path=abs_yolo_path)
    
    # 传递传感器配置
    if hasattr(config, "stealth") and hasattr(config.stealth, "sensor_mode"):
        driver.set_sensor_mode(config.stealth.sensor_mode)
        
    return driver


def enforce_preconditions(config):
    """强制检查前置条件，保证在非 init 模式下也能安全运行"""
    logger.info("Enforcing execution preconditions...")
    import requests
    import subprocess
    
    # 1. Check OCR Health
    try:
        session = requests.Session()
        session.trust_env = False
        health_url = config.ocr.endpoint.replace("/ocr", "/health")
        res = session.get(health_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if not data.get("engine_ready"):
                logger.error(f"🚨 CRITICAL: OCR engine not ready: {data}. Aborting.")
                sys.exit(1)
        else:
            logger.error(f"🚨 CRITICAL: OCR service returned HTTP {res.status_code}. Aborting.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"🚨 CRITICAL: OCR service unreachable. Is ocr_server.py running? Error: {e}")
        sys.exit(1)

    # 2. Check XHS App Installation
    adb_prefix = ["adb"] if not config.device.serial else ["adb", "-s", config.device.serial]
    try:
        result = subprocess.run(
            adb_prefix + ["shell", "dumpsys", "package", "com.xingin.xhs"],
            capture_output=True, text=True, timeout=10
        )
        if "versionName=" not in result.stdout:
            logger.error("🚨 CRITICAL: XHS app not installed! Aborting.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"🚨 CRITICAL: Failed to check XHS app installation: {e}")
        sys.exit(1)


def _build_components(config):
    """构建所有核心组件"""
    # 强制执行前置条件验证 (确保安全底线)
    enforce_preconditions(config)
    
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

    # Phase 2: 构建轻量级页面检测器（通过 dumpsys，零风控风险）
    from mobile_core.page_detector import LightPageDetector
    page_detector = LightPageDetector(driver.adb_prefix)

    watchdog = PopupWatchdog(vision, driver, ocr_client=ocr, page_detector=page_detector)
    navigator = XHSNavigator(driver, vision, ocr, config, page_detector=page_detector)
    searcher = XHSSearcher(driver, vision, ocr, keyboard, navigator, config)
    reader = PostReader(driver, vision, ocr, config)
    commenter = SmartCommenter(driver, vision, ocr, keyboard, config)
    farmer = AccountFarmer(driver, vision, ocr, navigator, reader, commenter, config,
                           keyboard=keyboard, watchdog=watchdog)

    return {
        "driver": driver, "vision": vision, "ocr": ocr,
        "keyboard": keyboard, "watchdog": watchdog,
        "navigator": navigator, "searcher": searcher,
        "reader": reader, "commenter": commenter, "farmer": farmer,
        "page_detector": page_detector,
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
    """养号模式 (已迁移至 Pipeline 引擎)"""
    logger.info("Starting Farm Pipeline...")
    action_pipeline(config, pipeline_path="farm_session")


def action_intercept(config):
    """话题搜索评论截流"""
    if getattr(config.intercept, "version", "v1") == "v2":
        logger.info("Starting Intercept V2 (Natural Browsing)...")
        components = _build_components(config)
        from flows.intercept_flow_v2 import InterceptV2Orchestrator
        orchestrator = InterceptV2Orchestrator(
            navigator=components["navigator"],
            searcher=components["searcher"],
            reader=components["reader"],
            commenter=components["commenter"],
            farmer=components["farmer"],
            driver=components["driver"],
            config=config,
            watchdog=components["watchdog"]
        )
        orchestrator.run()
    else:
        logger.info("Starting Intercept Pipeline (V1)...")
        keywords = config.intercept.keywords
        for keyword in keywords:
            logger.info(f"Intercepting keyword: {keyword}")
            action_pipeline(
                config, 
                pipeline_path="intercept_comment", 
                context={"current_keyword": keyword}
            )


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
        action_pipeline(config, pipeline_path="farm_session")

        # 再截流
        action_intercept(config)
        
    elif mode == "mixed":
        # 交替执行：养号一轮 → 截流一个关键词 → 养号 → ...
        all_keywords = list(config.intercept.keywords)
        for keyword in all_keywords:
            # 养号热身
            action_pipeline(config, pipeline_path="farm_session")
            
            # 截流单个关键词
            action_pipeline(
                config, 
                pipeline_path="intercept_comment", 
                context={"current_keyword": keyword}
            )
    else:
        logger.error(f"Unknown run_mode: {mode}")


def action_agent(config, prompt):
    """LLM Agent 模式驱动"""
    if not prompt:
        logger.error("Agent mode requires --prompt to be specified.")
        sys.exit(1)

    components = _build_components(config)
    from mobile_core.tool_registry import ToolRegistry
    from mobile_core.tools import (
        GoHomeTool, DetectPageTool, TapTool, SwipeTool,
        SearchKeywordTool, ReadPostTool, FinishTool
    )
    from mobile_core.agent_loop import AgentLoop

    registry = ToolRegistry()
    registry.register(GoHomeTool(components["navigator"]))
    registry.register(DetectPageTool(components["navigator"]))
    registry.register(TapTool(components["driver"]))
    registry.register(SwipeTool(components["driver"]))
    registry.register(SearchKeywordTool(components["searcher"]))
    registry.register(ReadPostTool(components["reader"]))
    registry.register(FinishTool())

    agent = AgentLoop(registry, config)
    logger.info(f"Starting Agent with prompt: {prompt}")
    result = agent.run(prompt)
    
    print("\n--- AGENT RESULT ---")
    print(result)
    print("--------------------\n")


def action_pipeline(config, pipeline_path=None, context=None, override=None, entry=None, report=False):
    """Pipeline 模式 — YAML 声明式自动化执行"""
    import os
    from mobile_core.pipeline.loader import PipelineLoader
    from mobile_core.pipeline.recognition import RecognitionRegistry
    from mobile_core.pipeline.actions import ActionRegistry
    from mobile_core.pipeline.engine import PipelineExecutor
    from mobile_core.pipeline.middleware import (
        WatchdogMiddleware, LoopDetectorMiddleware, LoggingMiddleware
    )

    # 1. 构建所有组件
    components = _build_components(config)

    # 2. 构建 Pipeline 引擎
    reco_registry = RecognitionRegistry(
        vision=components["vision"],
        ocr=components["ocr"],
        page_detector=components["page_detector"],
        driver=components["driver"],
        config=config,
    )
    action_registry = ActionRegistry(
        driver=components["driver"],
        navigator=components["navigator"],
        keyboard_vision=components["keyboard"],
        config=config,
    )
    executor = PipelineExecutor(
        driver=components["driver"],
        recognition_registry=reco_registry,
        action_registry=action_registry,
        config=config,
    )

    # 3. 注册中间件
    executor.add_middleware(
        WatchdogMiddleware(
            watchdog=components["watchdog"],
            check_interval_ms=config.pipeline.watchdog_interval_ms,
        )
    )

    from mobile_core.loop_detector import LoopDetector
    loop_detector = LoopDetector()
    executor.add_middleware(
        LoopDetectorMiddleware(
            loop_detector=loop_detector,
            max_stuck_count=config.pipeline.max_stuck_count,
        )
    )

    if config.pipeline.enable_logging:
        os.makedirs(config.pipeline.log_dir, exist_ok=True)
        from datetime import datetime as dt
        log_file = os.path.join(
            config.pipeline.log_dir,
            f"pipeline_{dt.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        )
        executor.add_middleware(
            LoggingMiddleware(
                log_file=log_file,
                save_screenshots=config.pipeline.save_screenshots,
                screenshot_dir=os.path.join(config.pipeline.log_dir, "screenshots"),
            )
        )
        logger.info(f"Pipeline log: {log_file}")

    # 4. 加载 Pipeline YAML
    loader = PipelineLoader(strict=config.pipeline.strict_validation)

    if pipeline_path:
        # 指定了具体 YAML 文件
        if not os.path.isabs(pipeline_path):
            # 相对路径: 先查 pipeline_dir, 再查当前目录
            candidate = os.path.join(config.pipeline.pipeline_dir, pipeline_path)
            if os.path.exists(candidate):
                pipeline_path = candidate
            elif not pipeline_path.endswith(('.yaml', '.yml')):
                # 尝试添加后缀
                for ext in ('.yaml', '.yml'):
                    candidate = os.path.join(config.pipeline.pipeline_dir, pipeline_path + ext)
                    if os.path.exists(candidate):
                        pipeline_path = candidate
                        break
    else:
        # 使用默认 Pipeline
        if config.pipeline.default_pipeline:
            pipeline_path = os.path.join(
                config.pipeline.pipeline_dir,
                config.pipeline.default_pipeline + ".yaml"
            )
        else:
            logger.error("No --pipeline specified and no default_pipeline configured")
            sys.exit(1)

    if not os.path.exists(pipeline_path):
        logger.error(f"Pipeline file not found: {pipeline_path}")
        sys.exit(1)

    logger.info(f"Loading pipeline: {pipeline_path}")
    pipeline_def = loader.load(pipeline_path)

    # 5. 准备上下文
    ctx = context or {}
    ctx["_config"] = config

    # 6. 执行
    logger.info(f"Executing pipeline '{pipeline_def.name}' with context: {ctx}")
    stats = executor.run(
        pipeline=pipeline_def,
        context=ctx,
        override=override,
        entry=entry,
    )

    if report:
        try:
            from mobile_core.pipeline.reporter import HtmlReporter
            import os
            
            report_dir = os.path.join(os.path.dirname(__file__), "data", "pipeline_reports")
            reporter = HtmlReporter(report_dir)
            reporter.generate(pipeline_path, stats)
        except Exception as e:
            logger.error(f"Failed to generate HTML report: {e}")

    # 7. 输出结果
    print("\n--- PIPELINE RESULT ---")
    print(json.dumps(stats.summary(), ensure_ascii=False, indent=2))
    print("-----------------------\n")
    return stats



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
    components["driver"].physical_tap(x, y)
    components["driver"].human_sleep(4.0, 1.0)
    result = components["reader"].extract_current_post()
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
        choices=["init", "farm", "intercept", "auto", "scan", "extract", "reply", "agent", "pipeline"],
        help=(
            "init       - 真机一键初始化（关闭动画/采集模板/检测登录）\n"
            "farm       - 自动养号（浏览/点赞/收藏/搜索）\n"
            "intercept  - 话题搜索评论截流\n"
            "auto       - 全自动（根据 schedule.run_mode 配置执行）\n"
            "scan       - 信息流扫描\n"
            "extract    - 提取指定帖子内容\n"
            "reply      - 回复指定坐标\n"
            "agent      - 启动 LLM Agent 模式\n"
            "pipeline   - Pipeline YAML 声明式执行"
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
    parser.add_argument("--max-runtime", type=int, help="脚本最大运行时间(分钟)，超时将自动强制关闭")
    
    # 传统参数（extract/reply 专用）
    parser.add_argument("--force", action="store_true", default=False,
                        help="强制重新初始化（跳过幂等检测，全量执行所有步骤）")
    # 传统参数（extract/reply 专用）
    parser.add_argument("--x", type=int, help="X坐标")
    parser.add_argument("--y", type=int, help="Y坐标")
    parser.add_argument("--text", type=str, help="评论文本")
    
    # Agent 参数
    parser.add_argument("--prompt", type=str, help="Agent 任务提示词")

    # Pipeline 参数
    parser.add_argument("--pipeline", type=str,
                        help="Pipeline YAML 文件路径或名称 (如 intercept_comment)")
    parser.add_argument("--context", type=str,
                        help='Pipeline 上下文 JSON (如 \'{"current_keyword": "旅游"}\')')
    parser.add_argument("--pipeline-override", type=str,
                        help='Pipeline 节点覆盖 JSON')
    parser.add_argument("--pipeline-entry", type=str,
                        help="Pipeline 覆盖入口节点")
    parser.add_argument("--report", action="store_true",
                        help="Generate HTML report after pipeline execution (saved to data/pipeline_reports)")

    return parser.parse_args()


def main():
    args = parse_args()

    if args.max_runtime:
        def timeout_handler():
            from mobile_core.logger import get_logger
            log = get_logger("watchdog")
            log.critical(f"⏳ 脚本已运行超过最大时间限制 ({args.max_runtime} 分钟)，强制终止进程。")
            os._exit(1)
        timer = threading.Timer(args.max_runtime * 60, timeout_handler)
        timer.daemon = True
        timer.start()

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
    elif args.action == "agent":
        action_agent(config, args.prompt)
    elif args.action == "pipeline":
        # 解析 context JSON
        ctx = None
        if args.context:
            try:
                ctx = json.loads(args.context)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid --context JSON: {e}")
                sys.exit(1)

        # 解析 override JSON
        override = None
        if args.pipeline_override:
            try:
                override = json.loads(args.pipeline_override)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid --pipeline-override JSON: {e}")
                sys.exit(1)

        action_pipeline(config, args.pipeline, ctx, override)


if __name__ == "__main__":
    main()
