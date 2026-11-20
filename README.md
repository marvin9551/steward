# Steward

> A small, auditable personal agent you can read end to end.

English | [简体中文](README.zh-CN.md)

Steward is a self-hosted personal assistant: it talks to any OpenAI-compatible
model, reads and edits files in a workspace you choose, runs shell commands
with your approval, and keeps every conversation in a local SQLite file.

It is also a **reference implementation**. The whole harness is about 1,100
lines of Python (comments and docstrings included) across a handful of files,
with exactly one runtime dependency (`httpx`); the optional web UI adds FastAPI
and uvicorn. Nothing is hidden behind a framework, so every decision — how tool
calls stream in, how history is trimmed, when a command needs approval — is
visible and changeable.

The name is deliberate: a steward manages someone else's affairs. It is trusted
with access, expected to stay inside its remit, and should be able to explain
what it did.

## Status

`0.1.0` — a complete single-user loop: streaming chat, tool calls, persistent
sessions, permissions, a CLI and a local web UI. See
[docs/roadmap.md](docs/roadmap.md) for what comes next.

## Quick start

Requires Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[web]"      # drop [web] for a terminal-only install

cp config.example.toml config.toml
# edit config.toml: set base_url / model, and the key
# (or export STEWARD_API_KEY=...)

steward web        # browser UI on http://127.0.0.1:8765
# or
steward            # the same loop in the terminal
```

First run creates `~/.steward/` (database) and `~/.steward/workspace/` (the
only directory the file tools may touch). Try:

```text
you › 在这个目录建一个 notes.md，写三条今天的待办
```

## Commands

```bash
steward                          # start a conversation
steward web                      # serve the web UI (opens a browser)
steward web --port 9000 --no-open
steward chat --new               # force a new session
steward chat --session <id>      # resume a session
steward sessions                 # list saved sessions
steward history <id>             # print the raw messages the model saw
steward chat --permission read-only
steward chat --yes               # auto-approve shell commands
steward --config ./my.toml chat
```

In-chat commands: `/new`, `/sessions`, `/exit`.

## Web UI

`steward web` serves one page from `http://127.0.0.1:8765`. It is the same
`Runner` and the same SQLite sessions as the CLI — you can watch a conversation
in the browser and print it with `steward history` afterwards.

- The API is FastAPI + uvicorn, installed by the optional `[web]` extra; the
  agent loop itself still has one dependency. `/docs` lists every endpoint.
- Streaming text and tool calls arrive over SSE
  (`GET /api/stream`, sequence-numbered so a reload resumes cleanly).
- `run_shell` opens an approval card instead of a terminal prompt: allow once,
  allow for this process, or deny.
- Sessions, the permission mode, and the reasoning toggle live in the page;
  changing the mode takes effect on the next tool call.
- The gear icon opens provider settings: pick a preset (DeepSeek, OpenAI, GLM,
  Moonshot, local Ollama, or an internal gateway such as opencode go), paste
  the key, and **同步模型** pulls the model list straight from the gateway
  (`GET /v1/models`) so you pick instead of typing. Saves land in
  `~/.steward/settings.toml`, an overlay the page owns; your `config.toml` is
  never rewritten, and `STEWARD_*` environment variables still win (locked
  fields are shown as locked).
- The **Provider** tab shows what is in effect right now, plus every saved
  profile as a card — switch with one click, delete with a confirm. Profiles
  are filed by filling 「另存为档案」 in the settings dialog.

The built bundle is committed, so no Node toolchain is needed to run it. The
source is `web/` (React 18 + Vite + Tailwind + shadcn/ui); rebuild with:

```bash
make web      # cd web && npm ci && npm run build -> steward/web/static
make web-dev  # Vite dev server on :5173, proxying /api to :8765
```

## Configuration

Everything the page can edit lives in the `settings` table inside
`steward.db` (SQLite): provider endpoint / model / key, temperature, usage
reporting, the permission mode, and named provider profiles. Edit it from
the web settings dialog — there is no config file to hand-edit any more.

Only the database *location* is configured outside SQLite. Environment
variables (weakest to strongest: defaults → database → environment):

| Variable | Meaning |
| --- | --- |
| `STEWARD_API_KEY` | provider API key (pins the field) |
| `STEWARD_BASE_URL` | OpenAI-compatible base URL (pins the field) |
| `STEWARD_MODEL` | model id (pins the field) |
| `STEWARD_WORKSPACE` | workspace directory |
| `STEWARD_DATA_DIR` | database / data directory |
| `STEWARD_PERMISSION` | `read-only`, `workspace-write`, `full` (pins the mode) |

An env var *pins* its field: the settings dialog locks it, because the env
var would win on every restart. A deployment that still carries the old
`config.toml` / `settings.toml` is migrated into the database on first web
start; after that the files are ignored.

Any OpenAI-compatible endpoint works: OpenAI, DeepSeek, GLM, Qwen, Moonshot,
MiniMax, a local Ollama or vLLM server, or a gateway. Tool calling must be
supported by the model.

## Safety model

Three permission modes:

| Mode | Files | Shell |
| --- | --- | --- |
| `read-only` | read only | denied |
| `workspace-write` (default) | read; every write/edit **asks first** | asks once per call |
| `full` | read + write inside the workspace | allowed without asking |

`ask-before-write` still loads as a deprecated alias for `workspace-write` —
it had the same behavior.

What this is **not**: a sandbox. File tools resolve paths and refuse anything
outside the workspace, and shell commands get a small environment allowlist,
but a shell command can still do anything your user can do. If you need a real
boundary, run Steward in a container or under an OS sandbox
(bubblewrap/Seatbelt). See [docs/decisions.md](docs/decisions.md).

Keys are never sent anywhere except the configured `base_url`.

## Layout

```
web/                     React 18 + Vite + Tailwind + shadcn/ui (the page)
steward/
  cli.py                 flags, REPL, event rendering, approval prompt
  config.py              TOML + env overrides
  core/
    runner.py            the agent loop (the file to read first)
    context.py           turn-based trimming + tool-output shrink
    prompt.py            system prompt
    events.py            typed events (what a UI renders)
  providers/
    openai_compat.py     httpx + hand-written SSE + tool-call assembly
  tools/
    base.py              registry, schemas, timeout/truncation
    fs.py                read/write/edit/list, workspace-confined
    shell.py             run_shell with env allowlist
  policy/permissions.py  allow / ask / deny table
  session/store.py       append-only SQLite conversation log
  web/
    api.py               FastAPI routes, JSON, SSE, static files
    server.py            uvicorn runner, port selection
    service.py           one turn at a time, transcript blocks
    stream.py            sequence-numbered event log
    approvals.py         allow once / always / deny
    static/              the built page (committed)
```

## Documentation

English docs live in `docs/`; a full Chinese translation is in
[README.zh-CN.md](README.zh-CN.md) and [`docs/zh/`](docs/zh/architecture.md).

- [docs/architecture.md](docs/architecture.md) — data flow, message model,
  tool contract, context budgeting, database schema.
- [docs/decisions.md](docs/decisions.md) — why each piece is the way it is.
- [docs/roadmap.md](docs/roadmap.md) — phases and what each one teaches.
- [docs/learning.md](docs/learning.md) — experiments, reading map, evaluation.
- [docs/vscode.md](docs/vscode.md) — run and debug with breakpoints in VS Code.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite replays recorded SSE chunks, exercises every tool, and drives the
loop with a scripted provider — no network or API key required. The web tests
cover the event log, the approval registry, and the HTTP surface; they skip
when the `[web]` extra is not installed.

## License

MIT.
