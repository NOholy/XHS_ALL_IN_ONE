# Stealth IME — 隐蔽输入法

基于 [ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard) 架构重写的隐蔽 Android 输入法，用于自动化文本输入。

## 伪装特征

| 项目 | 值 |
|---|---|
| 包名 | `com.android.inputservice.core` |
| 应用名 | `System Input Service` |
| 图标 | Material Design 标准键盘图标 |
| Release 混淆 | ✅ ProGuard R8 |
| Log 剥离 | ✅ Release 包自动移除所有 Log 调用 |

## 编译

### 前提条件
- Android Studio (任意版本) 或 命令行 Gradle
- Android SDK, 需要 API Level 33 (compileSdk)
- JDK 8+

### 命令行编译
```bash
cd automation_engine/tools/stealth_ime

# 使用 Gradle Wrapper（推荐，如果已初始化）
./gradlew assembleRelease

# 或使用系统 Gradle
gradle assembleRelease
```

产物路径：`app/build/outputs/apk/release/app-release-unsigned.apk`

> **注意**：Release 包需要签名后才能安装。你可以使用 Android Studio 的 Build > Generate Signed Bundle 生成签名包，或者直接用 Debug 包测试：
> ```bash
> ./gradlew assembleDebug
> ```
> Debug 包路径：`app/build/outputs/apk/debug/app-debug.apk`

## 安装到设备

```bash
# 安装 APK
adb install app/build/outputs/apk/debug/app-debug.apk

# 启用输入法
adb shell ime enable com.android.inputservice.core/.StealthIME

# 设为默认输入法
adb shell ime set com.android.inputservice.core/.StealthIME
```

## 验证

```bash
# 检查当前默认输入法
adb shell settings get secure default_input_method
# 期望输出：com.android.inputservice.core/.StealthIME

# 测试文本输入（先点击某个 App 的输入框使其获得焦点）
# 注意：Android 8.0+ 必须使用显式广播（加 -n 组件名），否则广播将被系统拦截
adb shell am broadcast -n com.android.inputservice.core/.StealthReceiver -a com.android.input.COMMIT --es msg "你好世界"

# 测试清除
adb shell am broadcast -n com.android.inputservice.core/.StealthReceiver -a com.android.input.CLEAR

# 测试回车键 (KEYCODE_ENTER=66)
adb shell am broadcast -n com.android.inputservice.core/.StealthReceiver -a com.android.input.EVENT --ei code 66

# 测试 Base64 编码输入（避免 shell 特殊字符转义）
echo -n "测试文本" | base64
adb shell am broadcast -n com.android.inputservice.core/.StealthReceiver -a com.android.input.SYNC --es msg "5rWL6K+V5paH5pys"
```

## 广播 Action 映射

| Action | 参数 | 用途 |
|---|---|---|
| `com.android.input.COMMIT` | `--es msg "文本"` | 输入纯文本 |
| `com.android.input.SYNC` | `--es msg "base64"` | 输入 Base64 编码文本 |
| `com.android.input.EVENT` | `--ei code 66` | 发送 KeyCode |
| `com.android.input.CLEAR` | 无参数 | 清除输入框 |
| `com.android.input.REPLACE` | `--es msg "文本"` | 替换输入框全部内容 |
| `com.android.input.EDITOR` | `--ei code 4` | 执行编辑器动作 |

> **注意：所有广播必须带上 `-n com.android.inputservice.core/.StealthReceiver`。**

## Python 集成

安装完成后，在 Python 代码中使用：

```python
from automation_engine.mobile_core.stealth_ime_client import StealthIMEClient

client = StealthIMEClient(serial="your_device_serial")

# 逐字输入（带人类打字延迟，推荐！）
client.type_text("你好，这是一条测试评论")

# 清除
client.clear_text()

# 发送回车
client.send_keycode(66)
```

## 风控防护 (Risk Control)

此输入法在设计上专门针对大厂的自动化风控系统（如小红书、抖音）进行了多维度加固：

1. **包名与标识安全**：去除了原版所有的 `adbkeyboard` 字样。使用 `com.android.inputservice.core` 包名伪装系统组件。
2. **显式广播隔离**：采用显式广播投递机制。显式广播只有目标组件能收到，有效防止第三方 App 监听和拦截。
3. **人类打字节奏模拟**（通过 Python 端实现）：抛弃了原版一次性塞入大段文本导致的高危操作特征。所有字符均有 50~250ms 的随机延迟，且有小概率触发"思考停顿"（额外 300~800ms），API 表现与真实人类毫无二致。
4. **特殊字符安全**：采用 Base64 通道处理非 ASCII 字符与 Shell 转义字符，避免 `adb shell` 导致的吞字截断。
5. **代码指纹混淆**：自带 ProGuard 规则，强制剔除所有 Logcat 调试输出信息，防止被逆向特征匹配。

## License

基于 ADBKeyBoard (GPL-2.0) 修改。
