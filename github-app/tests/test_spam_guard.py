import time
from app.workflows.mention_router import SpamGuard


def test_spam_guard_accumulates_strikes():
    guard = SpamGuard()
    username = "spammer_user"

    assert guard.is_rate_limited(username) is False

    assert guard.record_none_strike(username) is False
    assert guard.record_none_strike(username) is False
    assert guard.is_rate_limited(username) is False

    assert guard.record_none_strike(username) is True
    assert guard.is_rate_limited(username) is True


def test_spam_guard_expires_old_strikes():
    guard = SpamGuard()
    username = "occasional_user"

    old_time = time.time() - 600
    guard._strikes[username] = [old_time, old_time]

    cooldown_triggered = guard.record_none_strike(username)
    assert cooldown_triggered is False
    assert guard.is_rate_limited(username) is False


def test_spam_guard_unaffected_clean_user():
    guard = SpamGuard()
    guard.record_none_strike("bad_user")
    guard.record_none_strike("bad_user")
    guard.record_none_strike("bad_user")

    assert guard.is_rate_limited("bad_user") is True
    assert guard.is_rate_limited("good_user") is False
