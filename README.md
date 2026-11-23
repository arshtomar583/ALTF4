# ALTF4

AI agents plan tasks, use tools, and coordinate background work to deliver a practical result.

## Quickstart

Run from the repository root with Python 3.10 or later. The starter uses the Python standard library.

```powershell
python -m altf4 examples/engineering.txt examples/operations.txt
```

The command assigns one file per job and reads the files in parallel. The note-reader role extracts lines beginning with `TODO:`. The reviewer role combines the results into a checklist with each item's source file. Try editing either sample or passing your own text files.

## Project layout

```text
altf4/
  __main__.py     Command-line entry point
  workflow.py     Job assignment and parallel execution
  agents.py       Note reader and checklist reviewer
  tools.py        Local text-file access
examples/         Sample inputs for the demo
```

The demo uses fixed rules and reads files locally. Once the team chooses its use case, the role functions are the starting point for model calls and the tools module is the starting point for service integrations.
