"""Assign note reviews and collect their results."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agents import assemble_checklist, review_note


def run_handoff(notes: list[Path]) -> str:
    """Review notes concurrently and preserve their order in the checklist."""
    with ThreadPoolExecutor() as workers:
        reviews = list(workers.map(review_note, notes))
    return assemble_checklist(reviews)
