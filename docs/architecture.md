# Architecture

English | [简体中文](zh/architecture.md)

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
  └─ emit events -> CLI renderer / SSE stream
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
| Web routes, JSON, SSE, static files | `steward/web/api.py` |
| uvicorn runner, port selection | `steward/web/server.py` |
| One turn at a time, transcript blocks | `steward/web/service.py` |
| Replayable event log | `steward/web/stream.py` |
| Approval registry | `steward/web/approvals.py` |
| The page | `web/` → built into `steward/web/static/` |

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
| `workspace-write` | allow | ask | ask |
| `full` | allow | allow | allow |

`ask-before-write` loads as a deprecated alias for `workspace-write` — the
old fourth mode had the same table.

Path confinement is separate and always on: `tools/fs.py` resolves every path
and rejects anything outside `ToolContext.workspace`, in every mode. `full`
means "don't ask", not "any path".

## The web front end

`steward web` runs a FastAPI app under uvicorn (optional `[web]` extra) and
serves one page plus a small JSON/SSE API. It is a second *renderer* of the same
events, not a second agent:

```
page (React) ── POST /api/turn ──▶ WebService.start_turn
                                      │  asyncio.Task, one at a time
                                      ▼
                                 Runner.run_turn
                                      │ on_event
                                      ▼
                                 EventLog (seq numbers)
                                      │
page (EventSource) ◀─ GET /api/stream ┘  (async generator)
```

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | model, permission, workspace, busy flag; never the key |
| `GET /api/sessions` | sidebar list |
| `GET /api/sessions/<id>` | stored messages flattened into UI blocks |
| `POST /api/sessions` | mint a session id |
| `POST /api/turn` | start a turn (409 while one is running) |
| `GET /api/stream` | SSE; `Last-Event-ID` resumes, a stale cursor gets `resync` |
| `POST /api/approval` | answer an approval card |
| `POST /api/permission` | switch mode; applies to the next tool call |
| `GET /api/provider` | provider settings; the key is reported as a boolean |
| `POST /api/provider` | edit settings; empty `api_key` keeps the current one |
| `POST /api/provider/test` | one tiny real request to verify the saved key |
| `POST /api/models` | sync the model list from the gateway; accepts unsaved form values |
| `GET /api/profiles` | saved provider profiles; the active one is named, keys are booleans |
| `POST /api/profiles` | upsert a profile (the dialog marks it active) |
| `POST /api/profiles/apply` | copy a profile into the active provider |
| `DELETE /api/profiles/<name>` | remove a profile |

`/docs` and `/openapi.json` come with FastAPI; request bodies are Pydantic models
and every error is normalized to `{"error": "..."}`, the shape the page speaks.

Three choices keep this small:

1. **One turn at a time.** `WebService` holds a single task slot; a second
   request gets 409 instead of two turns interleaving writes into one session.
   Honest for a single-user tool, and it removes every queueing question.
2. **A cursor, not a queue.** The SSE generator tails a bounded deque every
   50 ms with `asyncio.sleep`. The latency is imperceptible; replay, resume and
   backpressure stop being problems, and no thread is parked per viewer.
3. **Blocks, not messages.** `WebService.transcript()` flattens the
   OpenAI-shaped messages into the same block list the live stream produces, so
   history and streaming render through one component tree.

Approvals are a future: the turn awaits `ApprovalRegistry.wait()` while
`POST /api/approval` resolves it — same loop, no locks. "always" is remembered
per tool name for the life of the process, so restarting restores the terminal's
caution.

**Provider settings from the page.** The gear dialog edits `base_url`, model,
key and temperature. Presets fill known endpoints; an internal gateway
("opencode go" style) is just a labeled custom slot — any OpenAI-compatible
URL works. **同步模型** asks the gateway for its model list so you pick instead
of typing, and accepts the unsaved form values, which makes the first visit one
flow: paste URL + key → sync → pick → save. Saves go to
`~/.steward/settings.toml` — an overlay `config.load()` applies on top of
`config.toml` — instead of rewriting your commented file. The key is write-only
over HTTP: `GET /api/provider` returns `api_key_set`, and posting an empty key
keeps the current one. Fields set by `STEWARD_*` environment variables can
never be replaced by a file, so the service refuses those edits and the dialog
locks the inputs.

**Profiles.** The same overlay stores named provider sets
(`[profiles.<name>]`, ignored by `config.load()`). The Provider tab lists them
with what is in effect now; `apply` copies a profile into the active provider —
absent fields keep their current values, env-locked fields refuse to come from
a file — and deleting the active profile clears the marker but leaves the
config alone. A profile without a key reuses whatever key is active, so one key
can serve several endpoints.

The page is `web/` (React 18, Vite, Tailwind, shadcn/ui). It builds into
`steward/web/static/`, which is committed so the UI needs no Node toolchain at
runtime.

## Extension points

- **New tool** — write the function in `tools/`, decorate it, import the module
  in `tools/__init__.py`. No runner change.
- **New provider** — implement the callable in `providers/base.py`'s protocol
  and pass it to `Runner(provider=...)`. Everything else is provider-agnostic.
- **New frontend/channel** — subscribe to `on_event`; the CLI renderer is ~40
  lines and `web/service.py` is ~250, both show the full contract.
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
