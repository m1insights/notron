"""Identity set by authentication adapters, never copied from a request body."""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    account_id: UUID
    device_id: UUID
    scopes: frozenset[str]
    kind: str

    def __post_init__(self):
        for field in ('account_id', 'device_id'):
            try:
                value = UUID(str(getattr(self, field)))
            except (ValueError, TypeError, AttributeError):
                raise ValueError('Invalid principal identity.') from None
            object.__setattr__(self, field, value)
        if self.kind not in {'managed', 'prototype'}:
            raise ValueError('Invalid principal kind.')
        if not isinstance(self.scopes, frozenset) or any(not isinstance(s, str) or not s for s in self.scopes):
            raise ValueError('Invalid principal scopes.')
