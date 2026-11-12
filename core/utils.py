from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")


def d(value):
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value):
    return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def cost(value):
    return d(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
