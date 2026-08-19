import json
import pytest
from app.github.state import (
    extract_metadata,
    embed_metadata,
    mark_step_completed_in_body,
)


def test_embed_and_extract_metadata_roundtrip():
    body = "### Implementation Plan\n\n- [ ] Step 1: Initialize database"
    metadata = {
        "issue_number": 42,
        "maintainer": "alex",
        "branch": "feature/init-db",
        "steps": [{"step_number": 1, "title": "Initialize database"}],
    }

    embedded_body = embed_metadata(body, metadata)
    assert "<!-- priesty-meta:" in embedded_body

    extracted = extract_metadata(embedded_body)
    assert extracted == metadata
    assert extracted["issue_number"] == 42
    assert extracted["maintainer"] == "alex"


def test_embed_metadata_updates_existing():
    initial_body = (
        'Plan text\n\n<!-- priesty-meta: {"issue_number": 1, "status": "pending"} -->'
    )
    new_meta = {"issue_number": 1, "status": "completed"}

    updated_body = embed_metadata(initial_body, new_meta)
    extracted = extract_metadata(updated_body)

    assert extracted["status"] == "completed"

    assert updated_body.count("<!-- priesty-meta:") == 1


def test_extract_metadata_missing_or_corrupt():
    assert extract_metadata("") is None
    assert extract_metadata("Just a normal PR body without metadata.") is None
    assert extract_metadata("<!-- priesty-meta: {invalid_json} -->") is None


@pytest.mark.parametrize(
    "initial_body,step_num,expected_snippet",
    [
        (
            "- [ ] **1. Create Models** (`models.py`)\n- [ ] **2. Add Route** (`app.py`)",
            1,
            "- [x] **1. Create Models** (`models.py`)\n- [ ] **2. Add Route** (`app.py`)",
        ),
        (
            "- [x] **1. Create Models**\n- [ ] **2. Add Route**",
            2,
            "- [x] **1. Create Models**\n- [x] **2. Add Route**",
        ),
        (
            "- [ ] 1. Fallback non-bold format",
            1,
            "- [x] 1. Fallback non-bold format",
        ),
    ],
)
def test_mark_step_completed_in_body(initial_body, step_num, expected_snippet):
    result = mark_step_completed_in_body(initial_body, step_num)
    assert expected_snippet in result
