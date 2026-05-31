import json
import time
import requests
from .tool_registry import ToolRegistry
from .loop_detector import LoopDetector
from .logger import get_logger

logger = get_logger("agent_loop")


class AgentLoop:
    """
    借鉴 ApkClaw DefaultAgentService 的 Agent 循环。
    保留现有 automation_engine 的 Minitouch 执行层和 Watchdog 安全层，
    将决策层从硬编码状态机替换为 LLM Function Calling。
    """

    MAX_API_RETRIES = 3

    def __init__(self, tool_registry: ToolRegistry, config):
        self.registry = tool_registry
        self.config = config
        self.loop_detector = LoopDetector()

    def run(self, task_prompt: str) -> str:
        """执行 Agent 循环，返回最终结果"""
        max_iter = self.config.agent.max_iterations
        if not self.config.agent.enabled:
            logger.error("Agent mode is disabled in config.")
            return "Agent mode disabled"

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": task_prompt},
        ]

        for iteration in range(1, max_iter + 1):
            logger.info(f"Agent iteration {iteration}/{max_iter}")

            # 上下文压缩
            if iteration > 5:
                self._compress_history(messages)

            # LLM 调用（带重试）
            try:
                response = self._chat_with_retry(messages)
            except Exception as e:
                logger.error(f"Agent loop failed due to API error: {e}")
                return f"Error: {e}"

            # 无工具调用 = 任务完成
            if not response.get("tool_calls"):
                content = response.get("content", "任务完成 (No tool calls)")
                logger.info(f"Agent finished: {content}")
                return content

            # 执行工具调用
            ai_msg = {"role": "assistant", "content": response.get("content"),
                      "tool_calls": response["tool_calls"]}
            messages.append(ai_msg)

            for tool_call in response["tool_calls"]:
                fn = tool_call["function"]
                tool_name = fn["name"]
                try:
                    tool_args = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info(f"  Tool: {tool_name}({tool_args})")
                result = self.registry.execute(tool_name, tool_args)

                # 记录指纹（死循环检测）
                self.loop_detector.record_action(tool_name, fn["arguments"])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps({"success": result.success, "data": result.data, "error": result.error}, ensure_ascii=False),
                })

                # finish 工具 = 任务完成
                if tool_name == "finish" and result.success:
                    return result.data or "任务完成"

            # 死循环检测
            if self.loop_detector.is_stuck():
                logger.warning("Dead loop detected!")
                messages.append({
                    "role": "user",
                    "content": ("[系统] 检测到你连续执行了相同的操作且屏幕没有变化。"
                                "请尝试按返回键退出、滑动页面、或调用 finish 说明原因。")
                })
                self.loop_detector.clear()

        logger.warning(f"Agent reached max iterations ({max_iter}).")
        return "达到最大迭代次数，任务中止"

    def _build_system_prompt(self) -> str:
        tools_desc = "\n".join(
            f"- {s['function']['name']}: {s['function']['description']}"
            for s in self.registry.get_all_schemas()
        )
        return f"""你是一个小红书自动化运营 Agent。你通过工具来操控一台 Android 真机。

可用工具:
{tools_desc}

规则:
1. 每次操作前先用 detect_page 了解当前状态
2. 使用 tap/swipe 进行物理操作
3. 遇到弹窗，底层的 watchdog 会自动尝试关闭，如果页面状态变化说明发生了弹窗处理
4. 搜索、进入帖子阅读、点赞/评论可以通过提供的专用工具完成
5. 完成任务后调用 finish
"""

    def _chat_with_retry(self, messages):
        cfg = self.config.agent
        for attempt in range(self.MAX_API_RETRIES):
            try:
                resp = requests.post(
                    cfg.llm_endpoint,
                    headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
                    json={
                        "model": cfg.llm_model,
                        "messages": messages,
                        "tools": self.registry.get_all_schemas(),
                        "temperature": 0.1,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]
            except Exception as e:
                if "401" in str(e) or "403" in str(e):
                    raise
                delay = (2 ** attempt)
                logger.warning(f"LLM call failed (attempt {attempt+1}), retrying in {delay}s: {e}")
                time.sleep(delay)
        raise RuntimeError("LLM API call failed after all retries")

    def _compress_history(self, messages):
        """上下文压缩：将旧的工具结果替换为摘要"""
        KEEP_RECENT = 6  # 保留最近 6 条消息的完整内容
        for i, msg in enumerate(messages):
            if i >= len(messages) - KEEP_RECENT:
                break
            if msg.get("role") == "tool" and len(msg.get("content", "")) > 200:
                try:
                    data = json.loads(msg["content"])
                    msg["content"] = json.dumps({
                        "success": data.get("success"),
                        "summary": str(data.get("data", ""))[:80] + "...[已压缩]"
                    }, ensure_ascii=False)
                except Exception:
                    msg["content"] = msg["content"][:100] + "...[已压缩]"
