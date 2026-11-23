"""Roles for collecting and reviewing handoff items."""

from dataclasses import dataclass
from pathlib import Path

from .tools import read_note


@dataclass
class NoteReview:
    source: Path
    action_items: list[str]


def review_note(path: Path) -> NoteReview:
    """Extract actionable lines from a single note."""
    action_items = []
    for line in read_note(path).splitlines():
        label, _, content = line.strip().partition(":")
        if label == "TODO" and content.strip():
            action_items.append(content.strip())
    return NoteReview(path, action_items)


def assemble_checklist(reviews: list[NoteReview]) -> str:
    """Combine the per-note results into a checklist with source references."""
    checklist = [
        f"- [ ] {item} ({review.source.as_posix()})"
        for review in reviews
        for item in review.action_items
    ]
    if not checklist:
        return "No action items found."
    return "\n".join(["Handoff checklist", *checklist])
