import pytest
from agents.router import check_static_heuristics, extract_json_block


@pytest.mark.parametrize(
    "prompt, expected_agent",
    [
        ("deep research the history of distributed systems", "deep_research"),
        ("look up the latest updates on python 3.13", "deep_research"),
        ("compare prices of cloud VPS hosting", "deep_research"),
        ("audit user permissions in this server", "react"),
        ("check member roles and voice status", "react"),
        ("channel stats and message history", "react"),
    ],
)
def test_check_static_heuristics_matches(prompt, expected_agent):
    result = check_static_heuristics(prompt)
    assert result is not None
    assert result["agent"] == expected_agent
    assert "plan" in result


def test_check_static_heuristics_unmatched():

    result = check_static_heuristics("can you help me with something general?")
    assert result is None


def test_extract_json_block_clean():
    raw_output = 'Here is your result: {"agent": "deep_research", "plan": "Search web."} Hope this helps!'
    extracted = extract_json_block(raw_output)
    assert extracted == '{"agent": "deep_research", "plan": "Search web."}'


def test_extract_json_block_no_json():
    raw_output = "No json here at all."
    assert extract_json_block(raw_output) == "No json here at all."
