import re

SEARCH_REPLACE_REGEX = re.compile(
    r"<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


def apply_search_replace(original_text: str, patch_text: str) -> str:
    matches = list(SEARCH_REPLACE_REGEX.finditer(patch_text))
    if not matches:
        return patch_text

    current_text = original_text
    for match in matches:
        search_block = match.group(1)
        replace_block = match.group(2)

        if search_block in current_text:
            current_text = current_text.replace(search_block, replace_block, 1)
        else:

            search_lines = [line.rstrip() for line in search_block.splitlines()]
            orig_lines = current_text.splitlines()

            matched_idx = -1
            for i in range(len(orig_lines) - len(search_lines) + 1):
                if [
                    l.rstrip() for l in orig_lines[i : i + len(search_lines)]
                ] == search_lines:
                    matched_idx = i
                    break

            if matched_idx != -1:
                orig_lines[matched_idx : matched_idx + len(search_lines)] = (
                    replace_block.splitlines()
                )
                current_text = "\n".join(orig_lines) + (
                    "\n" if current_text.endswith("\n") else ""
                )

    return current_text
