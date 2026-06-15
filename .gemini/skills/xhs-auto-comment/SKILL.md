---
name: xhs-auto-comment
description: AI-driven interactive automation for Xiaohongshu (XHS) commenting and lead generation (截流). Uses coordinate-based interactions to bypass risk controls.
---

# 小红书防风控截流自动化技能 (XHS Auto Comment)

这是一个专门用于小红书地陪/获客（截流）的交互式自动化技能。当你收到类似"开始小红书截流"、"执行地陪回复任务"等指令时，必须严格遵守本文件的规范执行。

## 核心设计理念：感知与执行分离
你（AI）作为"大脑"，不再自己编写复杂的 JS 提取脚本，而是使用专属的移动端物理适配器 `/Users/qi/ai-code-project/XHS_ALL_IN_ONE/scripts/xhs_mobile_driver.py` 来控制 Android 设备（真机/云手机）并执行物理操作。

## 🚨 绝对防风控铁律 (Red Lines)
执行本技能时，**绝对禁止**以下行为：
1. **禁止使用 DOM 触发点击**：绝对不能生成包含 `element.click()` 的代码。所有的点击必须基于提取到的坐标 `(x, y)`，使用 `click_at_xy(x, y)` 完成。
2. **禁止直接赋值输入**：绝对不能使用 `input.value = "..."`。所有的文本输入必须调用适配器的 `--reply` 功能（底层使用 `human_type` 逐字输入）。
3. **禁止盲目操作**：任何关键操作（进入帖子、发送评论）之后，必须要求适配器执行截图 (`capture_screenshot()`)，或通过日志输出验证当前状态。

## 标准操作程序 (SOP)

每次执行截流任务，必须按顺序执行以下 4 个阶段，绝不跳步：

### Phase A: 扫描与养号 (Scan & Farming)
1. 确保在小红书搜索页面。
2. 执行扫描指令获取当前帖子列表：
   ```bash
   python /Users/qi/ai-code-project/XHS_ALL_IN_ONE/scripts/xhs_mobile_driver.py --device <adb_serial_or_ip> --action scan
   ```
3. 阅读终端输出的 JSON 格式帖子列表（包含标题、作者、坐标）。
4. **🌾 养号动作 (Farming - 必须执行)**：为了稀释账号的业务纯度，**你必须在每 3-5 次正常截流之间，随机点开一个完全无关的热门帖子**（例如搞笑、日常分享）。执行抓取(`--action extract`)指令进入，强制停留 10-15 秒假装阅读，如果不截流则执行伪装关闭动作（不发任何评论），然后再重新扫描。
5. **截流决策**：完成养号后，分析标题，在心中选定一个需求意向强烈的目标帖子。

### Phase B: 深度阅读与评论抓取 (Read & Extract)
1. 执行抓取指令，传入选定帖子的坐标 `(x, y)`：
   ```bash
   python /Users/qi/ai-code-project/XHS_ALL_IN_ONE/scripts/xhs_mobile_driver.py --device <adb_serial_or_ip> --action extract --x <target_x> --y <target_y>
   ```
2. 这个指令会自动点击帖子，提取帖子正文，并向下滚动抓取前排的评论及"回复"按钮坐标。
3. 阅读终端输出的正文和评论树。

### Phase C: AI 思考与话术生成 (Think & Generate)
1. **情感与意图风控 (🚨 必须优先执行)**：仔细阅读正文和评论。如果判定帖子是"避雷贴"、"吐槽贴"或带有强烈的防备/负面情绪，**必须触发熔断**，明确声明"因情绪风险放弃截流"，然后直接跳到 Phase D 执行关闭操作。
2. 如果帖子安全且有意向，找出最适合截流的目标（如：有人问"多少钱"、"求推荐"）。请注意识别"楼中楼"上下文，避免强行介入已达成交易的对话。
3. 动态生成一段真实、自然、贴合上下文的回复文本。**拒绝千篇一律的废话**。

### Phase D: 执行物理回复与伪装 (Act & Disguise)
1. **如果是正常回复**：获取选定目标的回复按钮坐标或主评论框坐标，执行回复指令：
   ```bash
   # 默认执行为 DRY RUN 模式（只输入文本不点击发送）。如果需要真实发送，请追加 --live 参数。
   # 发送完成后，弹窗将默认保持打开状态，如果需要关闭帖子，请追加 --close 参数。
   python /Users/qi/ai-code-project/XHS_ALL_IN_ONE/scripts/xhs_mobile_driver.py --device <adb_serial_or_ip> --action reply --x <reply_x> --y <reply_y> --text "<你生成的回复话术>"
   ```
2. **如果是触发熔断或跳过**：不要执行回复。直接下发任意点击空白处或关闭弹窗的指令，模拟人类"看了一眼觉得没意思就关掉"的游荡行为。
3. **行为伪装**：在多轮循环中，你可以主动提出随机点开一两个毫不相干的帖子，阅读两秒后直接退出，以此污染风控日志，打破机械执行循环。
4. 任务结束，向用户汇报战果并请用户在浏览器中查验结果。

---
**系统提示**：如果你理解了以上规则，在未来的任务中，你必须优先使用 `python /Users/qi/ai-code-project/XHS_ALL_IN_ONE/scripts/xhs_mobile_driver.py` 来处理小红书任务，而不是去写 Web 端的 JS 脚本。
