"""Pure access policy. Existing local receipts do not require a paid entitlement."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Entitlement:
    status: str
    access_until: datetime | None
    allowance: int
    reason: str

    @property
    def allowed(self):
        return self.status in {'active', 'trialing', 'pilot'}


def evaluate(status, paid_until, trial_until, allowance, *, now, revoked=False):
    if revoked:
        return Entitlement('revoked', None, 0, 'revoked')
    if status == 'trialing' and trial_until and trial_until > now:
        return Entitlement('trialing', trial_until, allowance, 'trial')
    if status in {'active', 'past_due', 'canceled', 'pilot'} and paid_until and paid_until > now:
        return Entitlement('pilot' if status == 'pilot' else 'active', paid_until, allowance, 'paid_period' if status != 'pilot' else 'pilot')
    return Entitlement('paused', paid_until, 0, 'subscription_required')
