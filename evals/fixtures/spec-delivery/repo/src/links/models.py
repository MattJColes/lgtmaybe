"""Link model. `expires_at` already exists — the migration landed separately."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Link:
    amount: int
    customer_id: str
    expires_at: datetime | None = None
    redeemed_at: datetime | None = None
