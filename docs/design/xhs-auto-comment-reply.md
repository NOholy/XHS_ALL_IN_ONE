# 自动化评论与回复架构设计文档

> **版本**: v1.0  
> **日期**: 2026-05-17  
> **状态**: 草案  

---

## 1. 背景与目标

当前 `XHS_ALL_IN_ONE` 系统已经实现了通过 `auto_tasks.py` 自动搜索热门笔记并改写为自己的笔记进行发布的自动化链路（搬运/混剪模式）。
为了进一步提高账号活跃度、增加曝光量以及实现自动引流，需要引入**自动化互动**能力，即：在抓取到热门素材（笔记）后，自动在笔记下方发表相关评论，或者寻找热门评论进行跟帖回复。

由于频繁评论属于高风控操作，此功能**必须基于 `xhs-cli` 的浏览器自动化（CLI 模式）**来实现，以最大程度模拟真人行为。

### 1.2 核心业务流程

1. **自动模式 (触发与抓取)**：定时任务触发，通过关键词搜索获取高赞笔记。
2. **状态评估**：根据该笔记的评论区状态，决定是**发表一级评论**还是**回复现有评论**。
3. **AI 改写生成**：结合笔记正文，并从**用户配置的评论模版库**中随机抽取一条，通过 AI 大模型按照上下文语境进行改写生成，确保评论既有设定的话术方向，又贴合当前笔记内容。
4. **手动模式支持**：允许用户在查看抓取到的笔记时，直接输入评论内容并触发发送，后端通过同样的浏览器自动化接口完成评论。
5. **自动化执行**：无论是自动生成还是手动触发，底层均通过 CLI 适配器操作浏览器，将内容真实地打字输入并发送。

---

## 2. 数据库与数据模型扩展

需要在现有的 `AutoTask` 表中新增针对评论互动功能的配置字段：

**文件**: `backend/app/models/auto_task.py`

```python
class AutoTask(Base):
    # ... 现有字段 ...

    # 自动化评论与回复开关及配置
    enable_auto_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    comment_templates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True) # 用户提供的评论模版列表
    comment_instruction: Mapped[str] = mapped_column(Text, default="请根据笔记内容，结合选中的评论模版进行改写。要求符合真实用户口吻，字数控制在20字左右。")
    
    enable_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_templates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True) # 用户提供的回复模版列表
    reply_instruction: Mapped[str] = mapped_column(Text, default="请针对这篇笔记中的这条评论，结合选中的模版进行回复，制造话题感。")

    # 统计数据
    total_comments: Mapped[int] = mapped_column(Integer, default=0)
    total_replies: Mapped[int] = mapped_column(Integer, default=0)
```

需要在 Schema 中同步增加这些字段（`AutoTaskCreateRequest` 和 `AutoTaskUpdateRequest`）。

---

## 3. xhs-cli 底层能力补充

`xhs-cli/client.py` 已经支持了发表一级评论：`post_comment(note_id, content)`。
还需要新增**回复二级评论**的方法：`reply_comment(note_id, target_comment_id, content)`。

### 3.1 回复评论的 DOM 交互设计

```python
def reply_comment(self, note_id: str, comment_id: str, content: str, xsec_token: str = "") -> bool:
    """回复一条特定的评论。"""
    # 1. 导航到笔记页面，展开评论区
    # 2. 定位到对应的 comment_id 的 DOM 元素（通常评论的父容器带有特定的 ID 或 dataset）
    # 3. 找到该评论下方的 "回复" 按钮并点击，唤起评论输入框
    # 4. 在输入框中 type(content)
    # 5. 点击发送
    # 6. 验证是否发送成功
```

> **注意**：如果提取和定位特定 comment_id 的 DOM 比较困难，初期可以退化为寻找**点赞数最高**的前 3 条评论，随机挑一条点击"回复"按钮，而无需强绑定 `comment_id`。

---

## 4. API 适配器与手动干预接口

由于评论是一个动作（Action），它本质上是写入操作，但由于风控原因，我们将把它归入 **CLI 独占方法**。

### 4.1 CLI 适配器扩展
**文件**: `backend/app/adapters/xhs/cli_pc_api_adapter.py`

```python
def post_comment(self, note_url: str, content: str) -> tuple[bool, str, Any]:
    # ... 调用 client.post_comment ...

def reply_comment(self, note_url: str, comment_id: str, content: str) -> tuple[bool, str, Any]:
    # ... 调用 client.reply_comment ...
```

### 4.2 手动评论 API 支持
为了支持**用户手动评论**，需要新增一个独立的路由。
**文件**: `backend/app/api/platforms/xhs/pc.py` (或新增 `interactions.py`)

```python
@router.post("/notes/comments/post")
def manual_post_comment(
    payload: PostCommentRequest, # {account_id, note_url, content, reply_to_comment_id}
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_xhs_pc_api_adapter_factory)
):
    # 1. 鉴权并获取对应的 PC 账号 Cookies
    # 2. 强制使用 CLI 适配器（即便当前 read_client_type 是 direct，写评论也必须走 CLI 防风控）
    # 3. 如果 payload.reply_to_comment_id 存在，调用 adapter.reply_comment
    # 4. 否则，调用 adapter.post_comment
    # 5. 返回评论状态
```

---

## 5. 业务逻辑编排 (auto_tasks.py)

在现有的 `/auto-tasks/{task_id}/run` 接口中，增加互动阶段的逻辑：

### 阶段一：现有逻辑（找素材 -> 改写 -> 存草稿/发布）
（保持不变）

### 阶段二：互动逻辑 (如果开启了 enable_auto_comment 或 enable_auto_reply)

1. **获取评论区现状**：
   使用 `adapter.get_note_comments(best_note_url)` 抓取当前热门笔记的前 20 条评论。

2. **策略选择**：
   - 如果评论列表为空，或者没有符合条件的评论：**执行一级评论策略**（前提是开启了 `enable_auto_comment`）。
   - 如果评论列表中有高赞评论（如赞数 > 5），且开启了 `enable_auto_reply`：**执行回复策略**。

3. **调用 AI 生成文案**：
   **一级评论生成**：
   ```python
   template = random.choice(task.comment_templates) if task.comment_templates else "太赞了，学到了！"
   prompt = f"笔记标题：{title}\n笔记正文：{body}\n选定模版：{template}\n请根据笔记内容对选定模版进行改写，使之符合当前语境：{task.comment_instruction}"
   generated_comment = ai_client._complete(..., user_prompt=prompt)
   ```
   
   **回复评论生成**：
   ```python
   target_comment = max(comments, key=lambda x: x.get("like_count", 0))
   template = random.choice(task.reply_templates) if task.reply_templates else "说的对，我也是这么觉得的。"
   prompt = f"笔记标题：{title}\n目标评论：{target_comment['content']}\n选定模版：{template}\n请针对目标评论，结合选定模版进行改写回复：{task.reply_instruction}"
   generated_reply = ai_client._complete(..., user_prompt=prompt)
   ```

4. **执行互动**：
   调用 `adapter.post_comment` 或 `adapter.reply_comment` 将生成的文字发送到小红书。

5. **更新进度与记录**：
   将互动的成功与否记录到 `Task` 追踪表的 payload 中，并递增 `AutoTask.total_comments` 等计数器。

---

## 6. 异常处理与频率控制

小红书对机器行为非常敏感，特别是评论和回复：

1. **降频控制**：在调用 AI 和实际发出评论之间，加入随机 `sleep(3~8秒)`。
2. **静默失败**：由于网络或 DOM 变化，评论极易失败（例如输入框未找到）。这些错误被标记为 `Warning`，且**不会**导致整个 AutoTask 判定为失败。毕竟核心任务（搬运发布）可能已经完成。
3. **内容安全审查**：AI 生成的评论内容最好经过一次内部的极简安全词过滤，避免发出引战或违禁词导致账号立刻封禁。

---

## 7. 实施步骤

**Phase 1: 数据模型与 AI 接口**
- 在 `AutoTask` 模型中增加 `enable_auto_comment` 等 4 个字段。
- 编写 alembic migration 脚本。
- 在 FastAPI 的 schema 和 router 中暴露这些字段的 CRUD。

**Phase 2: 底层交互实现**
- 在 `xhs-cli/client.py` 中实现 `reply_comment` 方法，处理 DOM 点击。
- 在 `CliXhsPcApiAdapter` 中增加 `post_comment` 和 `reply_comment`。

**Phase 3: 业务流水线串联**
- 在 `auto_tasks.py` 的 run 逻辑最后部分，追加阶段二（抓评论 -> AI 生成 -> 调用适配器）。
- 进行端到端的真实环境测试。
