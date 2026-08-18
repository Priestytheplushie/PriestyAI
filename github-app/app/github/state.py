import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("priesty.state")

META_REGEX = re.compile(r"<!--\s*priesty-meta:\s*(\{.*?\})\s*-->", re.DOTALL)


def extract_metadata(body: str) -> Optional[Dict[str, Any]]:
    """Extracts hidden JSON metadata from a PR/Issue body."""
    if not body:
        return None
    match = META_REGEX.search(body)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception as e:
        logger.warning(f"Failed to parse embedded metadata: {e}")
        return None


def embed_metadata(body: str, metadata: Dict[str, Any]) -> str:
    """Embeds or updates hidden JSON metadata in the markdown body."""
    meta_json = json.dumps(metadata)
    meta_comment = f"\n\n<!-- priesty-meta: {meta_json} -->"

    if META_REGEX.search(body):
        return META_REGEX.sub(f"<!-- priesty-meta: {meta_json} -->", body)
    return body.rstrip() + meta_comment


def mark_step_completed_in_body(body: str, step_number: int) -> str:
    """Updates a step checkbox from - [ ] to - [x] in the markdown checklist."""
    pattern = re.compile(rf"- \[ \] \*\*{step_number}\.", re.IGNORECASE)
    if pattern.search(body):
        return pattern.sub(f"- [x] **{step_number}.", body)

    fallback = re.compile(rf"- \[ \] {step_number}\.", re.IGNORECASE)
    return fallback.sub(f"- [x] {step_number}.", body)
