import re
from datetime import date
from decimal import Decimal, InvalidOperation

_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PALLET_VALUE_RE = re.compile(r"^\s*\(([^)]+)\)\s*([0-9.,]+)\s*$")
_NUM_RE = re.compile(r"^[0-9.,]+$")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def normalize_query(value: object) -> str:
    return normalize_digits(clean_text(value)).lower()


def normalize_code_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)):
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            return normalize_query(value)
        if dec == dec.to_integral_value():
            return str(int(dec))
        return format(dec, "f").rstrip("0").rstrip(".")
    text = normalize_digits(clean_text(value))
    try:
        dec = Decimal(text)
    except InvalidOperation:
        return text
    if dec == dec.to_integral_value():
        return str(int(dec))
    return format(dec, "f").rstrip("0").rstrip(".")


def split_meter_pallet(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, (int, float, Decimal)):
        return format(value, "f").rstrip("0").rstrip("."), None
    text = str(value).strip()
    if not text:
        return None, None
    match = _PALLET_VALUE_RE.match(text)
    if match:
        pallet = match.group(1).strip()
        meter = match.group(2).strip()
        return meter, pallet
    return text, None


def format_unit(text: str, unit: str) -> str:
    if _NUM_RE.match(text):
        return f"{text} {unit}"
    return text


def gregorian_to_jalali(value: date) -> tuple[int, int, int]:
    gy = value.year
    gm = value.month
    gd = value.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def format_jalali_date(value: date, sep: str = "-") -> str:
    jy, jm, jd = gregorian_to_jalali(value)
    return f"{jy:04d}{sep}{jm:02d}{sep}{jd:02d}"
