# XHS Automation Engine V2 使用文档

> 工业级小红书真机自动化引擎 — 真机初始化 · 自动养号 · 话题截流

---

## 目录

- [1. 系统架构](#1-系统架构)
- [2. 环境准备](#2-环境准备)
- [3. 配置说明](#3-配置说明)
- [4. 命令使用](#4-命令使用)
- [5. 典型工作流](#5-典型工作流)
- [6. 配置参考](#6-配置参考)
- [7. 故障排查](#7-故障排查)

---

## 1. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    CLI 入口层                            │
│         start_mobile_driver_v2.py --action xxx          │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
┌──────────────▼───────┐  ┌───────────▼──────────────────┐
│    编排层 (flows/)    │  │     配置中心 (config.py)      │
│  init_flow.py        │  │  config.yaml + 环境变量       │
│  farm_flow.py        │  └──────────────────────────────┘
│  intercept_flow.py   │
└──────────────┬───────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│                能力层 (mobile_core/)                     │
│  Navigator · Searcher · Reader · Commenter · Farmer     │
└──────────────┬──────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────┐
│               基础设施层 (mobile_core/)                   │
│  AgentlessDriver · VisionEngine · OCRClient             │
│  KeyboardVision · Watchdog · StateMachine               │
└──────────────┬──────────────────────────────────────────┘
               │
         ┌─────▼─────┐     ┌────────────┐
         │ ADB/手机   │     │ OCR 微服务  │
         └───────────┘     │ (port 8001) │
                           └────────────┘
```

**核心原则**：100% 纯视觉交互 + 零端侧代理 + 全参数配置化

---

## 2. 环境准备

### 2.1 Python 依赖

```bash
cd automation_engine
pip install -r requirements.txt
```

### 2.2 启动 OCR 微服务

**所有自动化操作的前置依赖**，必须先启动：

```bash
python start_ocr_server.py
# 默认监听 http://localhost:8001
```

#### OCR 引擎与架构设计

OCR 微服务采用 **策略模式 (Strategy Pattern)** 设计，便于灵活插拔与扩展底层引擎。当前支持的 OCR 引擎类型包括：

1. **PaddleOCR (默认)**：
   - 默认加载 `PP-OCRv4` 中文轻量化模型（`lang="ch"`，首次启动会自动联网下载约 100MB 的模型权重文件）。
   - 自适应接口：针对 PaddleOCR 新版本自动调用高性能 `predict` 推理 API，对旧版本自动降级使用兼容的 `ocr` API。
2. **Mock 引擎 (`MockOCREngine`)**：
   - 虚拟 OCR 引擎，用于开发调试、集成测试或在无 GPU/模型依赖的环境下快速跑通自动化流。

#### 相关环境变量配置

你可以通过以下环境变量配置 OCR 服务的初始参数：
- `OCR_ENGINE_TYPE`：引擎类型，可选 `paddle` 或 `mock`（默认 `paddle`）。
- `OCR_LANG`：OCR 识别语言，默认 `ch`（中文）。
- `OCR_VERSION`：PaddleOCR 模型版本，默认 `PP-OCRv4`。

#### 动态 API 与服务验证

- **API 文档与交互测试**：`http://localhost:8001/docs` (能打开 Swagger 页面说明服务启动正常)。
- **深度健康检查**：`GET http://localhost:8001/health`
  - 该端点会执行轻量级空白图片推理探测以验证模型是否已加载完毕并可进行正常推理，返回当前载入的 `engine_type` 详情。
- **免重启热切换引擎**：`POST http://localhost:8001/config`
  - 支持在服务不停止的情况下动态切换 OCR 引擎或模型配置。请求 JSON 载荷示例：
    ```json
    {
      "engine_type": "paddle",
      "lang": "ch",
      "version": "PP-OCRv4"
    }
    ```

### 2.3 连接 Android 设备

```bash
# USB 连接
adb devices
# 确保输出中有你的设备且状态为 "device"

# WiFi 连接（推荐生产环境使用，可隐藏 USB 调试状态）
adb tcpip 5555
adb connect 192.168.x.x:5555
```

---

## 3. 配置说明

所有参数集中管理在 `config.yaml`，支持三级优先级：

```
环境变量 (AE_*) > config.yaml > 代码默认值
```

### 3.1 快速配置

编辑 `config.yaml`，核心参数：

```yaml
device:
  serial: null              # 设备序列号，null=自动检测第一台
  use_agentless: true       # 生产环境必须 true
  typing_mode: "clipboard"  # 或 "opencv"（纯视觉打字，更安全但更慢）

intercept:
  keywords:                 # 截流关键词（核心业务参数）
    - "地陪"
    - "旅游攻略"
  comment_mode: "template"  # 评论模式: template / contextual / llm
  live_mode: false          # false=试运行不发送, true=真实发送
  comment_templates:        # 评论模板库
    - "感谢分享，想了解更详细的安排！"
    - "楼主怎么收费呢？可以私信吗~"

schedule:
  run_mode: "farm_then_intercept"  # 运行策略
```

### 3.2 环境变量覆盖

敏感参数建议通过环境变量注入，而非写在配置文件中：

```bash
# LLM API Key（使用 llm 评论模式时必须）
export AE_LLM_API_KEY="sk-xxx"

# 设备序列号
export AE_DEVICE_SERIAL="192.168.1.100:5555"

# 强制真实发送模式
export AE_LIVE_MODE=true
```

---

## 4. 命令使用

### 统一入口

```bash
python start_mobile_driver_v2.py --action <ACTION> [OPTIONS]
```

### 4.1 真机初始化 (`init`)

**用途**：新设备首次接入时执行，完成所有系统级优化和视觉模板采集。

```bash
python start_mobile_driver_v2.py --action init
```

**执行步骤**（均可通过 config 开关控制）：

| 步骤 | 配置开关 | 说明 |
|------|---------|------|
| 1. ADB 连接校验 | — | 始终执行 |
| 2. 关闭系统动画 | `device.auto_disable_animations` | 提升 OCR 准确率 |
| 3. 屏幕常亮 | `device.auto_keep_screen_on` | 防止自动锁屏 |
| 4. XHS App 检测 | `device.check_xhs_installed` | 检查小红书是否安装 |
| 5. 登录状态检测 | `device.check_login_status` | OCR 检测是否已登录 |
| 6. UI 模板采集 | `device.auto_crop_templates_on_init` | 自动截取按钮模板 |
| 7. IP 轮换测试 | `device.auto_rotate_ip_on_init` | 验证飞行模式换 IP |

**输出**：JSON 格式的初始化报告，每步标注 OK / SKIPPED / FAILED。

**指定设备**：
```bash
python start_mobile_driver_v2.py --action init --device 192.168.1.100:5555
```

---

### 4.2 自动养号 (`farm`)

**用途**：模拟真人使用行为，降低账号黑产特征，提升账号权重。

```bash
# 使用默认时长（config.yaml 中的 farm.session_duration_minutes）
python start_mobile_driver_v2.py --action farm

# 指定养号时长
python start_mobile_driver_v2.py --action farm --farm-duration 60
```

**养号行为漏斗**（概率均可在 `config.yaml` 中调整）：

| 行为 | 默认概率 | 配置项 |
|------|---------|--------|
| 信息流下滑 | 基础行为 | — |
| 点入帖子阅读 | 30% | `farm.enter_post_probability` |
| 点赞 | 10% | `farm.like_probability` |
| 收藏 | 3% | `farm.collect_probability` |
| 评论 | 1% | `farm.comment_probability` |
| 随机搜索热词 | 5% | `farm.random_search_probability` |
| 浏览个人主页 | 3% | `farm.visit_profile_probability` |

**热门搜索词库**在 `config.yaml` 的 `farm.hot_keywords` 中配置。

---

### 4.3 话题截流 (`intercept`)

**用途**：搜索指定话题关键词 → 筛选目标帖子 → 自动评论截流引导私信。

```bash
# 试运行（不真实发送）
python start_mobile_driver_v2.py --action intercept

# 真实发送
python start_mobile_driver_v2.py --action intercept --live

# 覆盖关键词
python start_mobile_driver_v2.py --action intercept --keywords 地陪 重庆旅游

# 使用 LLM 生成评论
python start_mobile_driver_v2.py --action intercept --comment-mode llm --live
```

**截流 Pipeline**：

```
搜索关键词 → 翻页收集结果 → 标题关键词过滤
    → 伪装浏览（5-10个无关帖子）→ 进入目标帖子
    → 提取内容 → 生成评论 → 发送 → OCR验证上墙
    → 强制冷却(60-180s) → 概率性IP轮换 → 下一个
```

**三种评论模式**：

| 模式 | 配置值 | 说明 |
|------|--------|------|
| 模板随机 | `template` | 从 `comment_templates` 列表随机选取 |
| 上下文匹配 | `contextual` | 根据帖子内容匹配最相关的模板 |
| LLM 生成 | `llm` | 调用 AI API 根据帖子内容动态生成 |

**LLM 模式配置**：
```yaml
intercept:
  comment_mode: "llm"
  llm_endpoint: ""              # 留空使用 OpenAI 官方
  llm_api_key: ""               # 建议用环境变量 AE_LLM_API_KEY
  llm_model: "gpt-4o-mini"      # 可替换为任意兼容 API
  llm_prompt_template: >        # 可自定义 Prompt
    你是一个真实的小红书用户...
```

---

### 4.4 全自动模式 (`auto`)

**用途**：根据配置的运行策略自动执行养号和/或截流。

```bash
# 使用 config.yaml 中配置的策略
python start_mobile_driver_v2.py --action auto

# 覆盖运行策略
python start_mobile_driver_v2.py --action auto --run-mode farm_then_intercept
```

**四种运行策略**：

| 策略 | 配置值 | 行为 |
|------|--------|------|
| 先养后截 | `farm_then_intercept` | 先养号 N 分钟热身，再执行截流（**推荐**） |
| 仅截流 | `intercept_only` | 直接执行截流 |
| 仅养号 | `farm_only` | 仅执行养号 |
| 交替混合 | `mixed` | 每个关键词前养号10分钟，再截流该关键词 |

**热身时长**配置：
```yaml
schedule:
  run_mode: "farm_then_intercept"
  warmup_farm_minutes: 30        # 养号热身分钟数
```

---

### 4.5 辅助命令

```bash
# 扫描信息流（查看当前可见帖子坐标）
python start_mobile_driver_v2.py --action scan

# 提取指定帖子内容
python start_mobile_driver_v2.py --action extract --x 540 --y 800

# 回复指定坐标（试运行）
python start_mobile_driver_v2.py --action reply --x 200 --y 1800 --text "好的谢谢"

# 回复指定坐标（真实发送）
python start_mobile_driver_v2.py --action reply --x 200 --y 1800 --text "好的谢谢" --live
```

---

### 4.6 UI 模板管理与精准采集

**用途**：小红书 UI 经常更新，不同手机、浅色/深色模式下按钮样式也会不同。当自动化流程因为找不到某个图标而失败时，可以使用辅助采集工具**瞬间补充高清模板**。

相比于全自动采集，**半自动交互式采集 (assisted_crop)** 是最佳实践：由您手动把屏幕导航到正确的界面，脚本负责精准 OCR 抠图。

#### 完整模板清单

系统运行所需的全部 UI 模板如下。**请确保每个模板都已采集**，否则对应的功能将无法工作。

| 模板名 | OCR 关键字 | 用途 | 手机端准备步骤 |
|--------|-----------|------|--------------|
| `tab_home` | 首页 | 底部导航栏 - 首页 | 在首页即可 |
| `tab_profile` | 我 | 底部导航栏 - 我 | 在首页即可 |
| `tab_message` | 消息 | 底部导航栏 - 消息 | 在首页即可 |
| `search_input` | 搜索 | 首页顶部搜索入口 | 在首页即可 |
| `comment_input` | 说点什么 | 帖子底部评论输入框 | 打开一篇有评论区的帖子 |
| `send_button` | 发送 | 评论发送按钮 | 点击"说点什么"后输入任意文字，右侧出现"发送" |
| `reply_button` | 回复 | 评论区回复按钮 | 打开帖子，向下滑动到评论区 |

> **注意**：`send_button`（发送）按钮**只有在点击评论框并输入文字后才会出现**。采集时必须先手动输入至少一个字符。

#### 使用方法

```bash
# 1. 查看当前设备的模板缺失状态（推荐首先执行）
python tools/assisted_crop.py --list

# 2. 不确定该用什么关键字？预览当前屏幕的 OCR 识别结果
python tools/assisted_crop.py --preview

# 3. 按关键字精准采集（手机端先导航到正确页面）
python tools/assisted_crop.py --keyword "说点什么" --name "comment_input"

# 4. 覆盖已有模板（如 UI 改版后需重新采集）
python tools/assisted_crop.py --keyword "首页" --name "tab_home" --force
```

采集完成后脚本会自动在 Mac 上打开图片预览，确认裁剪结果无误。

## 5. 典型工作流

### 5.1 新设备首次部署

```bash
# ──── Step 1: 启动 OCR 服务 ────
python start_ocr_server.py &
# 等待输出 "Uvicorn running on http://0.0.0.0:8001"

# ──── Step 2: 初始化设备 ────
python start_mobile_driver_v2.py --action init --device YOUR_SERIAL
# 查看输出报告：
#   - login_status: LOGGED_IN → 继续
#   - login_status: NOT_LOGGED_IN → 请先在手机上手动登录小红书

# ──── Step 3: 检查并补齐模板 ────
python tools/assisted_crop.py --list
# 按照输出的缺失清单逐一采集，采集顺序建议：

# 3a. 首页模板（手机停在首页）
python tools/assisted_crop.py --keyword "首页" --name "tab_home"
python tools/assisted_crop.py --keyword "消息" --name "tab_message"
python tools/assisted_crop.py --keyword "我" --name "tab_profile"
python tools/assisted_crop.py --keyword "搜索" --name "search_input"

# 3b. 帖子模板（手机上打开一篇有评论的帖子）
python tools/assisted_crop.py --keyword "说点什么" --name "comment_input"
python tools/assisted_crop.py --keyword "回复" --name "reply_button"

# 3c. 发送按钮（点击"说点什么"，输入一个字，出现"发送"后执行）
python tools/assisted_crop.py --keyword "发送" --name "send_button"

# 3d. 再次检查，确认全部就绪
python tools/assisted_crop.py --list

# ──── Step 4: 养号测试 ────
python start_mobile_driver_v2.py --action farm --farm-duration 10

# ──── Step 5: 试运行截流（不真实发送） ────
python start_mobile_driver_v2.py --action intercept --keywords 地陪

# ──── Step 6: 确认无误，真实发送 ────
python start_mobile_driver_v2.py --action intercept --keywords 地陪 --live
```

### 5.2 模板修复（高频场景）

当自动化流程出现 `Template 'xxx' not found` 错误时，通常是模板失效了。

```bash
# 1. 先看看哪些模板缺失
python tools/assisted_crop.py --list

# 2. 手机上导航到对应页面

# 3. 不确定屏幕上有什么文字？用 preview 看一眼
python tools/assisted_crop.py --preview

# 4. 重新采集失效的模板（--force 覆盖旧文件）
python tools/assisted_crop.py --keyword "说点什么" --name "comment_input" --force

# 5. 确认修复完成
python tools/assisted_crop.py --list
```

**常见触发模板失效的场景**：
- 小红书 App 版本更新
- 切换手机深色/浅色模式
- 更换手机设备（分辨率不同）
- 更换小红书账号（UI 微调）

### 5.3 日常运营（推荐）

```bash
# ──── 运行前检查 ────
# 1. 确认 OCR 服务在线
curl -s http://localhost:8001/health | python -m json.tool

# 2. 确认设备连接
adb devices

# 3. 确认模板齐全
python tools/assisted_crop.py --list

# ──── 启动自动化 ────
# 全自动：先养号30分钟 → 再截流
python start_mobile_driver_v2.py --action auto

# ──── 运行中监控 ────
# 实时查看评论日志
python start_mobile_driver_v2.py --action auto 2>&1 | grep '"module": "commenter"'

# ──── 运行后检查 ────
# 查看今日评论记录
cat data/commented_posts.json | python -m json.tool
```

### 5.4 多设备部署

```bash
# 设备 A（通过环境变量区分）
AE_DEVICE_SERIAL=device_a:5555 python start_mobile_driver_v2.py --action auto &

# 设备 B
AE_DEVICE_SERIAL=device_b:5555 python start_mobile_driver_v2.py --action auto &
```

### 5.5 故障恢复

当养号或截流中途异常退出时：

```bash
# 1. 查看日志定位问题
#    - 如果是 "Template not found" → 走 5.2 模板修复流程
#    - 如果是 "OCR Server Error"   → 重启 OCR 服务
#    - 如果是 "ADB connection"     → 检查 USB/WiFi 连接

# 2. 重启 OCR 服务（如果它崩了）
pkill -f start_ocr_server
python start_ocr_server.py &

# 3. 重新连接设备（如果连接断了）
adb kill-server && adb start-server
adb devices

# 4. 重新启动自动化
python start_mobile_driver_v2.py --action auto
```

---
## 6. 配置参考

### 6.1 完整 CLI 参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--action` | 执行动作（必填） | `init / farm / intercept / auto / scan / extract / reply` |
| `--device` | 覆盖设备序列号 | `--device 192.168.1.100:5555` |
| `--agentless` | 强制无代理模式 | `--agentless` |
| `--typing-mode` | 覆盖打字模式 | `--typing-mode opencv` |
| `--live` | 覆盖为真实发送 | `--live` |
| `--keywords` | 覆盖截流关键词 | `--keywords 地陪 旅游攻略` |
| `--comment-mode` | 覆盖评论模式 | `--comment-mode llm` |
| `--run-mode` | 覆盖运行策略 | `--run-mode mixed` |
| `--farm-duration` | 覆盖养号时长(分钟) | `--farm-duration 60` |
| `--x / --y` | 坐标（extract/reply用） | `--x 540 --y 800` |
| `--text` | 评论文本（reply用） | `--text "好的谢谢"` |

### 6.2 环境变量清单

| 变量名 | 对应配置 | 说明 |
|--------|---------|------|
| `AE_CONFIG_FILE` | — | 指定配置文件路径 |
| `AE_DEVICE_SERIAL` | `device.serial` | 设备序列号 |
| `AE_USE_AGENTLESS` | `device.use_agentless` | true/false |
| `AE_TYPING_MODE` | `device.typing_mode` | clipboard/opencv |
| `AE_OCR_ENDPOINT` | `ocr.endpoint` | OCR 服务地址 |
| `AE_MAX_DAILY_COMMENTS` | `risk_control.max_daily_comments` | 日评论上限 |
| `AE_COMMENT_MODE` | `intercept.comment_mode` | 评论模式 |
| `AE_LIVE_MODE` | `intercept.live_mode` | 是否真实发送 |
| `AE_LLM_API_KEY` | `intercept.llm_api_key` | LLM API 密钥 |
| `AE_LLM_MODEL` | `intercept.llm_model` | LLM 模型名 |
| `AE_RUN_MODE` | `schedule.run_mode` | 运行策略 |
| `AE_FARM_DURATION` | `farm.session_duration_minutes` | 养号时长 |

### 6.3 风控配额默认值

| 指标 | 默认上限 | 配置项 |
|------|---------|--------|
| 每日评论 | 10 次 | `risk_control.max_daily_comments` |
| 每日点赞 | 30 次 | `risk_control.max_daily_likes` |
| 每日收藏 | 15 次 | `risk_control.max_daily_collects` |
| 每日关注 | 5 次 | `risk_control.max_daily_follows` |
| 每日搜索 | 20 次 | `risk_control.max_daily_searches` |
| 评论冷却 | 60-180 秒 | `risk_control.comment_cooldown_*` |
| IP轮换频率 | 每 3 条评论 | `risk_control.ip_rotate_every_n_comments` |

---

## 7. 故障排查

### 7.1 常见问题

**Q: OCR 服务无法启动**
```
A: 确保已安装 PaddlePaddle：pip install paddlepaddle paddleocr
   首次启动需要下载模型，请确保网络通畅。
```

**Q: ADB 连接失败**
```
A: 1. 检查 USB 线连接和手机授权弹窗
   2. 运行 adb kill-server && adb start-server
   3. 确认 adb devices 显示 "device" 状态
```

**Q: 视觉模板匹配不到按钮**
```
A: 1. 重新运行 --action init 重新采集模板
   2. 检查 data/ui_templates/ 目录下是否有 .png 文件
   3. 可能是分辨率不匹配，需在目标机型上重新采集
   4. 降低 vision.template_match_threshold（默认 0.75）
```

**Q: 评论发送后 OCR 验证失败**
```
A: 不一定是发送失败：
   1. 可能是网络延迟，评论尚未渲染到屏幕
   2. 可能是 Shadowban（评论仅自己可见）
   3. 检查日志中的 "Comment not found via OCR" 警告频率
   4. 如果频繁出现，建议暂停该账号并轮换 IP
```

**Q: 搜索无结果**
```
A: 1. 确认关键词是否正确（太长的词可能搜不到）
   2. 确认输入法是否正确弹出（检查 search_input 模板）
   3. 如果使用 opencv 打字模式，确保键盘字母模板已采集
   4. 尝试 --typing-mode clipboard 切换到剪贴板模式
```

### 7.2 日志格式

所有日志输出为结构化 JSON，便于 grep 和管道处理：

```json
{
  "timestamp": "2026-05-23T05:30:00Z",
  "level": "INFO",
  "module": "commenter",
  "message": "Comment posted! Daily count: 3"
}
```

筛选特定模块日志：
```bash
python start_mobile_driver_v2.py --action intercept 2>&1 | grep '"module": "commenter"'
```

### 7.3 去重记录

已评论帖子的 ID 记录在 `data/commented_posts.json` 中。
如需重置去重记录（例如换号后）：
```bash
rm data/commented_posts.json
```

---

## 文件结构速查

```
automation_engine/
├── config.py                     # 配置中心（dataclass + YAML + env）
├── config.yaml                   # 默认配置文件（所有参数带中文注释）
├── start_mobile_driver_v2.py     # CLI 统一入口（7 个 action）
├── start_ocr_server.py           # OCR 微服务入口
├── USAGE.md                      # 本文档
├── flows/                        # 业务编排层
│   ├── init_flow.py              # 真机初始化 Pipeline
│   ├── farm_flow.py              # 养号会话编排
│   └── intercept_flow.py         # 话题截流 Pipeline
├── mobile_core/                  # 能力层 + 基础设施层
│   ├── navigator.py              # 页面导航器
│   ├── searcher.py               # 搜索引擎
│   ├── reader.py                 # 帖子阅读器
│   ├── commenter.py              # 智能评论器
│   ├── farmer.py                 # 养号器
│   ├── agentless_driver.py       # 无代理物理驱动
│   ├── device_driver.py          # U2 驱动（调试用）
│   ├── device_optimizer.py       # 设备优化器
│   ├── vision.py                 # OpenCV 视觉引擎
│   ├── ocr_client.py             # OCR 微服务客户端
│   ├── keyboard_vision.py        # 纯视觉键盘输入
│   ├── watchdog.py               # 弹窗看门狗
│   ├── state_machine.py          # 状态机执行器
│   ├── logger.py                 # JSON 结构化日志
│   └── exceptions.py             # 异常定义
├── tools/                        # 工具脚本
│   ├── optimize_device.py        # 设备优化独立脚本
│   ├── auto_crop_templates.py    # UI 模板全自动采集（依赖弱网和容错机制）
│   └── assisted_crop.py          # 半自动 UI 模板辅助采集（推荐，指哪打哪）
```
