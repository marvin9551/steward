"""Steward: a small, auditable personal agent.

The point of this package is readability. Every layer is deliberately thin so
one person can hold the whole design in their head:

    cli -> runner -> provider
              |-> tools -> policy
              |-> session store (SQLite)

See docs/architecture.md for the data flow and docs/decisions.md for why each
piece is the way it is.
"""

__version__ = "0.1.0"
