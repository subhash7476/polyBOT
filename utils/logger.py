import logging
import sys
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_loggers: dict[str, logging.Logger] = {}
_loggers_lock = threading.Lock()

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def get_logger(name: str, log_dir: str | None = None) -> logging.Logger:
    """Return a named logger with a stdout stream handler and a rotating file handler."""
    with _loggers_lock:
        if name in _loggers:
            return _loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Clear any handlers that may have been added by a previous partial setup
        logger.handlers.clear()

        # stdout handler
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(stream_handler)

        # rotating file handler
        log_path = Path(log_dir if log_dir is not None else "logs")
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(log_path / f"{name}.log"),
            when="H",
            interval=3,
            backupCount=24,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(file_handler)

        _loggers[name] = logger
        return logger
