# Web 页面日志查询功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `steward` 增加基于 SQLite 持久化的结构化日志系统（覆盖 Agent 对话轮次、工具调用与审批、飞书渠道、系统与鉴权配置）以及前端独立日志查询主视图。

**Architecture:** 在 `SessionStore` (`steward.db`) 中新增 `logs` 表，提供带敏感字段脱敏和容量上限（10,000 条）裁剪的 `append_log`、`query_logs`、`clear_logs` 方法；在 `Runner`、`WebService`、`FeishuChannel`、`api.py` 关键节点写入结构化日志；通过 `GET /api/logs` 和 `DELETE /api/logs` 暴露查询与清空能力；在前端新增 `LogsView` 主视图与侧边栏导航入口。

**Tech Stack:** Python 3.11+, SQLite3, FastAPI, Pydantic, React 18, TypeScript, Tailwind CSS, Lucide Icons, unittest, basedpyright, ruff.

---

### Task 1: SQLite `logs` 表与 `SessionStore` 日志读写、脱敏、裁剪方法

**Files:**
- Modify: `steward/session/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: 在 `tests/test_store.py` 中编写日志存储与查询的失败测试**

在 `tests/test_store.py` 中新增测试方法：
```python
    def test_append_and_query_logs_with_filters_and_redaction(self) -> None:
        id1 = self.store.append_log(
            "INFO",
            "agent",
            "turn.start",
            "开始对话",
            session_id="s-1",
            detail={"model": "glm-5", "api_key": "sk-secret", "nested": {"app_secret": "sec-1"}},
        )
        id2 = self.store.append_log(
            "ERROR",
            "tool",
            "tool.result",
            "run_shell 执行失败",
            session_id="s-1",
            detail={"name": "run_shell", "ok": False},
        )
        id3 = self.store.append_log(
            "WARN",
            "system",
            "auth.login",
            "登录失败",
            detail={"username": "admin", "password": "pw"},
        )

        self.assertLess(id1, id2)
        self.assertLess(id2, id3)

        all_res = self.store.query_logs()
        self.assertFalse(all_res["has_more"])
        logs = all_res["logs"]
        self.assertEqual([item["id"] for item in logs], [id3, id2, id1])

        # Verify sensitive fields are redacted
        self.assertEqual(logs[0]["detail"]["password"], "***")
        self.assertEqual(logs[2]["detail"]["api_key"], "***")
        self.assertEqual(logs[2]["detail"]["nested"]["app_secret"], "***")

        # Filter by level
        err_res = self.store.query_logs(level="ERROR")
        self.assertEqual([item["id"] for item in err_res["logs"]], [id2])

        # Filter by category
        cat_res = self.store.query_logs(category="agent")
        self.assertEqual([item["id"] for item in cat_res["logs"]], [id1])

        # Filter by session_id
        sess_res = self.store.query_logs(session_id="s-1")
        self.assertEqual([item["id"] for item in sess_res["logs"]], [id2, id1])

        # Filter by keyword query
        q_res = self.store.query_logs(query="run_shell")
        self.assertEqual([item["id"] for item in q_res["logs"]], [id2])

        # Cursor pagination with limit and before_id
        page1 = self.store.query_logs(limit=2)
        self.assertTrue(page1["has_more"])
        self.assertEqual([item["id"] for item in page1["logs"]], [id3, id2])

        page2 = self.store.query_logs(limit=2, before_id=id2)
        self.assertFalse(page2["has_more"])
        self.assertEqual([item["id"] for item in page2["logs"]], [id1])

        # Clear logs
        cleared = self.store.clear_logs()
        self.assertEqual(cleared, 3)
        self.assertEqual(self.store.query_logs()["logs"], [])
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 -m unittest tests/test_store.py -v`
Expected: FAIL with `AttributeError: 'SessionStore' object has no attribute 'append_log'`

- [ ] **Step 3: 在 `steward/session/store.py` 中实现 `logs` 表、`append_log`、`query_logs`、`clear_logs` 及脱敏函数**

在 `steward/session/store.py` 中：
1. 在 `_DDL` 末尾增加 `logs` 表与索引：
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
2. 增加容量常量 `MAX_LOG_ROWS = 10_000` 和敏感词元组 `_SENSITIVE_KEYS = ("api_key", "secret", "password", "token", "authorization")` 以及递归脱敏函数 `_redact(value: Any) -> Any`。
3. 在 `SessionStore` 中实现 `append_log`、`query_logs`、`clear_logs`。

- [ ] **Step 4: 运行单元测试与类型检查**

Run: `python3 -m unittest tests/test_store.py -v && basedpyright steward/session/store.py tests/test_store.py`
Expected: PASS, 0 errors, 0 warnings

---

### Task 2: 在 `Runner`、`WebService`、`FeishuChannel` 中接入结构化日志采集

**Files:**
- Modify: `steward/core/runner.py`
- Modify: `steward/web/service.py`
- Modify: `steward/channel/feishu.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: 在 `tests/test_web.py` 中编写日志采集测试**

验证对话轮次、工具调用、审批决策、登录与配置修改都会写入结构化日志。

- [ ] **Step 2: 在 `steward/core/runner.py` 中添加 Agent 与工具执行日志**

在 `Runner.run_turn` 与 `_run_tool_call` 中记录：
- `turn.start` (`INFO`, `agent`)
- `turn.done` (`INFO`, `agent`)，包含 `steps`, `duration_ms`, `usage`
- `turn.max_steps` (`WARN`, `agent`)
- `tool.call` (`INFO`, `tool`)，包含 `call_id`, `name`, `args`
- `tool.result` (`INFO` 或 `ERROR`, `tool`)，包含 `call_id`, `name`, `ok`, `duration_ms`, `output_preview`

- [ ] **Step 3: 在 `steward/web/service.py` 中添加系统审计、审批与轮次异常日志**

在 `WebService` 中记录：
- `auth.login` (`INFO` / `WARN`, `system`) 与 `auth.locked` (`WARN`, `system`)
- `auth.password_changed` (`INFO`, `system`)
- `config.permission` (`INFO`, `system`)
- `config.provider` (`INFO`, `system`)（在 `set_provider`, `save_profile`, `apply_profile`, `delete_profile` 中记录）
- `tool.approval` (`INFO` / `WARN`, `tool`)（在 `_approve` 中记录）
- `turn.cancelled` (`WARN`, `agent`) 与 `turn.error` (`ERROR`, `agent`)（在 `_worker` 的 `except` 块中记录）
- 新增 `query_logs(...)` 和 `clear_logs()` 封装方法供 `api.py` 调用。

- [ ] **Step 4: 在 `steward/channel/feishu.py` 中添加飞书渠道运行日志**

在 `FeishuChannel` 中记录：
- `feishu.start` (`INFO` / `ERROR`, `channel`)
- `feishu.inbound` (`INFO`, `channel`)
- `feishu.card_action` (`INFO` / `ERROR`, `channel`)
- `feishu.outbound` (`INFO`, `channel`)
- `feishu.error` (`ERROR`, `channel`)
- `tool.approval` (`INFO` / `WARN`, `tool`)

- [ ] **Step 5: 运行测试与类型检查**

Run: `python3 -m unittest discover -s tests -v && basedpyright steward tests`
Expected: PASS, 0 errors, 0 warnings

---

### Task 3: 在 `steward/web/api.py` 中暴露 `/api/logs` 接口

**Files:**
- Modify: `steward/web/api.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: 在 `steward/web/api.py` 中添加 `GET /api/logs`、`DELETE /api/logs` 和 `/api/channel/feishu` 配置审计日志**

- `GET /api/logs` 支持 query 参数：`level: str = ""`, `category: str = ""`, `session_id: str = ""`, `q: str = ""`, `before_id: int | None = None`, `limit: int = 100`。
- `DELETE /api/logs` 清空日志并返回 `{"cleared": count}`。
- `POST /api/channel/feishu` 保存成功后写入一条 `config.feishu` (`INFO`, `system`) 日志。

- [ ] **Step 2: 在 `tests/test_web.py` 的 `TestHttpSurface` 中补充 HTTP 接口测试**

测试 `GET /api/logs` 过滤查询与 `DELETE /api/logs` 清空功能。

- [ ] **Step 3: 运行测试与静态检查**

Run: `python3 -m unittest discover -s tests -v && basedpyright steward tests && ruff check steward tests`
Expected: PASS, 0 errors, 0 warnings

---

### Task 4: 前端 API 客户端、日志查询主视图 `LogsView` 与侧边栏导航

**Files:**
- Modify: `web/src/lib/api.ts`
- Create: `web/src/components/LogsView.tsx`
- Modify: `web/src/components/SessionSidebar.tsx`
- Modify: `web/src/App.tsx`

- [ ] **Step 1: 在 `web/src/lib/api.ts` 中新增日志类型定义与 `api.logs` / `api.clearLogs` 方法**

定义 `LogLevel`, `LogCategory`, `LogRow`, `LogsQuery`, `LogsResponse` 以及 `api.logs(query)` 和 `api.clearLogs()`。

- [ ] **Step 2: 创建 `web/src/components/LogsView.tsx` 组件**

实现：
- 顶部栏：标题、条数统计、自动刷新开关（3 秒轮询）、手动刷新按钮、清空日志按钮（二次确认）。
- 筛选栏：分类切换按钮组（全部 / Agent / 工具 / 渠道 / 系统）、级别选择（全部 / INFO / WARN / ERROR）、会话 ID 过滤输入框、关键字搜索框。
- 日志列表：展示时间、级别 Badge、分类与事件名、可点击跳转的会话 ID 标签、消息摘要；点击行展开结构化 JSON 详情面板（支持一键复制详情和一键按该会话过滤）。
- 底部分页：`has_more` 时展示「加载更多」按钮。

- [ ] **Step 3: 更新 `web/src/components/SessionSidebar.tsx` 与 `web/src/App.tsx`**

- 在 `SessionSidebar.tsx` 底部「设置」按钮上方新增「日志」入口按钮（带 `activeView === 'logs'` 高亮状态）。
- 在 `App.tsx` 中管理 `activeView: 'chat' | 'logs'` 状态，点击会话或新对话自动切回 `'chat'`，在 `LogsView` 中点击某条日志的跳转会话按钮时选中该会话并切换到 `'chat'`。

- [ ] **Step 4: 构建前端并运行全量测试**

Run: `cd web && npm run build && cd .. && python3 -m unittest discover -s tests -v && basedpyright steward tests && ruff check steward tests`
Expected: 前端构建成功（产物输出到 `steward/web/static`），所有后端测试、类型检查、Lint 全部通过。
