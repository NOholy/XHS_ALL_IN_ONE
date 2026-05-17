from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

# Add the xhs-cli directory to sys.path so we can import its modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
XHS_CLI_PATH = PROJECT_ROOT / "xhs-cli"
if str(XHS_CLI_PATH) not in sys.path:
    sys.path.insert(0, str(XHS_CLI_PATH))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# camelCase → snake_case recursive converter
# ---------------------------------------------------------------------------
# xhs-cli extracts data from window.__INITIAL_STATE__ (Vue reactive state)
# which uses camelCase keys (noteCard, interactInfo, displayTitle, ...).
# The rest of the backend expects snake_case (note_card, interact_info, ...).
# By converting here — at the adapter boundary — all downstream normalization
# code stays clean and only needs to handle one naming convention.
# ---------------------------------------------------------------------------

_CAMEL_TO_SNAKE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Keys that should NOT be converted — they are natural camelCase identifiers
# used as values, not field names (e.g. xsec values, codec names).
_PASSTHROUGH_KEYS = frozenset()


def _camel_to_snake(name: str) -> str:
    """Convert a camelCase string to snake_case.

    Examples:
        noteCard      → note_card
        interactInfo  → interact_info
        displayTitle  → display_title
        likedCount    → liked_count
        xsecToken     → xsec_token
        imageList     → image_list
        userId        → user_id
        i18nCount     → i18n_count  (preserves digit boundaries)
    """
    return _CAMEL_TO_SNAKE_RE.sub("_", name).lower()


def _deep_snake_case(obj: Any, *, _depth: int = 0) -> Any:
    """Recursively convert all dict keys from camelCase to snake_case.

    Handles nested dicts, lists, and preserves non-dict/list values as-is.
    Depth-limited to prevent runaway recursion on circular references.
    """
    if _depth > 20:
        return obj
    if isinstance(obj, dict):
        return {
            _camel_to_snake(k): _deep_snake_case(v, _depth=_depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_deep_snake_case(item, _depth=_depth + 1) for item in obj]
    return obj


import concurrent.futures
import queue
import threading

_DAEMON_POOL: dict[str, XhsBrowserDaemon] = {}
_DAEMON_LOCK = threading.Lock()

class XhsBrowserDaemon:
    """A persistent background browser worker with a task queue."""
    def __init__(self, cookies: dict[str, str]):
        self.cookies = cookies
        self.task_queue = queue.Queue()
        self.thread = None
        self._start_thread()

    def _start_thread(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        from xhs_cli.client import XhsClient
        client = None
        try:
            client = XhsClient(self.cookies)
            client.start()
            while True:
                try:
                    # 10 minutes idle timeout to free up memory
                    item = self.task_queue.get(timeout=600)
                except queue.Empty:
                    logger.info("Browser daemon idle for 10 minutes, shutting down to save memory.")
                    break
                    
                if item is None:
                    break
                    
                task, future = item
                try:
                    result = task(client)
                    if not future.cancelled():
                        future.set_result(result)
                except Exception as e:
                    if not future.cancelled():
                        future.set_exception(e)
                        
                    # Auto-healing: If the underlying playwright browser crashed, break the loop.
                    # The thread will die and the next submit() will automatically respawn it.
                    err_str = str(e)
                    if "Target closed" in err_str or "Browser closed" in err_str or "Connection closed" in err_str:
                        logger.error("Fatal browser disconnection detected, killing daemon thread for auto-restart.")
                        break
                finally:
                    self.task_queue.task_done()
        except Exception as e:
            logger.error("Browser daemon crashed: %s", e)
            while not self.task_queue.empty():
                try:
                    task, future = self.task_queue.get_nowait()
                    if not future.cancelled():
                        future.set_exception(e)
                except queue.Empty:
                    break
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def submit(self, task) -> concurrent.futures.Future:
        if not self.thread or not self.thread.is_alive():
            logger.info("Restarting browser daemon")
            self._start_thread()
            
        f = concurrent.futures.Future()
        self.task_queue.put((task, f))
        return f

def get_browser_daemon(cookies_str: str, parsed_cookies: dict[str, str]) -> XhsBrowserDaemon:
    import hashlib
    cookie_hash = hashlib.md5(cookies_str.encode()).hexdigest()
    with _DAEMON_LOCK:
        if cookie_hash not in _DAEMON_POOL:
            _DAEMON_POOL[cookie_hash] = XhsBrowserDaemon(parsed_cookies)
        return _DAEMON_POOL[cookie_hash]


class CliXhsPcApiAdapter:
    """通过 xhs-cli 浏览器自动化实现的 PC 端读操作适配器。

    所有方法的签名和返回格式与 XhsPcApiAdapter 保持一致：
        返回 tuple[bool, str, Any]  →  (success, message, payload)

    关键设计：所有从 xhs-cli 返回的数据在离开适配器前都会经过
    _deep_snake_case() 递归转换，确保下游代码只需处理 snake_case 键名。
    
    采用常驻浏览器线程池 (XhsBrowserDaemon) 实现 0 秒冷启动和异步削峰。
    """

    def __init__(self, cookies: str) -> None:
        self.cookies_str = cookies
        self.cookie_dict = self._parse_cookies(cookies)

    @staticmethod
    def _parse_cookies(cookies_str: str) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for item in cookies_str.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    @staticmethod
    def _extract_user_id_from_url(user_url: str) -> str:
        """Extract user_id from a XHS profile URL."""
        match = re.search(r"/user/profile/([a-zA-Z0-9]+)", user_url)
        if match:
            return match.group(1)
        # Fallback: the input might already be a user_id
        stripped = user_url.strip().strip("/")
        if re.fullmatch(r"[a-zA-Z0-9]+", stripped):
            return stripped
        return ""

    # ===== 搜索笔记 =====

    def search_note(
        self,
        keyword: str,
        page: int = 1,
        sort_type_choice: int = 0,
        note_type: int = 0,
        note_time: int = 0,
        note_range: int = 0,
        pos_distance: int = 0,
        geo: str = "",
    ) -> tuple[bool, str, Any]:
        try:
            def _task(client):
                return client.search_notes(keyword)
                
            daemon = get_browser_daemon(self.cookies_str, self.cookie_dict)
            future = daemon.submit(_task)
            results = future.result(timeout=60)
            
            # Convert camelCase → snake_case at the boundary
            results = _deep_snake_case(results)
            payload = {
                "data": {
                    "items": results if isinstance(results, list) else [],
                    "has_more": False,
                    "page_size": 20,
                }
            }
            return True, "success", payload
        except Exception as e:
            logger.error("CliXhsPcApiAdapter.search_note failed: %s", e)
            return False, str(e), None

    # ===== 笔记详情 =====

    def get_note_info(self, url: str) -> tuple[bool, str, Any]:
        try:
            from xhs_cli.client import XhsClient

            note_id = XhsClient._extract_note_id_from_url(url)
            if not note_id:
                return False, "Invalid note URL", None

            # Extract xsec_token from URL if present
            xsec_token = ""
            token_match = re.search(r"xsec_token=([^&]+)", url)
            if token_match:
                xsec_token = token_match.group(1)

            def _task(client):
                return client.get_note_detail(note_id, xsec_token=xsec_token)
                
            daemon = get_browser_daemon(self.cookies_str, self.cookie_dict)
            future = daemon.submit(_task)
            detail = future.result(timeout=60)
            
            # Convert camelCase → snake_case at the boundary
            detail = _deep_snake_case(detail)
            payload = {"data": {"items": [detail]}}
            return True, "success", payload
        except Exception as e:
            logger.warning(
                "CliXhsPcApiAdapter.get_note_info CLI failed: %s — trying Direct API fallback",
                e,
            )
            return self._direct_api_fallback_note_info(url, cli_error=e)

    def _direct_api_fallback_note_info(
        self, url: str, cli_error: Exception
    ) -> tuple[bool, str, Any]:
        """Fall back to Direct API (with proper request signing) for note detail.

        The Direct API adapter calls edith.xiaohongshu.com/api/sns/web/v1/feed
        with generate_request_params / x-rap-param signing headers. It can
        often succeed even when xsec_token is empty because the signed request
        is structurally valid.
        """
        try:
            from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter

            return XhsPcApiAdapter(self.cookies_str).get_note_info(url)
        except Exception as fallback_err:
            logger.error(
                "Direct API fallback also failed: %s (original CLI error: %s)",
                fallback_err,
                cli_error,
            )
            return False, str(cli_error), None

    # ===== 笔记评论 =====

    def get_note_comments(self, note_url: str) -> tuple[bool, str, Any]:
        try:
            from xhs_cli.client import XhsClient

            note_id = XhsClient._extract_note_id_from_url(note_url)
            if not note_id:
                return False, "Invalid note URL", None

            xsec_token = ""
            token_match = re.search(r"xsec_token=([^&]+)", note_url)
            if token_match:
                xsec_token = token_match.group(1)

            def _task(client):
                # Request 1 scroll for manual UI browsing to ensure fast loading (<3s)
                client.get_note_detail(note_id, xsec_token=xsec_token)
                return client.scroll_and_read_comments(scroll_batches=1)
                
            daemon = get_browser_daemon(self.cookies_str, self.cookie_dict)
            future = daemon.submit(_task)
            comments = future.result(timeout=120)
            
            # Convert camelCase → snake_case at the boundary
            comments = _deep_snake_case(comments)
            payload = {
                "data": {
                    "comments": comments if isinstance(comments, list) else [],
                    "has_more": False,
                }
            }
            return True, "success", payload
        except Exception as e:
            logger.warning(
                "CliXhsPcApiAdapter.get_note_comments CLI failed: %s — trying Direct API fallback",
                e,
            )
            return self._direct_api_fallback_comments(note_url, cli_error=e)

    def _direct_api_fallback_comments(
        self, note_url: str, cli_error: Exception
    ) -> tuple[bool, str, Any]:
        """Fall back to Direct API for note comments."""
        try:
            from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter

            return XhsPcApiAdapter(self.cookies_str).get_note_comments(note_url)
        except Exception as fallback_err:
            logger.error(
                "Direct API comment fallback also failed: %s (original CLI error: %s)",
                fallback_err,
                cli_error,
            )
            return False, str(cli_error), None

    def post_comment(self, note_url: str, content: str) -> tuple[bool, str, Any]:
        try:
            from xhs_cli.client import XhsClient

            note_id = XhsClient._extract_note_id_from_url(note_url)
            if not note_id:
                return False, "Invalid note URL", None

            xsec_token = ""
            token_match = re.search(r"xsec_token=([^&]+)", note_url)
            if token_match:
                xsec_token = token_match.group(1)

            def _task(client):
                return client.post_comment(note_id, content, xsec_token=xsec_token)
                
            daemon = get_browser_daemon(self.cookies_str, self.cookie_dict)
            future = daemon.submit(_task)
            success = future.result(timeout=60)
            
            return success, "success" if success else "failed", None
        except Exception as e:
            logger.error("CliXhsPcApiAdapter.post_comment failed: %s", e)
            return False, str(e), None

    def reply_comment(self, note_url: str, comment_id: str, content: str) -> tuple[bool, str, Any]:
        try:
            from xhs_cli.client import XhsClient

            note_id = XhsClient._extract_note_id_from_url(note_url)
            if not note_id:
                return False, "Invalid note URL", None

            xsec_token = ""
            token_match = re.search(r"xsec_token=([^&]+)", note_url)
            if token_match:
                xsec_token = token_match.group(1)

            def _task(client):
                return client.reply_comment(note_id, comment_id, content, xsec_token=xsec_token)
                
            daemon = get_browser_daemon(self.cookies_str, self.cookie_dict)
            future = daemon.submit(_task)
            success = future.result(timeout=60)

            return success, "success" if success else "failed", None
        except Exception as e:
            logger.error("CliXhsPcApiAdapter.reply_comment failed: %s", e)
            return False, str(e), None

    # ===== 用户笔记列表 =====

    def get_user_notes(self, user_url: str) -> tuple[bool, str, Any]:
        try:
            from xhs_cli.client import XhsClient

            user_id = self._extract_user_id_from_url(user_url)
            if not user_id:
                return False, "Invalid user URL or user_id", None

            with XhsClient(self.cookie_dict) as client:
                notes = client.get_user_posts(user_id)
            # Convert camelCase → snake_case at the boundary
            notes = _deep_snake_case(notes)
            payload = {
                "data": {
                    "items": notes if isinstance(notes, list) else [],
                }
            }
            return True, "success", payload
        except Exception as e:
            logger.error("CliXhsPcApiAdapter.get_user_notes failed: %s", e)
            return False, str(e), None

    # ===== 账号自身信息 =====

    def get_self_info(self) -> Any:
        """与 XhsPcApiAdapter.get_self_info 保持一致的签名（非 tuple）。"""
        try:
            from xhs_cli.client import XhsClient

            with XhsClient(self.cookie_dict) as client:
                info = client.get_self_info()
            if not info or not isinstance(info, dict):
                raise RuntimeError("XHS self profile refresh failed")
            # Convert camelCase → snake_case at the boundary
            return _deep_snake_case(info)
        except Exception as e:
            logger.error("CliXhsPcApiAdapter.get_self_info failed: %s", e)
            raise RuntimeError(str(e)) from e
