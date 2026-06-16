"""
Automation Engine - 统一配置中心
所有硬编码参数集中管理，支持 环境变量 > .env > config.yaml > 默认值 的优先级加载。
"""
import os
import yaml
from dataclasses import dataclass, field
from typing import Optional


def _load_yaml_config() -> dict:
    """尝试从 config.yaml 加载配置"""
    config_path = os.environ.get(
        "AE_CONFIG_FILE",
        os.path.join(os.path.dirname(__file__), "config.yaml")
    )
    result = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            result = yaml.safe_load(f) or {}
            
    # Try loading the global default.yaml
    global_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "default.yaml")
    if os.path.exists(global_config_path):
        with open(global_config_path, "r", encoding="utf-8") as f:
            global_data = yaml.safe_load(f) or {}
            if "automation" in global_data:
                # Merge the automation section from global config
                result = _deep_merge(result, global_data["automation"])
                
    return result


def _env(key: str, default=None, cast=None):
    """从环境变量读取，支持类型转换"""
    val = os.environ.get(key)
    if val is None:
        return default
    if cast is bool:
        return val.lower() in ("true", "1", "yes")
    if cast is not None:
        return cast(val)
    return val


@dataclass
class UIElementsConfig:
    """UI 元素与交互关键字配置"""
    reply_keywords: list = field(default_factory=list)
    send_keywords: list = field(default_factory=list)
    input_placeholder_keywords: list = field(default_factory=list)


@dataclass
class DeviceConfig:
    """设备与驱动配置"""
    serial: Optional[str] = None
    use_agentless: bool = True           # 默认使用无代理模式（生产推荐）
    screen_width: int = 1080
    screen_height: int = 1920
    typing_mode: str = "clipboard"       # "clipboard" | "opencv"

    # 初始化
    auto_disable_animations: bool = True
    auto_keep_screen_on: bool = True
    auto_rotate_ip_on_init: bool = True
    auto_crop_templates_on_init: bool = True
    check_xhs_installed: bool = True
    check_login_status: bool = True

    # 传感器模拟 (防止触摸-传感器数据脱节的风控向量)
    sensor_mode: str = "always_on"       # "always_on" | "coupled" | "off"
    sensor_strict: bool = True           # True: 传感器不可用时拒绝启动; False: 仅警告


@dataclass
class OCRConfig:
    """OCR 微服务配置"""
    endpoint: str = "http://localhost:8001/ocr"
    timeout: int = 60
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: int = 60   # 熔断冷却秒数


@dataclass
class VisionConfig:
    """视觉引擎配置"""
    templates_dir: str = ""              # 空则自动计算
    template_match_threshold: float = 0.75
    card_min_area_ratio: float = 0.03    # 卡片最小面积占屏幕比例


@dataclass
class RiskControlConfig:
    """风控与配额配置"""
    # 日配额
    max_daily_comments: int = 10
    max_daily_likes: int = 30
    max_daily_collects: int = 15
    max_daily_follows: int = 5
    max_daily_searches: int = 20

    # 冷却时间（秒）
    comment_cooldown_min: int = 60
    comment_cooldown_max: int = 180
    like_cooldown_min: int = 3
    like_cooldown_max: int = 8
    search_cooldown_min: int = 10
    search_cooldown_max: int = 30

    # IP 轮换
    ip_rotate_every_n_comments: int = 3
    ip_rotate_delay: int = 5

    # 拟人化参数
    human_sleep_mu: float = 3.0
    human_sleep_sigma: float = 1.0
    tap_noise_px: int = 15               # 点击坐标随机偏移像素


@dataclass
class FarmConfig:
    """养号配置"""
    enabled: bool = True
    session_duration_minutes: int = 30
    farming_steps: int = 50

    # 行为漏斗概率（黄金比例 100:30:10:3:1）
    enter_post_probability: float = 0.30
    like_probability: float = 0.10
    collect_probability: float = 0.03
    comment_probability: float = 0.01
    follow_probability: float = 0.005

    # 阅读模拟
    read_duration_mu: float = 10.0       # 平均阅读时长（秒）
    read_duration_sigma: float = 4.0
    scroll_comments_probability: float = 0.33

    # 搜索行为（模拟真人的搜索习惯）
    random_search_probability: float = 0.05
    hot_keywords: list = field(default_factory=lambda: [
        "美食", "穿搭", "旅行", "护肤", "健身", "摄影", "家居", "宠物"
    ])

    # 个人主页浏览
    visit_profile_probability: float = 0.03
    
    # 工业级风控优化参数
    persona: str = "balanced"            # "balanced" | "liker" | "collector" | "commenter" | "lurker"
    fatigue_decay_enabled: bool = True   # 是否开启时间衰减曲线
    combo_boost_enabled: bool = True     # 是否开启连击概率加成(点赞后提升收藏/评论概率)
    
    # 废话评论生成配置
    enable_llm_farm_comments: bool = False
    llm_farm_prompt_template: str = (
        "你是一个在使用小红书的普通年轻网民。请根据以下帖子的正文内容，生成一句非常简短、情绪化、像真人的废话式评论。"
        "规则：1. 不要提供实质性建议，只表达情绪或共鸣。2. 如果是美食/美图，可以说类似'看着也太赞了吧'。3. 绝对不要超过10个字。不要使用任何标点符号。"
        "帖子正文：{content}"
    )


@dataclass
class InterceptConfig:
    """话题搜索评论截流配置"""
    enabled: bool = True

    # 搜索关键词（核心业务参数）
    keywords: list = field(default_factory=lambda: ["地陪", "旅游攻略"])
    # 标题过滤词（帖子标题必须包含其中之一才算目标）
    title_filter_keywords: list = field(default_factory=lambda: [
        "地陪", "找", "求", "推荐", "攻略", "约"
    ])
    # 搜索结果最大翻页数
    max_search_pages: int = 3

    # 评论生成方式: "template" | "contextual" | "llm"
    comment_mode: str = "template"
    comment_templates: list = field(default_factory=lambda: [
        "{感谢分享|谢谢楼主|太感谢了}，{想了解|求问|请问}{更详细的安排|怎么报名|具体怎么弄}！",
        "{楼主|博主|po主}{怎么收费呢|多少钱啊|什么价位}？{可以私信吗|求私|能加个v吗}~",
        "{马克一下|先码了|收藏了}，{近期|最近|下个月}{有出行计划|准备去|想出发}！",
        "{刚好需要|正好在找|太巧了}，{求联系方式|怎么联系你|求个v}~",
        "{想问问|请问一下|问下}{有推荐的路线吗|怎么安排比较好|能定制吗}？",
        "{看起来好棒|好心动啊|好想去}，{已收藏|先mark|已马克}！",
        "{太实用了|干货|真的很有用}，{感谢|谢谢|么么哒}{~|！|}",
        "{求带|带带我|可以一起吗}，{一直想去|心心念念|种草好久了}{！|~}",
        "{哇|天呐|绝了}，{也太好了吧|太赞了|爱了爱了}{！|~|}",
        "请问{费用|价格|预算}大概{多少|什么范围|是怎样的}呀？",
        "{已经|已}心动了，{怎么预约|如何预定|在哪报名}呢？",
        "{好详细|写得好好|太细了}，{先收藏再说|码住|收了}！",
        "想{问一下|知道|了解}{几个人成团|最少几个人|有没有拼团}{？|呀？}",
        "{朋友推荐来的|被种草了|刷到就是缘分}，{想咨询下|求详情|求介绍}~",
        "{下个月|暑假|国庆}{想去|准备出发|有计划}，{还有位置吗|能安排吗|可以吗}？",
        "有{同行的|一起的|拼团的}小伙伴吗？{求组队|一起呀|约约}~",
        "{坐标|我在}{北京|上海|广州|深圳|成都}，{方便吗|能安排吗|可以吗}？",
        "{太棒了|好喜欢|种草了}！{等我有假期就去|攒钱去|安排上}！",
        "{实用贴|干货贴|宝藏帖子}，{已转发|给朋友看看|分享了}！",
        "想带{家人|爸妈|孩子}一起去，{有什么建议吗|适合吗|方便吗}？",
    ])

    # LLM 配置（当 comment_mode="llm" 时使用）
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_prompt_template: str = (
        "你是一个真实的小红书用户，正在浏览关于{keyword}的帖子。"
        "帖子内容：{content}。"
        "请生成一条自然、口语化的评论，表达兴趣并引导私信联系。"
        "不要使用emoji，不要太正式。偶尔可以有错别字或不完整的句子。"
        "字数随机：可以很短(5-10字)也可以较长(20-50字)，不要每次都差不多长。"
    )

    # 截流行为伪装
    browse_before_comment_min: int = 5   # 每次评论前至少浏览N个无关帖子
    browse_before_comment_max: int = 10
    live_mode: bool = False              # False=试运行不发送，True=真实发送

    # 去重
    enable_dedup: bool = True
    dedup_record_file: str = ""          # 空则自动计算

    # ========== V2 自然浏览式截流 ==========
    version: str = "v2"
    v2_non_target_browse_probability: float = 0.30
    v2_non_target_like_probability: float = 0.10
    v2_non_target_collect_probability: float = 0.03
    v2_home_harvest_enabled: bool = True
    v2_home_harvest_duration_minutes: int = 20


@dataclass
class ScheduleConfig:
    """调度时段配置（模拟真人作息）"""
    enabled: bool = False
    # 活跃时段（24小时制）
    active_hours_start: int = 8          # 早8点开始
    active_hours_end: int = 23           # 晚11点结束
    # 运行模式: "farm_then_intercept" | "intercept_only" | "farm_only" | "mixed"
    run_mode: str = "farm_then_intercept"
    # farm_then_intercept 模式下，先养号多少分钟再截流
    warmup_farm_minutes: int = 30


@dataclass
class AgentConfig:
    """Agent模式配置"""
    enabled: bool = True
    llm_endpoint: str = "https://api.deepseek.com/chat/completions"
    llm_api_key: str = ""  # W9: 从环境变量 AE_AGENT_LLM_API_KEY 或 config.yaml 读取
    llm_model: str = "deepseek-v4-flash"
    max_iterations: int = 30


@dataclass
class PipelineConfig:
    """Pipeline 引擎配置"""
    # Pipeline YAML 文件目录
    pipeline_dir: str = ""               # 空则自动计算
    # 默认 Pipeline (不指定 --pipeline 时使用)
    default_pipeline: str = ""           # 如 "intercept_comment"
    # 执行日志
    enable_logging: bool = True
    log_dir: str = ""                    # 空则自动计算
    save_screenshots: bool = False       # 是否保存每步截图 (调试用)
    # Watchdog 中间件
    watchdog_interval_ms: int = 3000     # 弹窗检查间隔 (毫秒)
    # LoopDetector 中间件
    max_stuck_count: int = 3             # 连续卡死 N 次后恢复
    # 执行控制
    strict_validation: bool = False      # 加载时严格验证 DAG


@dataclass
class EngineConfig:
    """引擎顶层配置"""
    device: DeviceConfig = field(default_factory=DeviceConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    risk_control: RiskControlConfig = field(default_factory=RiskControlConfig)
    farm: FarmConfig = field(default_factory=FarmConfig)
    intercept: InterceptConfig = field(default_factory=InterceptConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    ui_elements: UIElementsConfig = field(default_factory=UIElementsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典"""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_dict_to_dataclass(dc, d: dict):
    """将字典值应用到 dataclass 实例"""
    for key, value in d.items():
        if hasattr(dc, key):
            current = getattr(dc, key)
            if hasattr(current, '__dataclass_fields__') and isinstance(value, dict):
                _apply_dict_to_dataclass(current, value)
            else:
                setattr(dc, key, value)


def load_config() -> EngineConfig:
    """
    加载配置，优先级：环境变量 > config.yaml > 默认值
    """
    config = EngineConfig()

    # 1. 从 YAML 加载
    yaml_data = _load_yaml_config()
    if yaml_data:
        _apply_dict_to_dataclass(config, yaml_data)

    # 2. 从环境变量覆盖（关键参数）
    env_overrides = {
        "AE_DEVICE_SERIAL": ("device", "serial", str),
        "AE_USE_AGENTLESS": ("device", "use_agentless", bool),
        "AE_TYPING_MODE": ("device", "typing_mode", str),
        "AE_OCR_ENDPOINT": ("ocr", "endpoint", str),
        "AE_MAX_DAILY_COMMENTS": ("risk_control", "max_daily_comments", int),
        "AE_COMMENT_MODE": ("intercept", "comment_mode", str),
        "AE_LIVE_MODE": ("intercept", "live_mode", bool),
        "AE_LLM_API_KEY": ("intercept", "llm_api_key", str),
        "AE_LLM_MODEL": ("intercept", "llm_model", str),
        "AE_RUN_MODE": ("schedule", "run_mode", str),
        "AE_FARM_DURATION": ("farm", "session_duration_minutes", int),
        "AE_AGENT_LLM_API_KEY": ("agent", "llm_api_key", str),
        "AE_AGENT_LLM_MODEL": ("agent", "llm_model", str),
    }
    for env_key, (section, attr, cast) in env_overrides.items():
        val = _env(env_key, cast=cast)
        if val is not None:
            section_obj = getattr(config, section)
            setattr(section_obj, attr, val)

    # 3. 自动填充计算路径
    base_dir = os.path.dirname(__file__)
    if not config.vision.templates_dir:
        config.vision.templates_dir = os.path.join(base_dir, "..", "data", "ui_templates")
    if not config.intercept.dedup_record_file:
        config.intercept.dedup_record_file = os.path.join(base_dir, "..", "data", "commented_posts.json")
    if not config.pipeline.pipeline_dir:
        config.pipeline.pipeline_dir = os.path.join(base_dir, "config", "pipelines")
    if not config.pipeline.log_dir:
        config.pipeline.log_dir = os.path.join(base_dir, "..", "data", "pipeline_logs")

    return config
