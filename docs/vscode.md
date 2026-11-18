# Debugging in VS Code

English | [简体中文](zh/vscode.md)

The repository ships `.vscode/launch.json` and `.vscode/settings.json`, so a
fresh checkout can be debugged with breakpoints without extra setup.

## Prerequisites

1. Install the **Python** extension (`ms-python.python`) — it provides the
   `debugpy` debugger used by the launch configurations.
2. Python 3.11 or newer.
3. A virtual environment with the package installed:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

4. In VS Code: **Command Palette → Python: Select Interpreter →**
   `.venv/bin/python` (or `.venv\Scripts\python.exe`).

5. Configure a model (either file works):

```bash
cp config.example.toml config.toml   # then edit base_url / model / api_key
```

or copy `.env.example` to `.env` and use the `Steward: chat (.env)` launch
configuration. `config.toml` and `.env` are gitignored.

## Start debugging

Open **Run and Debug** (`⇧⌘D` / `Ctrl+Shift+D`), pick a configuration, press
**F5**:

| Configuration | What it runs |
| --- | --- |
| **Steward: chat** | `python -m steward chat` — the normal REPL |
| **Steward: chat (read-only)** | same, with `--permission read-only` |
| **Steward: chat (.env)** | same, loading `.env` |
| **Steward: sessions** | `python -m steward sessions` |
| **Steward: offline demo** | `scripts/debug_demo.py` — scripted provider, no network |
| **Steward: unit tests** | `python -m unittest discover -s tests -v` |

All configurations use `"console": "integratedTerminal"` on purpose. The CLI
calls `input()` for the prompt and for tool approval; the default
`internalConsole` cannot feed stdin, so the REPL would appear to hang.

If F5 does nothing, check the **Debug Console** for an interpreter error and
confirm the Python extension is installed. On older extensions the debugger
`type` is `"python"` instead of `"debugpy"`; both keys are otherwise identical.

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

The Testing sidebar (flask icon) discovers the `unittest` suite because of
`.vscode/settings.json`. Click the bug icon next to a test to debug just that
test, or use **Steward: unit tests** to debug the whole suite.

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

## Remote / SSH

The same configurations work with **Remote - SSH**: open the folder on the
remote host, select the remote `.venv` interpreter, and press F5. The terminal
runs on the remote machine, so approval prompts and file paths behave normally.
