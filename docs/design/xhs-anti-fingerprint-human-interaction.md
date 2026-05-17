# XHS CLI 反指纹与拟人交互设计文档 (Anti-Fingerprint & Human Interaction Design)

## 1. 背景与挑战 (Background)

在自动化操作小红书 (XHS) 平台（如自动发布、评论、点赞、收藏）时，频繁遇到严格的平台风控机制。XHS 的风险控制 (Risk Control) 系统会收集并分析客户端的行为轨迹，主要的机器特征识别点包括：

*   **轨迹完美度**：直线移动的鼠标指针、匀速的击键间隔。
*   **交互瞬间性**：在元素不可见时触发点击，引发“零毫秒瞬间滚动”。
*   **非可信事件**：由 WebDriver 注入的标准 DOM 事件通常 `isTrusted=false`，极易被识别。
*   **探测探针**：XHS 会在前端重写 `querySelector` 等原生方法，探测执行环境。

之前采用的直接调用 `element.click()` 或简单的固定 `time.sleep()` 延时，已被证明无法长期绕过无感验证（如滑块验证或强踢下线）。

## 2. 设计目标 (Objectives)

1.  **高隐匿性**：全面消除自动化脚本的机器指纹，行为轨迹需符合人类生物学特征（Fitts's Law、非匀速移动、正态分布落点）。
2.  **低维护成本**：不将复杂的物理引擎（重力、贝塞尔曲线）直接硬编码到核心业务层中，降低代码臃肿度和平台策略变更带来的维护压力。
3.  **高兼容性**：抽象出底层交互调用，向下兼容主流的 Stealth Browsers（如 `cloakbrowser`，`camoufox`）。

## 3. 架构选型：方案 3（专业级防指纹库委托）

经过评估，最终确定采用 **“方案 3：接入专业级反指纹库委托模式”**。核心思想是**将物理级别的模拟行为下沉至专业的反侦察驱动层**，业务层仅做容错和状态兜底。

项目中已经集成了专门的反指纹库 `cloakbrowser`。该库内部包含成熟的 `human` 模块。

### 3.1 核心机制：Monkey Patching (动态替换)

当我们在 `browser.py` 中初始化 `cloakbrowser` 并传入 `humanize=True` 时：

```python
# browser.py
from cloakbrowser import launch
self.browser = launch(headless=self.headless, humanize=True)
```

`cloakbrowser` 会在运行时拦截（Patch）Playwright 的 `Page` 和 `Locator` 对象的交互行为。这意味着对 `element.click()` 的调用不再是瞬间完成的机器指令，而是由底层自动转化为：
1. 计算元素的 Bounding Box。
2. 生成基于真实物理引擎的贝塞尔曲线（Bezier Curve）鼠标轨迹点阵。
3. 执行非匀速的指针移动（起步快，对准慢）。
4. 带有物理延迟的 `mousedown` 与 `mouseup`。

### 3.2 深度防御：CDP Isolated Worlds (隔离世界)

为防止 XHS 前端脚本通过重写 DOM Getter/Setter 来捕获自动化行为，`cloakbrowser.human` 底层使用了 CDP (Chrome DevTools Protocol) 的 **Isolated Worlds** 进行坐标计算和焦点探测。通过 CDP 触发的键盘和鼠标事件天生带有 `isTrusted=true` 属性，与真实用户发出的硬件级事件在 JS 层面无法区分。

## 4. 业务层实现 (Implementation Details)

为了契合上述底层架构，业务代码（如 `xhs_cli/client.py`）无需重复造轮子实现曲线移动，只需封装容错及必要的前置动作：

### 4.1 鼠标点击 (`_human_click`)

```python
def _human_click(self, element):
    try:
        # 强制防错：避免 Playwright 瞬间滚动，这是被判为 Bot 的第一大诱因
        element.scroll_into_view_if_needed()
        self._human_wait(0.1, 0.4) # 人类视野寻找目标的反应时间
        
        # 委托给 cloakbrowser 拦截器执行贝塞尔移动
        element.click() 
    except Exception as e:
        logger.warning("Human click failed, falling back to simple click")
        element.click()
```

### 4.2 键盘输入 (`_human_type`)

```python
def _human_type(self, element, text: str):
    try:
        self._human_click(element) # 先模拟物理点击获取焦点
        self._human_wait(0.1, 0.3)
        # 委托给 cloakbrowser 拦截器执行拟人键程延迟
        element.type(text)
    except Exception as e:
        # ... fallback
```

## 5. 舍弃的替代方案 (Alternatives Considered)

| 方案 | 描述 | 为什么被舍弃 |
| :--- | :--- | :--- |
| **方案 1: Python 层手写贝塞尔曲线** | 在 `client.py` 中手写复杂的随机数、控制点计算，循环调用 `page.mouse.move`。 | 1. 干扰了 `cloakbrowser` 内部的 `_CursorState` 追踪机制。<br>2. 依然是 `isTrusted=false` 的模拟，容易被前端探针识破。<br>3. 算法写死在业务代码中，极其臃肿。 |
| **方案 2: 常量等待延迟** | 在所有动作前后加上 `time.sleep(2)`。 | 延时固定，小红书风控系统很容易通过统计学方差分析（Variance Analysis）将其识别为机器任务队列。 |

## 6. 总结 (Conclusion)

通过“业务层做视野检查（`scroll_into_view_if_needed`） + 驱动层做物理模拟（`cloakbrowser` Patch）”的协同策略，XHS CLI 获得了企业级的反指纹自动化能力。这在确保代码极简、易维护的同时，最大化地绕过了小红书平台的风险控制系统。
