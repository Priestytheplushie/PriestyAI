import argparse
import os
import sys
import tokenize
from io import BytesIO
import black

PRESERVED_DIRECTIVE_PREFIXES = (
    "type:",
    "type :",
    "type:ignore",
    "noqa",
    "pragma:",
    "fmt:",
    "isort:",
    "pylint:",
    "mypy:",
    "pyright:",
    "ruff:",
)

EXCLUDED_DIRS = {
    ".venv",
    "venv",
    ".git",
    "__pycache__",
    ".tox",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def should_preserve_comment(token_string: str, line_number: int) -> bool:
    if line_number == 1 and token_string.startswith("#!"):
        return True
    if line_number <= 2 and "coding:" in token_string:
        return True
    stripped = token_string.lstrip("#").strip().lower()
    return any(stripped.startswith(p) for p in PRESERVED_DIRECTIVE_PREFIXES)


def clean_and_format_source(source_bytes: bytes) -> bytes | None:
    io_obj = BytesIO(source_bytes)
    out_tokens = []

    try:
        for token in tokenize.tokenize(io_obj.readline):
            if token.type == tokenize.COMMENT:
                if should_preserve_comment(token.string, token.start[0]):
                    out_tokens.append(token)
                continue
            out_tokens.append(token)

        untokenized = tokenize.untokenize(out_tokens)
        source_str = untokenized.decode("utf-8")
        formatted = black.format_str(
            source_str, mode=black.FileMode(line_length=88)
        )
        return formatted.encode("utf-8")
    except tokenize.TokenError as e:
        print(f"Tokenize parse error: {e}", file=sys.stderr)
        return None
    except black.NothingChanged:
        return untokenized
    except Exception as e:
        print(f"Formatting error: {e}", file=sys.stderr)
        return None


def run_cleaner(
    root_dir: str = ".", check_mode: bool = False, force: bool = False
) -> int:
    unclean_files: list[str] = []
    modified_count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for filename in filenames:
            if filename.endswith(".py") and filename != os.path.basename(
                __file__
            ):
                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, "rb") as f:
                        original = f.read()
                except Exception as e:
                    print(f"Could not read {filepath}: {e}", file=sys.stderr)
                    continue

                cleaned = clean_and_format_source(original)
                if cleaned and cleaned != original:
                    unclean_files.append(filepath)
                    if force and not check_mode:
                        with open(filepath, "wb") as f:
                            f.write(cleaned)
                        modified_count += 1

    if check_mode:
        if unclean_files:
            print("\n" + "=" * 70, file=sys.stderr)
            print(
                "[FAIL] Repository Code Hygiene & Formatting Check",
                file=sys.stderr,
            )
            print("=" * 70, file=sys.stderr)
            print(
                "The following files do not meet code hygiene and formatting standards:\n",
                file=sys.stderr,
            )
            for f in unclean_files:
                print(f"  - {f}", file=sys.stderr)
            print(
                "\nRun the following command locally to resolve this before pushing:\n",
                file=sys.stderr,
            )
            print("    python scripts/clean_project.py --force\n", file=sys.stderr)
            return 1

        print(
            "[PASS] All Python files meet codebase hygiene and formatting standards."
        )
        return 0

    if force:
        print(f"[DONE] Cleaned and formatted {modified_count} file(s).")
    else:
        print("--- DRY RUN SUMMARY ---")
        if unclean_files:
            print(f"{len(unclean_files)} file(s) require formatting:")
            for f in unclean_files:
                print(f"  - {f}")
            print(
                "\nRun with --force to apply changes in place:\n    python scripts/clean_project.py --force"
            )
        else:
            print("All files meet codebase hygiene and formatting standards.")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Code hygiene and formatting tool."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate without modifying (used in CI).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Apply formatting changes in place."
    )
    parser.add_argument("--path", default=".", help="Root directory to scan.")
    args = parser.parse_args()
    sys.exit(
        run_cleaner(
            root_dir=args.path, check_mode=args.check, force=args.force
        )
    )


if __name__ == "__main__":
    main()