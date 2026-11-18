# Debugging in VS Code

English | [简体中文](zh/vscode.md)

The repository ships `.vscode/launch.json`, `.vscode/tasks.json` and
`.vscode/extensions.json`, so a fresh checkout can be debugged with breakpoints
without extra setup. (`.vscode/settings.json` is personal and gitignored.)

## Prerequisites

1. Install the **Python** extension (`ms-python.python`) — it provides the
   `debugpy` debugger used by the launch configurations. VS Code will suggest
   the rest from `.vscode/extensions.json`.
2. Python 3.11 or newer.
3. A virtual environment with the package installed:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[web]"            # drop [web] if you only want the CLI
```

4. In VS Code: **Command Palette → Python: Select Interpreter →**
   `.venv/bin/python` (or `.venv\Scripts\python.exe`).

5. Configure a model (either file works):

```bash
cp config.example.toml config.toml   # then edit base_url / model / api_key
```

or copy `.env.example` to `.env` and use a `(.env)` launch configuration.
`config.toml` and `.env` are gitignored.

6. Only for the frontend configs: Node 18+ and `npm ci` inside `web/`
   (or run the **web: install** task).

## Start debugging

Open **Run and Debug** (`⇧⌘D` / `Ctrl+Shift+D`), pick a configuration, press
**F5**:

| Configuration | What it runs |
| --- | --- |
| **Steward: chat** | `python -m steward chat` — the normal REPL |
| **Steward: chat (read-only)** | same, with `--permission read-only` |
| **Steward: chat (.env)** | same, loading `.env` |
| **Steward: sessions** | `python -m steward sessions` |
| **Steward: web** | `python -m steward web --port 8765`, opens the browser |
| **Steward: web (no browser)** | same with `--no-open` — for Vite, curl or a second tab |
| **Steward: web (read-only)** | same, `STEWARD_PERMISSION=read-only` in the environment |
| **Steward: web (.env)** | same, loading `.env` |
| **Web: Vite dev (5173)** | `npm run dev` in `web/` — the page with hot reload |
| **Steward: API + Vite (full stack)** | compound: both of the above, stopped together |
| **Steward: offline demo** | `scripts/debug_demo.py` — scripted provider, no network |
| **Steward: unit tests** | `python -m unittest discover -s tests -v` |

All configurations use `"console": "integratedTerminal"` on purpose. The CLI
calls `input()` for the prompt and for tool approval; the default
`internalConsole` cannot feed stdin, so the REPL would appear to hang.

If F5 does nothing, check the **Debug Console** for an interpreter error and
confirm the Python extension is installed. On older extensions the debugger
`type` is `"python"` instead of `"debugpy"`; both keys are otherwise identical.

## Debugging the web UI

Two ways to run the page, and they answer different questions:

| You are changing | Use | URL |
| --- | --- | --- |
| Python (routes, runner, tools) | **Steward: web** | `http://127.0.0.1:8765` — serves the committed bundle |
| The page itself | **Steward: API + Vite (full stack)** | `http://localhost:5173` — Vite hot reload, `/api` proxied to 8765 |

Notes that save time:

- Use **localhost**, not `127.0.0.1`, for the Vite URL: Vite binds the IPv6
  loopback by default.
- Python edits need a restart (`⇧⌘F5` / `Ctrl+Shift+F5`); there is no
  `--reload`, because uvicorn reload requires an import string and would fork a
  second process the debugger does not own. Frontend edits hot-reload on their
  own.
- The compound stops both processes together (`stopAll`). If you only restart
  the API, Vite keeps running and reconnects.
- Ports: the API steps to the next free port if 8765 is taken, and prints the
  one it chose. Vite's proxy is pinned to 8765, so keep the API on that port
  when using the compound (edit `args` in `launch.json` to change both).
- `steward web` reads `config.toml` and `STEWARD_*` environment variables, so a
  launch config's `"env"` block is the easiest way to pin a mode or a model
  without touching your real config.

### Where to put breakpoints (server)

| Goal | File / function | Notes |
| --- | --- | --- |
| See a request arrive | `steward/web/api.py` → `turn` | inspect the parsed `TurnRequest` |
| Follow a whole turn | `steward/web/service.py` → `_worker` | the turn runs as a task on the API loop |
| Debug an approval | `steward/web/service.py` → `_approve` | pauses before the card is answered; the page waits |
| Watch the stream | `steward/web/api.py` → `stream_events` | fires per batch — prefer a logpoint |
| Inspect stored blocks | `steward/web/service.py` → `transcript` | what the page renders after a reload |

Set `"justMyCode": false` on a web configuration to step into FastAPI/uvicorn.

## Tasks

**Terminal → Run Task** (`⇧⌘B` runs the default build):

| Task | Command |
| --- | --- |
| **web: install** | `npm ci` in `web/` |
| **web: build** | `npm run build` → `steward/web/static` (default build task) |
| **web: dev** | `npm run dev` as a background task |
| **test: unit** | the unittest suite with the selected interpreter |

Rebuild the bundle after changing the page if you want `steward web` (without
Vite) to show it; CI fails when the committed bundle drifts from the source.

## Where to put breakpoints

Start with the offline demo if you want to step through a complete turn
without spending tokens.

| Goal | File / function | Notes |
| --- | --- | --- |
| Inspect a full turn | `steward/core/runner.py` → `run_turn` | break after the provider returns to see `result` |
| Catch a specific tool | `steward/tools/base.py` → `execute` | conditional breakpoint: `name == "run_shell"` |
| Watch stream assembly | `steward/providers/openai_compat.py` → `StreamAccumulator.feed` | fires once per chunk — use a logpoint instead |
| Debug trimming | `steward/core/context.py` → `fit` | inspect `kept` and `used` |
| Debug approvals | `steward/policy/permissions.py` → `decide` | conditional: `tool_name == "run_shell"` |
| Debug persistence | `steward/session/store.py` → `append` | see the exact payload stored |

Useful watch expressions while paused in `Runner`:

```text
[m["role"] for m in messages]
result.tool_calls
call["function"]["arguments"]
[self.config.permission, self.config.auto_approve]
```

**Logpoints instead of breakpoints.** Right-click the gutter → *Add Logpoint*,
then write e.g. `chunk={chunk}` or `tool={name} args={args}`. Logpoints print
without pausing, which is the only sane way to follow SSE chunks.

**Stepping into libraries.** Set `"justMyCode": false` in the configuration to
step into `httpx`. It is noisy; prefer breakpoints in
`providers/openai_compat.py`.

## Debugging tests

The Testing sidebar (flask icon) discovers the `unittest` suite once you enable
it (**Python: Configure Tests → unittest → tests → `test_*.py`**); the setting
lives in your personal `.vscode/settings.json`, which is gitignored. Click the
bug icon next to a test to debug just that test, or use **Steward: unit tests**
to debug the whole suite.

A test that drives the loop with a scripted provider is often faster than the
REPL: set a breakpoint in `steward/core/runner.py`, run
`tests/test_runner.py::test_executes_a_tool_then_answers`, and you are inside a
complete tool-call cycle in one second.

## Common problems

| Symptom | Cause / fix |
| --- | --- |
| REPL hangs after `you ›` | launch config uses `internalConsole`; switch to `integratedTerminal` |
| `ModuleNotFoundError: steward` | run `pip install -e .` with the selected interpreter, or keep `cwd` at the repo root |
| `api_key is empty` | fill `config.toml` or `.env`, or export `STEWARD_API_KEY` |
| Breakpoint shows as hollow | file is not imported by the running config; use the matching launch config |
| Debugger extremely slow while streaming | a breakpoint in `feed()` is hit per chunk; replace it with a logpoint |
| Approval prompt never appears | `--yes`/`auto_approve` is on, or the mode is `full` |
| `.env` config fails immediately | copy `.env.example` to `.env` first |
| Web config exits with `needs FastAPI` | `pip install -e ".[web]"` with the selected interpreter |
| Vite page loads but every request fails | the API is not running, or it moved off port 8765 |
| `127.0.0.1:5173` refuses to connect | use `http://localhost:5173` (Vite binds IPv6 loopback) |
| `Web: Vite dev` fails to start | Node 18+ and `npm ci` in `web/` (task **web: install**) |

## Remote / SSH

The same configurations work with **Remote - SSH**: open the folder on the
remote host, select the remote `.venv` interpreter, and press F5. The terminal
runs on the remote machine, so approval prompts and file paths behave normally.
