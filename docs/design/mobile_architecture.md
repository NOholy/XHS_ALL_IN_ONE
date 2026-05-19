# Mobile Device Farm Architecture (Android Automation) — v3.0

本文档定义了将 XHS 自动化从 Web 迁移至 Android 移动端（真机/云手机）的部署标准与架构设计。

> **v3.0 更新 (2026-05-20)**：完成"去 Accessibility 化"重构，引入 PP-OCRv5 视觉引擎，彻底消除对 UI Tree 的依赖。

## 1. 架构选型 (Framework Selection)

基于 Python 后端开发生态，我们采用了 **`uiautomator2` + `opencv-python` + `PaddleOCR (PP-OCRv5)`** 的三轨混合架构。

### 技术栈角色分工

| 组件 | 角色 | 使用范围 |
|---|---|---|
| **uiautomator2** | 底层连接与物理触控注入 (`touch.down/move/up`, `press`, `screenshot`) | 设备连接、截屏、物理手势注入 |
| **OpenCV** | 视觉模板匹配（按钮定位、键盘定位） | `_find_template()`, `_opencv_physical_typing()` |
| **PaddleOCR** | 全屏文字识别（帖子/评论抓取、发送验证、模板采集） | `action_extract()`, `action_reply()` 验证, `auto_crop_templates.py` |

### ⚠️ 严禁使用的 API

以下 uiautomator2 API **已从代码中完全剔除**，任何新功能开发中也绝对禁止使用：

```python
# 🚫 全部禁止 — 会触发 Accessibility Service Dump，暴露自动化指纹
d(text="xxx")
d(textContains="xxx")
d(textMatches="xxx")
d(className="xxx")
d(resourceId="xxx")
d(resourceIdMatches="xxx")
d.xpath("//xxx")
element.click()       # UIA2 的 API 级点击
element.info           # 读取 UI 节点属性
```

### 为什么不选原生 Auto.js / Hamibot？
*   **语言割裂**：Auto.js 基于 JavaScript 运行在手机本地，而我们的大脑（LLM 意图识别与风控调度）是 Python 编写的后端服务。采用 Auto.js 需要额外开发一层中控 Server 和 WebSocket 协议，架构过重。
*   **集群能力**：`uiautomator2` 天生适合 PC/云端中控一拖多（群控）。我们可以用一台高配服务器，通过 TCP/IP ADB 直连几百台云手机，统一在 Python 层调度大模型，资源利用率最高。

### 为什么不选 Appium？
*   Appium 架构极其臃肿，中间经过了 Node.js 层的转发，响应慢，且环境配置复杂（需安装 Android SDK, JDK, Appium Server 等）。`uiautomator2` 直接将轻量级 RPC Server 推入手机，执行速度快一个数量级。

### 为什么引入 PaddleOCR？
*   小红书已经开始对 UI 树的 `text` 属性进行混淆/抹除，导致传统的 `d(text="发送")` 完全失效。
*   PaddleOCR 直接对屏幕截图进行像素级文字识别，无论 App 如何混淆底层数据结构，只要屏幕上**画着**文字，OCR 就能读出坐标和内容。
*   PP-OCRv5 支持中英文混合，在 CPU 上推理速度极快（<200ms/帧），完全满足实时交互需求。

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
*   **关闭无障碍服务**：由于我们已彻底去除了 UI Tree 依赖，可以在手机设置中关闭 uiautomator 相关的无障碍服务，实现底层零指纹。
*   **禁用日志**：云手机务必刷入 Magisk 模块，安装 `HideMyApplist` 或类似工具，针对小红书 App 屏蔽 `atx-agent` 进程的可见性。
*   **随机偏移**：脚本已在底层集成了 `_click_with_noise` 方法，所有的坐标点击强制带有 ±15px 的随机噪声 + 按压时长模拟。

### 2.4 依赖安装
```bash
pip install uiautomator2 opencv-python pypinyin paddlepaddle paddleocr
```

### 2.5 模板初始化（首次部署必做）
```bash
# 自动打开 XHS，进入帖子，OCR 识别并裁剪按钮模板
python scripts/auto_crop_templates.py
```
产出文件存放于 `data/ui_templates/`（`send_button.png`, `reply_button.png` 等）。

## 3. 执行流程 (Execution Flow)

```
┌─────────────────────────────────────────────────────────────┐
│                    AI 大脑 (Backend/LLM)                     │
│  1. subprocess 调用 xhs_mobile_driver.py --action scan      │
│  2. 接收 JSON（帖子坐标网格）                                   │
│  3. 再次调用 --action extract --x --y                        │
│  4. OCR 返回帖子文案 + 评论文本                                 │
│  5. LLM 判定是否回复，生成话术                                   │
│  6. 调用 --action reply --x --y --text "..." --live          │
│  7. OCR 验证评论是否成功挂载                                    │
└─────────────────────────────────────────────────────────────┘
           │                                    ▲
           ▼ subprocess                         │ stdout JSON
┌─────────────────────────────────────────────────────────────┐
│               xhs_mobile_driver.py (物理驱动层)               │
│                                                              │
│  感知层: PaddleOCR 截屏 → 文字识别 → 坐标解析                  │
│  定位层: OpenCV matchTemplate → 按钮/键盘定位                  │
│  执行层: touch.down/move/up → Bezier 曲线物理触控              │
│  校验层: OCR 回查 → 评论成功/失败/被吞 三态闭环                 │
└─────────────────────────────────────────────────────────────┘
           │ ADB over TCP/IP
           ▼
┌─────────────────────────────────────────────────────────────┐
│                 Android 设备 (真机/云手机)                     │
│  仅接收内核级触控事件 (input event)，无任何 Accessibility 痕迹   │
└─────────────────────────────────────────────────────────────┘
```

## 4. 输入模式 (Typing Modes)

| 模式 | CLI 参数 | 安全等级 | 原理 |
|---|---|---|---|
| **clipboard** (默认) | `--typing-mode clipboard` | ⭐⭐⭐ | 通过系统剪贴板中转注入 |
| **opencv** | `--typing-mode opencv` | ⭐⭐⭐⭐⭐ | 中文→拼音→OCR/CV 找键盘按键→物理逐字点击 |

> ⚠️ `adb` (send_keys) 模式已于 v2.1 彻底删除，因其直接暴露自动化输入法，极高封号风险。
