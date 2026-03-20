import uuid
import utils.logger
from utils.logger import get_logger


def _teardown(name: str) -> None:
    with utils.logger._loggers_lock:
        logger = utils.logger._loggers.pop(name, None)
    if logger is not None:
        for h in logger.handlers[:]:
            h.flush()
            h.close()
            logger.removeHandler(h)


def test_logger_writes_to_file(tmp_path):
    name = f"test_rot_{uuid.uuid4().hex}"
    try:
        log = get_logger(name, log_dir=str(tmp_path))
        log.info("hello")
        for h in log.handlers:
            h.flush()
        files = list(tmp_path.glob(f"{name}*.log"))
        assert len(files) == 1
        assert "hello" in files[0].read_text()
    finally:
        _teardown(name)


def test_logger_still_streams_to_stdout(tmp_path, capsys):
    name = f"test_stdout_{uuid.uuid4().hex}"
    try:
        log = get_logger(name, log_dir=str(tmp_path))
        log.info("stdout check")
        for h in log.handlers:
            h.flush()
        captured = capsys.readouterr()
        assert "stdout check" in captured.out
    finally:
        _teardown(name)
