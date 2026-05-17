# 小红书 API 读写分离架构设计文档

> **版本**: v1.0  
> **日期**: 2026-05-17  
> **状态**: 已批准  

---

## 1. 背景与目标

### 1.1 现状分析

当前 `XHS_ALL_IN_ONE` 项目通过 `Spider_XHS` SDK 直接发送 HTTP 请求（逆向 API）来完成所有小红书交互。这种方式在高频读操作（搜索、抓取、监控刷新）场景下极易触发平台风控（滑块验证码、账号限流、Cookie 失效）。

同时项目已引入 `xhs-cli` 子模块，它基于 `cloak`/`camoufox` 无头浏览器，模拟真实用户浏览行为提取 `__INITIAL_STATE__` 数据，具有天然的反风控优势。

### 1.2 核心目标

实现 **"读操作走 CLI 模拟人，写操作走 Direct 直连 API"** 的混合调用架构：

- **读操作**（搜索、抓取、监控）：走浏览器自动化，降低风控触发率
- **写操作**（发布、上传素材）：走直连 API，保证结构化数据组装的精确性和速度

---

## 2. 系统架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FastAPI 路由层                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────┐ │
│  │  pc.py     │  │  crawl.py  │  │monitoring.py│  │  creator.py    │ │
│  │  (读)      │  │  (读)      │  │  (读)       │  │  (写)          │ │
│  └─────┬──────┘  └─────┬──────┘  └──────┬──────┘  └───────┬────────┘ │
│        │               │               │                 │          │
│  ┌─────▼───────────────▼───────────────▼──┐    ┌─────────▼────────┐ │
│  │   get_xhs_pc_api_adapter_factory()     │    │ get_creator_...()│ │
│  │   → 读取 xhs_read_client_type          │    │ → 始终返回       │ │
│  │   → 返回 CLI 或 Direct 适配器           │    │   Direct 适配器  │ │
│  └─────┬──────────────────────────────────┘    └─────────┬────────┘ │
└────────┼─────────────────────────────────────────────────┼──────────┘
         │                                                 │
   ┌─────▼────────────────────┐               ┌───────────▼──────────┐
   │ CliXhsPcApiAdapter       │               │ XhsCreatorApiAdapter │
   │ (浏览器模拟 + 数据提取)   │               │ (HTTP 直连 API)      │
   │                          │               │                      │
   │ ┌──────────────────────┐ │               │ ┌──────────────────┐ │
   │ │  xhs_cli.XhsClient  │ │               │ │ Spider_XHS SDK   │ │
   │ │  ┌────────────────┐  │ │               │ │ xhs_creator_apis │ │
   │ │  │  BrowserEngine │  │ │               │ └──────────────────┘ │
   │ │  │  cloak(默认)   │  │ │               └──────────────────────┘
   │ │  │  camoufox      │  │ │
   │ │  └────────────────┘  │ │
   │ └──────────────────────┘ │
   └──────────────────────────┘
```

---

## 3. 配置层设计

### 3.1 Settings 模型变更

**文件**: `backend/app/core/config.py`

将原有的单一 `xhs_client_type` 拆分为读写独立的两个配置项：

```python
class Settings(BaseSettings):
    # ...

    # 读操作策略：cli = 浏览器模拟（抗风控），direct = HTTP 直连
    xhs_read_client_type: str = "cli"

    # 写操作策略：direct = HTTP 直连（结构化组装），cli = 浏览器自动化
    xhs_write_client_type: str = "direct"
```

### 3.2 环境变量 / YAML 配置

```yaml
# config/default.yaml
xhs:
  read_client_type: cli      # 读操作默认走浏览器
  write_client_type: direct  # 写操作默认走直连
```

或通过 `.env`：

```env
XHS_READ_CLIENT_TYPE=cli
XHS_WRITE_CLIENT_TYPE=direct
XHS_BROWSER_ENGINE=cloak
XHS_HUMANIZE=1
```

---

## 4. 接口分类与路由策略

### 4.1 完整接口清单与读写分类

经过对项目所有 API 路由和 Service 层的完整审计，以下是所有涉及小红书外部接口调用的端点：

#### 读操作接口（走 CLI 适配器）

这些接口的共同特征是：**高频调用、不修改平台数据、最易触发风控**。

| 模块文件 | 路由 | 业务功能 | 调用的适配器方法 |
|:---|:---|:---|:---|
| `pc.py` | `POST /xhs/pc/search/notes` | 搜索笔记 | `search_note()` |
| `pc.py` | `POST /xhs/pc/notes/detail` | 笔记详情 | `get_note_info()` |
| `pc.py` | `POST /xhs/pc/notes/comments` | 笔记评论 | `get_note_comments()` |
| `pc.py` | `POST /xhs/pc/users/notes` | 用户笔记列表 | `get_user_notes()` |
| `pc.py` | `POST /xhs/pc/homefeed/recommend` | 首页推荐 | *(当前为 mock)* |
| `crawl.py` | `POST /xhs/crawl/search-notes` | 批量搜索抓取 | `search_note()` |
| `crawl.py` | `POST /xhs/crawl/note-urls` | 批量 URL 抓取 | `get_note_info()` |
| `crawl.py` | `POST /xhs/crawl/user-notes` | 用户笔记抓取 | `get_user_notes()` |
| `crawl.py` | `POST /xhs/crawl/data` | 通用数据采集(SSE) | `search_note()` / `get_note_info()` / `get_note_comments()` |
| `monitoring.py` | `POST /xhs/monitoring/targets/{id}/refresh` | 监控目标刷新 | `search_note()` / `get_note_info()` / `get_user_notes()` |
| `monitoring_crawl_service.py` | *(定时调度)* | 自动化监控采集 | 同上 |

#### 写操作接口（走 Direct 适配器）

这些接口的共同特征是：**低频调用、需要精确的请求签名和结构化 Payload 组装**。

| 模块文件 | 路由 | 业务功能 | 调用的适配器方法 |
|:---|:---|:---|:---|
| `creator.py` | `POST /xhs/creator/topics/search` | 话题检索 | `get_topic()` |
| `creator.py` | `POST /xhs/creator/locations/search` | 地点检索 | `get_location_info()` |
| `creator.py` | `POST /xhs/creator/assets/upload` | 素材上传 | `upload_media()` |
| `creator.py` | `POST /xhs/creator/publish/image` | 图片笔记发布 | `post_note()` |
| `creator.py` | `POST /xhs/creator/publish/video` | 视频笔记发布 | `post_note()` |
| `creator.py` | `GET /xhs/creator/published` | 已发布列表 | `get_published_notes()` |
| `publish.py` | `POST /publish/assets/{id}/upload` | 发布素材上传 | `upload_media()` |
| `publish.py` | `POST /publish/jobs/{id}/publish` | 发布任务执行 | `upload_media()` + `post_note()` |

#### 登录/认证接口（保持 Direct，不可替换）

登录流程依赖 `Spider_XHS` 提供的二维码生成和验证码 API，与数据抓取完全不同，必须保持直连：

| 模块文件 | 路由 | 业务功能 |
|:---|:---|:---|
| `login_sessions.py` | `POST /xhs/login-sessions/pc/qrcode` | PC 扫码登录 |
| `login_sessions.py` | `POST /xhs/login-sessions/creator/qrcode` | 创作者扫码登录 |
| `login_sessions.py` | `POST /xhs/login-sessions/pc/phone/*` | 手机号登录 |
| `login_sessions.py` | `GET /xhs/login-sessions/{id}` | 轮询登录状态 |

#### 纯本地接口（不涉及外部调用）

以下接口只操作本地数据库，不需要任何适配器：

| 模块文件 | 路由 | 业务功能 |
|:---|:---|:---|
| `analytics.py` | `GET /xhs/analytics/*` | 数据分析（全部本地计算） |
| `monitoring.py` | `GET/POST/PATCH/DELETE /xhs/monitoring/targets` | 监控目标 CRUD |
| `publish.py` | `GET/PATCH/DELETE /publish/jobs/*` | 发布任务管理 |
| `accounts.py` | 所有路由 | 账号管理 |

### 4.2 xhs-cli XhsClient 能力覆盖矩阵

| 适配器方法 | xhs-cli 对应方法 | 覆盖状态 | 备注 |
|:---|:---|:---|:---|
| `search_note()` | `client.search_notes()` | ✅ 完整覆盖 | |
| `get_note_info()` | `client.get_note_detail()` | ✅ 完整覆盖 | 需从 URL 提取 note_id |
| `get_note_comments()` | `client.get_note_comments()` | ✅ 完整覆盖 | client.py L824 |
| `get_user_notes()` | `client.get_user_posts()` | ⚠️ 部分覆盖 | 需从 URL 提取 user_id |
| `get_self_info()` | `client.get_self_info()` | ✅ 完整覆盖 | |
| `get_topic()` | `client.search_topics()` | ⚠️ 结构差异 | 返回格式需要适配 |
| `upload_media()` | ❌ 不支持 | ❌ 无法覆盖 | CLI 无独立上传能力 |
| `post_note()` | `client.publish_note()` | ⚠️ 流程耦合 | 上传+发布一体化，不可拆分 |

---

## 5. 代码实现方案

### 5.1 配置层改造

**文件**: `backend/app/core/config.py`

```diff
 class Settings(BaseSettings):
-    xhs_client_type: str = "cli"  # "direct" or "cli"
+    xhs_read_client_type: str = "cli"    # 读操作: "cli" or "direct"
+    xhs_write_client_type: str = "direct" # 写操作: "direct" or "cli"
```

### 5.2 PC 端工厂（读操作）

**文件**: `backend/app/api/platforms/xhs/pc.py`

```python
def get_xhs_pc_api_adapter_factory():
    """读操作适配器工厂 — 根据 xhs_read_client_type 动态返回。"""
    client_type = get_settings().xhs_read_client_type
    if client_type == "cli":
        from backend.app.adapters.xhs.cli_pc_api_adapter import CliXhsPcApiAdapter
        return CliXhsPcApiAdapter
    return XhsPcApiAdapter
```

此工厂被以下模块共享（无需修改这些消费方）：
- `crawl.py` — 通过 `from ..pc import get_xhs_pc_api_adapter_factory` 引用
- `monitoring.py` — 同上
- `monitoring_crawl_service.py` — 通过参数 `adapter_factory` 传入

### 5.3 Creator 端工厂（写操作）

**文件**: `backend/app/api/platforms/xhs/creator.py`

```python
def get_creator_api_adapter_factory():
    """写操作适配器工厂 — 默认始终使用直连 API。"""
    # 写操作目前强制走 Direct，因为 xhs-cli 不支持独立的上传/发布 API 拆分
    return XhsCreatorApiAdapter
```

**文件**: `backend/app/api/publish.py`

```python
def get_creator_publish_adapter_factory():
    """发布流程适配器工厂 — 强制走 Direct API。"""
    return XhsCreatorApiAdapter
```

### 5.4 CliXhsPcApiAdapter 完善

**文件**: `backend/app/adapters/xhs/cli_pc_api_adapter.py`

需要补全以下方法的实现：

```python
class CliXhsPcApiAdapter:
    """通过 xhs-cli 浏览器自动化实现的 PC 端读操作适配器。"""

    def search_note(self, keyword, ...) -> tuple[bool, str, Any]:
        # ✅ 已实现

    def get_note_info(self, url) -> tuple[bool, str, Any]:
        # ✅ 已实现

    def get_note_comments(self, note_url) -> tuple[bool, str, Any]:
        # ⬜ 需实现：调用 client.get_note_comments()

    def get_user_notes(self, user_url) -> tuple[bool, str, Any]:
        # ⬜ 需实现：从 URL 提取 user_id，调用 client.get_user_posts()

    def get_self_info(self) -> Any:
        # ✅ 已实现
```

### 5.5 会话共享机制

读写分离架构的关键在于 **Cookies 的无缝共享**：

```
                    ┌──────────────────────────┐
                    │  AccountCookieVersion    │
                    │  (加密存储在数据库中)     │
                    └──────────┬───────────────┘
                               │
                    decrypt → cookie_string
                               │
              ┌────────────────┼─────────────────┐
              │                                   │
    ┌─────────▼──────────┐            ┌──────────▼───────────┐
    │ CliXhsPcApiAdapter │            │ XhsCreatorApiAdapter │
    │                    │            │                      │
    │ 解析为 dict        │            │ 原样作为 header      │
    │ → context.         │            │ → requests.post(     │
    │   add_cookies()    │            │     cookies=...)     │
    └────────────────────┘            └──────────────────────┘
```

两种适配器共享同一份 Cookie 数据源（`AccountCookieVersion` 表），只是注入方式不同：
- CLI 适配器：将 cookie string 解析为 `dict`，通过 `browser.context.add_cookies()` 注入
- Direct 适配器：将 cookie string 通过 `requests` 的 `cookies` 参数或 `Cookie` header 注入

---

## 6. 浏览器引擎配置

### 6.1 引擎选择

`xhs-cli` 支持两种底层反指纹浏览器引擎，通过环境变量 `XHS_BROWSER_ENGINE` 切换：

| 引擎 | 环境变量值 | 特点 | 推荐场景 |
|:---|:---|:---|:---|
| **CloakBrowser** | `cloak`（默认） | 支持 `humanize` 仿人模式 | 生产环境首选 |
| **Camoufox** | `camoufox` | Firefox 内核级指纹伪装 | `cloak` 被封时的回退 |

### 6.2 默认配置

```
XHS_BROWSER_ENGINE=cloak    # 默认引擎
XHS_HUMANIZE=1              # 默认开启仿人行为
```

---

## 7. 风险与降级策略

### 7.1 CLI 引擎故障降级

当 CLI 适配器调用失败（浏览器启动异常、超时等），系统应支持自动回退到 Direct API：

```python
def get_xhs_pc_api_adapter_factory():
    client_type = get_settings().xhs_read_client_type
    if client_type == "cli":
        try:
            from backend.app.adapters.xhs.cli_pc_api_adapter import CliXhsPcApiAdapter
            return CliXhsPcApiAdapter
        except ImportError:
            logger.warning("xhs-cli 未安装，回退到 Direct API")
            return XhsPcApiAdapter
    return XhsPcApiAdapter
```

### 7.2 性能考量

| 维度 | Direct API | CLI 浏览器 |
|:---|:---|:---|
| 单次请求延迟 | 200-500ms | 3-8s（含浏览器启动） |
| 并发能力 | 高（无状态 HTTP） | 低（每请求一个浏览器实例） |
| 风控触发率 | 高 | 极低 |
| 资源消耗 | 低（CPU/内存） | 高（浏览器进程） |

**优化方向**（后续迭代）：
- 实现浏览器连接池（Browser Pool），复用已启动的浏览器实例
- 对高频监控任务实现请求队列，避免同时启动过多浏览器

---

## 8. 实施步骤

### Phase 1：配置层拆分（当前迭代）
1. ✅ 将 `xhs_client_type` 拆分为 `xhs_read_client_type` + `xhs_write_client_type`
2. ✅ PC 端工厂根据 `xhs_read_client_type` 动态返回适配器
3. ✅ Creator 端工厂和 Publish 工厂强制返回 Direct 适配器

### Phase 2：CLI 适配器补全
4. 完善 `CliXhsPcApiAdapter.get_note_comments()` 实现
5. 完善 `CliXhsPcApiAdapter.get_user_notes()` 实现（URL → user_id 提取）

### Phase 3：稳定性加固
6. 添加 CLI 引擎故障时的自动降级逻辑
7. 添加浏览器启动/关闭的结构化日志
8. 评估浏览器连接池的必要性

---

## 9. 文件变更清单

| 文件路径 | 变更类型 | 说明 |
|:---|:---|:---|
| `backend/app/core/config.py` | 修改 | 拆分读写配置项 |
| `backend/app/api/platforms/xhs/pc.py` | 修改 | 工厂读取 `xhs_read_client_type` |
| `backend/app/api/platforms/xhs/creator.py` | 不变 | 工厂始终返回 Direct |
| `backend/app/api/publish.py` | 不变 | 工厂始终返回 Direct |
| `backend/app/adapters/xhs/cli_pc_api_adapter.py` | 修改 | 补全 comments/user_notes 方法 |
| `xhs-cli/xhs_cli/browser.py` | 已完成 | 移除 playwright，默认 humanize=1 |
