# Mobile Device Farm Architecture (Android Automation)

本文档定义了将 XHS 自动化从 Web 迁移至 Android 移动端（真机/云手机）的部署标准与架构设计。

## 1. 架构选型 (Framework Selection)

基于 Python 后端开发生态，我们采用了 **`uiautomator2` + `opencv-python`** 的双轨混合架构。

### 为什么不选原生 Auto.js / Hamibot？
*   **语言割裂**：Auto.js 基于 JavaScript 运行在手机本地，而我们的大脑（LLM 意图识别与风控调度）是 Python 编写的后端服务。采用 Auto.js 需要额外开发一层中控 Server 和 WebSocket 协议，架构过重。
*   **集群能力**：`uiautomator2` 天生适合 PC/云端中控一拖多（群控）。我们可以用一台高配服务器，通过 TCP/IP ADB 直连几百台云手机，统一在 Python 层调度大模型，资源利用率最高。

### 为什么不选 Appium？
*   Appium 架构极其臃肿，中间经过了 Node.js 层的转发，响应慢，且环境配置复杂（需安装 Android SDK, JDK, Appium Server 等）。`uiautomator2` 直接将轻量级 RPC Server 推入手机，执行速度快一个数量级。

### 为什么引入 OpenCV？ (兜底策略)
*   小红书经常通过热更新下发布局变动，或者通过动态混淆把 UI 树的 resource-id 抹除。此时 `d(text="发送").click()` 可能会失效。
*   OpenCV 模板匹配作为最后的兜底：无论 UI 树怎么变，只要人眼能看到“发送”按钮，系统就能通过截取屏幕，使用 `cv2.matchTemplate()` 匹配按钮图片找到坐标。

## 2. 硬件与环境准备 (Environment Setup)

### 2.1 物理真机 / 手机墙
*   开启手机的 **开发者模式 (Developer Options)**。
*   开启 **USB 调试 (USB Debugging)** 和 **停用 adb 授权超时**。
*   如果使用 Wi-Fi 调试（推荐），需手机端执行 `adb tcpip 5555`，后用 `adb connect <ip>:5555` 连接。

### 2.2 云手机 (Cloud Phones - 推荐)
云手机（如红手指专业版、雷电云、双子星）天然提供 Root 权限和公网 ADB 接口，适合大规模矩阵部署。
*   购买并进入管理后台，获取分配的 ADB IP 与端口（例如：`101.32.4.1:10034`）。
*   连接命令：`adb connect 101.32.4.1:10034`。

### 2.3 隐藏自动化特征 (Anti-Detection)
*   **禁用日志**：小红书会读取系统层面的辅助服务和运行日志，云手机务必刷入 Magisk 模块，安装 `HideMyApplist` 或类似工具，针对小红书 App 屏蔽 `uiautomator` 进程的可见性。
*   **随机偏移**：脚本已在底层集成了 `_click_with_noise` 方法，所有的坐标点击强制带有 ±10px 的随机噪声。

## 3. 执行流程 (Execution Flow)

新版架构的工作流如下：

1.  **AI 大脑调度**：通过 `subprocess.run` 或 API 调用 `scripts/xhs_mobile_driver.py --device <ip:port> --action scan`。
2.  **获取数据**：驱动器连接真机/云手机，执行模拟人类的随机滚动（Swipe），解析 UI XML 层级，抽取出视口内可点击的帖子列表并 JSON 化返回。
3.  **大模型裁决**：与原流程一致，进行意图风控判定与话术生成。
4.  **物理注入**：驱动器调用 `action_reply`，在手机端唤起小红书键盘并发送拟真指令，完成截流后按返回键退出。
