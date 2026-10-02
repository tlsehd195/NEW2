"""FundTransferApproval: the human-sourced, single-use token
`funding.bridge.execute_transfer` requires.

Same discipline as `live/approval.py`: nothing in `src/` constructs one
except this module's own tests and `scripts/run_fund_transfer.py` (which
only runs interactively). Protected by
`.claude/hooks/protect-safety-files.sh`.

**Single-use, not just human-sourced:** unlike `LiveActivationApproval`
(one approval enables a mode), this approval is tied to one specific
`FundTransferQuote` by `quote_id` — it cannot be replayed against a
different, later quote, and `execute_transfer` checks that binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cointrader._time import require_aware

REQUIRED_CONFIRMATION_TOKEN = "I CONFIRM FUND TRANSFER"

_AUTOMATED_ACTORS = frozenset({"AI", "SYSTEM", "CLAUDE", "BOT", "SCHEDULER"})


@dataclass(frozen=True)
class FundTransferApproval:
    quote_id: str  # must match the FundTransferQuote being executed
    approved_by: str
    approved_at: datetime
    confirmation_token: str

    def __post_init__(self) -> None:
        if not self.approved_by or self.approved_by.strip().upper() in _AUTOMATED_ACTORS:
            raise ValueError("approved_by must name a real human operator, never an automated actor")
        require_aware("FundTransferApproval.approved_at", self.approved_at)
        if self.confirmation_token != REQUIRED_CONFIRMATION_TOKEN:
            raise ValueError("confirmation_token does not match the required phrase")
        if not self.quote_id:
            raise ValueError("quote_id must not be empty")

    def is_valid(self) -> bool:
        return True
