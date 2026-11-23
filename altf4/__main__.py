"""Run the note handoff demo from the command line."""

import argparse
from pathlib import Path

from .workflow import run_handoff


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect TODOs from handoff notes.")
    parser.add_argument("notes", nargs="+", type=Path, help="UTF-8 text files to review")
    args = parser.parse_args()

    try:
        checklist = run_handoff(args.notes)
    except (OSError, UnicodeError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")

    print(checklist)


if __name__ == "__main__":
    main()
