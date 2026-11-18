# 学习笔记

[English](../learning.md) | 简体中文

这个项目既是工具，也是教学材料。下面的实验按顺序设计，每一个都会打破一个不同的假设。建议在一次性工作目录和便宜模型上做。

## 实验

### 1. 观察线上格式

把 `stream_usage` 设为 `true`，跑一轮调用工具的任务，并在 `StreamAccumulator.feed` 里临时加一句 `print(chunk)`。

要回答的问题：
- 哪些分片带 `id` / `name` / `arguments`？
- `finish_reason` 出现在哪里？
- usage 分片长什么样，你的 provider 会发吗？

### 2. 打破循环

把 `max_steps` 设为 3，让它做一件需要读 5 个文件的事。然后设为 50，并给它一个永远报错的工具。

观察：它在第几次停止重试？错误文本是否帮它改变了策略？修改 `tools.execute()` 的错误格式再对比一次。

### 3. 撑爆上下文

写一个 200KB 的文件，然后让它不分页地总结。观察 `context.fit()` 如何压缩工具结果，以及模型如何抱怨中间内容缺失。

再把 `max_tool_chars` 调高，让请求超过模型真实窗口。provider 返回的 400 就是本课：估算预算必须为输出 token 和系统提示词留空间。

### 4. 把 turn 切成两半

临时把 `fit()` 改成按单条消息裁剪并激进截断。所有 provider 都会报"tool 消息前面没有对应的 tool call"。恢复代码，再读一遍 `context.py` 里的注释。

### 5. 取消一个任务

加一个 sleep 30 秒的工具，启动后按 Ctrl+C。在 v0 里整个进程会退出。实现取消（在 provider 流和工具外面用 `asyncio.Task.cancel()`），并决定历史里存什么：cancelled 标记，还是什么都不存？

### 6. 量化"工具税"

同一个问题，分别在不挂工具、挂工具、以及工具描述故意含糊的情况下跑。对比 token 用量，以及模型到底有没有调用工具。

这是理解"**schema 质量比调提示词更重要**"最便宜的方式。

## 阅读地图

下面两个开源项目在同一套理念上做到了生产规模。读完本项目对应模块后再看，而不是反过来。

来自 [CowAgent](https://github.com/zhayujie/CowAgent)：

| 概念 | 文件 |
| --- | --- |
| 工具注册与动态加载 | `agent/tools/tool_manager.py` |
| 权限模式 | `agent/permission/policy.py` |
| 会话存储 | `agent/memory/conversation_store.py` |
| 成熟的流式/工具循环 | `agent/protocol/agent_stream.py` |

来自 [nanobot](https://github.com/HKUDS/nanobot)：

| 概念 | 文件 |
| --- | --- |
| 渠道面向 vs 模型面向的循环拆分 | `nanobot/agent/loop.py`、`nanobot/agent/runner.py` |
| 渠道与内核之间的异步消息总线 | `nanobot/bus/queue.py` |
| 带历史/分支的 SQLite 会话 | `nanobot/session/sqlite_store.py` |
| 操作系统沙箱后端 | `nanobot/execution/sandbox.py` |
| 走 WebSocket 的 Responses API | `nanobot/providers/openai_responses/` |

经验法则：抄它们的**边界**，不要抄它们的功能清单。

## 不用框架做评测

一条 golden 任务是一个 JSON 文件：

```json
{
  "name": "edit-existing-file",
  "prompt": "在 notes.md 的末尾加一行 'done'",
  "setup": {"notes.md": "first line\n"},
  "expect_tools": ["read_file", "edit_file"],
  "expect_file": {"notes.md": "first line\ndone\n"}
}
```

一个 runner 要做五件事：

1. 按 `setup` 创建临时工作目录；
2. 用脚本化或真实 provider 驱动 `Runner`；
3. 断言被调用工具的名称（作为多重集合）；
4. 断言最终文件内容；
5. 记录 token、步数、耗时。

20 条这样的任务能抓住大多数提示词回归。它们也是比较模型最诚实的方式：先看工具调用准确率，再看文笔。

## 读代码时值得注意的地方

- `providers/openai_compat.py` 完全不知道工具"是什么意思"——它只知道 schema。把策略挡在 provider 之外，是能随意换模型的关键。
- `Runner` 从不直接碰文件系统，只调用 `execute()`。这个缝就是沙箱、远程执行器或 mock 应该出现的位置。
- `SessionStore.messages()` 把存储行转换回精确的请求结构。这里的任何不匹配都会变成一个很难追的 provider 400，所以转换被收在一个小函数里。
- `context.fit()` 是唯一决定"模型看到什么"的地方。如果 bug 看起来像"模型忘了"，先看这里，再看 `history`。
