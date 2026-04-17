from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.toml"
STATE_PATH = DATA_DIR / "state.json"
QUEUE_PATH = DATA_DIR / "queue.json"
MEDIA_DIR = DATA_DIR / "downloads"
LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "tgmsgcopy.log"
SESSION_DIR = DATA_DIR / "sessions"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)
