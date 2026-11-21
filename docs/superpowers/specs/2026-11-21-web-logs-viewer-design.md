# Web 页面日志查询功能设计文档

## 1. 背景与目标

当前 `steward` 项目在终端和 Web 模式下缺乏持久化的运行日志以及页面查询入口：
- 对话轮次（Turn）和工具调用（Tool Call）仅作为模型上下文保存在 `messages` 表中，缺乏耗时、审批决策、Token 消耗及错误堆栈等可观测性数据。
- 飞书渠道（`FeishuChannel`）在后台守护线程中运行，连接异常、收发消息或卡片回调报错仅保留最后一条字符串在内存属性 `self.error` 中，极易被覆盖且无法回溯。
- 系统级操作（登录鉴权、连续失败锁定、供应商/权限/密码配置修改）没有审计记录。

本设计的目标是在不引入外部重型依赖的前提下，基于现有的 SQLite 存储（`steward.db`）与 React 前端，构建一套轻量、结构化、可多维检索的页面日志查询系统。

---

## 2. 架构与数据模型

### 2.1 SQLite 表结构（`steward/session/store.py`）

在 `SessionStore` 的 `_DDL` 中新增 `logs` 表与索引：

```sql
CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    level       TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    session_id  TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_logs_id_desc
    ON logs(id DESC);
CREATE INDEX IF NOT EXISTS ix_logs_filter
    ON logs(category, level, session_id, id DESC);
```

字段约束说明：
- `id`: 自增主键，同时用作游标分页（`before_id`）的稳定排序依据。
- `created_at`: UTC 时间字符串（`datetime('now')`，格式 `YYYY-MM-DD HH:MM:SS`），与 `messages` 表一致。
- `level`: 日志级别，取值 `'INFO'` | `'WARN'` | `'ERROR'`。
- `category`: 日志分类，取值 `'agent'` | `'tool'` | `'channel'` | `'system'`。
- `event`: 细粒度事件标识（如 `'turn.start'`, `'tool.result'`, `'feishu.inbound'`, `'auth.login'`）。
- `session_id`: 关联的会话 ID；无关联会话的系统或渠道级事件存空字符串 `''`。
- `message`: 单行人类可读摘要，用于列表主视图直接展示。
- `detail`: JSON 序列化后的字典，存放结构化上下文（耗时、Token、参数、输出摘要、错误详情等）。

### 2.2 容量上限与敏感信息脱敏

- **自动裁剪**：定义常量 `MAX_LOG_ROWS = 10_000`。在 `append_log` 写入时，定期（或每次写入后使用子查询）清理超出上限的旧记录：
  ```sql
  DELETE FROM logs
  WHERE id <= (
      SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET 10000
  )
  ```
- **敏感字段脱敏**：`append_log` 序列化 `detail` 前，递归检查字典键名；若键名包含 `api_key`、`secret`、`password`、`token`、`authorization`（不区分大小写），统一将值替换为 `'***'`，确保密钥绝不落入日志表。

---

## 3. 日志采集埋点设计

### 3.1 Agent 与工具执行日志（`steward/core/runner.py` & `steward/web/service.py`）

| Category | Event | Level | 触发时机与记录内容 |
| :--- | :--- | :--- | :--- |
| `agent` | `turn.start` | `INFO` | `Runner.run_turn` 开始：记录 `session_id`、用户输入摘要（前 200 字符）、当前 `model` 和 `permission`。 |
| `agent` | `turn.done` | `INFO` | `Runner.run_turn` 正常结束：记录 `session_id`、总步数 `steps`、总耗时 `duration_ms`、Token 用量 `usage`、回复长度。 |
| `agent` | `turn.max_steps` | `WARN` | 达到 `max_steps` 停止：记录 `session_id`、`max_steps`、`duration_ms`。 |
| `agent` | `turn.cancelled` | `WARN` | 任务被取消：记录 `session_id`。 |
| `agent` | `turn.error` | `ERROR` | 模型调用报错（`ProviderError`）或未捕获异常：记录 `session_id`、错误类型与错误信息。 |
| `tool` | `tool.call` | `INFO` | 模型发起工具调用：记录 `session_id`、工具名 `name`、`call_id`、参数 `args`。 |
| `tool` | `tool.approval` | `INFO` / `WARN` | 工具审批决策完成（`WebService._approve` / `FeishuChannel._approve`）：记录工具名 `name`、`approval_id`、决策结果（`once`/`always`/`deny`；拒绝或超时为 `WARN`）。 |
| `tool` | `tool.result` | `INFO` / `ERROR` | 工具执行完毕：记录 `session_id`、工具名 `name`、`ok` 状态、耗时 `duration_ms`、输出预览（前 500 字符）；当 `ok=False` 时级别为 `ERROR`。 |

### 3.2 渠道运行日志（`steward/channel/feishu.py`）

| Category | Event | Level | 触发时机与记录内容 |
| :--- | :--- | :--- | :--- |
| `channel` | `feishu.start` | `INFO` / `ERROR` | 飞书长连接启动成功或因缺少配置/依赖启动失败。 |
| `channel` | `feishu.inbound` | `INFO` | 收到飞书消息：记录 `chat_id`、`chat_type`、映射的 `session_id`、文本摘要。 |
| `channel` | `feishu.card_action` | `INFO` / `ERROR` | 收到飞书审批卡片点击回调：记录 `approval_id`、`decision` 或异常。 |
| `channel` | `feishu.outbound` | `INFO` | 向飞书发送回复或审批卡片成功：记录 `chat_id`、`msg_type`。 |
| `channel` | `feishu.error` | `ERROR` | WebSocket 连接异常重连、消息发送失败（非 200 或 `code != 0`）、`tenant_access_token` 获取失败。 |

### 3.3 系统与审计日志（`steward/web/service.py` & `api.py`）

| Category | Event | Level | 触发时机与记录内容 |
| :--- | :--- | :--- | :--- |
| `system` | `auth.login` | `INFO` / `WARN` | 用户登录成功（`INFO`）或密码错误（`WARN`，记录用户名与当前失败次数）。 |
| `system` | `auth.locked` | `WARN` | 登录连续失败触发短暂锁定：记录用户名与锁定时长。 |
| `system` | `auth.password_changed` | `INFO` | 用户修改密码成功。 |
| `system` | `config.permission` | `INFO` | 切换权限模式：记录新 `permission`。 |
| `system` | `config.provider` | `INFO` | 修改供应商设置或切换/保存/删除供应商预设：记录 `base_url`、`model`、预设名（不含 `api_key`）。 |
| `system` | `config.feishu` | `INFO` | 更新飞书渠道 `app_id` 与密钥配置。 |

---

## 4. 后端接口设计

### 4.1 `SessionStore` 方法签名

```python
def append_log(
    self,
    level: str,
    category: str,
    event: str,
    message: str,
    *,
    session_id: str = "",
    detail: dict[str, Any] | None = None,
) -> int: ...

def query_logs(
    self,
    *,
    level: str = "",
    category: str = "",
    session_id: str = "",
    query: str = "",
    before_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]: ...

def clear_logs(self) -> int: ...
```

- `query_logs` 查询逻辑：
  - 按 `id DESC` 排序。
  - 若传入 `level`（非空），追加 `level = ?`。
  - 若传入 `category`（非空），追加 `category = ?`。
  - 若传入 `session_id`（非空），追加 `session_id LIKE ?`（支持前缀或精确匹配）。
  - 若传入 `query`（非空），追加 `(message LIKE ? OR event LIKE ? OR detail LIKE ? OR session_id LIKE ?)`。
  - 若传入 `before_id`，追加 `id < ?`。
  - 实际 SQL 查询 `LIMIT limit + 1` 条，若返回条数 `> limit`，则 `has_more = True` 并截取前 `limit` 条。
  - 每条记录中的 `detail` 解析为 `dict[str, Any]` 返回给前端。

### 4.2 FastAPI 路由（`steward/web/api.py`）

1. **`GET /api/logs`**（需登录 `UserDep`）
   - Query 参数：
     - `level: str = ""`
     - `category: str = ""`
     - `session_id: str = ""`
     - `q: str = ""`
     - `before_id: int | None = None`
     - `limit: int = 100`（限制在 `1..500` 之间）
   - 响应示例：
     ```json
     {
       "logs": [
         {
           "id": 42,
           "created_at": "2026-11-21 08:30:12",
           "level": "INFO",
           "category": "tool",
           "event": "tool.result",
           "session_id": "20261121-0830-a1b2",
           "message": "read_file 执行成功 (12ms)",
           "detail": {
             "name": "read_file",
             "ok": true,
             "duration_ms": 12,
             "output_preview": "..."
           }
         }
       ],
       "has_more": false
     }
     ```
2. **`DELETE /api/logs`**（需登录 `UserDep`）
   - 调用 `service.clear_logs()` 清空日志表。
   - 响应：`{"cleared": 128}`。

---

## 5. 前端界面与交互设计

### 5.1 侧边栏与主视图切换
- **`web/src/App.tsx`**：
  - 新增状态 `activeView: 'chat' | 'logs'`（默认 `'chat'`）以及 `logSessionFilter: string`（默认 `''`）。
  - 当点击左侧边栏的会话项或「新对话」按钮时，自动将 `activeView` 切回 `'chat'`。
  - 当在日志视图中点击某条日志的 `session_id` 标签时，调用 `steward.selectSession(id)` 并切回 `'chat'` 视图。
- **`web/src/components/SessionSidebar.tsx`**：
  - 在底部「设置」按钮上方增加「日志」导航按钮（使用 `ScrollText` 图标），当前处于 `'logs'` 视图时高亮显示。

### 5.2 日志查询主视图（`web/src/components/LogsView.tsx`）
- **顶部标题与操作栏**：
  - 左侧显示标题「运行日志」与当前加载条数。
  - 右侧提供：
    - 「自动刷新」Switch 开关（开启后每 3 秒自动拉取最新筛选结果）。
    - 「刷新」按钮（手动拉取最新日志）。
    - 「清空日志」按钮（点击第一次变为“确认清空”，再次点击调用 `DELETE /api/logs`）。
- **多维筛选栏**：
  - **分类选择**：`全部` | `Agent (agent)` | `工具 (tool)` | `渠道 (channel)` | `系统 (system)`。
  - **级别选择**：`全部` | `INFO` | `WARN` | `ERROR`。
  - **会话 ID 过滤框**：支持输入或从某条日志一键筛选该会话的全部日志。
  - **关键字搜索框**：支持按消息内容、事件名或参数关键字搜索（带 250ms 防抖或回车触发）。
- **日志列表与展开详情**：
  - 每行显示：`时间`、`级别 Badge`（`INFO` 蓝/灰、`WARN` 橙色、`ERROR` 红色）、`分类·事件`、`会话 ID 按钮`（若有）、`message 摘要`。
  - 点击任意行可展开/折叠该行的 `detail` JSON 详情面板，支持一键复制 JSON 内容。
  - 列表底部当 `has_more === true` 时展示「加载更多」按钮，基于最后一条日志的 `id` 请求 `before_id` 追加数据。

---

## 6. 测试与验证计划

1. **后端类型检查与单元测试**：
   - 运行 `basedpyright`，确保所有新增代码通过严格类型检查（0 error, 0 warning）。
   - 在 `tests/test_session_store.py` 中测试 `append_log`、敏感词脱敏、容量自动裁剪、`query_logs` 多条件过滤与游标分页、`clear_logs`。
   - 在 `tests/test_web.py` 中测试 `GET /api/logs`、`DELETE /api/logs` 路由鉴权、对话与工具执行后自动产生的 `agent` / `tool` / `system` 日志。
2. **前端构建检查**：
   - 在 `web/` 下执行 `npm run build`（同步更新 `steward/web/static` 构建产物），确保 TypeScript 编译与 Vite 打包无报错。
