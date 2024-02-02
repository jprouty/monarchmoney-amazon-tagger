# 50 Micro dollars we'll consider equal (this allows for some
# division/multiplication rounding wiggle room).
from typing import Any


MICRO_USD_EPS = 50
CENT_MICRO_USD = 10000

DOLLAR_EPS = 0.0001


class MicroUSD:
    def __init__(self, micro_usd: int):
        self.micro_usd = micro_usd

    def __repr__(self) -> str:
        return f"MicroUSD({self.micro_usd})"

    def __str__(self) -> str:
        return (
            f"{'' if self.micro_usd >= -5000 else '-'}$" f"{abs(self.to_float()):.2f}"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MicroUSD):
            return False
        return abs(self.micro_usd - other.micro_usd) < MICRO_USD_EPS

    def __neg__(self) -> "MicroUSD":
        return MicroUSD(-self.micro_usd)

    def __add__(self, other: "MicroUSD") -> "MicroUSD":
        return MicroUSD(self.micro_usd + other.micro_usd)

    def __sub__(self, other: "MicroUSD") -> "MicroUSD":
        return MicroUSD(self.micro_usd - other.micro_usd)

    def __mul__(self, other: Any) -> "MicroUSD":
        return MicroUSD(self.micro_usd * other)

    def round_to_cent(self) -> "MicroUSD":
        """Rounds to the nearest cent."""
        return MicroUSD.from_float(self.to_float())

    def to_float(self) -> float:
        return round(self.micro_usd / 1000000.0 + DOLLAR_EPS, 2)

    @classmethod
    def from_float(cls, float_usd: float) -> "MicroUSD":
        return MicroUSD(round(float_usd * 1000000))

    @classmethod
    def parse(cls, amount: float | str) -> "MicroUSD":
        if isinstance(amount, float):
            return cls.from_float(amount)
        # Remove any formatting/grouping commas.
        amount = amount.replace(",", "")
        # Remove any quoting.
        amount = amount.replace("'", "")
        negate = False
        if "-" == amount[0]:
            negate = True
            amount = amount[1:]
        if "$" == amount[0]:
            amount = amount[1:]
        return cls.from_float(float(amount) if not negate else -float(amount))
