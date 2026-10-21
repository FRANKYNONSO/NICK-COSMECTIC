import re

_DIGITS_ONLY = re.compile(r"^\d+$")


def validate_digits_only(value: str | None, field_name: str) -> str | None:
    if value is None or value == "":
        return value
    if not _DIGITS_ONLY.fullmatch(value):
        raise ValueError(f"{field_name} must contain digits only")
    return value
