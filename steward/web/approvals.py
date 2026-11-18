"""Approval cards: the browser equivalent of the CLI's `y/N` prompt.

The runner takes an async `approver`, and FastAPI gives us an event loop, so the
whole handshake is asyncio:

    turn task                     request handler
    ---------                     ---------------
    create()  ── future ──▶       pending id
    await wait()                  ◀──────────── POST /api/approval
                                  resolve() sets the future's result

No locks and no worker threads: every caller runs on the same loop, which is
also why a pending approval costs nothing while it waits.

"always" is remembered by tool name for the life of the process, mirroring the
CLI: once you approve `run_shell` you stop being asked until restart.
"""

from __future__ import annotations

import asyncio
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
    future: asyncio.Future
    decision: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "args": self.args,
            "decision": self.decision or None,
        }


@dataclass
class _Pending:
    items: dict = field(default_factory=dict)
    counter: int = 0


class ApprovalRegistry:
    def __init__(self) -> None:
        self._pending = _Pending()

    def create(self, name: str, args: dict) -> Approval:
        """Must be called from the loop the turn runs on."""
        self._pending.counter += 1
        approval = Approval(
            id=f"ap_{self._pending.counter}_{uuid.uuid4().hex[:6]}",
            name=name,
            args=dict(args),
            created_at=time.time(),
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending.items[approval.id] = approval
        return approval

    def resolve(self, approval_id: str, decision: str) -> bool:
        """Record a decision. False for an unknown or already answered id."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
        approval = self._pending.items.pop(approval_id, None)
        if approval is None or approval.future.done():
            return False
        approval.decision = decision
        approval.future.set_result(decision)
        return True

    async def wait(self, approval_id: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Wait for an answer. Empty string means timeout or cancellation."""
        approval = self._pending.items.get(approval_id)
        if approval is None:
            return ""
        try:
            # shield: a timeout must not cancel the future we may still resolve
            return await asyncio.wait_for(asyncio.shield(approval.future), timeout)
        except asyncio.TimeoutError:
            self.resolve(approval_id, DECISION_DENY)
            return ""

    def pending(self) -> list[dict]:
        return [item.to_dict() for item in self._pending.items.values()]

    def cancel_all(self, decision: str = DECISION_DENY) -> None:
        for approval_id in list(self._pending.items):
            self.resolve(approval_id, decision)
