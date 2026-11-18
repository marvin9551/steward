# Roadmap

English | [简体中文](zh/roadmap.md)

Each phase ends with something usable. The point is to keep the loop running
while adding one concept at a time.

## P0 — the loop (done)

- OpenAI-compatible streaming chat, tool-call assembly
- tools: `read_file`, `write_file`, `edit_file`, `list_dir`, `run_shell`
- permission modes + approval prompt
- SQLite sessions, `history` / `sessions` commands
- turn-based context trimming, tool output shrink

**Done when:** a fresh clone can edit a file and explain the change, and
restarting the CLI resumes the conversation.

## P1 — make it trustworthy

- [ ] **Summarise dropped turns.** When the budget forces old turns out, call
      the model once to produce a short summary and keep it as a system message.
      Measure: a 30-turn session should stay coherent after compaction.
- [ ] **Cancellation.** Ctrl+C should cancel the in-flight stream and the tool,
      leave a `cancelled` marker in history, and keep the session usable.
- [ ] **Retries.** Retry 429/5xx with jittered backoff, but never retry a tool
      call whose result was already appended.
- [ ] **Usage and cost.** Persist `usage` per turn; `steward usage` prints
      tokens/cost by day. Needed before raising the step budget.
- [ ] **A 20-task golden set.** Scripted prompts + expected tool calls, run
      against a cheap model in CI nightly.

**Done when:** killing a turn mid-stream leaves a consistent database, and the
golden set catches a deliberately broken tool description.

## P2 — personal assistant

- [ ] **Memory files.** `USER.md` (preferences) and `MEMORY.md` (facts) inside
      the workspace, both injected into the system prompt with a size cap.
      Prefer inspectable files over embeddings for the first version.
- [ ] **Daily summary job.** Once a day (or after N turns), append durable
      facts to `MEMORY.md`; keep the prompt to the model small.
- [ ] **Scheduler.** SQLite table of cron entries, one asyncio task, results
      delivered to the active channel. No overlap: a job that is still running
      is skipped, not queued.
- [ ] **Telegram channel.** Long polling, allowlist by user id, same `Runner`.
      This is where `on_event` earns its keep.

**Done when:** the assistant remembers a preference across sessions without a
vector database, and a scheduled job survives a restart.

## P3 — a window into the work (done)

- [x] **SSE endpoint.** `GET /api/stream` replays events by sequence number, so
      a dropped connection resumes instead of restarting the transcript.
- [x] **One-page UI.** Conversation, tool calls, approval buttons. React 18 +
      shadcn/ui on a stdlib HTTP server; no second backend.
- [x] **Approval cards.** Allow once / allow for this process / deny, enforced by
      the same `policy.decide` table the terminal prompt used.

**Done when:** the browser and `steward history` agree about a turn — same
messages, same tool calls, same results.

Still open in this phase: a file diff view, and cancelling a turn from the page
(which needs the P1 cancellation work first).


## P4 — boundaries, if you need them

- [ ] **Sandbox backends.** bubblewrap on Linux, Seatbelt on macOS, Job Object
      on Windows. Banner must say whether commands are sandboxed.
- [ ] **MCP client.** Official SDK, stdio + HTTP, per-server env allowlist.
- [ ] **Subagent as a tool.** Spawn a bounded task with its own step/token
      budget; return only the summary. Not a second orchestrator.
- [ ] **Packaging.** A wheel with an entry point, plus a Dockerfile that mounts
      the workspace read-write and the data dir read-only.

**Done when:** "run this untrusted script" is safe by construction, not by
convention.

## Explicit non-goals

- multi-tenant auth, billing, quotas
- a plugin marketplace
- 20 chat channels
- a custom vector database
- an agent-orchestration DSL

If a future requirement needs one of these, it should arrive with a decision
record explaining what changed.
