# 在 VS Code 中调试

[English](../vscode.md) | 简体中文

仓库自带 `.vscode/launch.json` 和 `.vscode/settings.json`，新 clone 下来不用额外配置就能打断点调试。

## 前置准备

1. 安装 **Python** 扩展（`ms-python.python`），它提供启动配置里使用的 `debugpy` 调试器。
2. Python 3.11 或更高版本。
3. 创建虚拟环境并安装本项目：

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

4. VS Code 中：**命令面板 → Python: Select Interpreter →** 选择
   `.venv/bin/python`（Windows 是 `.venv\Scripts\python.exe`）。

5. 配置模型（二选一）：

```bash
cp config.example.toml config.toml   # 然后编辑 base_url / model / api_key
```

或者把 `.env.example` 复制为 `.env`，使用 **Steward: chat (.env)** 配置。
`config.toml` 和 `.env` 都在 `.gitignore` 中。

## 开始调试

打开 **运行和调试**（`⇧⌘D` / `Ctrl+Shift+D`），选择一个配置，按 **F5**：

| 配置 | 实际运行 |
| --- | --- |
| **Steward: chat** | `python -m steward chat` —— 正常 REPL |
| **Steward: chat (read-only)** | 同上，加 `--permission read-only` |
| **Steward: chat (.env)** | 同上，从 `.env` 读取环境变量 |
| **Steward: sessions** | `python -m steward sessions` |
| **Steward: offline demo** | `scripts/debug_demo.py` —— 脚本化 provider，不联网 |
| **Steward: unit tests** | `python -m unittest discover -s tests -v` |

所有配置都刻意使用 `"console": "integratedTerminal"`。CLI 的输入提示和工具审批都走 `input()`，默认的 `internalConsole` 无法提供 stdin，REPL 会看起来卡住。

如果按 F5 没反应，看 **调试控制台** 的报错，并确认 Python 扩展已安装。老版本扩展的调试器 `type` 是 `"python"` 而不是 `"debugpy"`，其余字段一样。

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

因为有了 `.vscode/settings.json`，左侧 Testing（烧瓶图标）会自动发现 `unittest` 用例。点测试旁边的虫子图标即可只调试该用例，也可以用 **Steward: unit tests** 调试整个套件。

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

## 远程 / SSH

**Remote - SSH** 下同样可用：在远程主机打开目录，选择远程的 `.venv` 解释器，按 F5。终端运行在远端，审批提示和文件路径都按远端环境工作。
