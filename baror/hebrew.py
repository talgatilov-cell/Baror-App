"""Hebrew count phrases. Feminine: שנייה, דקה, חזרה. Masculine: יום."""

_FORMS = {  # unit: (singular, dual-construct, plural, gender)
    "second": ("שנייה", "שתי שניות", "שניות", "f"),
    "minute": ("דקה", "שתי דקות", "דקות", "f"),
    "rep": ("חזרה", "שתי חזרות", "חזרות", "f"),
    "day": ("יום", "יומיים", "ימים", "m"),
}


def count_phrase(unit: str, n: int) -> str:
    """1 -> 'שנייה אחת'; 2 -> 'שתי שניות'; 0, 3+ -> 'N שניות'."""
    if n < 0:
        raise ValueError("negative count")
    one, two, many, gender = _FORMS[unit]
    if n == 1:
        return f"{one} {'אחת' if gender == 'f' else 'אחד'}"
    if n == 2:
        return two
    return f"{n} {many}"


def _vav(phrase: str) -> str:
    return ("ו-" if phrase[0].isdigit() else "ו") + phrase


def duration_phrase(total_seconds: int) -> str:
    """Gap as minutes and seconds, never clock format: 190 -> '3 דקות ו-10 שניות'."""
    m, s = divmod(int(total_seconds), 60)
    if m == 0:
        return count_phrase("second", s)
    if s == 0:
        return count_phrase("minute", m)
    return f"{count_phrase('minute', m)} {_vav(count_phrase('second', s))}"


def retry_phrase(total_seconds: int) -> str:
    """Rate-limit wait: under 60 s in seconds, else rounded up to minutes."""
    if total_seconds < 60:
        return count_phrase("second", max(1, total_seconds))
    return count_phrase("minute", -(-total_seconds // 60))
