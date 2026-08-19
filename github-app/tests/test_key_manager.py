import time
from app.core.key_manager import KeyManager


def create_test_key_manager(keys=None):
    km = KeyManager()
    km.keys = keys or ["key_alpha", "key_beta", "key_gamma"]
    km._current_index = 0
    km._tpm_cooldowns = {}
    km._rpd_exhausted = {k: set() for k in km.keys}
    return km


def test_key_manager_round_robin_rotation():
    km = create_test_key_manager(["key_1", "key_2", "key_3"])

    assert km.get_available_key("gemini-3.7-flash") == "key_1"
    assert km.get_available_key("gemini-3.7-flash") == "key_2"
    assert km.get_available_key("gemini-3.7-flash") == "key_3"
    assert km.get_available_key("gemini-3.7-flash") == "key_1"


def test_key_manager_skips_tpm_cooldown_key():
    km = create_test_key_manager(["key_1", "key_2"])

    km.mark_tpm_limit("key_1", cooldown_seconds=60)

    assert km.get_available_key("gemini-3.7-flash") == "key_2"

    assert km.get_available_key("gemini-3.7-flash") == "key_2"


def test_key_manager_all_keys_in_cooldown_returns_none():
    km = create_test_key_manager(["key_1", "key_2"])

    km.mark_tpm_limit("key_1", cooldown_seconds=60)
    km.mark_tpm_limit("key_2", cooldown_seconds=60)

    assert km.get_available_key("gemini-3.7-flash") is None


def test_key_manager_rpd_exhausted_for_specific_model():
    km = create_test_key_manager(["key_1", "key_2"])

    km.mark_rpd_limit("key_1", "gemini-3.7-flash")

    assert km.get_available_key("gemini-3.7-flash") == "key_2"

    assert km.get_available_key("gemini-3.5-flash-lite") == "key_1"
