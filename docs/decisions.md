# Decisions

English | [简体中文](zh/decisions.md)

Short records of choices that shape the project. Each entry states the
alternatives so a future change can argue with the reasoning rather than the
code.

## 1. asyncio, not threads

**Decision:** the whole runtime is `async`; blocking tools are pushed to a
worker thread with `asyncio.to_thread`.

**Why:** streaming, per-tool timeouts, cancellation and multiple channels are
all I/O waits. `asyncio.wait_for` expresses a timeout in one line; a thread
pool needs a queue, a sentinel and a way to abandon work.

**Cost:** blocking work needs care (`to_thread`), and a CPU-heavy tool will
still block its worker.

## 2. Hand-written OpenAI SSE instead of an SDK

**Decision:** `httpx` + ~90 lines of SSE and tool-call assembly.

**Why:** this is a learning project and the wire format is the thing worth
learning. The assembly of `tool_calls` by `index` — arguments split over many
`data:` chunks — is the least obvious part of every agent, and an SDK hides it.

**Cost:** we inherit the job of handling keep-alives, `[DONE]`, malformed JSON
and provider quirks. For production, an official SDK is the safer default; the
`Provider` protocol means swapping one in does not touch the loop.

## 3. SQLite, append-only, storing model messages

**Decision:** one table, JSON payloads, no updates, no ORM.

**Why:** a conversation is an ordered log. Append-only gives deterministic
replay, trivial backup, and a path to branching. Storing the model-facing
message (rather than rendered text) means `history` is a debugging tool.

**Alternatives rejected:** JSONL files (harder to query/filter), Postgres
(unnecessary for one user), ORM (adds a layer with no payoff at this size).

## 4. Trim by whole turns

**Decision:** `context.fit()` keeps the newest turns that fit; it never splits
one.

**Why:** an assistant message with `tool_calls` and its `tool` results are a
unit. Cutting between them produces a request every provider rejects. Shrinking
tool output is preferred over dropping the turn because a 40 KB file read is
usually less valuable than the conversation that led to it.

**Deferred:** summarising dropped turns. When added, the summary becomes a
system message so the log on disk stays untouched.

## 5. No agent framework

**Decision:** no LangChain/LlamaIndex/etc.

**Why:** the loop is small and the framework's abstractions (chains, runnables,
callbacks) would obscure the four steps this project exists to teach. The
integration points a framework provides — model adapters, tool schemas,
retries — are each under 100 lines here.

**Cost:** we own retries, schema validation and provider quirks ourselves.
That is the point, but it is a real maintenance cost.

## 6. Tools confined to one workspace

**Decision:** every file path is resolved and checked against the workspace,
in every permission mode.

**Why:** "the assistant deleted the wrong file" is the failure users actually
fear. A single directory boundary is easy to reason about and easy to explain
in the system prompt.

**Limitation:** shell commands can still reach outside the workspace. That is
why shell needs approval in `workspace-write` mode, and why the README says
this is not a sandbox.

## 7. Approval instead of an OS sandbox (for now)

**Decision:** `run_shell` asks once per call unless `--yes`/`auto_approve`.

**Why:** a personal assistant on a laptop runs commands in the user's own
account; a real boundary requires bubblewrap (Linux) or Seatbelt (macOS) and a
mount policy. That is a separate phase; pretending a subprocess allowlist is a
sandbox would be worse than saying it isn't.

**Next:** `execution/sandbox.py`-style backends, selected per platform, with a
clear "sandboxed / unsandboxed" status in the banner.

## 8. Events, not print statements

**Decision:** the runner emits typed dataclasses; only `cli.py` prints.

**Why:** the second frontend (a web UI or a chat channel) then costs one
renderer, not a refactor. It also makes tests assert on events instead of
captured stdout.

## 9. Secrets are not inherited by subprocesses

**Decision:** `run_shell` passes a small environment allowlist.

**Why:** the parent environment holds API keys. A command like `env` or a
third-party script would otherwise print them into the transcript, which is
then stored in SQLite and sent to the model. Any variable a CLI actually needs
can be added to the list explicitly.

## 10. CLI first, web later

**Decision:** the only UI in v0 is a REPL.

**Why:** the REPL proves the loop, streaming and approval flow with ~100 lines.
A web UI before the event model is stable would be rework. The roadmap adds an
SSE endpoint and a single page once the loop stops changing.
