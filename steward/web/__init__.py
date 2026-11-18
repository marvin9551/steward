"""The web front end: one local HTTP server, one page, no build step.

This package is the third consumer of `on_event` (after the CLI renderer and
the tests). It imports the runner; nothing in the runner or the CLI imports it
back, which keeps the dependency pointing one way.
"""
