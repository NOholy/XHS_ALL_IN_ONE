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
# 首次启动会自动下载 PaddleOCR 模型（约 100MB）
```

验证 OCR 服务：
```bash
curl http://localhost:8001/docs
# 能打开 Swagger 文档页说明启动成功
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

## 5. 典型工作流

### 5.1 新设备首次部署

```bash
# 1. 启动 OCR 服务
python start_ocr_server.py &

# 2. 初始化设备
python start_mobile_driver_v2.py --action init --device YOUR_SERIAL

# 3. 检查初始化报告
#    - 如果 login_status 显示 NOT_LOGGED_IN，请手动登录小红书
#    - 如果 template_crop 显示 FAILED，请手动检查 data/ui_templates/ 目录

# 4. 先跑一轮养号测试
python start_mobile_driver_v2.py --action farm --farm-duration 10

# 5. 试运行截流（不真实发送）
python start_mobile_driver_v2.py --action intercept --keywords 地陪

# 6. 确认无误后，真实发送
python start_mobile_driver_v2.py --action intercept --keywords 地陪 --live
```

### 5.2 日常运营（推荐）

```bash
# 全自动：先养号30分钟 → 再截流
python start_mobile_driver_v2.py --action auto
```

### 5.3 多设备部署

```bash
# 设备 A（通过环境变量区分）
AE_DEVICE_SERIAL=device_a:5555 python start_mobile_driver_v2.py --action auto &

# 设备 B
AE_DEVICE_SERIAL=device_b:5555 python start_mobile_driver_v2.py --action auto &
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
└── tools/                        # 工具脚本
    ├── optimize_device.py        # 设备优化独立脚本
    └── auto_crop_templates.py    # UI 模板自动采集
```
