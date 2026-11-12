"""UPC-A helpers: check digits, validation and sequential generation from a GS1 company prefix."""


def check_digit(digits11):
    """UPC-A check digit for an 11-digit string (positions 1,3,5... weighted x3)."""
    if len(digits11) != 11 or not digits11.isdigit():
        raise ValueError("UPC-A body must be exactly 11 digits.")
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(digits11))
    return (10 - total % 10) % 10


def ean13_check_digit(digits12):
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits12))
    return (10 - total % 10) % 10


def complete(code):
    """Return a full 12-digit UPC from 11 digits (adds check digit), or the code unchanged if 12."""
    code = (code or "").strip()
    if len(code) == 11 and code.isdigit():
        return code + str(check_digit(code))
    return code


def is_valid(code):
    """Validate UPC-A (12) or EAN-13 (13) check digits. Other formats are not checked."""
    code = (code or "").strip()
    if not code.isdigit():
        return True
    if len(code) == 12:
        return int(code[-1]) == check_digit(code[:11])
    if len(code) == 13:
        return int(code[-1]) == ean13_check_digit(code[:12])
    return True


def build(prefix, item_ref):
    """UPC from company prefix + item reference (zero padded to fill 11 digits) + check digit."""
    prefix = (prefix or "").strip()
    if not prefix.isdigit() or not 6 <= len(prefix) <= 10:
        raise ValueError("UPC company prefix must be 6–10 digits (set it in Settings → Company).")
    width = 11 - len(prefix)
    if item_ref >= 10 ** width:
        raise ValueError(f"Item reference range exhausted for prefix {prefix} (max {10 ** width - 1}).")
    body = f"{prefix}{item_ref:0{width}d}"
    return body + str(check_digit(body))
