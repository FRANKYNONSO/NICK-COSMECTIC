"""Money value object.

All monetary amounts in the system are stored and manipulated as
integer kobo (1 Naira = 100 kobo) to avoid float rounding errors in
financial calculations. Never use `float` for money anywhere in this
codebase — use `Money`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Money:
    kobo: int

    def __post_init__(self) -> None:
        if not isinstance(self.kobo, int):
            raise TypeError("Money must be constructed from an integer kobo amount")

    @classmethod
    def from_naira(cls, naira: float) -> "Money":
        """Convenience constructor for human-entered Naira amounts
        (e.g. from a form field). Rounds to the nearest kobo.
        """
        return cls(round(naira * 100))

    @property
    def naira(self) -> float:
        return self.kobo / 100

    def __add__(self, other: "Money") -> "Money":
        return Money(self.kobo + other.kobo)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.kobo - other.kobo)

    def __mul__(self, factor: int) -> "Money":
        return Money(self.kobo * factor)

    def __lt__(self, other: "Money") -> bool:
        return self.kobo < other.kobo

    def __le__(self, other: "Money") -> bool:
        return self.kobo <= other.kobo

    def __str__(self) -> str:
        return f"₦{self.naira:,.2f}"
