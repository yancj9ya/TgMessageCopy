import json

from .logging_config import logger
from .paths import STATE_PATH, ensure_data_dirs


def load_state() -> dict[str, int]:
    ensure_data_dirs()
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning("读取 state.json 失败，将使用空状态: %s", exc)
    return {}


def save_state(state: dict[str, int]) -> None:
    ensure_data_dirs()
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_target_key(source, destination) -> str:
    return f"{source}=>{destination}"
