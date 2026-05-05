"""Tests for core data models."""

from __future__ import annotations

import pytest

from app.models import FileIssue, FileReview, FinalReview, InlineComment


def test_file_issue_rejects_invalid_severity() -> None:
    with pytest.raises(ValueError):
        FileIssue(
            severity="critical",
            category="security",
            description="Bad issue",
            recommendation="Fix it",
        )


def test_file_review_normalizes_lists_and_confidence() -> None:
    review = FileReview(
        filename="app/main.py",
        facts="Single fact",
        risks=["risk-a", "risk-b"],
        confidence="UNKNOWN",
    )
    assert review.facts == ["Single fact"]
    assert review.risks == ["risk-a", "risk-b"]
    assert review.confidence == "medium"


def test_final_review_validates_risk_level() -> None:
    with pytest.raises(ValueError):
        FinalReview(
            executive_summary="summary",
            summary_of_changes=["change"],
            risk_level="critical",
            key_issues=[],
            behavior_changes=["change"],
            final_decision="Merge",
            reasoning="reasoning",
            business_functional_context="context",
            scope_of_review="scope",
            files_changed_summary=["file"],
        )


def test_inline_comment_eligibility_rule() -> None:
    eligible = InlineComment(
        path="src/app.py",
        line=14,
        body="Validate payload schema before using fields.",
        severity="High",
        confidence="High",
    )
    ineligible = InlineComment(
        path="src/app.py",
        line=15,
        body="Consider renaming this variable.",
        severity="Low",
        confidence="High",
    )
    assert eligible.is_eligible is True
    assert ineligible.is_eligible is False
