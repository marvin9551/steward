"""The web front end: a FastAPI app, one page, no second agent.

This package is the third consumer of `on_event` (after the CLI renderer and
the tests). It imports the runner; nothing in the runner or the CLI imports it
back, which keeps the dependency pointing one way.

FastAPI and uvicorn are an optional extra (`pip install -e ".[web]"`), so a
terminal-only install still has exactly one dependency.
"""
