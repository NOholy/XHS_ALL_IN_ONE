from ..tool_registry import BaseTool, ToolResult

class GoHomeTool(BaseTool):
    def __init__(self, navigator):
        self.nav = navigator

    def name(self) -> str:
        return "go_home"

    def description(self) -> str:
        return "导航回到小红书首页推荐流"

    def execute(self, params) -> ToolResult:
        success = self.nav.go_home()
        return ToolResult.ok(success)

class DetectPageTool(BaseTool):
    def __init__(self, navigator):
        self.nav = navigator

    def name(self) -> str:
        return "detect_page"

    def description(self) -> str:
        return "检测当前处于小红书的哪个页面。返回值可能是 home_feed, search_results, post_detail, profile, search_page, comment_panel 等"

    def execute(self, params) -> ToolResult:
        page = self.nav.detect_current_page()
        return ToolResult.ok(page)
