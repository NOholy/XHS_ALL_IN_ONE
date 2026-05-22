# 小红书工业级自动化：真机初始化与风控对抗指南

本文档规定了 `XHS_ALL_IN_ONE` 项目中 Android 真实物理设备接入、初始化配置、底层参数优化以及深度风控对抗（Anti-Risk）的标准作业流程（SOP）。

## 1. 核心架构回顾 (V2 架构)

为了对抗小红书极严的风控系统，当前自动化框架已全面重构为**微服务解耦与纯物理注入**架构：
*   **OCR 微服务解耦**：移除脚本内置的重量级 OCR 引擎，改为独立部署 FastAPI 节点 (`ocr_server.py`)，大幅降低群控设备的内存占用。
*   **Agentless 零代理模式**：彻底废弃易被风控侦测的 `uiautomator2/ATX` 驻留代理，底层转为纯原生 ADB 与 `minitouch` 节点注入，实现物理级仿生操控。
*   **100% 纯视觉交互**：所有中文输入采用自建 `KeyboardVisionTyping`，通过模拟物理点击输入法按键+OCR识别候选词完成打字，完全绕过剪贴板监控与事件挂钩拦截。

---

## 2. 真机环境准备与初始化流程

### 2.1 依赖与服务启动
在执行任何设备操作前，必须先启动后端的 OCR 视觉中枢微服务：
```bash
# 激活虚拟环境
source venv/bin/activate

# 启动 OCR 节点（绑定于 8001 端口以防止与后端业务冲突）
python scripts/ocr_server.py
```

### 2.2 机器级底层优化 (Device Optimization)
为保证纯视觉 OpenCV 模版匹配的极高成功率与防休眠死锁，新接入的真机必须进行系统级动画剥离：
```bash
# 确保真机已通过 USB 连接且授权 (adb devices 可见)
python scripts/optimize_device.py
```
**该脚本会自动执行以下优化**：
1. 强制将 `window_animation_scale`、`transition_animation_scale`、`animator_duration_scale` 写入 `0.0`，彻底关闭 UI 过渡动画。
2. 锁定屏幕常亮（写入超时 30 分钟，并强制挂起 WakeLock）。

### 2.3 自动化 UI 元素采集 (Auto Cropper)
本系统依赖纯视觉交互，需要针对每种不同分辨率的机型自动截取坐标系内的按钮模板（如“回复”、“发送”按钮）：
```bash
# 在真机插线状态下运行，将全自动操控小红书进行切图保存
python scripts/auto_crop_templates.py
```
*执行完毕后，项目 `data/ui_templates/` 目录将生成真机原生分辨率的 UI 切片，供视觉引擎使用。如果暂无真机，可附加 `--mock` 参数生成测试用的彩色占位图。*

---

## 3. 终极风控对抗策略 (Anti-Fingerprint)

由于小红书的风控体系极为严密（基于设备指纹、网络 IP、UI 行为及 USB 状态），工业级机房部署必须严格落实以下对抗措施：

### 3.1 终极屏蔽检测与 Root 隐藏 (SOP)

小红书会通过 API 读取 `Settings.Global.ADB_ENABLED` 状态，检测 5555 端口以及检查 `su` 二进制文件，若发现处在调试模式或 Root 环境，会直接限流或封禁。
针对不同机型与系统版本，必须采取差异化的初始化方案，严禁盲目升级 Magisk 导致变砖（Bootloop）：

*   **方案 A（通用轻量级，无需 Root）**：
    执行 `adb tcpip 5555` 开启无线端口，然后**拔掉物理数据线**，采用局域网 WiFi 无线连接。最重要的一步是：进入系统设置**手动关闭**“开发者选项”的总开关。
    *(原理：TCP 仍保持连接状态，但系统 API 报关，适合所有无法获取 Root 的测试机)*

*   **方案 B（工业级现代机型，需 Android 10+ & Magisk v24+，推荐）**：
    为了实现工业级的安全隔离，我们要求在此类主流机型上强制落实基于底层 Hook 的防检测流水线：
    
    ##### 阶段一：获取系统底层的绝对控制权 (Root)
    1. **解锁 Bootloader**：这是前置必备要求（会清空所有数据）。
    2. **提取并修补引导镜像**：下载与当前设备系统版本**绝对一致**的官方刷机包，提取出 `boot.img`（或 `init_boot.img`）。放入手机并用 Magisk App 进行“修补”。
    3. **Fastboot 刷入**：将生成的 `magisk_patched.img` 拷回电脑，手机进入 Fastboot 模式，执行 `fastboot flash boot magisk_patched.img` 获取完整 Root 权限。*(注：此过程无需重装系统)*。
    
    ##### 阶段二：部署隐形伪装矩阵 (Anti-Fingerprint Modules)
    1. **系统孵化器注入 (Zygisk)**：在 Magisk 设置中开启 Zygisk 功能。
    2. **隐藏 Root 轨迹 (Shamiko)**：刷入 Shamiko 模块。利用内核 Mount Namespace 隔离技术，彻底对小红书隐蔽 Magisk 存在的痕迹。
    3. **API 拦截框架 (LSPosed)**：刷入 LSPosed 模块，为 Java 层提供 Hook 能力。
    4. **强效特征欺骗**：安装 **HideMyApplist**（阻断小红书对自动化相关 App 的扫描）和 **DevOptsHide**（拦截开发者选项 API，对小红书始终返回 `adb_enabled = 0`）。

*   **方案 C（老旧 Root 机型，Android 7-9 & Magisk < v24）**：
    早期三星（如 Note 9）等 System-As-Root 设备，**严禁强制跨版本升级至 Magisk v24+（极易导致无限重启卡 Logo 或丢失数据）**。
    应当维持现有老版本，在命令行执行 `adb shell su -c "magiskhide enable"` 开启内置的 MagiskHide 功能，并将小红书（com.xingin.xhs）加入黑名单。对于 ADB 隐藏，因旧版不支持 Zygisk+LSPosed，请务必结合使用 **方案 A** 的拔线法来掩盖调试状态。

> [!WARNING]
> 严禁在工业级生产环境使用任何“免 Root 双开沙箱”（如 VMOS、VirtualXposed）。此类容器具有极其明显的虚拟机指纹，不仅会被风控瞬间秒杀，还会导致长时间挂机时频繁卡死崩溃。

### 3.2 阻断 IP 连坐封禁 (IP Rotation)
群控农场（Device Farm）严禁多台设备共用同一局域网 WiFi 出口 IP，否则“一机封禁，全网连坐”。
*   **解决方案**：拔掉 WiFi 模块，全部改插 4G/5G 物联流量卡。
*   **动态换 IP 机制**：调用 `DeviceOptimizer.toggle_airplane_mode()` 接口。该接口通过免 Root 的状态栏下拉及 `svc data disable` 的方式，每隔几十分钟模拟一次飞行模式开关，强制基站分配全新的干净动态公网 IP。

### 3.3 漏斗养号模型 (Funnel Behavior)
代码中已实装 `100:30:10` 黄金比例行为漏斗 (`run_farm` 模式)。机器在执行核心评论或发帖前，必须带有 Fitts's 定律的拟人化噪声点击，并掺杂大比例的无效阅读（滑动、发呆），从而大幅度稀释账号的黑产特征数据包，将设备行为伪装成真实用户。
