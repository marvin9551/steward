# Learning notes

English | [简体中文](zh/learning.md)

This project is a teaching artifact as much as a tool. The exercises below are
ordered so each one breaks a different assumption. Do them on a throwaway
workspace and a cheap model.

## Experiments

### 1. Watch the wire

Set `stream_usage = true`, run a turn that calls one tool, and add a temporary
`print(chunk)` in `StreamAccumulator.feed`.

Questions to answer:
- which chunks carry `id` / `name` / `arguments`?
- where does `finish_reason` appear?
- what does the usage chunk look like, and does your provider send one?

### 2. Break the loop

Set `max_steps = 3` and ask for something that needs five file reads. Then set
it to 50 and give the model a tool that always errors.

Observe: at what point does it stop retrying? Does the error text help it
change strategy? Rewrite `tools.execute()`'s error format and compare.

### 3. Blow the context

Write a 200 KB file, then ask the model to summarise it without paging.
Watch `context.fit()` shrink the tool result and the model complain about
missing middle content.

Then set `max_tool_chars` high enough that the request exceeds the real model
window. The provider's 400 is the lesson: estimated budgets must leave room for
the output tokens and the system prompt.

### 4. Cut a turn in half

Temporarily change `fit()` to keep individual messages instead of whole turns
and trim aggressively. Every provider will reject the request with a message
about tool messages without a preceding tool call. Restore it and read the
comment in `context.py` again.

### 5. Cancel something

Add a tool that sleeps for 30 seconds, start it, and hit Ctrl+C. In v0 the
whole process exits. Implement cancellation (`asyncio.Task.cancel()` around the
provider stream and the tool) and decide what to store: a cancelled marker, or
nothing?

### 6. Measure the tool tax

Run the same question with and without tools attached, and with a deliberately
vague tool description versus a precise one. Compare token usage and whether
the model calls the tool at all.

This is the cheapest way to learn that **schema quality beats prompt tweaking**.

## Reading map

Two open-source projects implement the same ideas at production scale. Read
these files after the corresponding module here, not before.

From [CowAgent](https://github.com/zhayujie/CowAgent):

| Concept | File |
| --- | --- |
| Tool registry and dynamic loading | `agent/tools/tool_manager.py` |
| Permission modes | `agent/permission/policy.py` |
| Conversation storage | `agent/memory/conversation_store.py` |
| A mature streaming/tool loop | `agent/protocol/agent_stream.py` |

From [nanobot](https://github.com/HKUDS/nanobot):

| Concept | File |
| --- | --- |
| Channel-facing vs model-facing loop split | `nanobot/agent/loop.py`, `nanobot/agent/runner.py` |
| Async message bus between channels and core | `nanobot/bus/queue.py` |
| SQLite sessions with history/branching | `nanobot/session/sqlite_store.py` |
| OS sandbox backends | `nanobot/execution/sandbox.py` |
| Responses API with WebSocket transport | `nanobot/providers/openai_responses/` |

Rule of thumb: copy their **boundaries**, not their feature lists.

## Evaluation without a framework

A golden task is a JSON file:

```json
{
  "name": "edit-existing-file",
  "prompt": "在 notes.md 的末尾加一行 'done'",
  "setup": {"notes.md": "first line\n"},
  "expect_tools": ["read_file", "edit_file"],
  "expect_file": {"notes.md": "first line\ndone\n"}
}
```

A runner that:

1. creates a temp workspace from `setup`,
2. drives `Runner` with a scripted or real provider,
3. asserts the tool names that were called (as a multiset),
4. asserts the final file contents,
5. records tokens, steps and wall time.

Twenty of these catch most prompt regressions. They are also the only honest
way to compare models: run the same set, look at tool-call accuracy before
looking at prose quality.

## Things to notice while reading the code

- `providers/openai_compat.py` has no knowledge of tools' meaning — only their
  schema. Keeping policy out of the provider is what lets you swap models.
- `Runner` never touches the filesystem directly; it only calls `execute()`.
  That is the seam where a sandbox, a remote executor, or a mock belongs.
- `SessionStore.messages()` converts storage rows back into the exact request
  shape. Any mismatch here becomes a provider 400 that is hard to trace, which
  is why the conversion is a single small function.
- `context.fit()` is the only place that decides what the model sees. If a bug
  looks like "the model forgot", start there, then look at `history`.
