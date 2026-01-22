#!/usr/bin/env python3
"""
Utility script to rename clara references to clarissa when porting files.

Usage:
    python scripts/rename_clara_to_clarissa.py <file_path>  # Transform a single file
    python scripts/rename_clara_to_clarissa.py --text "clara_core"  # Transform text
"""

import argparse
import re
import sys
from pathlib import Path

# Mapping of clara names to clarissa equivalents
RENAME_MAPPINGS = [
    # Module/package names
    ("clara_core", "clarissa_core"),
    ("clara_files", "clarissa_files"),
    # Class/function prefixes
    ("ClaraConfig", "ClarissaConfig"),
    ("Clara", "Clarissa"),
    # Environment variable prefixes
    ("CLARA_", "CLARISSA_"),
    # String references (case sensitive order matters)
    ("clara", "clarissa"),
]


def rename_clara_to_clarissa(text: str) -> str:
    """
    Replace all clara references with clarissa equivalents.
    Processes replacements in order to handle longer matches first.
    """
    result = text
    for old, new in RENAME_MAPPINGS:
        # Use word boundary for most replacements
        result = re.sub(rf"\b{old}\b", new, result)
        # Also handle UPPER case versions
        result = re.sub(rf"\b{old.upper()}\b", new.upper(), result)

    return result


def process_file(file_path: Path) -> tuple[str, str]:
    """
    Read file, transform content, return (original, transformed).
    """
    original = file_path.read_text()
    transformed = rename_clara_to_clarissa(original)
    return original, transformed


def main():
    parser = argparse.ArgumentParser(
        description="Rename clara references to clarissa"
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        help="Path to file to transform",
    )
    parser.add_argument(
        "--text",
        "-t",
        help="Transform text directly instead of a file",
    )
    parser.add_argument(
        "--write",
        "-w",
        action="store_true",
        help="Write changes back to file (default: print to stdout)",
    )
    parser.add_argument(
        "--diff",
        "-d",
        action="store_true",
        help="Show diff instead of full output",
    )

    args = parser.parse_args()

    if args.text:
        print(rename_clara_to_clarissa(args.text))
        return

    if not args.file_path:
        parser.print_help()
        sys.exit(1)

    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    original, transformed = process_file(file_path)

    if args.diff:
        import difflib

        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            transformed.splitlines(keepends=True),
            fromfile=str(file_path),
            tofile=str(file_path) + " (renamed)",
        )
        print("".join(diff))
    elif args.write:
        file_path.write_text(transformed)
        print(f"Wrote transformed content to {file_path}")
    else:
        print(transformed)


if __name__ == "__main__":
    main()
