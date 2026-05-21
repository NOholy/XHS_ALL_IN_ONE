# 自动化引擎前后端调用链路改造方案

## 1. 现状痛点分析

当前系统中存在两种自动化执行模式，都有不同程度的局限性：
1. **API 中的同步执行**：在 `backend/app/api/auto_tasks.py` 中， `/run` 接口会在一次 HTTP 请求中同步执行搜索、AI重写甚至浏览器点赞评论。这会导致 HTTP 接口长时间阻塞甚至超时。
2. **脱机的物理端脚本**：我们刚刚剥离出的 `automation_engine/start_mobile_driver_v2.py` 目前只能通过命令行手动执行，Web 后端无法动态调度它，前端也无法获知它的执行进度。

## 2. 改造目标 (The Goal)

实现 **“前端触发 -> 后端分发 -> 引擎执行 -> 状态实时回传”** 的工业级异步调度架构。让沉重的移动端物理驱动和浏览器自动化能够在后台稳定运行。

## 3. 架构设计 (Target Architecture)

建议采用 **Redis 消息队列 (Task Queue)** 配合 **轮询/WebSocket** 的异步架构。

```mermaid
graph TD
    A[Frontend React UI] -->|1. POST /api/tasks/start| B(Web Backend - FastAPI)
    B -->|2. Create Task (Pending)| C[(PostgreSQL Database)]
    B -->|3. Publish Job| D[Redis Queue]
    B -.->|4. Return Task ID| A
    D -->|5. Pop Job| E(Automation Engine Worker)
    E -->|6. Execute start_mobile_driver| F[Android Device / Browser]
    E -->|7. Update Progress| D
    E -->|8. Update Status (Completed/Failed)| C
    A -.->|9. Poll Status via GET /api/tasks/id| B
```

## 4. 详细改造步骤

### Phase 1: 引入异步任务队列机制
为系统引入轻量级的任务队列（建议使用 `Redis` 配合 `RQ` 或直接使用 `Redis Stream`，避免引入过重的 Celery）。

### Phase 2: Web 后端 (Backend) 接口改造
修改后端 API，将原本的同步等待改为异步提交。
1. **新增调度接口**：
   ```python
   # backend/app/api/device_tasks.py
   @router.post("/execute/{driver_type}")
   def start_automation_task(driver_type: str, db: Session = Depends(get_db)):
       # 1. 数据库创建 Task 记录，状态设为 pending
       task = Task(task_type=driver_type, status="pending")
       db.add(task)
       db.commit()
       
       # 2. 推送任务到 Redis 队列
       redis_client.lpush("automation_tasks", json.dumps({
           "task_id": task.id,
           "driver_type": driver_type, # e.g., 'mobile_v2' or 'browser'
           "config": {...}
       }))
       
       # 3. 立即返回
       return {"task_id": task.id, "status": "pending"}
   ```
2. **提供进度查询接口**：
   ```python
   @router.get("/{task_id}/status")
   def get_task_status(task_id: int):
       # 查询数据库或 Redis 获取当前 progress 和 status
       return {"status": "running", "progress": 45, "logs": "..."}
   ```
3. **新增设备初始化专用接口**：
   设备首次接入或重置时，必须通过 Backend 触发底层优化（关闭动画、唤醒锁）和 UI 模板采集。这完全替代了人工在终端执行脚本。
   ```python
   @router.post("/devices/{device_id}/init")
   def init_device(device_id: str, db: Session = Depends(get_db)):
       task = Task(task_type="device_init", payload={"device_id": device_id}, status="pending")
       db.add(task)
       db.commit()
       
       redis_client.lpush("automation_tasks", json.dumps({
           "task_id": task.id,
           "driver_type": "device_init",
           "device_id": device_id
       }))
       return {"task_id": task.id, "message": "设备初始化指令已下发"}
   ```

### Phase 3: 自动化引擎 (Automation Engine) 改造为 Worker
将 `automation_engine/start_mobile_driver_v2.py` 从一个无限循环的死脚本，改造成一个监听 Redis 队列的 Consumer (消费者)。

```python
# automation_engine/worker.py
import redis
import json
from start_mobile_driver_v2 import run_driver_logic

redis_cli = redis.Redis(host='localhost', port=6379)

def main_worker_loop():
    while True:
        # 1. 阻塞等待任务
        _, message = redis_cli.brpop("automation_tasks")
        job = json.loads(message)
        
        task_id = job['task_id']
        driver_type = job['driver_type']
        
        try:
            # 2. 更新状态为 running (通过 API 回调或直连 DB)
            update_status(task_id, status="running")
            
            # 3. 执行物理自动化核心逻辑
            if driver_type == 'device_init':
                # 调用 tools 下的设备初始化与视觉模板采集逻辑
                from tools.optimize_device import run_optimization
                from tools.auto_crop_templates import run_cropper
                device_id = job.get('device_id')
                run_optimization(device_id)
                run_cropper(device_id)
            elif driver_type == 'mobile_v2':
                run_driver_logic(job['config'], task_id)
                
            # 4. 执行成功，更新状态
            update_status(task_id, status="completed")
            
        except Exception as e:
            # 5. 异常捕获，更新失败状态
            update_status(task_id, status="failed", error=str(e))

if __name__ == "__main__":
    main_worker_loop()
```
*在 `run_driver_logic` 内部，原来的 `logger.info` 可以同步写一份数据到 Redis 中对应的 `task:{id}:logs` 列表，供前端拉取展示。*

### Phase 4: 前端 (Frontend) UI 改造
前端调用执行接口后，展示一个进度条或终端日志控制台。
1. 调用 POST 接口触发任务，拿到 `task_id`。
2. 开启 `setInterval` (每 2 秒一次) 轮询 `GET /api/tasks/{task_id}/status`。
3. 当状态变为 `completed` 或 `failed` 时停止轮询，并给用户弹窗提示结果。

## 5. 为什么不使用直接调用或 HTTP?
- **为什么不用 `subprocess.Popen`**：极其脆弱，主进程崩溃会导致僵尸进程，无法在多台物理机间横向扩展。
- **为什么不直接让 Automation Engine 开 FastAPI 让后端去调 HTTP**：如果是物理机自动化，往往需要排队（比如手机正在跑 A 任务，此时来了 B 任务，HTTP 会直接超时）。使用队列机制（Queue）完美契合“物理设备同一时间只能做一件事”的互斥特性，任务会天然排队执行。

## 6. 后续演进 (Evolution)
未来如果接入多台手机，只需在多台设备上同时启动 `automation_engine/worker.py`，它们会自动从同一个 Redis 抢任务执行，形成分布式的**设备机群**。

## 7. 深度工业化改造路线图 (Deep Industrialization Roadmap)

如果我们要将这套系统做到**真正的“工业级机房”水平**（支撑高并发、无人值守、极低封号率），目前的“队列+Worker”仅仅是基础设施。还需要进行以下五个维度的深度改造：

### 7.1 设备与资源池管理 (Device & Resource Pool)
目前的 Worker 是无状态的，但物理设备是有状态的（有的连着网，有的离线，有的在跑任务）。
- **设备注册中心 (Device Registry)**：每次启动 `worker.py` 时，Worker 自动将连接的设备（通过 ADB 获取 Serial Number）注册到 Postgres/Redis `devices` 表中。
- **任务定向路由 (Targeted Routing)**：不能仅仅是盲目地从 Queue 抢任务。后端需要能指定：“把 Task-A 分配给账号对应的专用手机 emulator-5554”。引入 RabbitMQ 的 Routing Key 或 Redis 的多 Channel 机制。
- **设备健康度心跳 (Heartbeat)**：Worker 每 10 秒向 Redis 发送设备状态（空闲、工作中、电量低、断开连接），后端 Dashboard 可以全局监控设备墙。

### 7.2 幂等性与异常恢复 (Idempotency & Resilience)
移动端 UI 自动化极其不稳定（网络卡顿、突然弹出的广告、App 崩溃）。
- **状态机持久化**：目前 `mobile_core/state_machine.py` 是内存级的。如果 Worker 崩溃，任务必须从头开始。工业化做法是将每一步 State (例如 `ENTERED_SEARCH_PAGE`) 实时写入 Redis。重启后直接恢复到断点继续执行。
- **死信队列 (Dead Letter Queue - DLQ)**：失败的任务放入重试队列，并采用指数退避（Exponential Backoff，如 1分钟后重试，5分钟后重试）。如果 3 次失败，则推入 DLQ 并触发飞书/钉钉告警。
- **操作幂等 (Idempotency)**：如果在“评论完成”的一瞬间断网，重启后绝不能重复评论。必须在发评论前锁定 `Action_Lock:{note_id}`。

### 7.3 全局风控与流量配额 (Global Risk Control & Quotas)
小红书等平台对账号的日均行为有严格监控，机器行为很容易触发风控（限流、封号）。
- **配额管理中心 (Quota Manager)**：在后端建立账号画像和额度管理。例如，限制 Account-A 每天最多点赞 30 次，评论 10 次。Worker 执行动作前必须向 Backend Request Quota，超限则休眠该账号。
- **网络与环境轮换 (Environment Rotation)**：结合物理机特性，加入自动切换飞行模式（更换基站 IP）、自动清理 App 缓存、自动更换代理 IP 的逻辑层。
- **仿生操作矩阵 (Biomimetic Telemetry)**：每次点击引入贝塞尔曲线轨迹，且在后端动态下发每次滑动的速度配置，避免使用千篇一律的固定延迟。

### 7.4 集中式日志与可观测性 (Observability)
抛弃传统的写入本地 `logger.py` 文本文件的做法。
- **Log Streaming**：Worker 的所有日志必须带上 `[TaskID-XXX]` 的 Trace ID，并通过 Redis Stream 或 Kafka 异步推送到后端。
- **WebSocket 实时控制台**：前端不仅仅是看进度条，而是有一个类似 GitHub Actions 的实时黑色控制台，能看到设备端正在输出什么日志（甚至截取当前画面的 Base64 发送到前端）。
- **截屏留证与排错**：每当发生 Exception 时，自动触发设备截屏，将图片传到 OSS 或后端，在后台面板展示“失败现场图”。

### 7.5 配置中心化 (Dynamic Config & Hot Reload)
UI 经常更新，OCR 的匹配阈值也需要微调。
- **动态模板与配置**：不要把阈值或坐标写死在代码里。通过后端的配置中心统一下发。Worker 启动时拉取最新配置，或者通过 Redis Pub/Sub 监听配置更新，实现不停机热加载 (Hot Reload)。
