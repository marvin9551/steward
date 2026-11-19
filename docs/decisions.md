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
why every mutation needs approval in `workspace-write` mode, and why the
README says this is not a sandbox.

## 7. Approval instead of an OS sandbox (for now)

**Decision:** the riskiest operations ask once per call unless
`--yes`/`auto_approve`. In `workspace-write` that means shell; the
`ask-before-write` mode (added later, same mechanism) extends the ask to file
writes and edits, because "the assistant changed the wrong file" deserves the
same human check as "the assistant ran a command".

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

**Now:** the loop stopped changing, so P3 added the page — with no change to the
runner, only two new consumers of `on_event`. See decisions 11 and 12.

## 11. A stdlib HTTP server, not a Python web framework (superseded by 13)

**Decision:** `http.server.ThreadingHTTPServer` with hand-written routing, JSON
and SSE. The page is React 18 + Vite + Tailwind + shadcn/ui in `web/`, built
into `steward/web/static/`.

**Why:** eight endpoints do not need a router, a dependency-injection layer or
an ASGI server, and skipping FastAPI keeps the install at one package. The
frontend goes the other way on purpose: shadcn/ui is copied-in source, so the
components stay readable and editable, and a real component set is worth one
build step.

**Cost:** we own request parsing, error responses and static serving (~280
lines), and the page needs Node to rebuild. The built bundle is committed, so
running it never does.

**Superseded:** the threading cost turned out to be the wrong trade. See
decision 13. The frontend half of this decision still stands.

## 12. One turn at a time, and a cursor instead of a queue

**Decision:** `WebService` holds a single turn slot, and a second
`POST /api/turn` gets 409. `GET /api/stream` is a cursor over a bounded
in-memory event log, not a durable queue.

**Why:** one person asking one assistant one question is the real usage, and two
turns writing into one session produce a history nobody wants to read. A cursor
over a deque gives replay and resume for free, while the durable log stays in
SQLite where it already was.

**Cost:** a client that falls further behind than the buffer is told to `resync`
(reload from SQLite) instead of being caught up event by event. It can therefore
miss in-flight deltas for the current step; committed history is never lost.

## 13. FastAPI for the web layer, as an optional extra

**Decision:** the web API is FastAPI + uvicorn (`steward/web/api.py`,
`server.py`), installed through `pip install -e ".[web]"`. The agent loop keeps
its single runtime dependency.

**Why:** the hand-written `http.server` version worked, but it paid for its
smallness in the wrong currency — a thread per SSE connection, a thread per turn
running its own event loop, and approvals bridged through `threading.Event`.
Under ASGI the turn is an `asyncio.Task`, an approval is a future, and SSE is an
async generator: the same primitives decision 1 already chose for the runtime,
with no second concurrency model to reason about. Request validation, a
consistent error shape and `/docs` come with it.

**Cost:** two more packages for anyone who wants the UI, plus a framework's
conventions. Keeping it an extra means the CLI install is still one dependency,
and the web tests skip cleanly when it is absent.

**What did not change:** the HTTP contract, the page, `Runner`, and the rule
that behavior lives in `service.py` while routes stay thin.

## 14. The page edits an overlay, never your config.toml

**Decision:** the web settings dialog persists to `~/.steward/settings.toml`,
which `config.load()` applies on top of `config.toml`. `STEWARD_*` environment
variables still win, and the service refuses page edits of env-pinned fields.

**Why:** `config.toml` is a file you edit by hand, with comments worth keeping;
silently rewriting it from a form would be hostile. A separate overlay gives the
page something it owns, makes "undo everything the page did" a one-file delete,
and keeps the existing precedence story true: defaults < config.toml < overlay
< environment.

**Cost:** one more place a value can come from, which is why the dialog says
where a save lands and locks fields the environment already set. The API key is
write-only over HTTP — `GET` reports a boolean — so a saved key can never be
read back out of the page.

## 15. workspace-write asks for writes too

**Decision:** `workspace-write` now asks before every write, edit **and**
shell call. `ask-before-write` is removed as a separate mode but still loads
as a deprecated alias for `workspace-write`, because the old mode already had
exactly this decision table.

**Why:** file tools are bounded by the workspace, but bounded reach is not
the same as reviewed change — a write inside the workspace can still
overwrite something the user cares about, and reads-only-are-free keeps the
mode cheap to run. Three rungs (deny / ask / allow) also explain better than
four rungs where two of them were identical.

**Cost:** the default mode now interrupts on every write. `--yes` /
`auto_approve` and the web approval card's "always" decision are the escape
hatches; the web page's mode dropdown lists three entries instead of four.
