"""
Stealth IME Client — 隐蔽输入法的 Python 控制端。

通过 ADB 广播与安装在 Android 设备上的 Stealth IME（伪装输入法）通信，
实现带有人类打字节奏模拟的文本输入。

核心特性：
- 逐字输入 + 随机字间延迟（50~250ms），模拟人类打字节奏
- Base64 编码通道，避免 ADB shell 特殊字符转义问题
- 自动安装和设置默认输入法
- 多设备群控支持（通过 serial 参数区分设备）

使用示例：
    from automation_engine.mobile_core.stealth_ime_client import StealthIMEClient

    client = StealthIMEClient(serial="emulator-5554")
    client.install_ime("path/to/stealth-ime.apk")
    client.type_text("你好，这是一条测试评论")
"""

import atexit
import base64
import random
import signal
import subprocess
import time
from .logger import get_logger

logger = get_logger("stealth_ime")

# ─────────── 伪装配置（与 Android 端 StealthReceiver.java 对应） ───────────
# 如果你修改了 Android 端的 Action 字符串，这里必须同步修改

IME_PACKAGE = "com.android.inputservice.core"
IME_SERVICE = f"{IME_PACKAGE}/.StealthIME"

ACTION_COMMIT  = "com.android.input.COMMIT"   # 纯文本输入
ACTION_SYNC    = "com.android.input.SYNC"      # Base64 编码文本
ACTION_EVENT   = "com.android.input.EVENT"     # KeyCode 事件
ACTION_CLEAR   = "com.android.input.CLEAR"     # 清除输入框
ACTION_REPLACE = "com.android.input.REPLACE"   # 替换输入框内容
ACTION_EDITOR  = "com.android.input.EDITOR"    # 编辑器动作

# Android KeyCode 常量
KEYCODE_ENTER = 66
KEYCODE_DEL = 67         # 退格键
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

    通过 ADB 广播与手机端的隐蔽输入法通信，
    所有通信使用伪装后的 Action 名称，融入 Android 系统的广播噪音中。
    """

    def __init__(self, serial=None, typing_delay_range=(0.05, 0.25)):
        """
        Args:
            serial: ADB 设备序列号。None 表示默认设备。
            typing_delay_range: 逐字输入时的字间延迟范围（秒），
                                默认 (0.05, 0.25) 模拟人类 50~250ms 的打字间隔。
        """
        self.serial = serial
        self.adb_prefix = ["adb"] if not serial else ["adb", "-s", serial]
        self.typing_delay_min = typing_delay_range[0]
        self.typing_delay_max = typing_delay_range[1]
        
        # 记录原始输入法
        self._original_ime = None
        # 防崩溃守护：确保进程退出时自动还原输入法
        self._atexit_registered = False
        self._original_sigint = None
        self._original_sigterm = None

    # ─────────── 核心输入方法 ───────────

    def type_text(self, text: str):
        """
        逐字输入文本，每个字符之间加入随机延迟，模拟人类打字节奏。

        这是推荐的输入方式 — 风控系统看到的是每隔 50~250ms 写入一个字符，
        与真人用键盘打字的节奏几乎一模一样。

        Args:
            text: 要输入的文本（支持中文、emoji 等任何 Unicode 字符）
        """
        if not text:
            return

        logger.info(f"Stealth IME typing {len(text)} chars with human delay...")

        for i, char in enumerate(text):
            # 非 ASCII 字符（中文、标点、emoji）以及空格等 shell 不安全字符
            # 通过 Base64 通道，避免 adb shell 转义吞字问题
            if ord(char) > 127 or char in ' \t\n\r"\'`$\\!(){}[]|&;<>':
                self._broadcast_b64(char)
            else:
                self._broadcast(ACTION_COMMIT, msg=char)

            # 最后一个字符不需要延迟
            if i < len(text) - 1:
                delay = random.uniform(self.typing_delay_min, self.typing_delay_max)

                # 偶尔出现"思考停顿"（模拟人类打字时偶尔的犹豫）
                if random.random() < 0.08:
                    delay += random.uniform(0.3, 0.8)

                time.sleep(delay)

        logger.info(f"Stealth IME typing complete: '{text[:20]}{'...' if len(text) > 20 else ''}'")

    def type_text_fast(self, text: str):
        """
        一次性输入整段文本（不模拟打字延迟）。

        适用于非敏感场景（如搜索框输入），或者当你确定目标 App
        不会监控输入速度时使用。

        Args:
            text: 要输入的文本
        """
        if not text:
            return

        logger.info(f"Stealth IME fast input: '{text[:30]}{'...' if len(text) > 30 else ''}'")

        # 对于包含特殊字符的文本，使用 Base64 通道避免 shell 转义问题
        if any(c in text for c in "'\"$`\\!(){}[]|&;<>"):
            self._broadcast_b64(text)
        else:
            self._broadcast(ACTION_COMMIT, msg=text)

    def clear_text(self):
        """清除当前输入框的所有文本。"""
        logger.info("Stealth IME clearing text")
        self._broadcast(ACTION_CLEAR)

    def set_text(self, text: str):
        """
        替换当前输入框的所有文本为指定内容。
        内部实现：先 selectAll + delete，再 commitText。

        Args:
            text: 要设置的新文本
        """
        logger.info(f"Stealth IME set text: '{text[:30]}{'...' if len(text) > 30 else ''}'")
        if any(c in text for c in "'\"$`\\!(){}[]|&;<>"):
            # 先清除，再用 B64 提交
            self._broadcast(ACTION_CLEAR)
            time.sleep(0.1)
            self._broadcast_b64(text)
        else:
            self._broadcast(ACTION_REPLACE, msg=text)

    def send_keycode(self, keycode: int):
        """
        发送 Android KeyCode 事件。

        常用 KeyCode:
            66 = ENTER (回车)
            67 = DEL (退格/删除)
            62 = SPACE (空格)

        Args:
            keycode: Android KeyEvent 的 keyCode 值
        """
        logger.info(f"Stealth IME sending keycode: {keycode}")
        self._broadcast(ACTION_EVENT, code=keycode)

    def send_editor_action(self, action_code: int):
        """
        发送编辑器动作（对应键盘上的"发送"/"搜索"/"完成"等按钮）。

        常用 action_code:
            2 = IME_ACTION_GO
            3 = IME_ACTION_SEARCH
            4 = IME_ACTION_SEND
            5 = IME_ACTION_NEXT
            6 = IME_ACTION_DONE

        Args:
            action_code: EditorInfo.IME_ACTION_* 的值
        """
        logger.info(f"Stealth IME editor action: {action_code}")
        self._broadcast(ACTION_EDITOR, code=action_code)

    def press_enter(self):
        """按下回车键。"""
        self.send_keycode(KEYCODE_ENTER)

    def press_delete(self, count: int = 1):
        """
        按下退格键删除字符。

        Args:
            count: 删除的字符数
        """
        for i in range(count):
            self.send_keycode(KEYCODE_DEL)
            if i < count - 1:
                time.sleep(random.uniform(0.03, 0.1))

    # ─────────── 安装与管理 ───────────

    def install_ime(self, apk_path: str) -> bool:
        """
        安装 Stealth IME APK 到设备，并自动启用和设为默认输入法。

        Args:
            apk_path: APK 文件的本地路径

        Returns:
            True 如果安装和设置成功
        """
        logger.info(f"Installing Stealth IME from: {apk_path}")

        # 1. 安装 APK
        result = subprocess.run(
            self.adb_prefix + ["install", "-r", apk_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0 or "Failure" in result.stdout:
            logger.error(f"APK install failed: {result.stdout} {result.stderr}")
            return False
        logger.info("APK installed successfully")

        # 2. 启用输入法
        result = subprocess.run(
            self.adb_prefix + ["shell", "ime", "enable", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        logger.info(f"IME enable: {result.stdout.strip()}")

        # 3. 设为默认输入法
        result = subprocess.run(
            self.adb_prefix + ["shell", "ime", "set", IME_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        logger.info(f"IME set default: {result.stdout.strip()}")

        return self.check_ime_status()

    def get_current_ime(self) -> str:
        """获取当前系统默认的输入法包名/类名。"""
        result = subprocess.run(
            self.adb_prefix + ["shell", "settings", "get", "secure", "default_input_method"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()

    def check_ime_installed(self) -> bool:
        """检查设备上是否已安装 Stealth IME 的包名"""
        result = subprocess.run(
            self.adb_prefix + ["shell", "pm", "list", "packages", IME_PACKAGE],
            capture_output=True, text=True, timeout=5
        )
        return IME_PACKAGE in result.stdout

    def check_ime_status(self) -> bool:
        """
        检查 Stealth IME 是否已设为默认输入法。

        Returns:
            True 如果当前默认输入法是 Stealth IME
        """
        current = self.get_current_ime()
        is_active = current == IME_SERVICE
        logger.info(f"IME status: current='{current}', stealth_active={is_active}")
        return is_active

    def ensure_ime_active(self) -> bool:
        """
        确保 Stealth IME 是当前默认输入法。如果不是，自动设置并记录原本的输入法。
        首次激活时自动注册进程级退出钩子，保证异常退出也能还原。

        Returns:
            True 如果已激活或成功激活
        """
        current = self.get_current_ime()
        if current == IME_SERVICE:
            # 即使已经激活，也要确保退出钩子已注册
            self._register_exit_hook()
            return True

        # 记录原本的输入法，以便后续恢复
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
        """
        注册进程级退出守护钩子。
        无论 Python 进程是正常退出、未捕获异常崩溃、还是被 Ctrl+C / kill 杀掉，
        都会在最后一刻尝试将手机输入法还原为用户原来的系统键盘。
        """
        if self._atexit_registered:
            return
        self._atexit_registered = True

        # 1. atexit 钩子 — 覆盖正常退出和未捕获异常
        atexit.register(self._exit_restore_ime)
        logger.info("[IME Guard] atexit hook registered.")

        # 2. 信号拦截 — 覆盖 Ctrl+C (SIGINT) 和 kill (SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        logger.info("[IME Guard] SIGINT/SIGTERM handlers registered.")

    def _exit_restore_ime(self):
        """atexit 回调：静默还原输入法。"""
        try:
            if self._original_ime:
                logger.info(f"[IME Guard] atexit: Restoring original IME: {self._original_ime}")
                subprocess.run(
                    self.adb_prefix + ["shell", "ime", "set", self._original_ime],
                    capture_output=True, text=True, timeout=5
                )
        except Exception as e:
            # 进程即将死亡，能做多少做多少，绝不抛异常
            try:
                logger.error(f"[IME Guard] atexit restore failed: {e}")
            except Exception:
                pass

    def _signal_handler(self, signum, frame):
        """信号处理器：在进程被杀之前紧急还原输入法，然后恢复原始信号行为。"""
        logger.warning(f"[IME Guard] Caught signal {signum}, restoring IME before exit...")
        self._exit_restore_ime()

        # 恢复原始信号处理器并重新发送信号，让 Python 执行默认行为（如打印 KeyboardInterrupt）
        if signum == signal.SIGINT and self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)
        elif signum == signal.SIGTERM and self._original_sigterm:
            signal.signal(signal.SIGTERM, self._original_sigterm)

        # 重新发送信号给自身，触发原始行为
        import os
        os.kill(os.getpid(), signum)

    def restore_ime(self) -> bool:
        """
        恢复原本的系统输入法。

        Returns:
            True 如果恢复成功或没有需要恢复的输入法
        """
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

    # ─────────── 内部方法 ───────────

    def _broadcast(self, action: str, msg: str = None, code: int = None):
        """
        通过 ADB 发送广播到 Stealth IME。

        Args:
            action: 伪装后的 Action 字符串
            msg: 文本参数 (--es msg "...")
            code: 整数参数 (--ei code N)
        """
        # 使用显式广播（-n 指定组件名），绕过 Android 8.0+ 隐式广播限制
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
        """
        通过 Base64 编码通道发送文本，避免 ADB shell 的特殊字符转义问题。

        Args:
            text: 要输入的原始文本
        """
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._broadcast(ACTION_SYNC, msg=encoded)
