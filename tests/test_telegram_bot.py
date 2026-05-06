"""Tests for telegram_bot.py — pure unit tests, no real Telegram API calls."""
from telegram_bot import is_authorized, sanitize_output, get_pid, is_running


def test_authorized_chat_passes():
    assert is_authorized(chat_id=12345, allowed_id="12345") is True


def test_unauthorized_chat_blocked():
    assert is_authorized(chat_id=99999, allowed_id="12345") is False


def test_empty_allowed_id_blocks_all():
    assert is_authorized(chat_id=12345, allowed_id="") is False


def test_sanitize_escapes_html():
    raw = "<b>hello</b> & <i>world</i>"
    clean = sanitize_output(raw)
    assert "<b>" not in clean
    assert "&amp;" in clean


def test_sanitize_escapes_angle_brackets():
    assert "&lt;" in sanitize_output("<script>")
    assert "&gt;" in sanitize_output("</script>")


def test_get_pid_returns_none_when_no_file(tmp_path, monkeypatch):
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "PID_FILE", tmp_path / "nonexistent.pid")
    assert telegram_bot.get_pid() is None


def test_get_pid_returns_int_when_file_exists(tmp_path, monkeypatch):
    import telegram_bot
    pid_file = tmp_path / "bot.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(telegram_bot, "PID_FILE", pid_file)
    assert telegram_bot.get_pid() == 12345


def test_is_running_returns_false_for_none():
    assert is_running(None) is False


def test_is_running_returns_true_for_current_process():
    import os
    assert is_running(os.getpid()) is True
