import re
from typing import Any


def normalize_text(text: str | None) -> str:
    return (text or "").strip().lower()


def keyword_allowed(message_text: str, target: dict[str, Any]) -> bool:
    text = normalize_text(message_text)
    blacklist = [normalize_text(item) for item in target.get("blacklist_keywords", []) if str(item).strip()]
    whitelist = [normalize_text(item) for item in target.get("whitelist_keywords", []) if str(item).strip()]

    if blacklist and any(word in text for word in blacklist):
        return False

    if whitelist:
        return any(word in text for word in whitelist)

    return True


def regex_allowed(message_text: str, target: dict[str, Any]) -> bool:
    blacklist_regex = [str(item).strip() for item in target.get("blacklist_regex", []) if str(item).strip()]
    whitelist_regex = [str(item).strip() for item in target.get("whitelist_regex", []) if str(item).strip()]

    for pattern in blacklist_regex:
        if re.search(pattern, message_text, re.IGNORECASE | re.DOTALL):
            return False

    if whitelist_regex:
        return any(re.search(pattern, message_text, re.IGNORECASE | re.DOTALL) for pattern in whitelist_regex)

    return True

