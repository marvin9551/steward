# Architecture

This document maps behavior to files. Read it with the source open; the source
is the specification.

## One turn

```
CLI (input)
  │  user text
  ▼
Runner.run_turn
  ├─ store.append(user)
  │
  ├─ loop (max_steps):
  │    ├─ fit_messages(): system prompt + history, trimmed by whole turns
  │    ├─ provider.stream_chat(): httpx POST + SSE accumulation
  │    ├─ store.append(assistant, text + tool_calls)
  │    ├─ if no tool_calls: emit TurnDone, return
  │    └─ for each tool call:
  │         ├─ policy.decide(mode, name) -> allow | ask | deny
  │         ├─ (ask) approver() -> user y/N
  │         ├─ tools.execute(): timeout, errors as text, truncation
  │         └─ store.append(tool, result)
  │
  └─ emit events -> CLI renderer
```

Files:

| Concern | File |
| --- | --- |
| Loop, tool dispatch, step budget | `steward/core/runner.py` |
| Message-to-token estimate, trimming | `steward/core/context.py` |
| System prompt | `steward/core/prompt.py` |
| Typed UI events | `steward/core/events.py` |
| HTTP + SSE + tool-call assembly | `steward/providers/openai_compat.py` |
| Tool registry and execution | `steward/tools/base.py` |
| File / shell tools | `steward/tools/fs.py`, `steward/tools/shell.py` |
| Allow/ask/deny table | `steward/policy/permissions.py` |
| Session persistence | `steward/session/store.py` |

## The message model

Steward stores exactly what it sends to the model, in the OpenAI chat shape:

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

Two rules follow from this shape:

1. **An assistant message with `tool_calls` must be followed by one `tool`
   message per call id.** Trimming may therefore never cut between them. This
   is why `context.fit()` keeps whole turns, not individual messages.
2. **`arguments` is a JSON string, not an object.** It arrives split across
   stream chunks, so the provider assembles it before the runner parses it. A
   parse failure becomes a tool result (`ERROR: ... not valid JSON`), not an
   exception, so the model can correct itself.

## The tool contract

Each tool is a name, a description, a JSON Schema, and a function:

```python
@tool("read_file", "Read a text file ...", { ... })
def read_file(ctx: ToolContext, path: str, offset: int = 1) -> str: ...
```

`tools.execute()` owns everything that must be uniform:

- unknown tool name → text listing the available tools
- bad arguments (`TypeError`) → text explaining the mismatch
- timeout → text saying so
- any exception → `ERROR: Type: message`
- output truncation to `config.max_tool_output`

The model sees tool failures as ordinary content, which is what lets it retry
with different arguments instead of aborting the turn.

The registry is populated by importing `steward.tools`; adding a module plus
one import in `tools/__init__.py` is enough to expose new tools.

## Context budgeting

`context.fit()` runs before every model call:

1. System messages are always kept.
2. History is split into turns (each begins at a user message).
3. Turns are added newest-first until the rough token budget is reached.
4. Tool messages longer than a threshold are shrunk (head + tail) before the
   request is sent.

The token count is an estimate (CJK ≈ 1 token/char, other text ≈ 4 chars/token).
It only decides when to trim. The real number comes back in `usage` when the
provider reports it.

Not implemented yet: summarisation of dropped turns. When it lands it belongs
here, and the summary is inserted as a system message so the original turns can
stay on disk unchanged.

## Session storage

```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,          -- user | assistant | tool
  payload TEXT NOT NULL,       -- JSON, shape depends on role
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_messages_session_seq ON messages(session_id, seq);
```

Append-only. There is no update path, so replay is deterministic and a future
fork/branch feature is "copy a prefix and give it a new id". `steward history`
prints the stored messages, i.e. what the model actually saw.

If a provider call fails, the user message is already stored. That is
intentional: history records the attempt. Fix the key/config and send the next
message; the failed turn remains visible in `history`.

## Permissions

`policy.decide(mode, tool_name)` returns one of three verbs:

| Mode | read tools | write/edit | shell |
| --- | --- | --- | --- |
| `read-only` | allow | deny | deny |
| `workspace-write` | allow | allow | ask |
| `full` | allow | allow | allow |

Path confinement is separate and always on: `tools/fs.py` resolves every path
and rejects anything outside `ToolContext.workspace`, in every mode. `full`
means "don't ask", not "any path".

## Extension points

- **New tool** — write the function in `tools/`, decorate it, import the module
  in `tools/__init__.py`. No runner change.
- **New provider** — implement the callable in `providers/base.py`'s protocol
  and pass it to `Runner(provider=...)`. Everything else is provider-agnostic.
- **New frontend/channel** — subscribe to `on_event`; the CLI renderer is ~40
  lines and shows the full contract.
- **Memory / RAG** — add a step in `Runner.run_turn` that enriches the system
  prompt, and a background job that writes summaries. Do not put it in the
  provider layer.

## Known failure modes

| Symptom | Likely cause |
| --- | --- |
| `HTTP 400` mentioning `tools` | model does not support tool calling |
| model never calls a tool | weak tool description, or the prompt discourages it |
| `arguments` parse errors | model emits non-strict JSON; the error text invites a retry |
| context overflow | `context_budget` too high for the model, or one huge tool result |
| 404 on every call | wrong `base_url` (missing `/v1`, or pasted full endpoint) |
| `api_key is empty` | config/env not set |
