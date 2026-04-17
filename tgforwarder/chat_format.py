from __future__ import annotations

from urllib.parse import urlparse


def normalize_chat_input(raw_value: object) -> int | str:
    if isinstance(raw_value, int):
        return raw_value

    text = str(raw_value).strip()
    if not text:
        raise ValueError("频道输入不能为空")

    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        host = (parsed.netloc or "").lower()
        if host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            raise ValueError("仅支持 t.me / telegram.me 链接格式")
        path = parsed.path.strip("/")
        if not path:
            raise ValueError("t.me 链接中缺少频道名")
        text = path.split("/", 1)[0]

    if text.startswith("@"):
        text = text[1:].strip()

    if not text:
        raise ValueError("频道输入不能为空")

    if text.lstrip("-").isdigit():
        return int(text)

    return text

