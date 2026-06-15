"""
Pipeline YAML Loader — 从 YAML 文件加载并验证 Pipeline 定义。

职责:
    1. 解析 YAML 文件为 PipelineDefinition 对象
    2. 将 defaults 区段合并到每个节点
    3. 验证 DAG 完整性 (引用存在性、孤儿节点、环检测、JumpBack 引用)
    4. 支持目录批量加载和运行时 override

YAML 格式示例:
    defaults:
      rate_limit: 1000
      timeout: 15000

    NodeName:
      recognition:
        type: ocr_text
        expected: "首页"
      action:
        type: tap
        target: true
      next: ["NodeB"]
      on_error: ["ErrorNode"]
      timeout: 10000

设计参考:
    - MaaFramework Pipeline JSON Loader
    - Airflow DAG validation patterns
"""

import copy
import os
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from mobile_core.logger import get_logger
from mobile_core.pipeline.models import (
    ActionSpec,
    ActionType,
    PipelineDefinition,
    PipelineNode,
    RecognitionSpec,
    RecognitionType,
)

logger = get_logger("pipeline.loader")

# ============================================================
#  保留关键字 — YAML 顶层中非节点的键
# ============================================================

_RESERVED_KEYS = frozenset({
    "defaults",
    "entry_node",
    "description",
    "pipeline_name",
    "nodes",
})

# ============================================================
#  节点级字段 — 直接映射到 PipelineNode 的属性
# ============================================================

_NODE_FIELDS = frozenset({
    "recognition",
    "action",
    "next",
    "on_error",
    "timeout",
    "rate_limit",
    "inverse",
    "enabled",
    "max_hit",
    "pre_delay",
    "post_delay",
    "pre_wait_freezes",
    "post_wait_freezes",
    "repeat",
    "repeat_delay",
    "probability",
    "quota_check",
    "description",
    "tags",
})

# 可以从 defaults 合并的数值型/简单字段
_DEFAULTABLE_FIELDS = frozenset({
    "timeout",
    "rate_limit",
    "pre_delay",
    "post_delay",
    "pre_wait_freezes",
    "post_wait_freezes",
    "repeat",
    "repeat_delay",
    "probability",
})


class PipelineLoadError(Exception):
    """Pipeline YAML 加载错误。"""
    pass


class PipelineValidationError(Exception):
    """Pipeline DAG 验证错误。"""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(
            f"Pipeline validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


class PipelineLoader:
    """
    Pipeline YAML 加载器。

    负责从 YAML 文件解析 Pipeline 定义，合并默认值，
    验证 DAG 引用完整性，并支持运行时参数覆盖。

    Usage:
        loader = PipelineLoader()
        definition = loader.load("pipelines/browse_feed.yaml")
        errors = loader.validate(definition)
        if errors:
            raise PipelineValidationError(errors)
    """

    def __init__(self, *, strict: bool = False):
        """
        初始化加载器。

        Args:
            strict: 严格模式。为 True 时，验证警告也视为错误。
        """
        self.strict = strict

    # ============================================================
    #  公开 API
    # ============================================================

    def load(self, yaml_path: str) -> PipelineDefinition:
        """
        从 YAML 文件加载 Pipeline 定义。

        解析流程:
            1. 读取并解析 YAML 文件
            2. 提取 defaults / 元数据
            3. 解析每个节点 (recognition + action + 流程控制)
            4. 合并 defaults 默认值
            5. 构建 PipelineDefinition 并验证

        Args:
            yaml_path: YAML 文件路径 (绝对路径或相对路径)。

        Returns:
            PipelineDefinition 实例。

        Raises:
            PipelineLoadError: YAML 解析失败或关键字段缺失。
            FileNotFoundError: 文件不存在。
        """
        path = Path(yaml_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Pipeline YAML not found: {path}")
        if not path.is_file():
            raise PipelineLoadError(f"Path is not a file: {path}")

        logger.info(f"Loading pipeline from: {path}")

        # --- 解析 YAML ---
        raw_data = self._read_yaml(path)
        if not isinstance(raw_data, dict):
            raise PipelineLoadError(
                f"Pipeline YAML root must be a mapping, got {type(raw_data).__name__}: {path}"
            )

        # --- 提取元数据 ---
        pipeline_name = raw_data.get("pipeline_name", path.stem)
        description = raw_data.get("description", "")
        entry_node = raw_data.get("entry_node", None)
        defaults = raw_data.get("defaults", {}) or {}

        if not isinstance(defaults, dict):
            raise PipelineLoadError(
                f"'defaults' must be a mapping, got {type(defaults).__name__}: {path}"
            )

        # --- 解析节点 ---
        # 支持两种格式:
        # 1. 扁平格式 (MaaFramework 风格): 节点直接作为顶层 key
        # 2. 嵌套格式: 所有节点在 "nodes:" key 下
        node_source = raw_data
        if "nodes" in raw_data and isinstance(raw_data["nodes"], dict):
            node_source = raw_data["nodes"]

        nodes: Dict[str, PipelineNode] = {}
        for key, value in node_source.items():
            if key in _RESERVED_KEYS:
                continue

            if not isinstance(value, dict):
                logger.warning(
                    f"Skipping non-mapping entry '{key}' in {path.name}"
                )
                continue

            node = self._parse_node(key, value, defaults)
            nodes[key] = node

        if not nodes:
            raise PipelineLoadError(f"No nodes found in pipeline: {path}")

        # --- 构建定义 ---
        definition = PipelineDefinition(
            name=pipeline_name,
            nodes=nodes,
            defaults=defaults,
            entry_node=entry_node,
            description=description,
        )

        # --- 验证引用完整性 ---
        errors = self.validate(definition)
        hard_errors = [e for e in errors if not e.startswith("WARNING:")]

        if hard_errors:
            for err in hard_errors:
                logger.error(f"Validation error: {err}")
            if self.strict:
                raise PipelineValidationError(hard_errors)

        # 警告也记录
        warnings = [e for e in errors if e.startswith("WARNING:")]
        for warn in warnings:
            logger.warning(warn)

        logger.info(
            f"Pipeline '{pipeline_name}' loaded: "
            f"{len(nodes)} nodes, entry='{definition.get_entry()}'"
        )

        return definition

    def load_dir(self, dir_path: str) -> Dict[str, PipelineDefinition]:
        """
        批量加载目录下所有 YAML Pipeline 文件。

        仅加载 .yaml 和 .yml 后缀的文件，跳过加载失败的文件并记录错误。

        Args:
            dir_path: 目录路径。

        Returns:
            {pipeline_name: PipelineDefinition} 映射。
        """
        directory = Path(dir_path).resolve()
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        pipelines: Dict[str, PipelineDefinition] = {}
        yaml_files = sorted(
            p for p in directory.iterdir()
            if p.is_file() and p.suffix in (".yaml", ".yml")
        )

        if not yaml_files:
            logger.warning(f"No YAML files found in {directory}")
            return pipelines

        logger.info(f"Loading {len(yaml_files)} pipeline(s) from {directory}")

        for yaml_file in yaml_files:
            try:
                definition = self.load(str(yaml_file))
                pipelines[definition.name] = definition
            except (PipelineLoadError, PipelineValidationError) as exc:
                logger.error(f"Failed to load {yaml_file.name}: {exc}")
            except Exception as exc:
                logger.error(
                    f"Unexpected error loading {yaml_file.name}: {exc}",
                    exc_info=True,
                )

        logger.info(f"Successfully loaded {len(pipelines)}/{len(yaml_files)} pipeline(s)")
        return pipelines

    def validate(self, definition: PipelineDefinition) -> List[str]:
        """
        验证 Pipeline 定义的完整性和正确性。

        检查项:
            1. 所有 next / on_error 引用指向存在的节点
            2. JumpBack 引用的节点存在
            3. 孤儿节点检测 (从入口不可达)
            4. 环检测 (DAG 不允许环，但 JumpBack 引用除外)

        Args:
            definition: 待验证的 Pipeline 定义。

        Returns:
            错误/警告消息列表。空列表表示验证通过。
            警告消息以 "WARNING:" 前缀标记。
        """
        issues: List[str] = []

        all_names = set(definition.nodes.keys())

        # --- 1) 引用存在性检查 ---
        issues.extend(self._check_references(definition, all_names))

        # --- 2) 入口节点检查 ---
        try:
            entry = definition.get_entry()
            if entry not in all_names:
                issues.append(
                    f"Entry node '{entry}' does not exist in pipeline '{definition.name}'"
                )
        except ValueError as exc:
            issues.append(str(exc))

        # --- 3) 孤儿节点检查 ---
        orphan_warnings = self._check_orphans(definition, all_names)
        issues.extend(orphan_warnings)

        # --- 4) 环检测 ---
        cycle_errors = self._check_cycles(definition, all_names)
        issues.extend(cycle_errors)

        return issues

    def apply_override(
        self,
        definition: PipelineDefinition,
        override: dict,
    ) -> PipelineDefinition:
        """
        应用运行时 Pipeline 覆盖参数。

        允许在不修改 YAML 文件的情况下调整节点参数。
        常用于 A/B 测试、调试、不同账号差异化配置。

        Args:
            definition: 原始 Pipeline 定义。
            override: 覆盖字典，格式:
                {
                    "defaults": {"timeout": 30000},
                    "NodeName": {"timeout": 5000, "enabled": False},
                    "NodeName.recognition": {"threshold": 0.8},
                    "NodeName.action": {"noise": 20},
                }

        Returns:
            新的 PipelineDefinition 实例 (深拷贝，不修改原始对象)。
        """
        # 深拷贝避免修改原始定义
        new_def = copy.deepcopy(definition)

        if not override:
            return new_def

        # --- 应用 defaults 覆盖 ---
        if "defaults" in override:
            new_def.defaults.update(override["defaults"])

        # --- 应用节点级覆盖 ---
        for key, value in override.items():
            if key == "defaults":
                continue

            if not isinstance(value, dict):
                logger.warning(f"Override for '{key}' is not a dict, skipping")
                continue

            # 处理 "NodeName.recognition" / "NodeName.action" 格式
            if "." in key:
                node_name, sub_section = key.rsplit(".", 1)
                node = new_def.nodes.get(node_name)
                if node is None:
                    logger.warning(
                        f"Override references unknown node '{node_name}', skipping"
                    )
                    continue

                if sub_section == "recognition":
                    self._apply_dict_to_dataclass(node.recognition, value)
                elif sub_section == "action":
                    self._apply_dict_to_dataclass(node.action, value)
                else:
                    logger.warning(
                        f"Unknown sub-section '{sub_section}' in override key '{key}'"
                    )
            else:
                # 直接节点覆盖
                node = new_def.nodes.get(key)
                if node is None:
                    logger.warning(
                        f"Override references unknown node '{key}', skipping"
                    )
                    continue
                self._apply_dict_to_dataclass(node, value)

        logger.info(
            f"Applied {len(override)} override(s) to pipeline '{new_def.name}'"
        )
        return new_def

    # ============================================================
    #  内部解析方法
    # ============================================================

    def _read_yaml(self, path: Path) -> Any:
        """
        安全读取 YAML 文件。

        使用 yaml.safe_load 防止任意代码执行。

        Args:
            path: YAML 文件路径。

        Returns:
            解析后的 Python 对象。

        Raises:
            PipelineLoadError: YAML 语法错误。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                raise PipelineLoadError(f"Empty YAML file: {path}")
            return data
        except yaml.YAMLError as exc:
            # 提取 YAML 错误中的行号信息
            error_detail = str(exc)
            if hasattr(exc, "problem_mark") and exc.problem_mark:
                mark = exc.problem_mark
                error_detail = (
                    f"line {mark.line + 1}, column {mark.column + 1}: "
                    f"{getattr(exc, 'problem', 'unknown error')}"
                )
            raise PipelineLoadError(
                f"YAML parse error in {path.name}: {error_detail}"
            ) from exc

    def _parse_node(
        self,
        name: str,
        raw: dict,
        defaults: dict,
    ) -> PipelineNode:
        """
        将 YAML 原始字典解析为 PipelineNode。

        解析流程:
            1. 合并 defaults 默认值
            2. 解析 recognition 子区段
            3. 解析 action 子区段
            4. 映射其余字段到 PipelineNode 属性

        Args:
            name: 节点名称。
            raw: YAML 中该节点的原始字典。
            defaults: 全局默认值字典。

        Returns:
            PipelineNode 实例。
        """
        # 合并 defaults
        merged = self._apply_defaults(raw, defaults)

        # 解析 recognition
        recognition = RecognitionSpec()
        if "recognition" in merged:
            reco_raw = merged["recognition"]
            if isinstance(reco_raw, dict):
                recognition = self._parse_recognition(reco_raw)
            else:
                logger.warning(
                    f"Node '{name}': 'recognition' should be a dict, "
                    f"got {type(reco_raw).__name__}, using defaults"
                )

        # 解析 action
        action = ActionSpec()
        if "action" in merged:
            action_raw = merged["action"]
            if isinstance(action_raw, dict):
                action = self._parse_action(action_raw)
            else:
                logger.warning(
                    f"Node '{name}': 'action' should be a dict, "
                    f"got {type(action_raw).__name__}, using defaults"
                )

        # 解析 next / on_error — 统一为列表
        next_refs = self._ensure_list(merged.get("next", []))
        on_error_refs = self._ensure_list(merged.get("on_error", []))

        # 构建节点
        node = PipelineNode(
            name=name,
            recognition=recognition,
            action=action,
            next=next_refs,
            on_error=on_error_refs,
            timeout=int(merged.get("timeout", 20000)),
            rate_limit=int(merged.get("rate_limit", 1000)),
            inverse=bool(merged.get("inverse", False)),
            enabled=bool(merged.get("enabled", True)),
            max_hit=int(merged.get("max_hit", 999999)),
            pre_delay=merged.get("pre_delay"),
            post_delay=merged.get("post_delay"),
            pre_wait_freezes=int(merged.get("pre_wait_freezes", 0)),
            post_wait_freezes=int(merged.get("post_wait_freezes", 0)),
            repeat=int(merged.get("repeat", 1)),
            repeat_delay=merged.get("repeat_delay"),
            probability=float(merged.get("probability", 1.0)),
            quota_check=merged.get("quota_check"),
            description=str(merged.get("description", "")),
            tags=self._ensure_list(merged.get("tags", [])),
        )

        # 检查未知字段
        known_keys = _NODE_FIELDS | {"recognition", "action", "next", "on_error"}
        unknown = set(merged.keys()) - known_keys
        if unknown:
            logger.debug(
                f"Node '{name}': ignoring unknown fields: {unknown}"
            )

        return node

    def _parse_recognition(self, raw: dict) -> RecognitionSpec:
        """
        解析 recognition 配置区段。

        将 YAML 字典转换为 RecognitionSpec 数据类实例。
        type 字段从字符串映射到 RecognitionType 枚举。

        Args:
            raw: recognition 区段的原始字典。

        Returns:
            RecognitionSpec 实例。
        """
        spec = RecognitionSpec()

        # 解析 type
        if "type" in raw:
            spec.type = self._parse_enum(
                raw["type"], RecognitionType, "recognition.type"
            )

        # 通用参数
        if "roi" in raw:
            spec.roi = self._as_number_list(raw["roi"], "recognition.roi")
        if "roi_offset" in raw:
            spec.roi_offset = self._as_int_list(raw["roi_offset"], "recognition.roi_offset")

        # OCR 参数
        if "expected" in raw:
            spec.expected = str(raw["expected"])
        if "threshold" in raw:
            spec.threshold = float(raw["threshold"])

        # Template 参数
        if "template" in raw:
            spec.template = str(raw["template"])
        if "method" in raw:
            spec.method = int(raw["method"])

        # Color Shift 参数
        if "target_color" in raw:
            spec.target_color = self._as_int_list(raw["target_color"], "recognition.target_color")
        if "color_range" in raw:
            spec.color_range = self._as_int_list(raw["color_range"], "recognition.color_range")

        # Screen Diff 参数
        if "mse_threshold" in raw:
            spec.mse_threshold = float(raw["mse_threshold"])

        # And / Or 组合
        if "all_of" in raw:
            spec.all_of = raw["all_of"] if isinstance(raw["all_of"], list) else [raw["all_of"]]
        if "any_of" in raw:
            spec.any_of = raw["any_of"] if isinstance(raw["any_of"], list) else [raw["any_of"]]

        # YOLO 参数
        if "model" in raw:
            spec.model = str(raw["model"])
        if "labels" in raw:
            spec.labels = self._ensure_list(raw["labels"])

        # Custom 参数
        if "handler" in raw:
            spec.handler = str(raw["handler"])
        if "params" in raw:
            spec.params = raw["params"] if isinstance(raw["params"], dict) else None

        return spec

    def _parse_action(self, raw: dict) -> ActionSpec:
        """
        解析 action 配置区段。

        将 YAML 字典转换为 ActionSpec 数据类实例。
        type 字段从字符串映射到 ActionType 枚举。

        Args:
            raw: action 区段的原始字典。

        Returns:
            ActionSpec 实例。
        """
        spec = ActionSpec()

        # 解析 type
        if "type" in raw:
            spec.type = self._parse_enum(raw["type"], ActionType, "action.type")

        # Tap / DoubleTap
        if "target" in raw:
            spec.target = raw["target"]  # 保留原始类型 (bool / list / str)
        if "offset" in raw:
            spec.offset = self._as_int_list(raw["offset"], "action.offset")
        if "noise" in raw:
            spec.noise = int(raw["noise"])

        # Swipe / HumanSwipe
        if "direction" in raw:
            spec.direction = str(raw["direction"])
        if "distance" in raw:
            spec.distance = float(raw["distance"])
        if "begin" in raw:
            spec.begin = self._as_int_list(raw["begin"], "action.begin")
        if "end" in raw:
            spec.end = self._as_int_list(raw["end"], "action.end")

        # InputText / ClipboardInput
        if "text" in raw:
            spec.text = str(raw["text"])
        if "mode" in raw:
            spec.mode = str(raw["mode"])

        # LLM Generate
        if "prompt_template" in raw:
            spec.prompt_template = str(raw["prompt_template"])
        if "output_anchor" in raw:
            spec.output_anchor = str(raw["output_anchor"])
        if "context_from" in raw:
            spec.context_from = str(raw["context_from"])

        # Navigate
        if "target_page" in raw:
            spec.target_page = str(raw["target_page"])

        # LaunchApp / StopApp
        if "package" in raw:
            spec.package = str(raw["package"])

        # Wait
        if "duration" in raw:
            spec.duration = raw["duration"]  # 保留原始类型 (int / list / str)

        # ScreencapSave
        if "filename" in raw:
            spec.filename = str(raw["filename"])

        # Custom
        if "handler" in raw:
            spec.handler = str(raw["handler"])
        if "params" in raw:
            spec.params = raw["params"] if isinstance(raw["params"], dict) else None

        # Fallback
        if "fallback_keyevent" in raw:
            spec.fallback_keyevent = int(raw["fallback_keyevent"])

        return spec

    def _apply_defaults(self, node_data: dict, defaults: dict) -> dict:
        """
        将 defaults 区段的默认值合并到节点数据中。

        合并策略:
            - 仅合并 _DEFAULTABLE_FIELDS 中定义的字段
            - 节点级值优先于 defaults (不会被覆盖)
            - 使用浅拷贝避免修改原始 dict

        Args:
            node_data: 节点原始数据字典。
            defaults: 全局默认值字典。

        Returns:
            合并后的节点数据字典 (新对象)。
        """
        if not defaults:
            return dict(node_data)

        merged = {}

        # 先填入 defaults 中可合并的字段
        for field_name in _DEFAULTABLE_FIELDS:
            if field_name in defaults:
                merged[field_name] = defaults[field_name]

        # 节点级数据覆盖 defaults
        merged.update(node_data)

        return merged

    # ============================================================
    #  验证辅助方法
    # ============================================================

    def _check_references(
        self,
        definition: PipelineDefinition,
        all_names: Set[str],
    ) -> List[str]:
        """
        检查所有 next / on_error 引用是否指向存在的节点。

        同时验证 [JumpBack] 前缀引用的目标节点存在性。

        Args:
            definition: Pipeline 定义。
            all_names: 所有节点名的集合。

        Returns:
            错误消息列表。
        """
        errors: List[str] = []

        for name, node in definition.nodes.items():
            # 检查 next 引用
            for ref in node.next:
                clean_ref, is_jumpback = PipelineNode.parse_ref(ref)
                if clean_ref not in all_names:
                    prefix = "[JumpBack] " if is_jumpback else ""
                    errors.append(
                        f"Node '{name}': next references undefined "
                        f"{prefix}node '{clean_ref}'"
                    )

            # 检查 on_error 引用
            for ref in node.on_error:
                clean_ref, is_jumpback = PipelineNode.parse_ref(ref)
                if clean_ref not in all_names:
                    prefix = "[JumpBack] " if is_jumpback else ""
                    errors.append(
                        f"Node '{name}': on_error references undefined "
                        f"{prefix}node '{clean_ref}'"
                    )

        return errors

    def _check_orphans(
        self,
        definition: PipelineDefinition,
        all_names: Set[str],
    ) -> List[str]:
        """
        检测孤儿节点 — 从入口节点不可达的节点。

        使用 BFS 从入口节点遍历所有可达节点。
        不可达的节点产生 WARNING。

        Args:
            definition: Pipeline 定义。
            all_names: 所有节点名的集合。

        Returns:
            警告消息列表 (以 "WARNING:" 前缀)。
        """
        warnings: List[str] = []

        try:
            entry = definition.get_entry()
        except ValueError:
            # 无有效入口，无法检查孤儿
            return warnings

        if entry not in all_names:
            return warnings

        # BFS 可达性分析
        reachable: Set[str] = set()
        queue: deque[str] = deque([entry])

        while queue:
            current = queue.popleft()
            if current in reachable:
                continue
            reachable.add(current)

            node = definition.nodes.get(current)
            if node is None:
                continue

            # 遍历 next + on_error 引用
            for ref in node.next + node.on_error:
                clean_ref, _ = PipelineNode.parse_ref(ref)
                if clean_ref not in reachable and clean_ref in all_names:
                    queue.append(clean_ref)

        # 孤儿 = 全部节点 - 可达节点
        orphans = all_names - reachable
        for orphan in sorted(orphans):
            warnings.append(
                f"WARNING: Node '{orphan}' is unreachable from "
                f"entry node '{entry}' in pipeline '{definition.name}'"
            )

        return warnings

    def _check_cycles(
        self,
        definition: PipelineDefinition,
        all_names: Set[str],
    ) -> List[str]:
        """
        检测 Pipeline DAG 中的环路。

        使用 DFS 三色标记法 (WHITE/GRAY/BLACK) 检测环。
        [JumpBack] 引用被视为特殊回边，不计入环检测
        (因为 JumpBack 是有意设计的中断-返回模式)。

        Args:
            definition: Pipeline 定义。
            all_names: 所有节点名的集合。

        Returns:
            错误消息列表。
        """
        errors: List[str] = []

        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {name: WHITE for name in all_names}
        # 记录 DFS 路径用于报告环路径
        path: List[str] = []

        def _dfs(node_name: str) -> bool:
            """DFS 遍历，返回 True 表示发现环。"""
            color[node_name] = GRAY
            path.append(node_name)

            node = definition.nodes.get(node_name)
            if node is not None:
                # 遍历所有非 JumpBack 的 next 引用
                for ref in node.next:
                    clean_ref, is_jumpback = PipelineNode.parse_ref(ref)
                    if is_jumpback:
                        continue  # JumpBack 是有意的回边，跳过
                    if clean_ref not in all_names:
                        continue  # 引用不存在的节点已在别处报告

                    if color[clean_ref] == GRAY:
                        # 找到环: 从 path 中提取环路径
                        cycle_start = path.index(clean_ref)
                        cycle_path = path[cycle_start:] + [clean_ref]
                        cycle_str = " → ".join(cycle_path)
                        errors.append(
                            f"Cycle detected in pipeline '{definition.name}': "
                            f"{cycle_str}"
                        )
                        return True
                    elif color[clean_ref] == WHITE:
                        if _dfs(clean_ref):
                            return True

                # on_error 引用也检查环 (但通常不构成主路径环)
                for ref in node.on_error:
                    clean_ref, is_jumpback = PipelineNode.parse_ref(ref)
                    if is_jumpback:
                        continue
                    if clean_ref not in all_names:
                        continue

                    if color[clean_ref] == GRAY:
                        cycle_start = path.index(clean_ref)
                        cycle_path = path[cycle_start:] + [clean_ref]
                        cycle_str = " → ".join(cycle_path)
                        errors.append(
                            f"Cycle detected via on_error in pipeline "
                            f"'{definition.name}': {cycle_str}"
                        )
                        return True
                    elif color[clean_ref] == WHITE:
                        if _dfs(clean_ref):
                            return True

            path.pop()
            color[node_name] = BLACK
            return False

        # 从每个未访问节点启动 DFS
        for name in all_names:
            if color[name] == WHITE:
                _dfs(name)

        return errors

    # ============================================================
    #  工具方法
    # ============================================================

    @staticmethod
    def _parse_enum(value: Any, enum_cls: type, field_hint: str) -> Any:
        """
        将字符串值解析为枚举成员。

        大小写不敏感，支持带/不带枚举名前缀。

        Args:
            value: 原始值 (字符串)。
            enum_cls: 目标枚举类。
            field_hint: 字段提示 (用于错误消息)。

        Returns:
            枚举成员。

        Raises:
            PipelineLoadError: 无法匹配任何枚举值。
        """
        if isinstance(value, enum_cls):
            return value

        str_value = str(value).strip().lower()

        # 尝试直接按 value 匹配
        for member in enum_cls:
            if member.value == str_value:
                return member

        # 尝试按 name 匹配 (大小写不敏感)
        for member in enum_cls:
            if member.name.lower() == str_value:
                return member

        valid_values = [m.value for m in enum_cls]
        raise PipelineLoadError(
            f"Invalid {field_hint} value '{value}'. "
            f"Valid values: {valid_values}"
        )

    @staticmethod
    def _ensure_list(value: Any) -> list:
        """
        确保值是列表。

        字符串和单个值会被包装为单元素列表。
        None 返回空列表。

        Args:
            value: 原始值。

        Returns:
            列表。
        """
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) if not isinstance(v, str) else v for v in value]
        return [str(value)]

    @staticmethod
    def _as_number_list(value: Any, field_hint: str) -> List[float]:
        """
        将值转换为浮点数列表 (支持 ROI 等参数)。

        Args:
            value: 原始值 (列表或其他)。
            field_hint: 字段提示。

        Returns:
            浮点数列表。
        """
        if isinstance(value, (list, tuple)):
            try:
                return [float(v) for v in value]
            except (ValueError, TypeError) as exc:
                raise PipelineLoadError(
                    f"Invalid number list for {field_hint}: {value}"
                ) from exc
        raise PipelineLoadError(
            f"{field_hint} must be a list, got {type(value).__name__}: {value}"
        )

    @staticmethod
    def _as_int_list(value: Any, field_hint: str) -> List[int]:
        """
        将值转换为整数列表。

        Args:
            value: 原始值。
            field_hint: 字段提示。

        Returns:
            整数列表。
        """
        if isinstance(value, (list, tuple)):
            try:
                return [int(v) for v in value]
            except (ValueError, TypeError) as exc:
                raise PipelineLoadError(
                    f"Invalid int list for {field_hint}: {value}"
                ) from exc
        raise PipelineLoadError(
            f"{field_hint} must be a list, got {type(value).__name__}: {value}"
        )

    @staticmethod
    def _apply_dict_to_dataclass(instance: Any, overrides: dict) -> None:
        """
        将字典中的值应用到 dataclass 实例的对应属性上。

        仅修改 dataclass 中已有的属性，忽略未知键。

        Args:
            instance: dataclass 实例。
            overrides: 要覆盖的键值对字典。
        """
        for key, value in overrides.items():
            if hasattr(instance, key) and not key.startswith("_"):
                setattr(instance, key, value)
