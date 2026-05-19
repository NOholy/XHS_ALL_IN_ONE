# 移动端防风控核心：五层渗透模型落实 — v3.0

> **v3.0 更新 (2026-05-20)**：完成"去 Accessibility 化 + OCR 视觉体"重构。此文档描述的所有防风控策略均已在 `scripts/xhs_mobile_driver.py` 中落地实现。

## 1. 行为轨迹伪装 (Broke the "Perfect Machine")

### 1.1 高斯分布停留 (`_human_sleep`)
废弃了 `random.uniform`。所有的页面停留都基于 `np.random.normal(mu, sigma)` 计算，模拟人类"大部分时间看几秒就走，极少数时间看很久"的正态分布特性。

### 1.2 物理触控点击 (`_click_with_noise`)
废弃了所有 `d(text="xxx").click()` 的 API 级点击。当前实现：
```python
# 1. 坐标 ±15px 随机噪声
# 2. touch.down → 40~120ms 按压延迟 → touch.up
# 3. 模拟真实手指的物理按压过程
```

### 1.3 贝塞尔曲线滑动 (`_physical_swipe`)
废弃了 UIA2 的线性 `d.swipe()`。当前实现：
*   二阶贝塞尔曲线生成 18~28 个中间采样点。
*   控制点带 ±150px 随机偏移，形成自然的弧形轨迹。
*   速度非线性：起止段慢（15~25ms/点），中间段快（5~10ms/点），模拟人类手腕的加减速。

### 1.4 犹豫滑动 (`_human_swipe`)
10% 概率触发"回滑"（假装看漏了回头看），起止坐标带 ±60px 横向漂移，模拟手指不稳定性。

## 2. 发呆期与冷却时间 (Cooldown)

*   在 `action_reply` 真实点击"发送"按钮并通过 **OCR 验证** 成功后，强制切入 **1~2 分钟的高斯发呆期** `self._human_sleep(90.0, 30.0)`。
*   配合外层 AI Agent 的宏观调度，形成 `100:30:10:2` 的健康转化漏斗。

## 3. 滑块熔断阻断 (Risk Intervention) — 已升级为纯视觉

### v2 (旧方案，已废弃)
```python
# 🚫 旧方案：通过 UI Tree 文本查询触发
if self.d(textContains="拖动滑块").exists:  # 会暴露无障碍服务
```

### v3 (当前方案)
```python
# ✅ 新方案：单次截屏 + OpenCV 模板匹配，零 UI Tree 接触
img = self.d.screenshot(format='opencv')
slider = self._find_template("slider_puzzle", threshold=0.8, screen_img=img)
verify = self._find_template("security_verification", threshold=0.8, screen_img=img)
```

*   一旦匹配到风控弹窗，**脚本立刻死锁（进入无限 while 循环）** 并打印高危报警。
*   复用同一张截图进行双重匹配（slider + verification），避免冗余截屏开销。
*   绝对不尝试用机器去硬解滑块，保住账号最后一丝生命线。

## 4. LLM 千人千评 (LLM Dynamic Gen)

*   系统每次都会通过 **PaddleOCR 对截屏进行全文字识别**，将帖子标题、正文、评论树作为 Context 传递给 LLM。
*   LLM 生成极其自然、带有网感的非标准话术，从根本上杜绝了文本哈希风控。
*   评论发送后，再次通过 **OCR 回查** 验证评论是否成功挂载在 UI 上，形成完整闭环。

## 5. 物理隔离与去指纹 (Deployment & Stealth)

### 5.1 零 Accessibility 架构
*   代码中 **100% 剔除了对 `d(...)` / `d.xpath(...)` 的调用**。
*   感知层完全交给 PaddleOCR + OpenCV，执行层只使用 `touch.down/move/up` 和 `press`。
*   因此可以在手机设置中 **关闭 uiautomator 的无障碍服务**，彻底消除自动化指纹。

### 5.2 前台守护 (`_ensure_app_foreground`)
*   每次执行前检查 `d.app_current()['package']` 是否为 `com.xingin.xhs`。
*   如果 App 被来电、通知或其他应用打断，自动拉回前台，防止脚本对着空气操作。

### 5.3 模板前置校验 (`_validate_templates`)
*   执行需要视觉定位的动作前（reply/extract），强制检查 `data/ui_templates/` 目录下的特征图是否齐全。
*   缺失时立刻阻断并输出修复指令，避免盲执行浪费时间。

### 5.4 部署纪律
*   **拔掉数据线**，使用 Wi-Fi ADB (`adb connect <ip>:5555`) 或局域网连接。
*   使用 **4G/5G 纯流量卡**，不要连公司 Wi-Fi。
*   矩阵号之间通过 **开关飞行模式** 刷新基站 IP，确保出口 IP 隔离。

## 6. 输入模式安全等级

| 模式 | 安全等级 | 描述 |
|---|---|---|
| `clipboard` | ⭐⭐⭐ | 剪贴板中转注入，速度快，隐蔽性好 |
| `opencv` | ⭐⭐⭐⭐⭐ | 中文→拼音→OpenCV 逐键定位→物理点击，零输入法痕迹 |
| ~~`adb`~~ | ❌ 已删除 | send_keys 直接暴露自动化输入法，已于 v2.1 彻底删除 |
