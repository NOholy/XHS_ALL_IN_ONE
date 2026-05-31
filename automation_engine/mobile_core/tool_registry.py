from dataclasses import dataclass
from typing import Any, Dict, List

@dataclass
class ToolParam:
    name: str
    type: str  # "string", "integer", "number", "boolean"
    description: str
    required: bool = True

@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""

    @staticmethod
    def ok(data=None):
        return ToolResult(success=True, data=data)

    @staticmethod
    def fail(error: str):
        return ToolResult(success=False, error=error)


class BaseTool:
    """借鉴 ApkClaw 的 BaseTool 抽象"""

    def name(self) -> str:
        raise NotImplementedError

    def description(self) -> str:
        raise NotImplementedError

    def parameters(self) -> List[ToolParam]:
        return []

    def execute(self, params: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI function calling 格式"""
        properties = {}
        required = []
        for p in self.parameters():
            properties[p.name] = {"type": p.type, "description": p.description}
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }


class ToolRegistry:
    """借鉴 ApkClaw 的 ToolRegistry 单例"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name()] = tool

    def execute(self, name: str, params: dict) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.fail(f"Unknown tool: {name}")
        try:
            return tool.execute(params)
        except Exception as e:
            return ToolResult.fail(str(e))

    def get_all_schemas(self) -> list:
        return [t.to_openai_schema() for t in self._tools.values()]
