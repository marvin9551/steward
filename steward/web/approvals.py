"""Approval cards: the browser equivalent of the CLI's `y/N` prompt.

The runner already accepts an async `approver`, so the only work here is making
that await something a human can answer in another thread:

    turn thread          HTTP thread
    -----------          -----------
    create()  ────────▶  pending id
    wait()    (blocked)  ◀──────────── POST /api/approval
              returns     resolve()

`wait()` blocks, so the service awaits it through `asyncio.to_thread` and the
turn's event loop keeps streaming other work in the meantime — there is nothing
else to do while waiting for a human, but blocking a thread is still cheaper
than blocking the loop.

"always" is remembered by tool name for the life of the process. That mirrors
the CLI: once you have approved `run_shell`, you stop being asked until restart.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

DECISION_ONCE = "once"
DECISION_ALWAYS = "always"
DECISION_DENY = "deny"
DECISIONS = (DECISION_ONCE, DECISION_ALWAYS, DECISION_DENY)

DEFAULT_TIMEOUT = 900.0


@dataclass
class Approval:
    id: str
    name: str
    args: dict
    created_at: float
    decision: str = ""
    answered: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "args": self.args,
            "decision": self.decision or None,
        }


class ApprovalRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, Approval] = {}
        self._counter = 0

    def create(self, name: str, args: dict) -> Approval:
        with self._lock:
            self._counter += 1
            approval = Approval(
                id=f"ap_{self._counter}_{uuid.uuid4().hex[:6]}",
                name=name,
                args=dict(args),
                created_at=time.time(),
            )
            self._pending[approval.id] = approval
            return approval

    def resolve(self, approval_id: str, decision: str) -> bool:
        """Record a decision. Returns False for an unknown or answered id."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
        with self._lock:
            approval = self._pending.pop(approval_id, None)
        if approval is None or approval.answered.is_set():
            return False
        approval.decision = decision
        approval.answered.set()
        return True

    def wait(self, approval_id: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Block until answered. Empty string means timeout or cancellation."""
        with self._lock:
            approval = self._pending.get(approval_id)
        if approval is None:
            return ""
        if not approval.answered.wait(timeout):
            self.resolve(approval_id, DECISION_DENY)
            return ""
        return approval.decision

    def pending(self) -> list[dict]:
        with self._lock:
            return [item.to_dict() for item in self._pending.values()]

    def cancel_all(self, decision: str = DECISION_DENY) -> None:
        with self._lock:
            ids = list(self._pending)
        for approval_id in ids:
            self.resolve(approval_id, decision)
