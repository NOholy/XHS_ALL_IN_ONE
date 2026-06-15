---
name: xhs-automation-engine
description: 工业级小红书真机自动化引擎操控技能。涵盖设备初始化、养号、截流、Pipeline YAML 编排、Agent 智能体模式、OCR 服务管理、模板采集、YOLO 训练、故障排查。使用纯视觉 + 物理模拟的零侵入方案。经过三轮深度反风控加固（V1-V12 + W1-W10）。
---

# 小红书真机自动化引擎操控技能 (XHS Automation Engine)

这是一个专门用于操控 `automation_engine` 工业级真机自动化系统的技能。当你收到类似"启动截流"、"初始化设备"、"养号"、"调试 Pipeline"、"训练 YOLO 模型"等指令时，**必须严格遵守本文件的规范执行**。

## 项目路径

```
/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/
```

所有命令默认在此目录下执行。

---

## 🏗 系统架构理解

引擎采用 5 层架构：

```
CLI 入口 (start_mobile_driver_v2.py)
    ↓
编排层 (flows/) / Pipeline 引擎 (config/pipelines/*.yaml)
    ↓
能力层 (Navigator · Searcher · Reader · Commenter · Farmer · AgentLoop)
    ↓
基础设施层 (AgentlessDriver · VisionEngine · OCRClient · StealthIME · Watchdog)
    ↓
外部 (ADB/手机 · OCR微服务:8001 · LLM API)
```

**核心原则：100% 纯视觉交互 + 零端侧代理 + 全参数配置化**

---

## 🚨 绝对铁律 (Red Lines)

执行本技能时，**绝对禁止**以下行为：

1. **禁止使用 UIAutomator/Accessibility 方案** — 引擎已完全摒弃传统控件树遍历。所有交互必须通过 AgentlessDriver 的物理模拟完成。
2. **禁止修改 `agentless_driver.py` 的触控核心逻辑** — SensorHalService (原 TouchInjector) + 贝塞尔曲线 + 传感器模拟是经过深度调优的反风控方案。
3. **禁止在配置文件中硬编码 API Key** — 敏感参数必须通过环境变量 (`AE_*`) 注入。`config.py` 中的 API Key 默认为空字符串。
4. **禁止跳过 OCR 服务前置检查** — 所有自动化操作依赖 OCR 微服务，必须先确认 `http://localhost:8001/health` 可达。
5. **禁止在生产环境使用 `--live` 前未经用户确认** — 默认始终为 dry-run 模式。
6. **禁止直接修改 Pipeline YAML 的核心反风控参数**（冷却时间、IP轮换频率）— 除非用户明确要求。
7. **禁止使用 `adb shell input` 或 `am broadcast` 进行文本输入** — 所有输入必须通过 Stealth IME Socket 通道。
8. **禁止在日志/注释中出现 `TouchInjector` 字样** — 已全面替换为 `SensorHalService` 或中文描述，防止取证指纹。

---

## 📋 标准操作程序 (SOP)

### SOP-1: 环境准备（每次会话首先执行）

```bash
# 1. 确认 OCR 服务在线
curl -s http://localhost:8001/health | python3 -m json.tool

# 2. 确认设备连接
adb devices

# 3. 如果 OCR 未启动
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine
python start_ocr_server.py &
# 等待输出 "Uvicorn running on http://0.0.0.0:8001"

# 4. 如果设备未连接 (WiFi)
adb connect <IP>:5555
```

### SOP-2: 新设备首次初始化

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# Step 1: 初始化设备
python start_mobile_driver_v2.py --action init --device <SERIAL>

# Step 2: 检查并补齐 UI 模板
python tools/assisted_crop.py --list

# Step 3: 按缺失清单采集模板（手机先导航到正确页面）
python tools/assisted_crop.py --keyword "首页" --name "tab_home"
python tools/assisted_crop.py --keyword "消息" --name "tab_message"
python tools/assisted_crop.py --keyword "我" --name "tab_profile"
python tools/assisted_crop.py --keyword "搜索" --name "search_input"
# 打开一篇帖子后:
python tools/assisted_crop.py --keyword "说点什么" --name "comment_input"
python tools/assisted_crop.py --keyword "回复" --name "reply_button"
# 点击评论框输入至少一个字后:
python tools/assisted_crop.py --keyword "发送" --name "send_button"

# Step 4: 确认模板齐全
python tools/assisted_crop.py --list
```

### SOP-3: 养号

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 默认时长 (config.yaml 中 farm.session_duration_minutes)
python start_mobile_driver_v2.py --action farm

# 指定时长
python start_mobile_driver_v2.py --action farm --farm-duration 60
```

行为漏斗概率（黄金比例 100:30:10:3:1）：

| 行为 | 默认概率 | 配置项 |
|------|---------|--------|
| 信息流下滑 | 基础行为 | — |
| 点入帖子阅读 | 30% | `farm.enter_post_probability` |
| 点赞 | 10% | `farm.like_probability` |
| 收藏 | 3% | `farm.collect_probability` |
| 评论 | 1% | `farm.comment_probability` |
| 随机搜索 | 5% | `farm.random_search_probability` |

### SOP-4: 话题截流

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 试运行（不真实发送）— 默认
python start_mobile_driver_v2.py --action intercept

# 覆盖关键词
python start_mobile_driver_v2.py --action intercept --keywords 地陪 重庆旅游

# LLM 生成评论
python start_mobile_driver_v2.py --action intercept --comment-mode llm

# 🚨 真实发送（必须用户确认后才可执行）
python start_mobile_driver_v2.py --action intercept --live
```

截流 Pipeline 流程：
```
搜索关键词 → 翻页收集结果 → 标题关键词过滤
  → 伪装浏览(5-10个无关帖子) → 进入目标帖子
  → 提取内容 → 生成评论 → NLP反指纹后处理 → 发送 → OCR验证上墙
  → 对数正态冷却(~120s) → 随机化IP轮换 → 循环
```

三种评论模式：
- `template` — 从 20+ Spintax 模板随机选取 + `_humanize_comment()` 后处理
- `contextual` — 根据帖子内容匹配最相关模板 + 后处理
- `llm` — 调用 LLM API 动态生成 + 后处理（需配置 `AE_LLM_API_KEY`）

评论后处理 (`_humanize_comment`)：
- 15% 概率「的/得/地」混用错别字
- 10% 概率注入口癖填充词 (哈哈、嗯嗯、啊啊啊)
- 10% 概率截断尾部标点
- 5% 概率末尾重复字符

### SOP-5: 全自动模式

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 使用配置文件策略
python start_mobile_driver_v2.py --action auto

# 覆盖运行策略
python start_mobile_driver_v2.py --action auto --run-mode farm_then_intercept
```

四种策略：
- `farm_then_intercept` — 先养号N分钟热身，再截流 **（推荐）**
- `intercept_only` — 直接截流
- `farm_only` — 仅养号
- `mixed` — 每个关键词前养号再截流

### SOP-6: Agent 智能体模式

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

python start_mobile_driver_v2.py --action agent --prompt "去搜索下旅游攻略并点赞第一篇帖子"
```

Agent 需要在 `config.yaml` 中配置 LLM API：
```yaml
agent:
  enabled: true
  llm_endpoint: "https://api.deepseek.com/chat/completions"
  llm_api_key: ""  # 通过 AE_AGENT_LLM_API_KEY 环境变量注入
  llm_model: "deepseek-v4-flash"
  max_iterations: 30
```

### SOP-7: Pipeline YAML 声明式执行

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 执行指定 Pipeline
python start_mobile_driver_v2.py --action pipeline --pipeline intercept_comment \
  --context '{"current_keyword": "旅游"}'

# 指定入口节点（从 DAG 中间开始）
python start_mobile_driver_v2.py --action pipeline --pipeline intercept_comment \
  --pipeline-entry Navigate_Search

# 生成 HTML 报告
python start_mobile_driver_v2.py --action pipeline --pipeline farm_session --report
```

Pipeline YAML 存放在 `config/pipelines/` 目录。

### SOP-8: 辅助命令

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 扫描信息流
python start_mobile_driver_v2.py --action scan

# 提取帖子
python start_mobile_driver_v2.py --action extract --x 540 --y 800

# 回复（试运行）
python start_mobile_driver_v2.py --action reply --x 200 --y 1800 --text "好的谢谢"

# 回复（真实发送）
python start_mobile_driver_v2.py --action reply --x 200 --y 1800 --text "好的谢谢" --live
```

---

## ⚙ 配置管理

### 配置文件路径

```
/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/config.yaml
```

### 环境变量覆盖

| 变量名 | 配置项 | 说明 |
|--------|--------|------|
| `AE_DEVICE_SERIAL` | `device.serial` | 设备序列号 |
| `AE_TYPING_MODE` | `device.typing_mode` | clipboard/opencv/vision |
| `AE_OCR_ENDPOINT` | `ocr.endpoint` | OCR 服务地址 |
| `AE_MAX_DAILY_COMMENTS` | `risk_control.max_daily_comments` | 日评论上限 |
| `AE_COMMENT_MODE` | `intercept.comment_mode` | 评论模式 |
| `AE_LIVE_MODE` | `intercept.live_mode` | 是否真实发送 |
| `AE_LLM_API_KEY` | `intercept.llm_api_key` | LLM API 密钥 |
| `AE_RUN_MODE` | `schedule.run_mode` | 运行策略 |
| `AE_FARM_DURATION` | `farm.session_duration_minutes` | 养号时长 |
| `AE_AGENT_LLM_API_KEY` | `agent.llm_api_key` | Agent API Key |

### 风控配额与反指纹参数

| 指标 | 默认值 | 说明 |
|------|--------|------|
| 每日评论 | 10 次 | `max_daily_comments` |
| 每日点赞 | 30 次 | `max_daily_likes` |
| 每日收藏 | 15 次 | `max_daily_collects` |
| 评论冷却 | 对数正态 ~120s | W6: 非均匀分布, 10%长冷却 |
| IP轮换 | 每 2-6 条评论 | W1: 阈值每次随机, 延迟30-120s |
| IP轮换方式 | svc data (隐蔽) | W1: 不触发飞行模式广播 |
| 延迟分布 | 对数正态 + 5%漂移 + 1%中断 | V5: 替代高斯分布 |
| 动画缩放 | 0.5 | W5: 避免全禁检测 |
| 触控端口 | 随机 10000-60000 | V6: 替代硬编码 1111 |
| 截图缓存 | 500ms TTL | V2: 减少 screencap 频率 |
| dumpsys 缓存 | 2s TTL | V10: 减少 adb shell 频率 |

---

## 🔧 故障排查

### 问题 1: OCR 服务无法启动

```bash
# 检查是否安装了 PaddlePaddle
pip install paddlepaddle paddleocr

# 使用 Mock 引擎绕过（开发调试）
OCR_ENGINE_TYPE=mock python start_ocr_server.py
```

### 问题 2: ADB 连接失败

```bash
adb kill-server && adb start-server
adb devices
# 确保设备状态为 "device"（不是 "unauthorized"）
```

### 问题 3: 模板匹配失败 ("Template not found")

```bash
# 1. 检查缺失模板
python tools/assisted_crop.py --list

# 2. 预览当前屏幕 OCR 结果
python tools/assisted_crop.py --preview

# 3. 重新采集失效模板
python tools/assisted_crop.py --keyword "说点什么" --name "comment_input" --force

# 4. 降低匹配阈值（编辑 config.yaml）
# vision.template_match_threshold: 0.70  (默认0.75)
```

常见模板失效原因：
- 小红书 App 版本更新
- 切换手机深色/浅色模式
- 更换手机设备（分辨率不同）

### 问题 4: SensorHalService (触控注入) 启动失败

```bash
# 检查 dex 文件 (V4: 已伪装为系统扩展)
adb shell ls /data/local/tmp/framework-ext.dex
# 注意: V11 机制会在加载后自动删除此文件, 这是正常行为

# 手动推送
adb push mobile_core/injector/touch_injector.dex /data/local/tmp/framework-ext.dex

# 检查 app_process 是否被占用
adb shell ps | grep app_process
adb shell killall app_process
```

### 问题 5: Stealth IME 不工作

```bash
# W3: 包名已变更为 com.android.providers.settings
# 检查 IME 状态
adb shell settings get secure default_input_method
# 期望输出: com.android.providers.settings/.StealthIME

# 手动激活
adb shell ime enable com.android.providers.settings/.StealthIME
adb shell ime set com.android.providers.settings/.StealthIME

# V1: 测试 Socket 通信
adb forward tcp:18888 localabstract:com.android.inputservice.internal
echo -n "p" | nc localhost 18888
# 期望收到 "pong"

# 如果从旧版升级, 需要先卸载旧包名:
adb uninstall com.android.inputservice.core
adb install stealth-ime-v3.apk
```

### 问题 6: 评论验证失败

不一定是发送失败：
1. 可能是网络延迟，评论尚未渲染
2. 可能是 Shadowban（评论仅自己可见）
3. 检查日志中 "Comment not found via OCR" 频率
4. 频繁出现则暂停该账号并轮换 IP

### 问题 7: Pipeline 节点卡死

```bash
# 查看 Pipeline 日志
ls data/pipeline_logs/
cat data/pipeline_logs/pipeline_*.jsonl | tail -20

# 检查 LoopDetector 是否触发
grep "stuck" data/pipeline_logs/pipeline_*.jsonl
```

---

## 📁 关键文件索引

| 文件 | 用途 |
|------|------|
| `start_mobile_driver_v2.py` | CLI 统一入口 (9个action) |
| `start_ocr_server.py` | OCR 微服务 (PaddleOCR/Mock) |
| `config.py` | 配置中心 (10个dataclass) |
| `config.yaml` | 默认配置文件 |
| `mobile_core/agentless_driver.py` | 核心物理驱动 (SensorHalService) |
| `mobile_core/stealth_ime_client.py` | 隐蔽输入法 Socket 客户端 |
| `mobile_core/vision.py` | OpenCV 视觉引擎 |
| `mobile_core/ocr_client.py` | OCR 微服务客户端 |
| `mobile_core/farmer.py` | 养号器 (含 V9 兴趣记忆模型) |
| `mobile_core/commenter.py` | 智能评论器 (含 W2 NLP反指纹) |
| `mobile_core/page_detector.py` | 页面检测器 (含 V10 缓存) |
| `mobile_core/device_optimizer.py` | 设备优化 (含 W1 隐蔽IP轮换) |
| `mobile_core/agent_loop.py` | LLM Agent 循环 |
| `mobile_core/pipeline/engine.py` | Pipeline 执行器 |
| `mobile_core/pipeline/loader.py` | YAML 加载器 |
| `mobile_core/injector/SensorHalService.java` | V4: 触控注入 (伪装类名) |
| `mobile_core/injector/build.sh` | dex 编译脚本 |
| `config/pipelines/intercept_comment.yaml` | 截流 Pipeline 定义 |
| `config/pipelines/farm_session.yaml` | 养号 Pipeline 定义 |
| `tools/stealth_ime/` | Stealth IME APK 源码 |
| `tools/assisted_crop.py` | 半自动模板采集 |
| `data/commented_posts.json` | 评论去重记录 |

---

## 🧠 Pipeline YAML 编写指南

当用户需要创建新的 Pipeline YAML 时，遵循以下规范：

### 节点结构

```yaml
NodeName:
  description: "节点描述"
  probability: 0.5          # 可选，概率执行
  quota_check: "daily_xxx"  # 可选，配额检查
  recognition:
    type: ocr_text | template_match | activity_detect | custom | direct_hit | and | or
    expected: "识别目标"
    roi: [x%, y%, w%, h%]   # 感兴趣区域(归一化)
    threshold: 0.7
  action:
    type: tap | launch_app | clipboard_input | wait | navigate | press_back | llm_generate | ip_rotate | custom | do_nothing | human_swipe
    target: true | [x, y] | "{{anchor.xxx}}"
    noise: 15               # 坐标噪声像素
  next:
    - NextNode
    - "[JumpBack]PopupHandler"  # 弹窗拦截
  on_error:
    - ErrorRecoveryNode
  timeout: 10000             # 毫秒
  rate_limit: 1000           # 识别间隔
  pre_delay: [200, 800]     # 前置随机延迟
  post_delay: [300, 1000]   # 后置随机延迟
  tags: ["watchdog"]
```

### 变量引用

- `{{context.xxx}}` — 来自 CLI `--context` 或代码注入
- `{{anchor.xxx}}` — 来自前序节点 `output_anchor` 输出
- `{{config.section.field}}` — 来自 config.yaml

### 组合识别

```yaml
recognition:
  type: and  # 所有条件必须满足
  all_of:
    - type: activity_detect
      expected: "post_detail"
    - type: ocr_text
      expected: "说点什么"
      roi: [0.0, 0.85, 1.0, 0.15]
```

```yaml
recognition:
  type: or  # 任一条件满足即可
  any_of:
    - type: template_match
      template: "tab_home"
    - type: ocr_text
      expected: "首页"
```

---

## 🏋 YOLO 模型训练

```bash
cd /Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine

# 1. 批量截屏采集训练数据
python tools/batch_screencap.py

# 2. 数据增强
python tools/augment_dataset.py

# 3. 准备数据集 (V4 格式)
python training/xhs_yolo/prepare_dataset_v4.py

# 4. 训练 YOLOv8s
python training/xhs_yolo/train_yolo_v4.py

# 5. 分析数据集分布
python training/xhs_yolo/analyze_dataset.py
```

模型配置：`models/xhs_ui_yolo_v4/config.yaml`

---

## 📊 日志与监控

所有日志为结构化 JSON 格式：

```bash
# 实时查看评论器日志
python start_mobile_driver_v2.py --action auto 2>&1 | grep '"module": "commenter"'

# 查看今日评论记录
cat data/commented_posts.json | python3 -m json.tool

# 重置去重记录（换号后）
rm data/commented_posts.json
```

---

**系统提示**：收到与小红书真机自动化相关的任务时，你必须优先参考本技能文件，使用 `python start_mobile_driver_v2.py` 统一入口执行操作，而不是去写独立的 Python 脚本或使用旧版 `scripts/xhs_mobile_driver.py`。所有配置修改优先通过 `config.yaml` 或环境变量完成。

---

## 🛡 反风控加固清单 (三轮 22 项)

引擎经过三轮深度反风控审计和加固，覆盖以下维度：

### 第二轮 — 设备端本地取证对抗 (V1-V12)

| # | 加固项 | 核心变更 |
|---|--------|----------|
| V1 | 广播→Socket | StealthIME 使用 LocalServerSocket, 零广播 |
| V2 | 截图缓存 | 500ms TTL 避免 screencap 高频 fork |
| V3 | Manifest 清理 | 移除 Receiver 静态注册 |
| V4 | 进程名伪装 | SensorHalService + framework-ext.dex |
| V5 | 延迟分布 | 对数正态 + 5%漂移 + 1%中断 |
| V6 | 端口随机 | randint(10000, 60000) 替代硬编码 |
| V7 | 键盘 UI | 无文字灰色占位视图 |
| V8 | 双击偏移 | 第二击 ±8px + pressure 变化 |
| V9 | 行为记忆 | 20 步滑动窗口兴趣模型 |
| V10 | dumpsys 缓存 | 2s TTL 减少 adb 调用 |
| V11 | dex 清理 | 加载后立即 rm -f |
| V12 | 命令合并 | clean_screenshot 4-6次→2-3次 |

### 第三轮 — 后端大数据 + NLP 对抗 (W1-W10)

| # | 加固项 | 核心变更 |
|---|--------|----------|
| W1 | IP 轮换 | 随机阈值 + 30-120s 延迟 + svc data 隐蔽模式 |
| W2 | 评论 NLP | 20+ Spintax 模板 + 错别字/口癖后处理 |
| W3 | IME 包名 | com.android.providers.settings 伪装 |
| W4 | 启动方式 | am start 替代 monkey |
| W5 | 动画缩放 | 0.0 → 0.5 避免全禁检测 |
| W6 | 冷却分布 | 对数正态 + 10% 长冷却 |
| W7 | gauss 残留 | 全部替换为 lognormal |
| W8 | 重启延迟 | 固定 2s → random(1.5, 5.0) |
| W9 | API Key | 源码中空默认, 走环境变量 |
| W10 | logcat | IME 激活后清除日志缓冲区 |

### Stealth IME 部署 (APK v3)

```bash
# ⚠️ 如果从旧版升级, 必须先卸载旧包名
adb uninstall com.android.inputservice.core

# 安装新版 (W3: 新包名)
adb install tools/stealth_ime/app/build/outputs/apk/release/stealth-ime-v3.apk

# 激活
adb shell ime enable com.android.providers.settings/.StealthIME
adb shell ime set com.android.providers.settings/.StealthIME
```

### SensorHalService dex 部署

```bash
# 正常情况下 init_flow 会自动处理, 无需手动操作
# dex 推送后会被命名为 framework-ext.dex (V4 伪装)
# 加载完成后 dex 文件会自动删除 (V11 清理)

# 如需手动重建:
cd mobile_core/injector && bash build.sh
```
