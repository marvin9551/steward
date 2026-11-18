# Steward

> 一个可以完整读完、可审计的个人 Agent。

[English](README.md) | 简体中文

Steward 是一个自托管的个人助理：连接任意 OpenAI 兼容模型，在你指定的工作目录里读写文件，在你批准后执行 shell 命令，并把每一轮对话存进本地 SQLite。

它同时也是一个**参考实现**。整个 harness 约 1,100 行 Python（含注释与文档字符串），运行时只依赖一个第三方包（`httpx`）。没有任何框架把细节藏起来——工具调用怎么流式拼装、历史怎么裁剪、什么命令需要审批——每一处都可见、可改。

名字是有意的：steward 是替人打理事务的管家。它被授予访问权限，被期望待在自己的职责范围内，并且应当能解释自己做过什么。

## 状态

`0.1.0` —— 一个完整的单用户闭环：流式对话、工具调用、持久会话、权限与 CLI。后续规划见 [docs/zh/roadmap.md](docs/zh/roadmap.md)。

## 快速开始

需要 Python 3.11+。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp config.example.toml config.toml
# 编辑 config.toml：设置 base_url / model 和 key
# （或者 export STEWARD_API_KEY=...）

steward
```

首次运行会创建 `~/.steward/`（数据库）和 `~/.steward/workspace/`（文件工具唯一能访问的目录）。可以试试：

```text
you › 在这个目录建一个 notes.md，写三条今天的待办
```

## 命令

```bash
steward                          # 开始对话
steward chat --new               # 强制新建会话
steward chat --session <id>      # 恢复会话
steward sessions                 # 列出已保存会话
steward history <id>             # 打印模型实际看到的消息
steward chat --permission read-only
steward chat --yes               # 自动批准 shell 命令
steward --config ./my.toml chat
```

对话内命令：`/new`、`/sessions`、`/exit`。

## 配置

`config.toml` 分两段：`[provider]` 和 `[agent]`，所有字段见 [`config.example.toml`](config.example.toml)。环境变量会覆盖文件，因此 key 不必写进仓库：

| 环境变量 | 含义 |
| --- | --- |
| `STEWARD_API_KEY` | 模型 API Key |
| `STEWARD_BASE_URL` | OpenAI 兼容的 base URL |
| `STEWARD_MODEL` | 模型 id |
| `STEWARD_WORKSPACE` | 工作目录 |
| `STEWARD_DATA_DIR` | 数据库 / 数据目录 |
| `STEWARD_PERMISSION` | `read-only`、`workspace-write`、`full` |

任意 OpenAI 兼容端点都可以：OpenAI、DeepSeek、GLM、Qwen、Moonshot、MiniMax、本地 Ollama / vLLM，或聚合网关。模型需要支持工具调用。

## 安全模型

三档权限：

| 模式 | 文件 | Shell |
| --- | --- | --- |
| `read-only` | 只读 | 拒绝 |
| `workspace-write`（默认） | 在**工作目录内**读写 | 每次调用前询问 |
| `full` | 在工作目录内读写 | 不询问，直接执行 |

需要明确的是：**这不是沙箱**。文件工具会解析路径并拒绝工作目录之外的访问，shell 子进程只继承一小份环境变量白名单；但 shell 命令仍然拥有你当前用户的全部权限。如果需要真正的隔离，请把 Steward 放进容器，或使用操作系统级沙箱（bubblewrap / Seatbelt）。详见 [docs/zh/decisions.md](docs/zh/decisions.md)。

Key 除了发往配置的 `base_url` 之外，不会发往任何地方。

## 目录结构

```
steward/
  cli.py                 参数、REPL、事件渲染、审批提示
  config.py              TOML + 环境变量覆盖
  core/
    runner.py            Agent 主循环（建议先读这个文件）
    context.py           按完整 turn 裁剪 + 工具输出压缩
    prompt.py            系统提示词
    events.py            类型化事件（未来 UI 的渲染契约）
  providers/
    openai_compat.py     httpx + 手写 SSE + tool_calls 拼装
  tools/
    base.py              注册表、schema、超时与截断
    fs.py                read / write / edit / list，限定在工作目录
    shell.py             run_shell，环境变量白名单
  policy/permissions.py  allow / ask / deny 权限表
  session/store.py       追加式 SQLite 会话日志
```

## 文档

- [docs/zh/architecture.md](docs/zh/architecture.md) —— 数据流、消息模型、工具契约、上下文预算、数据库结构。
- [docs/zh/decisions.md](docs/zh/decisions.md) —— 每个设计选择背后的原因。
- [docs/zh/roadmap.md](docs/zh/roadmap.md) —— 分阶段计划，以及每个阶段要学什么。
- [docs/zh/learning.md](docs/zh/learning.md) —— 实验、阅读地图、评测方案。

（英文原版见 [README.md](README.md) 与 [docs/architecture.md](docs/architecture.md)。）

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试会回放录制的 SSE 分片、覆盖每个工具、并用脚本化的 provider 驱动主循环——不需要网络和 API Key。

## 许可证

MIT。
