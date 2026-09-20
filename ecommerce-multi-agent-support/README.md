# VoltCore 跨境电商多 Agent 智能客服系统

基于 LangGraph `StateGraph` 的可运行、可评测、可审计客服 V1。外部路由契约固定为：

`product_inquiry / aftersales_handling / order_query / logistics_tracking / unsupported`

## 架构与数据流

```text
[JWT：sub + shop_id]
    │ 校验用户数据库记录中的 shop_id / market / timezone
    ▼
[LangGraph Supervisor]
    ├─ product_inquiry ─► Catalog Agent ─► shop 商品 Repository
    ├─ order_query ─────► OrderStatusNode ─┐
    ├─ logistics_tracking ► 同一节点/Tool ─┴► shop + user + order_no 查询
    ├─ aftersales_handling ► 订单事实 + 固定政策 ► 待审批方案
    └─ unsupported ─────► 固定拒答
    ▼
[回答 + facts + 脱敏 tool_trace]
    ▼
[shop + user 隔离的脱敏 Audit]
```

## 已实现能力

- 商品查询按可信 `shop_id` 隔离，返回商品自己的 `USD / EUR / GBP` 币种。
- 订单与物流是两条外部路由，但复用现有 `OrderStatusNode`、`GetOrderStatusTool` 和 Repository 逻辑。
- Repository 使用 `shop_id + user_id + order_no` 校验订单/物流归属。
- JWT 同时包含 `sub + shop_id`；鉴权时必须与数据库用户记录匹配。
- 用户模型包含 `shop_id / market / timezone`；商品、订单含 `shop_id / currency`；审计含 `shop_id`。
- 地址变更归 `aftersales_handling`，产生 `address_change / address_change_review / requires_approval=true`。
- 地址变更仅是提案：不保存地址、不修改订单、不执行外部动作；完整地址不进入 trace 或 audit。
- 固定种子生成 12 用户、60 商品、100 订单、100 物流，覆盖：
  - `shop-us / USD / US / America/Los_Angeles`
  - `shop-eu / EUR / EU / Europe/Berlin`
  - `shop-uk / GBP / UK / Europe/London`

## 验证结果

- pytest：`118 passed`（含新增 21 个多轮订单上下文测试用例；1 个 Starlette/httpx 弃用警告）
- 本地确定性 eval：`47/47`，路由准确率 `100%`，工具选择准确率 `100%`，安全案例 `4/4`
- 隔离数据库 smoke：商品/订单/物流/售后链路通过，跨用户订单返回 404，退款只生成 `refund_review + requires_approval=true`
- 结果见 [`docs/V1_EVALUATION_REPORT.md`](docs/V1_EVALUATION_REPORT.md)

评测只证明本地 `rule_fallback` 和确定性 Tool 链路，不代表云端模型、生产流量或真实平台业务效果。

## 快速运行

```powershell
cd ecommerce-multi-agent-support
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe evals\run_eval.py
.\.venv\Scripts\python.exe scripts\smoke_demo.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

Swagger：`http://127.0.0.1:8002/docs`。演示账号 `demo_user_01` 至 `demo_user_12`，密码统一为 `DemoPass123!`。

## 主要接口

- `GET /health`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/chat`
- `GET /api/v1/chat/audits`
- `POST /api/v1/routing/preview`
- `GET /api/v1/products`
- `GET /api/v1/orders`
- `GET /api/v1/orders/{order_no}`
- `GET /api/v1/orders/{order_no}/shipment`

## 典型演示

```text
商品：推荐一款 300 元以内的 65W 充电器
订单：查询订单 VLT-2026-0001 的订单状态
物流：订单 VLT-2026-0001 到哪里了
售后：订单 VLT-2026-0015 的商品破损了，我要退款（demo_user_03）
地址：订单 VLT-2026-0001 修改收货地址为 <地址>
越权：demo_user_01 查询 VLT-2026-0002
```

详细步骤见 [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md)。

## 多轮订单上下文

`POST /api/v1/chat` 和兼容入口 `/api/v1/chat/preview` 支持同一会话的订单/物流追问：

```text
session_id=order-demo
第一轮：查一下订单 VLT-2026-0001
第二轮：那它什么时候到？
结果：logistics_tracking，重新查询 VLT-2026-0001 的订单与物流。
```

- SQLite `session_contexts` 表以 `shop_id + user_id + session_id` 为联合主键；身份来自鉴权后的用户，不接受消息中的身份覆盖。
- 只保存 `active_order_no / pending_intent / updated_at / expires_at`，不保存 GraphState、订单事实、物流事实、地址或对话原文。
- `SESSION_CONTEXT_TTL_MINUTES` 默认 30 分钟。上下文更新时续期；过期订单号不会用于查询，也不会因追问自动恢复。
- 当前消息的单个明确订单号优先。新号码即使查不到或无权访问，也不会回退旧号码。
- 无订单号时，仅明确的订单/物流问题可使用有效的历史订单号；多个不同订单号、模糊指代、格式错误或过期时要求确认，不调用订单工具。
- 切换到商品、售后或非电商话题后清空活动订单，避免将后续“它”强行解释为之前的订单。
- 支持“物流查询缺订单号 → 下一轮只发订单号”继续物流查询。售后缺订单号续接尚未实现；只发订单号不会自动恢复退款意图。
- 每轮重新走 `GetOrderStatusTool → Repository`，用 `shop_id + user_id + order_no` 校验归属并读取最新事实；无权限与不存在使用相同回复。
- 会话写入和现有审计在同一数据库事务提交；trace/audit 不新增对话或事实快照。

启动时现有 `init_db()/create_all()` 会新增此表，无需为本功能删除旧库。上下文可跨重启保留，多个进程必须使用同一 SQLite 文件；不同副本各自的数据库不会共享记忆。当前未实现同一会话并发轮次排序，最后完成写入的请求覆盖上下文，客户端应串行提交该会话的请求。SQLite 写并发限制仍然存在，过期行目前逻辑失效而非定时物理清理。

新增测试见 `tests/test_session_context.py`，覆盖双入口追问、事实更新、订单切换、身份隔离、重新鉴权、过期、多订单及模糊指代、话题切换、待补充物流意图和结构化存储边界。测试仅验证本地确定性链路，不代表真实模型或生产验证。

## 其他边界

- 当前 SQLite 初始化使用 `create_all`，不迁移旧库；已有旧结构数据库需重建后再 seed。
- `requires_approval=true` 只表示待人工审核方案，不代表审批记录、恢复点或动作执行器已经存在。
- V2/V3 仍计划引入政策 RAG、Checkpointer、真实测试商店和幂等 Human-in-the-loop 执行链路。
