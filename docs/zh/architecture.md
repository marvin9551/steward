# 架构

[English](../architecture.md) | 简体中文

本文把行为映射到文件。建议对照源码阅读；源码就是规范。

## 一轮对话

```
CLI（输入）
  │  用户文本
  ▼
Runner.run_turn
  ├─ store.append(user)
  │
  ├─ 循环（max_steps）：
  │    ├─ fit_messages()：系统提示词 + 历史，按完整 turn 裁剪
  │    ├─ provider.stream_chat()：httpx POST + SSE 累积
  │    ├─ store.append(assistant, 文本 + tool_calls)
  │    ├─ 没有 tool_calls：发出 TurnDone，返回
  │    └─ 对每个工具调用：
  │         ├─ policy.decide(mode, name) -> allow | ask | deny
  │         ├─ （ask）approver() -> 用户 y/N
  │         ├─ tools.execute()：超时、错误转文本、截断
  │         └─ store.append(tool, 结果)
  │
  └─ 发出事件 -> CLI 渲染 / SSE 推送
```

对应文件：

| 关注点 | 文件 |
| --- | --- |
| 主循环、工具分发、步数预算 | `steward/core/runner.py` |
| token 估算与裁剪 | `steward/core/context.py` |
| 系统提示词 | `steward/core/prompt.py` |
| 类型化 UI 事件 | `steward/core/events.py` |
| HTTP + SSE + 工具调用拼装 | `steward/providers/openai_compat.py` |
| 工具注册与执行 | `steward/tools/base.py` |
| 文件 / shell 工具 | `steward/tools/fs.py`、`steward/tools/shell.py` |
| allow / ask / deny 表 | `steward/policy/permissions.py` |
| 会话持久化 | `steward/session/store.py` |
| 路由、JSON、SSE、静态文件 | `steward/web/api.py` |
| uvicorn 启动、端口选择 | `steward/web/server.py` |
| 同时只跑一轮、消息转 block | `steward/web/service.py` |
| 可回放事件日志 | `steward/web/stream.py` |
| 审批登记表 | `steward/web/approvals.py` |
| 页面源码 | `web/` → 构建进 `steward/web/static/` |

## 消息模型

Steward 保存的就是它发给模型的内容，采用 OpenAI chat 结构：

```jsonc
{"role": "user",      "content": "create a.txt"}
{"role": "assistant", "content": "", "tool_calls": [
  {"id": "call_1", "type": "function",
   "function": {"name": "write_file",
                "arguments": "{\"path\":\"a.txt\",\"content\":\"hi\"}"}}
]}
{"role": "tool", "tool_call_id": "call_1", "content": "wrote 2 chars to a.txt"}
{"role": "assistant", "content": "Done."}
```

由此有两条硬规则：

1. **带 `tool_calls` 的 assistant 消息，后面必须紧跟每个 call id 对应的 `tool` 消息。** 因此裁剪时不能从二者中间切开。这正是 `context.fit()` 以"完整 turn"为单位而不是以单条消息为单位的原因。
2. **`arguments` 是 JSON 字符串，不是对象。** 它在流式响应里被拆成多个分片，provider 先拼好，runner 再解析。解析失败会变成一条工具结果（`ERROR: ... not valid JSON`），而不是抛异常，这样模型能自我纠正。

## 工具契约

每个工具由名称、描述、JSON Schema 和一个函数组成：

```python
@tool("read_file", "Read a text file ...", { ... })
def read_file(ctx: ToolContext, path: str, offset: int = 1) -> str: ...
```

`tools.execute()` 统一负责所有必须一致的行为：

- 未知工具名 → 返回可用工具列表的文本
- 参数错误（`TypeError`）→ 说明不匹配的文本
- 超时 → 明确说明超时
- 任意异常 → `ERROR: 类型: 信息`
- 输出按 `config.max_tool_output` 截断

模型看到的工具失败就是普通内容，这正是它能换个参数重试、而不是让整轮对话中断的原因。

工具通过导入 `steward.tools` 完成注册；新增一个模块并在 `tools/__init__.py` 里加一行 import 即可暴露。

## 上下文预算

每次调用模型前都会执行 `context.fit()`：

1. 系统消息永远保留。
2. 历史按 turn 切分（每个 turn 从一条 user 消息开始）。
3. 从最新 turn 往前累加，直到达到粗略的 token 预算。
4. 发送前，超过阈值的 tool 消息会被压缩（保留头尾）。

token 数是估算值（中文约 1 token/字，其他文本约 4 字符/token）。它只用来决定何时裁剪；真实数字以 provider 返回的 `usage` 为准。

暂未实现：对被丢弃的 turn 做摘要。将来实现时也归在这里，摘要作为 system 消息插入，磁盘上的原始 turn 保持不变。

## 会话存储

```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,          -- user | assistant | tool
  payload TEXT NOT NULL,       -- JSON，结构随 role 变化
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_messages_session_seq ON messages(session_id, seq);
```

只追加，没有更新路径。因此回放是确定性的，未来的 fork/branch 只是"复制前缀 + 分配新 id"。`steward history` 打印的就是模型实际看到的消息。

如果一次模型调用失败，user 消息已经落库。这是有意的：历史记录的是"尝试过什么"。修好 key/配置后继续发下一条即可，失败的那一轮仍能在 `history` 里看到。

## 权限

`policy.decide(mode, tool_name)` 返回三种动词之一：

| 模式 | 读工具 | 写/编辑 | shell |
| --- | --- | --- | --- |
| `read-only` | 允许 | 拒绝 | 拒绝 |
| `workspace-write` | 允许 | 允许 | 询问 |
| `full` | 允许 | 允许 | 允许 |

路径限制是另一层，且始终生效：`tools/fs.py` 会解析每个路径，拒绝工作目录之外的访问，**所有模式都一样**。`full` 的含义是"不要问我"，不是"任意路径"。

## 网页前端

`steward web` 用 uvicorn 跑一个 FastAPI 应用（可选依赖 `[web]`），提供一个页面和一层很小的 JSON / SSE 接口。它是同一套事件的第二个**渲染器**，不是第二个 agent：

```
页面（React）── POST /api/turn ──▶ WebService.start_turn
                                    │  asyncio.Task，同时只跑一个
                                    ▼
                               Runner.run_turn
                                    │ on_event
                                    ▼
                               EventLog（带序号）
                                    │
页面（EventSource）◀─ GET /api/stream ┘（异步生成器）
```

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 模型、权限、工作区、是否忙碌；绝不含 key |
| `GET /api/sessions` | 侧边栏会话列表 |
| `GET /api/sessions/<id>` | 把存下的消息拍平成 UI block |
| `POST /api/sessions` | 生成一个会话 id |
| `POST /api/turn` | 开始一轮（正在跑时返回 409） |
| `GET /api/stream` | SSE；带 `Last-Event-ID` 续传，游标过期则发 `resync` |
| `POST /api/approval` | 回答审批卡片 |
| `POST /api/permission` | 切换权限模式，对下一次工具调用生效 |
| `GET /api/provider` | Provider 设置；key 只以布尔值形式返回 |
| `POST /api/provider` | 修改设置；`api_key` 传空表示保持不变 |
| `POST /api/provider/test` | 发一次极小的真实请求验证 key |
| `POST /api/models` | 从网关同步模型列表；可传未保存的表单值 |
| `GET /api/profiles` | 已保存的档案；标出当前启用的，key 只以布尔值出现 |
| `POST /api/profiles` | 新建 / 更新档案（对话框保存时标记为当前） |
| `POST /api/profiles/apply` | 把某个档案套用到当前 Provider |
| `DELETE /api/profiles/<name>` | 删除档案 |

`/docs` 和 `/openapi.json` 是 FastAPI 自带的；请求体是 Pydantic 模型，所有错误统一成 `{"error": "..."}`，也就是页面已经在用的结构。

三个让实现保持很小的决定：

1. **同时只跑一轮。** `WebService` 只有一个 task 槽位；第二个请求拿到 409，而不是让两轮对话交叉写入同一个会话。对单用户工具来说这是诚实的，也省掉了全部排队问题。
2. **用游标，而不是队列。** SSE 生成器每 50ms 用 `asyncio.sleep` 轮询一个有界队列。这点延迟感知不到，却顺带解决了回放、续传和背压，而且每个观看者不再占一个线程。
3. **存 block，不直接存消息。** `WebService.transcript()` 把 OpenAI 形状的消息拍平成实时流产生的同一种 block，于是历史记录和流式输出共用一套渲染组件。

审批就是一个 future：turn 在 `ApprovalRegistry.wait()` 上等待，`POST /api/approval` 来兑现——同一个事件循环，不需要锁。`always` 按工具名记住，仅在本次进程内有效，重启就恢复了终端的谨慎。

**页面上改 Provider 设置。** 齿轮对话框编辑 `base_url`、模型、key 和 temperature。预设会填入已知端点；内部网关（opencode go 这类）就是一个带标签的自定义槽位——任意 OpenAI 兼容地址都行。**同步模型**向网关请求模型列表，挑选代替手打名字，并且接受未保存的表单值，因此首次配置就是一条流：贴 URL + key → 同步 → 挑选 → 保存。保存写入 `~/.steward/settings.toml`——`config.load()` 会把它叠在 `config.toml` 之上的覆盖文件——而不是重写你带注释的配置文件。key 在 HTTP 上是只写的：`GET /api/provider` 只返回 `api_key_set` 布尔值，POST 空 key 表示保持不变。`STEWARD_*` 环境变量设置的字段永远不可能被文件顶掉，所以服务端拒绝这类修改，对话框里也会把输入框锁住。

**档案。** 同一个覆盖文件还存命名的 Provider 档案（`[profiles.<名字>]`，`config.load()` 会忽略它们）。Provider 页签把档案和当前生效的配置一起列出；`apply` 把档案复制进当前 Provider——缺省字段保持现值、环境锁定的字段拒绝来自文件——删除当前档案只会清掉标记，不会动配置。没有 key 的档案复用当前生效的 key，因此一个 key 可以服务多个端点。

页面源码在 `web/`（React 18、Vite、Tailwind、shadcn/ui），构建进 `steward/web/static/`。构建产物已提交，所以运行时不需要 Node 工具链。

## 扩展点

- **新增工具** —— 在 `tools/` 写函数并加装饰器，在 `tools/__init__.py` 里 import。不需要改 runner。
- **新增 provider** —— 实现 `providers/base.py` 里的协议，传给 `Runner(provider=...)`。其余部分与 provider 无关。
- **新增前端 / 渠道** —— 订阅 `on_event` 即可；CLI 渲染器约 40 行，`web/service.py` 约 250 行，两者都展示了完整契约。
- **记忆 / RAG** —— 在 `Runner.run_turn` 里加一步来丰富系统提示词，再加一个后台任务写摘要。不要塞进 provider 层。

## 已知故障模式

| 现象 | 可能原因 |
| --- | --- |
| `HTTP 400` 且提到 `tools` | 模型不支持工具调用 |
| 模型从不调用工具 | 工具描述太弱，或提示词在劝退 |
| `arguments` 解析错误 | 模型输出了不严格的 JSON；错误文本会引导它重试 |
| 上下文溢出 | `context_budget` 相对模型窗口太大，或某条工具结果特别大 |
| 每次调用都 404 | `base_url` 写错（缺 `/v1`，或粘贴了完整接口地址） |
| `api_key is empty` | 配置 / 环境变量未设置 |
