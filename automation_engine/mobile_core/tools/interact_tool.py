from ..tool_registry import BaseTool, ToolParam, ToolResult

class TapTool(BaseTool):
    def __init__(self, driver):
        self.driver = driver

    def name(self) -> str:
        return "tap"

    def description(self) -> str:
        return "在屏幕指定坐标点击"

    def parameters(self) -> list:
        return [
            ToolParam("x", "integer", "X 坐标"),
            ToolParam("y", "integer", "Y 坐标"),
        ]

    def execute(self, params) -> ToolResult:
        self.driver.physical_tap(params["x"], params["y"])
        self.driver.human_sleep(1.0, 0.5)
        return ToolResult.ok()

class SwipeTool(BaseTool):
    def __init__(self, driver):
        self.driver = driver

    def name(self) -> str:
        return "swipe"

    def description(self) -> str:
        return "滑动屏幕。支持方向: up, down, left, right"

    def parameters(self) -> list:
        return [
            ToolParam("direction", "string", "滑动方向 (up/down/left/right)"),
        ]

    def execute(self, params) -> ToolResult:
        self.driver.human_swipe(params["direction"])
        self.driver.human_sleep(1.0, 0.5)
        return ToolResult.ok()

class SearchKeywordTool(BaseTool):
    def __init__(self, searcher):
        self.searcher = searcher

    def name(self) -> str:
        return "search_keyword"

    def description(self) -> str:
        return "在小红书中搜索指定关键词，返回帖子列表结果"

    def parameters(self) -> list:
        return [
            ToolParam("keyword", "string", "搜索关键词"),
        ]

    def execute(self, params) -> ToolResult:
        results = self.searcher.search_keyword(params["keyword"])
        return ToolResult.ok(results)

class ReadPostTool(BaseTool):
    def __init__(self, reader):
        self.reader = reader

    def name(self) -> str:
        return "read_post"

    def description(self) -> str:
        return "读取当前处于详情页的帖子内容（必须在 post_detail 页面使用）"

    def execute(self, params) -> ToolResult:
        post_data = self.reader.extract_current_post()
        return ToolResult.ok(post_data)

class FinishTool(BaseTool):
    def name(self) -> str:
        return "finish"

    def description(self) -> str:
        return "完成当前任务，退出 Agent 循环"

    def parameters(self) -> list:
        return [
            ToolParam("reason", "string", "完成任务的理由或总结"),
        ]

    def execute(self, params) -> ToolResult:
        return ToolResult.ok(params["reason"])
