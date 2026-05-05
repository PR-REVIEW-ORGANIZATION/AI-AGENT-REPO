"""Tests for PR-level synthesis fallback logic."""

from __future__ import annotations

from app.models import (
    ChangedFile,
    FileIssue,
    FileReview,
    PRMetadata,
    PrecheckIssue,
    PrecheckResult,
)
from app.synthesizer import build_fallback_review, determine_final_decision, determine_risk_level


def _sample_metadata() -> PRMetadata:
    return PRMetadata(
        repository="owner/repo",
        number=12,
        title="Improve auth handling",
        body="Adds token rotation and permission checks.",
        author="octocat",
        state="open",
        base_branch="main",
        head_branch="feature/auth",
        commit_sha="abc123",
        created_at="2026-04-22T10:00:00Z",
        updated_at="2026-04-22T10:05:00Z",
        changed_files_count=1,
        additions=25,
        deletions=5,
    )


def test_determine_risk_level_high_on_high_issue() -> None:
    prechecks = PrecheckResult()
    issues = [FileIssue(severity="high", category="security", description="Issue", recommendation="Fix")]
    risk = determine_risk_level(prechecks, issues)
    assert risk == "High"


def test_determine_final_decision_blocks_high_risk() -> None:
    issues = [FileIssue(severity="low", category="style", description="Issue", recommendation="Fix")]
    assert determine_final_decision("High", issues) == "Do Not Merge"


def test_build_fallback_review_populates_required_fields() -> None:
    metadata = _sample_metadata()
    changed_files = [
        ChangedFile(
            filename="src/auth/service.py",
            status="modified",
            additions=25,
            deletions=5,
            changes=30,
            patch="+print('hello')",
        )
    ]
    prechecks = PrecheckResult(
        risky_files=["src/auth/service.py"],
        issues=[
            PrecheckIssue(
                code="risky_file_path",
                severity="high",
                message="auth file",
                filename="src/auth/service.py",
            )
        ],
    )
    file_reviews = [
        FileReview(
            filename="src/auth/service.py",
            facts=["Token validation flow changed."],
            issues=[
                FileIssue(
                    severity="medium",
                    category="security",
                    description="Missing replay protection guard.",
                    recommendation="Add nonce and expiry checks.",
                )
            ],
        )
    ]
    final_review = build_fallback_review(metadata, changed_files, prechecks, file_reviews)
    assert final_review.executive_summary
    assert final_review.business_functional_context
    assert final_review.scope_of_review
    assert final_review.purpose_of_pr
    assert final_review.behavior_before
    assert final_review.behavior_after
    assert final_review.core_logic_changes
    assert final_review.implementation_changes
    assert final_review.final_recommendation in {"Merge", "Do Not Merge"}
    assert final_review.final_decision == "Merge"


def test_risky_path_only_does_not_force_high_risk() -> None:
    prechecks = PrecheckResult(
        issues=[
            PrecheckIssue(
                code="risky_file_path",
                severity="high",
                message="auth file",
                filename="src/auth/service.py",
            )
        ]
    )
    risk = determine_risk_level(prechecks, [])
    assert risk == "Low"


def test_build_fallback_review_collects_inline_comment_candidates() -> None:
    metadata = _sample_metadata()
    changed_files = [
        ChangedFile(
            filename="src/auth/service.py",
            status="modified",
            additions=3,
            deletions=1,
            changes=4,
            patch="@@ -10,2 +10,3 @@\n old\n+new\n old2\n",
        )
    ]
    prechecks = PrecheckResult()
    file_reviews = [
        FileReview(
            filename="src/auth/service.py",
            facts=["Validation path updated."],
            confidence="high",
            issues=[
                FileIssue(
                    severity="high",
                    category="logic",
                    description="Missing error handling when token parsing fails.",
                    recommendation="Return an explicit auth failure response.",
                    line=11,
                )
            ],
        )
    ]

    final_review = build_fallback_review(metadata, changed_files, prechecks, file_reviews)
    assert len(final_review.inline_comments) == 1
    assert final_review.inline_comments[0].path == "src/auth/service.py"
    assert final_review.inline_comments[0].line == 11
