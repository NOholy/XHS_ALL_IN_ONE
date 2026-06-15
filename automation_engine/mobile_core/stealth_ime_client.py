"""
Stealth IME Client — 隐蔽输入法的 Python 控制端。

V2: Socket-first 架构
通信通道优先级：
  1. LocalSocket (adb forward localabstract) — 零广播, 零进程创建, 最隐蔽
  2. ADB broadcast (fallback) — 仅在 Socket 不可用时降级使用

核心特性：
- 逐字输入 + 随机字间延迟（50~250ms），模拟人类打字节奏
- Base64 编码通道，避免 ADB shell 特殊字符转义问题
- 自动安装和设置默认输入法
- 多设备群控支持（通过 serial 参数区分设备）
"""

import atexit
import base64
import random
import signal
import socket
import subprocess
import time
from .logger import get_logger

logger = get_logger("stealth_ime")

# ─────────── 伪装配置（与 Android 端 StealthIME.java 对应） ───────────

IME_PACKAGE = "com.android.inputservice.settings"  # W3: 伪装为系统 settings provider
IME_SERVICE = f"{IME_PACKAGE}/.StealthIME"

# Socket 通信配置
SOCKET_NAME = "com.android.inputservice.settings.internal"  # 与 StealthIME.java 一致
SOCKET_BANNER = "STEALTH_IME"  # Banner 前缀用于验证连接

# 广播 Action (fallback only)
ACTION_COMMIT  = "com.android.input.COMMIT"
ACTION_SYNC    = "com.android.input.SYNC"
ACTION_EVENT   = "com.android.input.EVENT"
ACTION_CLEAR   = "com.android.input.CLEAR"
ACTION_REPLACE = "com.android.input.REPLACE"
ACTION_EDITOR  = "com.android.input.EDITOR"

# Android KeyCode 常量
KEYCODE_ENTER = 66
KEYCODE_DEL = 67
KEYCODE_FORWARD_DEL = 112
KEYCODE_SPACE = 62
KEYCODE_TAB = 61

# EditorInfo IME Action 常量
IME_ACTION_DONE = 6
IME_ACTION_GO = 2
IME_ACTION_NEXT = 5
IME_ACTION_SEARCH = 3
IME_ACTION_SEND = 4


class StealthIMEClient:
    """
    Stealth IME 的 Python 控制端客户端。

    V2: Socket-first — 通过 adb forward + LocalSocket 直连 IME 进程,
    所有文本操作都是 InputConnection 内部调用, 零广播, 零新进程。
    """

    def __init__(self, serial=None, typing_delay_range=(0.05, 0.25)):
        self.serial = serial
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]
        self.typing_delay_min = typing_delay_range[0]
        self.typing_delay_max = typing_delay_range[1]

        # Socket 连接状态
        self._socket = None
        self._socket_port = 0
        self._socket_available = False

        # 记录原始输入法
        self._original_ime = None
        self._atexit_registered = False
        self._original_sigint = None
        self._original_sigterm = None

    # ─────────── Socket 连接管理 ───────────

    def _ensure_socket(self) -> bool:
        """确保 Socket 连接可用。返回 True 表示连接成功。"""
        if self._socket_available and self._socket:
            try:
                # Ping 测试连接是否还活着
                self._socket.send(b"p\n")
                self._socket.settimeout(2.0)
                resp = self._socket.recv(64)
                if b"pong" in resp:
                    return True
            except Exception:
                self._cleanup_socket()

        return self._connect_socket()

    def _connect_socket(self) -> bool:
        """建立 Socket 连接到 Stealth IME。"""
        try:
            self._cleanup_socket()

            # 1. 找一个空闲的本地端口
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                self._socket_port = s.getsockname()[1]

            # 2. 建立 adb forward
            result = subprocess.run(
                self.adb_prefix + [
                    "forward",
                    f"tcp:{self._socket_port}",
                    f"localabstract:{SOCKET_NAME}"
                ],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                logger.debug(f"adb forward failed: {result.stderr}")
                return False

            # 3. 连接 TCP
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(3.0)
            self._socket.connect(("127.0.0.1", self._socket_port))

            # 4. 读取 banner
            banner = self._socket.recv(256).decode("utf-8", errors="ignore")
            if SOCKET_BANNER not in banner:
                logger.warning(f"Unexpected IME socket banner: {banner}")
                self._cleanup_socket()
                return False

            self._socket.settimeout(5.0)
            self._socket_available = True
            logger.info(f"Stealth IME socket connected (port {self._socket_port})")
            return True

        except Exception as e:
            logger.debug(f"Socket connection failed: {e}")
            self._cleanup_socket()
            return False

    def _cleanup_socket(self):
        """清理 Socket 资源。"""
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        self._socket = None
        self._socket_available = False
        if self._socket_port:
            try:
                subprocess.run(
                    self.adb_prefix + ["forward", "--remove", f"tcp:{self._socket_port}"],
                    capture_output=True, timeout=3
                )
            except Exception:
                pass

    def _socket_send(self, cmd: str):
        """通过 Socket 发送命令。"""
        if self._socket:
            try:
                self._socket.send((cmd + "\n").encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.warning("Socket broken, attempting reconnect...")
                self._socket_available = False
                if self._connect_socket():
                    self._socket.send((cmd + "\n").encode("utf-8"))

    # ─────────── 核心输入方法 ───────────

    def type_text(self, text: str):
        """
        逐字输入文本，每个字符之间加入随机延迟，模拟人类打字节奏。
        优先使用 Socket 通道（零广播），降级使用广播。
        """
        if not text:
            return

        logger.info(f"Stealth IME typing {len(text)} chars with human delay...")
        use_socket = self._ensure_socket()

        for i, char in enumerate(text):
            encoded = base64.b64encode(char.encode("utf-8")).decode("ascii")
            if use_socket:
                self._socket_send(f"t {encoded}")
            else:
                self._broadcast_b64(char)

            if i < len(text) - 1:
                delay = random.uniform(self.typing_delay_min, self.typing_delay_max)
                if random.random() < 0.08:
                    delay += random.uniform(0.3, 0.8)
                time.sleep(delay)

        logger.info(f"Stealth IME typing complete: '{text[:20]}{'...' if len(text) > 20 else ''}'")

    def type_text_fast(self, text: str):
        """一次性输入整段文本（不模拟打字延迟）。"""
        if not text:
            return

        logger.info(f"Stealth IME fast input: '{text[:30]}{'...' if len(text) > 30 else ''}'")

        if self._ensure_socket():
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            self._socket_send(f"t {encoded}")
        else:
            # Broadcast fallback
            if any(c in text for c in "'\"$`\\!(){}[]|&;<>"):
                self._broadcast_b64(text)
            else:
                self._broadcast(ACTION_COMMIT, msg=text)

    def clear_text(self):
        """清除当前输入框的所有文本。"""
        logger.info("Stealth IME clearing text")
        if self._ensure_socket():
            self._socket_send("c")
        else:
            self._broadcast(ACTION_CLEAR)

    def set_text(self, text: str):
        """替换当前输入框的所有文本为指定内容。"""
        logger.info(f"Stealth IME set text: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        if self._ensure_socket():
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            self._socket_send(f"r {encoded}")
        else:
            if any(c in text for c in "'\"$`\\!(){}[]|&;<>"):
                self._broadcast(ACTION_CLEAR)
                time.sleep(0.1)
                self._broadcast_b64(text)
            else:
                self._broadcast(ACTION_REPLACE, msg=text)

    def send_keycode(self, keycode: int):
        """发送 Android KeyCode 事件。"""
        logger.info(f"Stealth IME sending keycode: {keycode}")
        if self._ensure_socket():
            self._socket_send(f"k {keycode}")
        else:
            self._broadcast(ACTION_EVENT, code=keycode)

    def send_editor_action(self, action_code: int):
        """发送编辑器动作（搜索/发送/完成等）。"""
        logger.info(f"Stealth IME editor action: {action_code}")
        if self._ensure_socket():
            self._socket_send(f"e {action_code}")
        else:
            self._broadcast(ACTION_EDITOR, code=action_code)

    def press_enter(self):
        """按下回车键。"""
        self.send_keycode(KEYCODE_ENTER)

    def press_delete(self, count: int = 1):
        """按下退格键删除字符。"""
        for i in range(count):
            self.send_keycode(KEYCODE_DEL)
            if i < count - 1:
                time.sleep(random.uniform(0.03, 0.1))

    # ─────────── 安装与管理 ───────────

    def install_ime(self, apk_path: str) -> bool:
        """安装 Stealth IME APK 到设备。"""
        logger.info(f"Installing Stealth IME from: {apk_path}")

        result = subprocess.run(
            self.adb_prefix + ["install", "-r", apk_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0 or "Failure" in result.stdout:
            logger.error(f"APK install failed: {result.stdout} {result.stderr}")
            return False
        logger.info("APK installed successfully")

        result = subprocess.run(
            self.adb_prefix + ["shell", "ime", "enable", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        logger.info(f"IME enable: {result.stdout.strip()}")

        result = subprocess.run(
            self.adb_prefix + ["shell", "ime", "set", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        logger.info(f"IME set default: {result.stdout.strip()}")

        return self.check_ime_status()

    def get_current_ime(self) -> str:
        """获取当前系统默认的输入法。"""
        result = subprocess.run(
            self.adb_prefix + ["shell", "settings", "get", "secure", "default_input_method"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()

    def check_ime_installed(self) -> bool:
        """检查设备上是否已安装 Stealth IME。"""
        result = subprocess.run(
            self.adb_prefix + ["shell", "pm", "list", "packages", IME_PACKAGE],
            capture_output=True, text=True, timeout=5
        )
        return IME_PACKAGE in result.stdout

    def check_ime_status(self) -> bool:
        """检查 Stealth IME 是否已设为默认输入法。"""
        current = self.get_current_ime()
        is_active = current == IME_SERVICE
        logger.info(f"IME status: current='{current}', stealth_active={is_active}")
        return is_active

    def ensure_ime_active(self) -> bool:
        """确保 Stealth IME 是当前默认输入法。"""
        current = self.get_current_ime()
        if current == IME_SERVICE:
            self._register_exit_hook()
            return True

        if current and current != "null":
            self._original_ime = current
            logger.info(f"Original IME saved: {self._original_ime}")

        logger.warning("Stealth IME not active, attempting to activate...")
        subprocess.run(
            self.adb_prefix + ["shell", "ime", "enable", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        subprocess.run(
            self.adb_prefix + ["shell", "ime", "set", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )

        activated = self.check_ime_status()
        if activated:
            self._register_exit_hook()
        return activated

    def _register_exit_hook(self):
        """注册进程级退出守护钩子。"""
        if self._atexit_registered:
            return
        self._atexit_registered = True

        atexit.register(self._exit_cleanup)
        logger.info("[IME Guard] atexit hook registered.")

        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        logger.info("[IME Guard] SIGINT/SIGTERM handlers registered.")

    def _exit_cleanup(self):
        """atexit 回调：清理 Socket + 还原输入法。"""
        try:
            self._cleanup_socket()
        except Exception:
            pass
        try:
            if self._original_ime:
                logger.info(f"[IME Guard] atexit: Restoring original IME: {self._original_ime}")
                subprocess.run(
                    self.adb_prefix + ["shell", "ime", "set", self._original_ime],
                    capture_output=True, text=True, timeout=5
                )
        except Exception as e:
            try:
                logger.error(f"[IME Guard] atexit restore failed: {e}")
            except Exception:
                pass

    def _signal_handler(self, signum, frame):
        """信号处理器。"""
        logger.warning(f"[IME Guard] Caught signal {signum}, cleaning up...")
        self._exit_cleanup()

        if signum == signal.SIGINT and self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)
        elif signum == signal.SIGTERM and self._original_sigterm:
            signal.signal(signal.SIGTERM, self._original_sigterm)

        import os
        os.kill(os.getpid(), signum)

    def restore_ime(self) -> bool:
        """恢复原本的系统输入法。"""
        if not self._original_ime:
            logger.info("No original IME recorded, nothing to restore.")
            return True

        current = self.get_current_ime()
        if current == self._original_ime:
            return True

        logger.info(f"Restoring original IME: {self._original_ime}")
        result = subprocess.run(
            self.adb_prefix + ["shell", "ime", "set", self._original_ime],
            capture_output=True, text=True, timeout=10
        )

        success = self.get_current_ime() == self._original_ime
        if success:
            logger.info("Original IME restored successfully.")
        else:
            logger.warning(f"Failed to restore original IME. Output: {result.stdout}")

        return success

    # ─────────── Broadcast Fallback ───────────

    def _broadcast(self, action: str, msg: str = None, code: int = None):
        """ADB 广播 (fallback, 仅在 Socket 不可用时使用)。"""
        cmd = self.adb_prefix + [
            "shell", "am", "broadcast",
            "-n", f"{IME_PACKAGE}/.StealthReceiver",
            "-a", action
        ]
        if msg is not None:
            cmd += ["--es", "msg", msg]
        if code is not None:
            cmd += ["--ei", "code", str(code)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode != 0 or "Exception" in result.stderr:
                logger.warning(f"Broadcast failed: {result.stderr[:200]}")
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Broadcast timeout for action: {action}")
        except Exception as e:
            logger.error(f"Broadcast error: {e}")

    def _broadcast_b64(self, text: str):
        """Base64 编码广播 (fallback)。"""
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._broadcast(ACTION_SYNC, msg=encoded)

    def __del__(self):
        """Cleanup on garbage collection."""
        self._cleanup_socket()
