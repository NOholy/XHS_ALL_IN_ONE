# 小红书 (XHS) 防风控交互式截流自动化架构设计 — v3.0

> **v3.0 更新 (2026-05-20)**：移动端架构完成"去 Accessibility 化"重构，引入 PP-OCRv5 视觉引擎。本文档同步更新 Web 端 + 移动端的统一防风控架构。

## 1. 架构概述 (Overview)

本系统采用 **"AI 大脑 (Antigravity) + 仿生物理驱动器"** 的读写分离双层架构，分为两条执行通道：

| 通道 | 驱动器 | 适用场景 |
|---|---|---|
| **Web 端 (PC)** | `xhs_browser_automation.py` + Cloakbrowser | 数据采集、登录态管理、内容发布 |
| **移动端 (Android)** | `xhs_mobile_driver.py` + uiautomator2 + PaddleOCR | 矩阵号群控、高频评论/私信、养号 |

两条通道共享同一个 LLM 大脑进行意图裁决和话术生成，但底层的物理模拟策略完全不同。

## 2. 核心架构设计 (Core Architecture)

系统严格按照以下四个阶段 (Phases) 闭环运作：

### Phase A: 扫描与养号 (Scan & Farming)

**移动端实现：`action_farm()` + `action_scan()`**

*   **动作**：在信息流中基于屏幕坐标网格（双列瀑布流 25%/75% 水平分区）进行概率性盲点浏览。
*   **防风控策略**：
    *   **养号漏斗 (100:30:10:2)**：每刷 100 屏，约 30% 概率点进帖子，其中 33% 概率打开评论区。绝不在养号阶段自动评论。
    *   **零 UI Tree 接触**：不使用 `d.xpath()` 或 `d(text=...)` 去扫描帖子列表，完全依赖数学坐标网格 + 概率模型。
    *   **前台守护**：每次操作前通过 `_ensure_app_foreground()` 检查 App 是否在前台，被打断则自动拉回。

### Phase B: 感知与深度抓取 (Perception & Extraction)

**移动端实现：`action_extract()`**

*   **动作**：通过坐标点击进入目标帖子，使用 **PaddleOCR** 对截屏进行全文字识别，提取帖子正文和评论文本。
*   **防风控策略**：
    *   **纯视觉感知**：不再通过 `d(resourceIdMatches=...)` 等 API 查询 DOM 结构，改为 `self.ocr.ocr(screen_img)` 直接读取屏幕像素上的文字。
    *   **滚动拼接**：先截取主贴区域进行 OCR，再通过贝塞尔曲线物理滑动露出评论区，二次截屏做 OCR。两次识别结果合并为完整的帖子上下文。
    *   **模板匹配辅助**：同时使用 OpenCV `_find_template("reply_button")` 定位"回复"按钮坐标，供后续回帖使用。

### Phase C: 认知、意图风控与话术生成 (Cognitive Fuse & Generation)

*   **动作**：AI 大脑阅读 OCR 提取的数据，决定是否回复，并动态生成话术。
*   **防风控策略**：
    *   **动态话术**：废弃死板的预设话术库。每次评论均由 LLM 结合上下文动态生成。
    *   **情绪熔断机制 (Sentiment Fuse)**：一旦识别出帖子属于负面情绪，系统触发熔断，拒绝生成话术。

### Phase D: 物理动作与显式状态闭环 (Physical Act & Verification)

**移动端实现：`action_reply()`**

*   **动作**：进行物理点击和键盘打字，使用 OCR 验证结果。
*   **防风控策略**：
    *   **贝塞尔曲线物理点击**：所有触控通过 `touch.down → touch.move(Bezier) → touch.up` 注入，模拟真实手指的加减速与颤动。
    *   **多模式输入**：
        *   `clipboard`：剪贴板中转，速度与安全性平衡。
        *   `opencv`：中文→拼音→OpenCV 视觉定位键盘按键→物理逐字点击，零输入法痕迹。
    *   **OCR 结果三态闭环**：点击"发送"后，等待 4 秒，截屏启动 OCR 校验：
        1.  `SUCCESS`：OCR 在屏幕上读到了发送内容的前 4 个字。
        2.  `SHADOWBAN`：OCR 未找到已发送文本，可能被折叠或吞评。
        3.  `CAPTCHA`：OpenCV 检测到滑块/验证码模板，脚本死锁等待人工介入。

## 3. 防风控铁律规范总结 (The Red Lines)

本系统的安全性基于对以下开发红线的绝对遵守：

### 移动端 (xhs_mobile_driver.py)
1.  **禁止 UI Tree 查询**：全流程代码中不得出现 `d(text=...)`, `d(className=...)`, `d.xpath(...)` 或 `element.click()`。所有感知通过 OCR/CV 完成，所有交互通过物理坐标注入。
2.  **感知与执行隔离**：OCR 只能用于"看"。"看"到的结果（如按钮坐标）必须传递给 `_click_with_noise` 进行物理"动"。
3.  **模板前置校验**：在执行 `reply`/`extract` 前，必须通过 `_validate_templates()` 确认所需的 OpenCV 特征图已就绪。
4.  **强制冷却**：每次真实评论后强制 `_human_sleep(90.0, 30.0)` 进入 1~2 分钟冷却。

### Web 端 (xhs_browser_automation.py)
1.  **禁止 DOM 触发交互**：全流程代码中不得出现 `element.click()` 或 `input.value = ""`。所有导致状态改变的交互必须通过 CDP 坐标 `click_at_xy` 和拟人打字 `human_type` 完成。
2.  **无状态环境规避**：必须挂载用户真实使用的 Chrome 实例（携带日常 Cookie 和 LocalStorage），严禁使用纯净无头的 Puppeteer 实例启动自动化。

## 4. 工具链一览

| 工具 | 路径 | 用途 |
|---|---|---|
| **主驱动** | `scripts/xhs_mobile_driver.py` | 移动端全功能自动化（scan/extract/reply/farm） |
| **模板采集** | `scripts/auto_crop_templates.py` | PP-OCRv5 全自动 UI 模板裁剪流水线 |
| **按钮模板** | `data/ui_templates/` | `send_button.png`, `reply_button.png`, `slider_puzzle.png` |
| **键盘模板** | `data/keyboard/` | `a.png` ~ `z.png`（opencv 输入模式专用） |
