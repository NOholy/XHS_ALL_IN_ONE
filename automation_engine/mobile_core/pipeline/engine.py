"""
Pipeline 执行引擎 — 核心调度器。

设计参考:
    - MaaFramework PipelineTask.cpp: run() + run_next() 双层循环
    - Airtest loop_find: 超时轮询 + 可配置间隔
    - XHS 特有: 概率门控、配额检查、人性化延迟

执行算法:
    1. 从入口节点开始，维护 next 候选列表
    2. 每轮截屏 → 遍历候选节点进行识别 → 首个命中执行动作
    3. 动作成功 → next = hit_node.next
    4. 动作失败或超时 → next = node.on_error
    5. JumpBack 节点完成子链后自动返回父节点
    6. 中间件(Watchdog/LoopDetector)在每轮截屏后自动执行
"""

import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mobile_core.logger import get_logger

from .models import (
    ActionSpec,
    AnchorStore,
    HitResult,
    PipelineDefinition,
    PipelineNode,
    RecognitionResult,
    RecognitionSpec,
)
from .mood_manager import MoodManager

logger = get_logger("pipeline_engine")


# ============================================================
#  中间件接口
# ============================================================

class PipelineMiddleware:
    """
    Pipeline 中间件基类。

    中间件在每轮识别循环中自动调用。
    用于: 弹窗检测(Watchdog)、卡死检测(LoopDetector)、操作日志等。
    """

    def on_screen_captured(self, screen, engine: 'PipelineExecutor') -> bool:
        """
        截屏后回调。

        Args:
            screen: numpy 截图
            engine: 引擎实例 (用于访问 driver 等)

        Returns:
            True = 本帧已被中间件消费 (需要重新截屏)
            False = 正常继续识别流程
        """
        return False

    def on_node_hit(self, node: PipelineNode, result: RecognitionResult,
                    engine: 'PipelineExecutor'):
        """节点命中时回调。用于日志记录。"""
        pass

    def on_action_executed(self, node: PipelineNode, success: bool,
                           engine: 'PipelineExecutor'):
        """动作执行后回调。用于日志记录。"""
        pass

    def on_error(self, node: PipelineNode, error: Exception,
                 engine: 'PipelineExecutor'):
        """错误发生时回调。"""
        pass


# ============================================================
#  执行统计
# ============================================================

@dataclass
class ExecutionStats:
    """Pipeline 单次执行的统计信息。"""
    total_nodes_hit: int = 0
    total_actions_executed: int = 0
    total_actions_failed: int = 0
    total_screenshots: int = 0
    total_errors: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    node_history: List[str] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    
    # Non-persisted fields for runtime passing
    last_screen: Any = field(default=None, repr=False)

    @property
    def duration_seconds(self) -> float:
        if self.end_time > 0:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def summary(self) -> dict:
        return {
            "duration_seconds": round(self.duration_seconds, 1),
            "nodes_hit": self.total_nodes_hit,
            "actions_executed": self.total_actions_executed,
            "actions_failed": self.total_actions_failed,
            "screenshots": self.total_screenshots,
            "errors": self.total_errors,
            "node_path": " → ".join(self.node_history[-20:]),
        }


# ============================================================
#  Pipeline 执行器
# ============================================================

class PipelineExecutor:
    """
    Pipeline DAG 执行器。

    核心执行循环参考 MaaFramework PipelineTask.cpp:
        run() 管理节点路由 + JumpBack 栈
        _recognize_loop() 管理截屏→识别轮询

    用法:
        executor = PipelineExecutor(driver, reco_registry, action_registry)
        executor.add_middleware(watchdog_middleware)
        stats = executor.run(pipeline_def, context={"current_keyword": "旅游"})
    """

    def __init__(self, driver, recognition_registry, action_registry,
                 config=None):
        """
        Args:
            driver: AgentlessMinitouchDriver 或兼容驱动
            recognition_registry: RecognitionRegistry 实例
            action_registry: ActionRegistry 实例
            config: EngineConfig (可选, 用于配额检查等)
        """
        self.driver = driver
        self.reco_registry = recognition_registry
        self.action_registry = action_registry
        self.config = config

        self.anchors = AnchorStore()
        self.middlewares: List[PipelineMiddleware] = []
        self.stats = ExecutionStats()

        self._should_stop = False
        self._jumpback_stack: List[Tuple[PipelineNode, List[str]]] = []

        # 配额追踪 (跨 Pipeline 持久化由外部管理)
        self._quota_counters: Dict[str, int] = {}
        
        # 情绪管理器
        self.mood_manager = MoodManager()
        self.anchors.set("_mood_manager", self.mood_manager)

    # --- 公开接口 ---

    def add_middleware(self, middleware: PipelineMiddleware):
        """添加中间件。"""
        self.middlewares.append(middleware)

    def stop(self):
        """请求停止执行。"""
        self._should_stop = True

    def run(self, pipeline: PipelineDefinition,
            context: Optional[Dict[str, Any]] = None,
            override: Optional[Dict[str, Any]] = None,
            entry: Optional[str] = None) -> ExecutionStats:
        """
        执行 Pipeline。

        Args:
            pipeline: PipelineDefinition 实例
            context: 外部上下文 (如 current_keyword, account_id)
            override: 运行时节点覆盖 (参考 MaaFramework pipeline_override)
            entry: 入口节点名 (默认用 pipeline.get_entry())

        Returns:
            ExecutionStats 执行统计
        """
        self._should_stop = False
        self.stats = ExecutionStats(start_time=time.time())
        self.anchors.clear()

        # 设置上下文
        if context:
            self.anchors.set_context(context)

        # 配置解析器
        if self.config:
            self.anchors.set_config_resolver(self._resolve_config_path)

        # 应用运行时覆盖
        if override:
            self._apply_override(pipeline, override)

        # 确定入口
        entry_name = entry or pipeline.get_entry()
        logger.info(f"Pipeline '{pipeline.name}' 从 '{entry_name}' 启动")

        try:
            self._execute_pipeline(pipeline, entry_name)
        except Exception as e:
            logger.critical(f"Pipeline 致命错误: {e}\n{traceback.format_exc()}")
            self.stats.total_errors += 1
            for mw in self.middlewares:
                mw.on_error(None, e, self)

        self.stats.end_time = time.time()
        summary = self.stats.summary()
        logger.info(f"Pipeline '{pipeline.name}' 完成: {summary}")

        return self.stats

    # --- 核心执行循环 ---

    def _execute_pipeline(self, pipeline: PipelineDefinition, entry_name: str):
        """
        主执行循环。

        参考 MaaFramework PipelineTask.cpp run() L19-106:
        维护 next 候选列表 + JumpBack 栈 + error_handling 标志。
        """
        self._jumpback_stack.clear()
        current_node: Optional[PipelineNode] = None
        next_candidates: List[str] = [entry_name]
        error_handling = False

        while next_candidates and not self._should_stop:
            # 识别循环: 截屏 → 扫描候选节点
            candidate_nodes = self._resolve_candidate_nodes(pipeline, next_candidates)

            if not candidate_nodes:
                logger.warning(f"No valid candidate nodes from: {next_candidates}")
                break

            # 计算超时 (取当前节点的 timeout，如果有的话)
            timeout_ms = (current_node.timeout if current_node else 20000)

            hit = self._recognize_loop(candidate_nodes, timeout_ms)

            if hit:
                error_handling = False
                node = hit.node
                node.record_hit()
                self.stats.total_nodes_hit += 1
                self.stats.node_history.append(node.name)
                
                # 记录 Timeline
                self.stats.timeline.append({
                    "timestamp": time.time(),
                    "node_name": node.name,
                    "reco_confidence": hit.reco_result.confidence if hit.reco_result else 0.0,
                    "reco_position": hit.reco_result.position if hit.reco_result else None,
                    "screen": self.stats.last_screen, # Will be handled by reporter
                })

                logger.info(
                    f"[HIT] {node.name} "
                    f"(conf={hit.reco_result.confidence if hit.reco_result else 0.0:.2f}, "
                    f"pos={hit.reco_result.position if hit.reco_result else None})"
                )

                # 通知中间件
                for mw in self.middlewares:
                    mw.on_node_hit(node, hit.reco_result, self)

                # 情绪推进
                self.mood_manager.update()

                # 概率门控: 结合基础概率和情绪倍率（仅对概率小于 1.0 的非必要交互节点生效）
                if node.probability < 1.0:
                    current_multiplier = self.mood_manager.get_multiplier()
                    adjusted_prob = min(1.0, node.probability * current_multiplier)
                else:
                    adjusted_prob = 1.0

                if random.random() > adjusted_prob:
                    logger.info(f"[跳过] {node.name} 概率门控触发 "
                                f"(基础 {node.probability:.0%}, 情绪 {self.mood_manager.get_state().name} -> {adjusted_prob:.0%})")
                    current_node = node
                    next_candidates = node.next
                    continue

                # 配额检查
                if node.quota_check and not self._check_quota(node.quota_check):
                    logger.warning(f"[配额] {node.quota_check} 已耗尽。停止执行。")
                    break

                # JumpBack 栈管理
                if hit.is_jumpback and current_node:
                    self._jumpback_stack.append(
                        (current_node, current_node.next)
                    )
                    logger.debug(f"[跳转返回] 压入返回点: {current_node.name}")

                # 写入识别结果的 Anchor
                if hit.reco_result and hit.reco_result.anchors:
                    self.anchors.update(hit.reco_result.anchors)

                # Pre-delay (人性化)
                self._apply_delay(node.pre_delay)

                # Pre-wait-freezes (等待画面稳定)
                if node.pre_wait_freezes > 0:
                    self._wait_for_screen_freeze(node.pre_wait_freezes)

                # 执行动作 (含重复)
                success = self._execute_action_with_repeat(node, hit.reco_result)

                # Post-wait-freezes
                if node.post_wait_freezes > 0:
                    self._wait_for_screen_freeze(node.post_wait_freezes)

                # Post-delay
                self._apply_delay(node.post_delay)

                # 通知中间件
                for mw in self.middlewares:
                    mw.on_action_executed(node, success, self)

                # 记录 Action 结果到 Timeline
                if self.stats.timeline:
                    self.stats.timeline[-1]["action_success"] = success

                # 路由到下一步
                if success:
                    current_node = node
                    next_candidates = node.next
                else:
                    logger.warning(f"[失败] 动作在 {node.name} 执行失败")
                    self.stats.total_actions_failed += 1
                    next_candidates = node.on_error
                    error_handling = True

            elif error_handling:
                # 错误恢复也超时了 → 停止
                logger.error("Error recovery timed out. Stopping pipeline.")
                next_candidates = []

            else:
                # 超时，无命中
                logger.warning(
                    f"[TIMEOUT] No match in candidates: "
                    f"{[n.name for n in candidate_nodes]} "
                    f"(timeout={timeout_ms}ms)"
                )
                error_handling = True
                if current_node:
                    next_candidates = current_node.on_error
                else:
                    next_candidates = []

            # JumpBack 返回: next 为空 + 非错误状态 + 栈非空
            if not next_candidates and not error_handling and self._jumpback_stack:
                parent_node, parent_next = self._jumpback_stack.pop()
                current_node = parent_node
                next_candidates = parent_next
                logger.info(f"[JUMPBACK] Returning to {parent_node.name}")

    def _recognize_loop(self, candidates: List[PipelineNode],
                        timeout_ms: int) -> Optional[HitResult]:
        """
        识别轮询循环。

        参考 MaaFramework PipelineTask.cpp run_next() L117-246:
        循环截屏 → 遍历候选节点识别 → 首个命中返回。

        Args:
            candidates: 候选 PipelineNode 列表
            timeout_ms: 超时毫秒数 (-1 = 无限)

        Returns:
            HitResult 或 None (超时)
        """
        start_time = time.time()
        rate_limit_s = max(
            c.rate_limit for c in candidates
        ) / 1000.0 if candidates else 1.0

        while not self._should_stop:
            # 1. 截屏
            screen = self.driver.screenshot()
            self.stats.total_screenshots += 1
            self.stats.last_screen = screen
            
            # 清理本帧的 OCR 缓存
            self.anchors.set("_ocr_cache", {})

            if screen is None:
                logger.warning("Screenshot returned None, device may be locked")
                time.sleep(1.0)
                continue

            # 2. 中间件处理
            middleware_consumed = False
            for mw in self.middlewares:
                if mw.on_screen_captured(screen, self):
                    middleware_consumed = True
                    break

            if middleware_consumed:
                continue  # 重新截屏

            # 3. 遍历候选节点进行识别
            for node in candidates:
                if not node.can_hit():
                    continue

                try:
                    result = self.reco_registry.recognize(
                        screen, node.recognition, self.anchors
                    )
                except Exception as e:
                    logger.error(f"Recognition error at {node.name}: {e}")
                    continue

                # 处理 inverse 逻辑
                if node.inverse:
                    if result is None or not result.matched:
                        # inverse + 未匹配 = 命中 (目标已消失)
                        result = RecognitionResult(
                            matched=True,
                            position=None,
                            confidence=1.0,
                        )
                    else:
                        continue  # inverse + 匹配 = 未命中 (目标仍存在)

                if result and result.matched:
                    # 检查是否通过 JumpBack 引用
                    is_jumpback = any(
                        PipelineNode.parse_ref(ref)[1]
                        for parent in candidates
                        for ref in (parent.next + parent.on_error)
                        if PipelineNode.parse_ref(ref)[0] == node.name
                    )

                    return HitResult(
                        node=node,
                        reco_result=result,
                        is_jumpback=is_jumpback,
                    )

            # 4. 超时检查
            if timeout_ms >= 0:
                elapsed_ms = (time.time() - start_time) * 1000
                if elapsed_ms >= timeout_ms:
                    return None

            # 5. Rate limit 节流
            time.sleep(rate_limit_s)

        return None  # stopped

    # --- 动作执行 ---

    def _execute_action_with_repeat(self, node: PipelineNode,
                                     reco_result: Optional[RecognitionResult]) -> bool:
        """执行动作 (含 repeat 重复)。"""
        success = True
        for i in range(node.repeat):
            try:
                ok = self.action_registry.execute(
                    node.action, reco_result, self.anchors
                )
                if not ok:
                    success = False
                    break
                self.stats.total_actions_executed += 1
            except Exception as e:
                logger.error(f"Action error at {node.name} (repeat {i+1}): {e}")
                self.stats.total_errors += 1
                for mw in self.middlewares:
                    mw.on_error(node, e, self)
                success = False
                break

            # Repeat delay
            if i < node.repeat - 1 and node.repeat_delay:
                self._apply_delay(node.repeat_delay)

        return success

    # --- 辅助方法 ---

    def _resolve_candidate_nodes(self, pipeline: PipelineDefinition,
                                  refs: List[str]) -> List[PipelineNode]:
        """将节点名引用列表解析为 PipelineNode 列表。"""
        nodes = []
        for ref in refs:
            clean_name, _ = PipelineNode.parse_ref(ref)
            node = pipeline.get_node(clean_name)
            if node:
                if node.enabled:
                    nodes.append(node)
                else:
                    logger.debug(f"Node '{clean_name}' is disabled, skipping")
            else:
                logger.warning(f"Reference to undefined node: '{clean_name}'")
        return nodes

    def _apply_delay(self, delay_spec: Any):
        """
        应用延迟。

        支持:
          - None: 无延迟
          - int: 固定毫秒
          - [min, max]: 随机范围毫秒
        """
        if delay_spec is None:
            return

        if isinstance(delay_spec, (list, tuple)) and len(delay_spec) == 2:
            ms = random.randint(int(delay_spec[0]), int(delay_spec[1]))
        else:
            ms = int(delay_spec)

        if ms > 0:
            if self.driver:
                sec = ms / 1000.0
                sigma = min(sec * 0.15, 1.0)
                self.driver.human_sleep(mu=sec, sigma=sigma)
            else:
                time.sleep(ms / 1000.0)

    def _wait_for_screen_freeze(self, duration_ms: int):
        """
        等待画面稳定 (参考 MaaFramework wait_freezes)。

        连续截屏比较 MSE，直到画面变化量低于阈值。
        """
        if duration_ms <= 0:
            return

        deadline = time.time() + duration_ms / 1000.0
        prev_screen = self.driver.screenshot()

        while time.time() < deadline and not self._should_stop:
            time.sleep(0.3)
            curr_screen = self.driver.screenshot()
            if prev_screen is not None and curr_screen is not None:
                # 简单 MSE 比较
                try:
                    import numpy as np
                    diff = np.mean((prev_screen.astype(float) - curr_screen.astype(float)) ** 2)
                    if diff < 5.0:  # 画面几乎不变
                        return
                except Exception:
                    pass
            prev_screen = curr_screen

    def _check_quota(self, quota_name: str) -> bool:
        """
        检查配额是否充足。

        通过 config.risk_control 获取上限。
        """
        if not self.config:
            return True

        limits = {
            "daily_comments": getattr(self.config.risk_control,
                                       'max_daily_comments', 10),
            "daily_likes": getattr(self.config.risk_control,
                                    'max_daily_likes', 30),
            "daily_collects": getattr(self.config.risk_control,
                                       'max_daily_collects', 15),
            "daily_follows": getattr(self.config.risk_control,
                                      'max_daily_follows', 5),
            "daily_searches": getattr(self.config.risk_control,
                                       'max_daily_searches', 20),
        }

        limit = limits.get(quota_name, 999999)
        current = self._quota_counters.get(quota_name, 0)

        if current >= limit:
            return False

        self._quota_counters[quota_name] = current + 1
        return True

    def _resolve_config_path(self, dotted_path: str) -> Any:
        """解析 config.xxx.yyy 路径。"""
        if not self.config:
            return None
        return AnchorStore._traverse(self.config, dotted_path)

    def _apply_override(self, pipeline: PipelineDefinition,
                         override: Dict[str, Any]):
        """
        应用运行时 pipeline_override。

        参考 MaaFramework 的多级覆盖机制。
        override 格式: {node_name: {field: value, ...}}
        """
        for node_name, overrides in override.items():
            node = pipeline.get_node(node_name)
            if not node:
                logger.warning(f"Override target node not found: {node_name}")
                continue

            for key, value in overrides.items():
                if key == "recognition" and isinstance(value, dict):
                    for rk, rv in value.items():
                        if hasattr(node.recognition, rk):
                            setattr(node.recognition, rk, rv)
                elif key == "action" and isinstance(value, dict):
                    for ak, av in value.items():
                        if hasattr(node.action, ak):
                            setattr(node.action, ak, av)
                elif hasattr(node, key):
                    setattr(node, key, value)

            logger.debug(f"Applied override to node '{node_name}': {overrides}")
