import logging

from .paths import LOG_PATH, ensure_data_dirs


logger = logging.getLogger("tgmsgcopy")


def setup_logging() -> logging.Logger:
    ensure_data_dirs()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
