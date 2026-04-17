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


def explain_keyword_filter(message_text: str, target: dict[str, Any]) -> str | None:
    text = normalize_text(message_text)
    blacklist = [normalize_text(item) for item in target.get("blacklist_keywords", []) if str(item).strip()]
    whitelist = [normalize_text(item) for item in target.get("whitelist_keywords", []) if str(item).strip()]

    for word in blacklist:
        if word in text:
            return f"命中关键词黑名单: {word}"

    if whitelist:
        for word in whitelist:
            if word in text:
                return None
        return "未命中任何关键词白名单"

    return None


def explain_regex_filter(message_text: str, target: dict[str, Any]) -> str | None:
    blacklist_regex = [str(item).strip() for item in target.get("blacklist_regex", []) if str(item).strip()]
    whitelist_regex = [str(item).strip() for item in target.get("whitelist_regex", []) if str(item).strip()]

    for pattern in blacklist_regex:
        if re.search(pattern, message_text, re.IGNORECASE | re.DOTALL):
            return f"命中正则黑名单: {pattern}"

    if whitelist_regex:
        for pattern in whitelist_regex:
            if re.search(pattern, message_text, re.IGNORECASE | re.DOTALL):
                return None
        return "未命中任何正则白名单"

    return None
