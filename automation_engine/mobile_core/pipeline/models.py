"""
Pipeline 数据模型 — 所有核心数据结构定义。

设计参考:
    - MaaFramework Pipeline Protocol v2 (recognition/action 嵌套结构)
    - Airtest Template / loop_find 概念
    - XHS 风控特有需求 (概率门控、配额检查、人性化延迟)

所有字段均有合理默认值，最小化 YAML 配置量。
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


# ============================================================
#  枚举: 识别类型 & 动作类型
# ============================================================

class RecognitionType(str, Enum):
    """
    识别方式枚举。

    参考 MaaFramework 的 10 种识别类型，
    加入 XHS 特有的 COLOR_SHIFT (点赞/收藏验证) 和 ACTIVITY_DETECT (dumpsys 快速页面识别)。
    """
    DIRECT_HIT = "direct_hit"              # 无条件命中 (用于顺序执行节点)
    TEMPLATE_MATCH = "template_match"      # OpenCV 模板匹配
    OCR_TEXT = "ocr_text"                  # OCR 文字识别 + 正则匹配
    COLOR_SHIFT = "color_shift"            # HSV 颜色变化检测 (点赞红/收藏黄)
    ACTIVITY_DETECT = "activity_detect"    # Android Activity 快速检测
    SCREEN_DIFF = "screen_diff"            # 屏幕变化量 MSE 检测 (防卡死)
    YOLO_DETECT = "yolo_detect"            # YOLOv8 目标检测
    AND = "and"                            # 组合: 全部子识别必须命中
    OR = "or"                              # 组合: 任一子识别命中即可
    CUSTOM = "custom"                      # 自定义 Python 回调


class ActionType(str, Enum):
    """
    动作类型枚举。

    参考 MaaFramework 的 16 种动作类型，
    加入 XHS 特有的 LLM_GENERATE (AI 评论生成)、IP_ROTATE (飞行模式换 IP)、
    HUMAN_SWIPE (贝塞尔人性化滑动)。
    """
    DO_NOTHING = "do_nothing"              # 空操作 (仅识别不执行)
    TAP = "tap"                            # 单击
    SAFE_TAP = "safe_tap"                  # YOLO + Anchor 防风控安全点击
    DOUBLE_TAP = "double_tap"              # 双击 (点赞)
    SWIPE = "swipe"                        # 精确滑动
    HUMAN_SWIPE = "human_swipe"            # 人性化滑动 (贝塞尔 + 惯性)
    INPUT_TEXT = "input_text"              # 视觉键盘输入 (中文 pinyin)
    CLIPBOARD_INPUT = "clipboard_input"    # 剪贴板粘贴输入 (快速)
    PRESS_BACK = "press_back"              # 返回键
    NAVIGATE = "navigate"                  # 导航到指定页面 (home/search/profile/back)
    LAUNCH_APP = "launch_app"              # 启动 App
    STOP_APP = "stop_app"                  # 停止 App
    IP_ROTATE = "ip_rotate"                # 飞行模式 IP 轮换
    LLM_GENERATE = "llm_generate"          # LLM 生成文本 (评论/标题)
    WAIT = "wait"                          # 等待 (人性化随机延迟)
    SCREENCAP_SAVE = "screencap_save"      # 保存截图
    CUSTOM = "custom"                      # 自定义 Python 回调


# ============================================================
#  识别规格
# ============================================================

@dataclass
class RecognitionSpec:
    """
    识别配置规格。

    描述 Pipeline 节点"如何判断当前屏幕状态"。
    不同的 type 使用不同的参数子集。
    """
    type: RecognitionType = RecognitionType.DIRECT_HIT

    # --- 通用参数 ---
    roi: Optional[List[float]] = None
    """搜索区域 [x, y, w, h]。支持像素值或 0.0~1.0 百分比。"""

    roi_offset: Optional[List[int]] = None
    """ROI 偏移 [dx, dy, dw, dh]。"""

    # --- OCR 参数 ---
    expected: Optional[str] = None
    """OCR 期望匹配的正则表达式。多个用 | 分隔。"""

    threshold: float = 0.6
    """识别置信度阈值。OCR 默认 0.6，Template 默认 0.75。"""

    # --- Template 参数 ---
    template: Optional[str] = None
    """模板图片名 (不含后缀，从 ui_templates/ 目录加载)。"""

    method: int = 5
    """cv2 匹配方法。默认 TM_CCOEFF_NORMED = 5。"""

    # --- Color Shift 参数 ---
    target_color: Optional[List[int]] = None
    """HSV 目标颜色 [H, S, V]。用于点赞红 / 收藏黄检测。"""

    color_range: Optional[List[int]] = None
    """HSV 容差范围 [dH, dS, dV]。"""

    # --- Screen Diff 参数 ---
    mse_threshold: float = 1.0
    """MSE 阈值。低于此值认为屏幕无变化 (卡死)。"""

    # --- And / Or 组合参数 ---
    all_of: Optional[List[dict]] = None
    """And 模式: 所有子识别规格列表。"""

    any_of: Optional[List[dict]] = None
    """Or 模式: 任一子识别规格列表。"""

    # --- YOLO 参数 ---
    model: Optional[str] = None
    """YOLO 模型路径。"""

    labels: Optional[List[str]] = None
    """期望检测的标签列表。"""

    yolo_class: Optional[str] = None
    """YOLO 目标类名 (如 'send_btn')。"""

    fallback_anchor: Optional[str] = None
    """主目标未检出时的兜底锚点类名。"""

    safe_offset: Optional[List[int]] = None
    """锚点偏移量 [dx, dy]。"""

    ocr_text: Optional[str] = None
    """检测框内用于二次校验的 OCR 预期文本。"""

    ocr_threshold: float = 0.6
    """识别置信度阈值。OCR 默认 0.6。"""

    # --- Custom 参数 ---
    handler: Optional[str] = None
    """自定义识别函数的 Python 路径 (module.function)。"""

    params: Optional[Dict[str, Any]] = None
    """传递给 handler 的额外参数。"""

    # --- Activity Detect 参数 ---
    # (复用 expected 字段作为包名/Activity 名匹配, handler 作为自定义检测函数)


# ============================================================
#  动作规格
# ============================================================

@dataclass
class ActionSpec:
    """
    动作配置规格。

    描述 Pipeline 节点"识别成功后执行什么操作"。
    不同的 type 使用不同的参数子集。
    """
    type: ActionType = ActionType.DO_NOTHING

    # --- Tap / DoubleTap ---
    target: Any = None
    """
    点击目标:
      - True: 点击识别结果坐标
      - [x, y]: 固定像素坐标
      - "{{anchor.xxx}}": Anchor 变量引用
    """

    offset: Optional[List[int]] = None
    """点击偏移 [dx, dy]。"""

    noise: int = 15
    """Fitts 定律噪声像素范围 (±noise)。"""

    # --- Safe Tap ---
    yolo_class: Optional[str] = None
    """YOLO 目标类名 (如 'send_btn')。"""
    
    fallback_anchor: Optional[str] = None
    """兜底锚点类名 (如 'input_area')。"""
    
    safe_offset: Optional[List[int]] = None
    """锚点偏移量 [dx, dy]。"""

    # --- Swipe / HumanSwipe ---
    direction: Optional[str] = None
    """滑动方向: up/down/left/right。"""

    distance: float = 0.5
    """滑动距离 (屏幕百分比)。"""

    begin: Optional[List[int]] = None
    """滑动起点 [x, y]。"""

    end: Optional[List[int]] = None
    """滑动终点 [x, y]。"""

    # --- InputText / ClipboardInput ---
    text: Optional[str] = None
    """输入文本。支持 {{anchor.xxx}} 和 {{context.xxx}} 变量引用。"""

    mode: str = "clipboard"
    """输入方式: clipboard (剪贴板) / vision (视觉键盘)。"""

    # --- LLM Generate ---
    prompt_template: Optional[str] = None
    """Prompt 模板名 (从 config 中查找)。"""

    output_anchor: Optional[str] = None
    """LLM 生成结果存入的 Anchor 键名。"""

    context_from: Optional[str] = None
    """LLM 上下文来源 (支持 Anchor 引用)。"""

    # --- Navigate ---
    target_page: Optional[str] = None
    """导航目标页: home/search/profile/back。"""

    # --- LaunchApp / StopApp ---
    package: Optional[str] = None
    """App 包名。默认 com.xingin.xhs。"""

    # --- Wait ---
    duration: Any = None
    """
    等待时长:
      - int: 固定毫秒数
      - [min, max]: 随机范围 (人性化)
      - "{{config.xxx}}": 配置引用
    """

    # --- ScreencapSave ---
    filename: Optional[str] = None
    """截图保存文件名。"""

    # --- Custom ---
    handler: Optional[str] = None
    """自定义动作函数的 Python 路径 (module.function)。"""

    params: Optional[Dict[str, Any]] = None
    """传递给 handler 的额外参数。"""

    # --- Fallback ---
    fallback_keyevent: Optional[int] = None
    """当主动作失败时的兜底按键 (keyevent code)。如 Enter=66。"""


# ============================================================
#  Pipeline 节点
# ============================================================

@dataclass
class PipelineNode:
    """
    Pipeline 有向图中的单个节点。

    参考 MaaFramework Pipeline Protocol:
    - 每个节点 = 识别条件 + 执行动作 + 流程路由
    - next: 成功后的候选节点列表 (首个命中的节点执行)
    - on_error: 超时或失败后的兜底节点
    - [JumpBack]xxx: 处理完后自动返回父节点的 next 列表
    """
    name: str

    # --- 核心三要素 ---
    recognition: RecognitionSpec = field(default_factory=RecognitionSpec)
    action: ActionSpec = field(default_factory=ActionSpec)

    # --- 流程控制 ---
    next: List[str] = field(default_factory=list)
    """成功后的候选节点名列表。带 [JumpBack] 前缀的节点会压栈。"""

    on_error: List[str] = field(default_factory=list)
    """超时或动作失败后的兜底节点名列表。"""

    timeout: int = 20000
    """next 列表扫描超时 (毫秒)。-1 = 无限等待。"""

    rate_limit: int = 1000
    """识别轮询间隔 (毫秒)。控制截屏频率。"""

    inverse: bool = False
    """反向匹配: True = 等待识别条件消失。"""

    enabled: bool = True
    """是否启用。False 则跳过。"""

    max_hit: int = 999999
    """最大命中次数。超过后不再匹配。"""

    # --- 时序控制 ---
    pre_delay: Any = None
    """
    动作前延迟 (毫秒):
      - int: 固定延迟
      - [min, max]: 随机范围 (人性化)
    """

    post_delay: Any = None
    """动作后延迟。同 pre_delay 格式。"""

    pre_wait_freezes: int = 0
    """动作前等待画面稳定的时间 (毫秒)。用于等待动画完成。"""

    post_wait_freezes: int = 0
    """动作后等待画面稳定的时间 (毫秒)。"""

    # --- 重复执行 ---
    repeat: int = 1
    """动作重复次数。"""

    repeat_delay: Any = None
    """重复间隔延迟 (毫秒)。"""

    # --- XHS 风控特色 ---
    probability: float = 1.0
    """执行概率 (0.0~1.0)。用于养号随机行为。"""

    quota_check: Optional[str] = None
    """配额检查项: daily_comments / daily_likes / daily_collects 等。"""

    # --- 元数据 ---
    description: str = ""
    """节点描述 (用于日志和报告)。"""

    tags: List[str] = field(default_factory=list)
    """标签 (用于过滤和分组)。"""

    # --- 运行时状态 (不序列化) ---
    _hit_count: int = field(default=0, repr=False)

    @property
    def hit_count(self) -> int:
        return self._hit_count

    def record_hit(self):
        self._hit_count += 1

    def can_hit(self) -> bool:
        return self.enabled and self._hit_count < self.max_hit

    def is_jumpback_ref(self, ref: str) -> bool:
        """检查节点引用是否是 JumpBack 引用。"""
        return ref.startswith("[JumpBack]")

    @staticmethod
    def parse_ref(ref: str) -> Tuple[str, bool]:
        """
        解析节点引用字符串。

        Returns:
            (node_name, is_jumpback)
        """
        if ref.startswith("[JumpBack]"):
            return ref[len("[JumpBack]"):], True
        return ref, False


# ============================================================
#  识别结果
# ============================================================

@dataclass
class RecognitionResult:
    """
    单次识别的结果。

    包含命中位置、置信度、文本等信息。
    通过 anchors 字段可向 AnchorStore 写入数据。
    """
    matched: bool = False
    """是否命中。"""

    position: Optional[Tuple[int, int]] = None
    """命中位置 (x, y) 像素坐标。"""

    box: Optional[List[int]] = None
    """命中区域 [x, y, w, h]。"""

    confidence: float = 0.0
    """匹配置信度 0.0~1.0。"""

    text: Optional[str] = None
    """识别到的文本 (OCR 场景)。"""

    anchors: Dict[str, Any] = field(default_factory=dict)
    """需要写入 AnchorStore 的键值对。"""

    raw: Any = None
    """原始识别数据 (调试用)。"""


# ============================================================
#  命中结果 (识别 + 节点 + JumpBack 元信息)
# ============================================================

@dataclass
class HitResult:
    """
    Pipeline 执行循环中的命中结果。

    将识别结果与节点元信息绑定。
    """
    node: PipelineNode
    """命中的节点。"""

    reco_result: Optional[RecognitionResult] = None
    """识别结果详情。"""

    is_jumpback: bool = False
    """是否通过 [JumpBack] 引用进入。"""


# ============================================================
#  Anchor 存储 (Pipeline 动态变量系统)
# ============================================================

class AnchorStore:
    """
    Pipeline 执行期间的动态变量存储。

    参考 MaaFramework 的 Anchor 机制:
    - 节点可以通过 RecognitionResult.anchors 写入数据
    - ActionSpec 中的 {{anchor.xxx}} 引用会被自动解析
    - 支持嵌套路径和数组索引: {{anchor.target_posts[0].title}}

    同时支持 {{context.xxx}} 引用外部注入的上下文 (如当前关键词)。
    还支持 {{config.xxx}} 引用配置项。
    """

    # 正则: 匹配 {{anchor.xxx}}, {{context.xxx}}, {{config.xxx}}
    _VAR_PATTERN = re.compile(r'\{\{(anchor|context|config)\.(.+?)\}\}')

    def __init__(self):
        self._anchors: Dict[str, Any] = {}
        self._context: Dict[str, Any] = {}
        self._config_resolver = None  # 延迟绑定配置解析器

    # --- 基础读写 ---

    def set(self, key: str, value: Any):
        """设置 Anchor 变量。"""
        self._anchors[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """获取 Anchor 变量。"""
        return self._anchors.get(key, default)

    def update(self, data: Dict[str, Any]):
        """批量更新 Anchor 变量。"""
        self._anchors.update(data)

    def clear(self):
        """清除所有 Anchor 变量 (保留 context)。"""
        self._anchors.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._anchors

    # --- Context (外部注入的只读上下文) ---

    def set_context(self, context: Dict[str, Any]):
        """设置外部上下文 (如当前关键词、账号信息)。"""
        self._context = context or {}

    def set_config_resolver(self, resolver):
        """设置配置解析回调: fn(dotted_path) -> value。"""
        self._config_resolver = resolver

    # --- 变量解析 ---

    def resolve(self, template: Any) -> Any:
        """
        解析模板中的变量引用。

        支持:
          - {{anchor.key}}
          - {{anchor.nested.key}}
          - {{anchor.list[0].field}}
          - {{context.current_keyword}}
          - {{config.intercept.comment_templates}}

        如果 template 不是字符串，直接返回原值。
        如果整个模板是单个变量引用，返回原始类型 (不转 str)。
        """
        if not isinstance(template, str):
            return template

        # 检查是否是单个完整引用 (返回原始类型)
        single_match = self._VAR_PATTERN.fullmatch(template)
        if single_match:
            return self._resolve_path(single_match.group(1), single_match.group(2))

        # 多引用或混合文本: 字符串替换
        def _replacer(match):
            value = self._resolve_path(match.group(1), match.group(2))
            return str(value) if value is not None else match.group(0)

        return self._VAR_PATTERN.sub(_replacer, template)

    def _resolve_path(self, scope: str, path: str) -> Any:
        """
        解析带路径的变量引用。

        scope: 'anchor' | 'context' | 'config'
        path: 'key' | 'key.subkey' | 'key[0].subkey'
        """
        if scope == "anchor":
            root = self._anchors
        elif scope == "context":
            root = self._context
        elif scope == "config":
            if self._config_resolver:
                return self._config_resolver(path)
            return None
        else:
            return None

        return self._traverse(root, path)

    @staticmethod
    def _traverse(obj: Any, path: str) -> Any:
        """
        遍历嵌套路径。支持 dict key、对象属性、数组索引。

        示例:
            _traverse({"a": [{"b": 1}]}, "a[0].b") → 1
        """
        parts = path.replace("]", "").replace("[", ".").split(".")
        current = obj

        for part in parts:
            if not part:
                continue
            if current is None:
                return None

            # 数组索引
            if part.isdigit():
                idx = int(part)
                if isinstance(current, (list, tuple)) and idx < len(current):
                    current = current[idx]
                else:
                    return None

            # Dict key
            elif isinstance(current, dict):
                current = current.get(part)

            # Object attribute
            elif hasattr(current, part):
                current = getattr(current, part)

            else:
                return None

        return current

    def __repr__(self) -> str:
        anchor_keys = list(self._anchors.keys())
        context_keys = list(self._context.keys())
        return f"AnchorStore(anchors={anchor_keys}, context={context_keys})"


# ============================================================
#  Pipeline 定义 (节点集合 + 元数据)
# ============================================================

@dataclass
class PipelineDefinition:
    """
    完整的 Pipeline 定义。

    由 Loader 从 YAML 文件解析生成。
    包含所有节点、默认值和元数据。
    """
    name: str
    """Pipeline 名称 (来自文件名)。"""

    nodes: Dict[str, PipelineNode] = field(default_factory=dict)
    """节点名 → 节点实例映射。"""

    defaults: Dict[str, Any] = field(default_factory=dict)
    """全局默认值。"""

    entry_node: Optional[str] = None
    """入口节点名。如果未指定，使用第一个非 JumpBack 节点。"""

    description: str = ""
    """Pipeline 描述。"""

    def get_node(self, name: str) -> Optional[PipelineNode]:
        """获取节点。自动处理 [JumpBack] 前缀。"""
        clean_name, _ = PipelineNode.parse_ref(name)
        return self.nodes.get(clean_name)

    def get_entry(self) -> str:
        """获取入口节点名。"""
        if self.entry_node:
            return self.entry_node
        # 默认: 第一个非 JumpBack 命名的节点
        for name in self.nodes:
            if not name.startswith("[JumpBack]"):
                return name
        raise ValueError(f"Pipeline '{self.name}' has no valid entry node.")

    def validate_refs(self) -> List[str]:
        """
        验证所有 next/on_error 引用的节点是否存在。

        Returns:
            错误消息列表 (空 = 验证通过)。
        """
        errors = []
        all_names = set(self.nodes.keys())

        for name, node in self.nodes.items():
            for ref in node.next + node.on_error:
                clean_ref, _ = PipelineNode.parse_ref(ref)
                if clean_ref not in all_names:
                    errors.append(
                        f"Node '{name}' references undefined node '{clean_ref}'"
                    )
        return errors
