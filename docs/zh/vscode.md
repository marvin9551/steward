# 在 VS Code 中调试

[English](../vscode.md) | 简体中文

仓库自带 `.vscode/launch.json`、`.vscode/tasks.json` 和 `.vscode/extensions.json`，新 clone 下来不用额外配置就能打断点调试。（`.vscode/settings.json` 属于个人配置，已忽略。）

## 前置准备

1. 安装 **Python** 扩展（`ms-python.python`），它提供启动配置里使用的 `debugpy` 调试器。其余推荐扩展 VS Code 会根据 `.vscode/extensions.json` 提示安装。
2. Python 3.11 或更高版本。
3. 创建虚拟环境并安装本项目：

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[web]"            # 只用终端的话去掉 [web]
```

4. VS Code 中：**命令面板 → Python: Select Interpreter →** 选择
   `.venv/bin/python`（Windows 是 `.venv\Scripts\python.exe`）。

5. 配置模型（二选一）：

```bash
cp config.example.toml config.toml   # 然后编辑 base_url / model / api_key
```

或者把 `.env.example` 复制为 `.env`，使用带 `(.env)` 的启动配置。
`config.toml` 和 `.env` 都在 `.gitignore` 中。

6. 只有前端相关配置需要：Node 18+，并在 `web/` 里执行 `npm ci`（或跑 **web: install** 任务）。

## 开始调试

打开 **运行和调试**（`⇧⌘D` / `Ctrl+Shift+D`），选择一个配置，按 **F5**：

| 配置 | 实际运行 |
| --- | --- |
| **Steward: chat** | `python -m steward chat` —— 正常 REPL |
| **Steward: chat (read-only)** | 同上，加 `--permission read-only` |
| **Steward: chat (.env)** | 同上，从 `.env` 读取环境变量 |
| **Steward: sessions** | `python -m steward sessions` |
| **Steward: web** | `python -m steward web --port 8765`，自动开浏览器 |
| **Steward: web (no browser)** | 同上但 `--no-open` —— 配合 Vite、curl 或另开标签页 |
| **Steward: web (read-only)** | 同上，环境里设 `STEWARD_PERMISSION=read-only` |
| **Steward: web (.env)** | 同上，从 `.env` 读取环境变量 |
| **Web: Vite dev (5173)** | 在 `web/` 跑 `npm run dev` —— 带热更新的页面 |
| **Steward: API + Vite (full stack)** | 复合配置：上面两个一起起、一起停 |
| **Steward: offline demo** | `scripts/debug_demo.py` —— 脚本化 provider，不联网 |
| **Steward: unit tests** | `python -m unittest discover -s tests -v` |

所有配置都刻意使用 `"console": "integratedTerminal"`。CLI 的输入提示和工具审批都走 `input()`，默认的 `internalConsole` 无法提供 stdin，REPL 会看起来卡住。

如果按 F5 没反应，看 **调试控制台** 的报错，并确认 Python 扩展已安装。老版本扩展的调试器 `type` 是 `"python"` 而不是 `"debugpy"`，其余字段一样。

## 调试网页界面

两种跑法，回答的是不同问题：

| 你在改什么 | 用哪个配置 | 地址 |
| --- | --- | --- |
| Python（路由、runner、工具） | **Steward: web** | `http://127.0.0.1:8765` —— 直接服务已提交的构建产物 |
| 页面本身 | **Steward: API + Vite (full stack)** | `http://localhost:5173` —— Vite 热更新，`/api` 代理到 8765 |

几个能省时间的点：

- Vite 地址用 **localhost**，不要用 `127.0.0.1`：Vite 默认绑的是 IPv6 回环。
- 改 Python 要重启（`⇧⌘F5` / `Ctrl+Shift+F5`）；没有 `--reload`，因为 uvicorn 的热重载需要 import 字符串，会另起一个调试器管不到的进程。改前端则自动热更新。
- 复合配置会一起停掉两个进程（`stopAll`）。只重启 API 时 Vite 继续跑，会自动重连。
- 端口：8765 被占用时 API 会顺延到下一个空闲端口并打印实际端口。Vite 代理写死 8765，所以用复合配置时让 API 留在 8765（要改就同时改 `launch.json` 里的 `args` 和 `web/vite.config.ts`）。
- `steward web` 会读 `config.toml` 和 `STEWARD_*` 环境变量，所以想临时钉住某个权限模式或模型，直接在启动配置的 `"env"` 里写，不用动真实配置。

### 断点打在哪里（服务端）

| 目标 | 文件 / 函数 | 说明 |
| --- | --- | --- |
| 看请求进来 | `steward/web/api.py` → `turn` | 检查解析后的 `TurnRequest` |
| 跟完整一轮 | `steward/web/service.py` → `_worker` | 这一轮是 API 事件循环上的一个 task |
| 调试审批 | `steward/web/service.py` → `_approve` | 停在卡片被回答之前，页面会一直等 |
| 观察推送 | `steward/web/api.py` → `stream_events` | 每批都会命中，建议用日志点 |
| 看落库后的 block | `steward/web/service.py` → `transcript` | 页面刷新后渲染的就是它 |

想单步进入 FastAPI / uvicorn，就在 web 配置里设 `"justMyCode": false`。

## 任务

**终端 → 运行任务**（`⇧⌘B` 跑默认构建）：

| 任务 | 命令 |
| --- | --- |
| **web: install** | 在 `web/` 执行 `npm ci` |
| **web: build** | `npm run build` → `steward/web/static`（默认构建任务） |
| **web: dev** | 以后台任务方式跑 `npm run dev` |
| **test: unit** | 用当前解释器跑 unittest 套件 |

改完页面想让不带 Vite 的 `steward web` 也生效，就要重新构建；构建产物和源码不一致时 CI 会失败。

## 断点打在哪里

如果想不花 token 就能完整走一轮，先跑 **Steward: offline demo**。

| 目标 | 文件 / 函数 | 说明 |
| --- | --- | --- |
| 看完整一轮 | `steward/core/runner.py` → `run_turn` | 在 provider 返回后停下，检查 `result` |
| 抓某个工具 | `steward/tools/base.py` → `execute` | 条件断点：`name == "run_shell"` |
| 观察流式拼装 | `steward/providers/openai_compat.py` → `StreamAccumulator.feed` | 每个分片都会命中，建议改用日志点 |
| 调试裁剪 | `steward/core/context.py` → `fit` | 检查 `kept` 和 `used` |
| 调试审批 | `steward/policy/permissions.py` → `decide` | 条件：`tool_name == "run_shell"` |
| 调试落库 | `steward/session/store.py` → `append` | 看实际存储的 payload |

停在 `Runner` 里时，可以加这些监视表达式：

```text
[m["role"] for m in messages]
result.tool_calls
call["function"]["arguments"]
[self.config.permission, self.config.auto_approve]
```

**用日志点代替断点。** 在行号槽右键 → *添加日志点*，写 `chunk={chunk}` 或 `tool={name} args={args}`。日志点只打印不暂停，是跟踪 SSE 分片唯一不折磨人的方式。

**单步进入第三方库。** 在配置里设 `"justMyCode": false` 就能步入 `httpx`，但噪音很大；一般直接在 `providers/openai_compat.py` 打断点更划算。

## 调试测试

先启用测试发现（**Python: Configure Tests → unittest → tests → `test_*.py`**），左侧 Testing（烧瓶图标）就会列出 `unittest` 用例；这个开关写在个人的 `.vscode/settings.json` 里，该文件已被 git 忽略。点测试旁边的虫子图标即可只调试该用例，也可以用 **Steward: unit tests** 调试整个套件。

用带脚本化 provider 的测试往往比 REPL 更快：在 `steward/core/runner.py` 打断点，运行 `tests/test_runner.py::test_executes_a_tool_then_answers`，一秒内就停在一个完整的工具调用循环里。

## 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| 停在 `you ›` 后没反应 | 启动配置用了 `internalConsole`，改成 `integratedTerminal` |
| `ModuleNotFoundError: steward` | 用当前选择的解释器执行 `pip install -e .`，或确认 `cwd` 是仓库根目录 |
| `api_key is empty` | 填 `config.toml` / `.env`，或 export `STEWARD_API_KEY` |
| 断点是空心圆 | 当前配置没有 import 这个文件；换对应的启动配置 |
| 流式时调试器极慢 | `feed()` 里的断点每个分片都会命中，换成日志点 |
| 从不弹审批 | 开了 `--yes` / `auto_approve`，或权限模式是 `full` |
| `.env` 配置一启动就失败 | 先把 `.env.example` 复制成 `.env` |
| 新接口报 `404 no route` 或 `405 Method Not Allowed` | 运行中的进程比代码旧：页面是每次请求从磁盘读的，`git pull` 之后前端可能比后端新。重启进程（⇧⌘F5，或 Ctrl-C 后重新 `steward web`） |
| Provider 对话框提示后端缺少 `/api/provider` | 同上——重启 `steward web`，在那之前对话框会自我锁定 |
| web 配置提示缺 FastAPI | 用当前解释器执行 `pip install -e ".[web]"` |
| Vite 页面能开但接口全失败 | API 没起，或者它没落在 8765 端口 |
| `127.0.0.1:5173` 连不上 | 改用 `http://localhost:5173`（Vite 绑的是 IPv6 回环） |
| **Web: Vite dev** 起不来 | 需要 Node 18+，并在 `web/` 里 `npm ci`（任务 **web: install**） |

## 远程 / SSH

**Remote - SSH** 下同样可用：在远程主机打开目录，选择远程的 `.venv` 解释器，按 F5。终端运行在远端，审批提示和文件路径都按远端环境工作。
