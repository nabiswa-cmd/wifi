"""
Phone number normalization for M-Pesa.

Customers type numbers every possible way: "0712345678", "0712 345 678",
"0712-345-678", "+254712345678", "254712345678", "712345678"... Daraja's
STK push requires exactly one format: 254XXXXXXXXX (12 digits, no plus,
no leading zero). This module is the single place that conversion happens
so it's never duplicated (or done inconsistently) across views.
"""
import re


def normalize_phone_number(raw: str) -> str | None:
    """
    Returns the number in Daraja's required 2547XXXXXXXX / 2541XXXXXXXX
    format, or None if it isn't a recognizable Safaricom number.
    """
    if not raw:
        return None

    digits = re.sub(r'\D', '', raw)  # strip spaces, dashes, +, brackets, etc.

    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif digits.startswith('254') and len(digits) == 12:
        pass
    elif len(digits) == 9 and digits[0] in '71':
        digits = '254' + digits
    else:
        return None

    if not re.fullmatch(r'254[71]\d{8}', digits):
        return None
    return digits


def display_phone_number(normalized: str) -> str:
    """254712345678 -> 0712 345 678, for showing back to the customer."""
    if not normalized or len(normalized) != 12:
        return normalized
    local = '0' + normalized[3:]
    return f'{local[:4]} {local[4:7]} {local[7:]}'
