"""Tests for PR summary and inline commenting utilities."""

from __future__ import annotations

from app.models import ChangedFile, FileReview, FinalReview, InlineComment, PRMetadata
from app.pr_commenter import (
    SUMMARY_MARKER,
    build_changed_line_map,
    build_summary_comment,
    extract_changed_right_lines,
    select_inline_comments,
    upsert_summary_comment,
)


class _FakeGitHubClient:
    def __init__(self, comments: list[dict] | None = None) -> None:
        self._comments = comments or []
        self.updated: list[tuple[str, int, str]] = []
        self.created: list[tuple[str, int, str]] = []

    def list_issue_comments(self, repository: str, issue_number: int) -> list[dict]:
        _ = repository, issue_number
        return list(self._comments)

    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> dict:
        self.updated.append((repository, comment_id, body))
        return {"id": comment_id, "body": body}

    def create_issue_comment(self, repository: str, issue_number: int, body: str) -> dict:
        self.created.append((repository, issue_number, body))
        return {"id": 999, "body": body}


def _metadata() -> PRMetadata:
    return PRMetadata(
        repository="owner/repo",
        number=34,
        title="Improve validation flow",
        body="Ensure malformed requests are rejected earlier.",
        author="octocat",
        state="open",
        base_branch="main",
        head_branch="feature/validation",
        commit_sha="abc123",
        created_at="2026-05-01T00:00:00Z",
        updated_at="2026-05-01T00:10:00Z",
        changed_files_count=2,
        additions=14,
        deletions=5,
    )


def _final_review() -> FinalReview:
    return FinalReview(
        executive_summary="Malformed requests are rejected earlier. Risk: Low. Recommendation: Merge.",
        purpose_of_pr="Ensure malformed requests are rejected earlier.",
        summary_of_changes=["Validation now rejects malformed payloads before business logic."],
        behavior_before=["Malformed payload handling was inconsistent."],
        behavior_after=["Malformed payloads are now rejected consistently before handlers execute."],
        core_logic_changes=["Added a shared validation gate in request handling."],
        implementation_changes=["Updated handler call sites to use shared validator."],
        risk_level="Low",
        real_issues_only=[],
        final_recommendation="Merge",
        key_issues=[],
        behavior_changes=["Malformed payloads are now rejected consistently before handlers execute."],
        final_decision="Merge",
        reasoning="Risk Low: no evidence-backed defects found.",
        business_functional_context="Ensure malformed requests are rejected earlier.",
        scope_of_review="Deterministic prechecks and AI file-level analysis completed.",
        files_changed_summary=["MODIFIED app/api.py (+10 / -3)"],
    )


def test_build_summary_comment_contains_required_sections() -> None:
    metadata = _metadata()
    changed_files = [
        ChangedFile("app/api.py", "modified", 10, 3, 13, patch="+x"),
        ChangedFile("app/validators.py", "modified", 4, 2, 6, patch="+y"),
    ]
    file_reviews = [FileReview(filename="app/api.py", test_gaps=["Add malformed request regression test."])]

    body = build_summary_comment(metadata, changed_files, _final_review(), file_reviews)
    assert SUMMARY_MARKER in body
    assert "## AI PR Review Summary" in body
    assert "**Decision:** Merge" in body
    assert "**Risk Level:** Low" in body
    assert "### What changed" in body
    assert "### Key findings" in body
    assert "### Testing recommendations" in body
    assert "A DOCX review report was generated in the workflow artifacts." in body


def test_extract_changed_right_lines_from_patch() -> None:
    patch = "\n".join(
        [
            "@@ -10,4 +10,5 @@",
            " context-a",
            "-old_a",
            "+new_a",
            " context-b",
            "+new_b",
        ]
    )
    lines = extract_changed_right_lines(patch)
    assert lines == {11, 13}


def test_select_inline_comments_filters_eligibility_and_duplicates() -> None:
    inline_comments = [
        InlineComment(
            path="app/api.py",
            line=11,
            body="Check for null input before parsing.",
            severity="High",
            confidence="High",
        ),
        InlineComment(
            path="app/api.py",
            line=11,
            body="check for null input before parsing.",
            severity="High",
            confidence="High",
        ),
        InlineComment(
            path="app/api.py",
            line=12,
            body="Add bounds check for index access.",
            severity="Medium",
            confidence="Low",
        ),
        InlineComment(
            path="app/api.py",
            line=40,
            body="Validate token expiry before continuing.",
            severity="High",
            confidence="High",
        ),
    ]
    changed_line_map = {"app/api.py": {11, 12}}
    existing_comments = [{"path": "app/api.py", "line": 11, "body": "Check for null input before parsing."}]

    selected = select_inline_comments(
        inline_comments=inline_comments,
        changed_line_map=changed_line_map,
        existing_comments=existing_comments,
        limit=5,
    )
    assert selected == []


def test_select_inline_comments_respects_limit() -> None:
    inline_comments = [
        InlineComment(
            path="app/api.py",
            line=11,
            body="Guard against empty payload before parsing.",
            severity="High",
            confidence="High",
        ),
        InlineComment(
            path="app/api.py",
            line=12,
            body="Check return value before using parsed data.",
            severity="Medium",
            confidence="High",
        ),
    ]
    changed_line_map = {"app/api.py": {11, 12}}
    selected = select_inline_comments(
        inline_comments=inline_comments,
        changed_line_map=changed_line_map,
        existing_comments=[],
        limit=1,
    )
    assert len(selected) == 1
    assert selected[0].line == 11


def test_upsert_summary_comment_updates_existing_marker_comment() -> None:
    client = _FakeGitHubClient(
        comments=[{"id": 12, "body": "existing\n<!-- ai-pr-review-agent -->\nsummary"}]
    )
    action = upsert_summary_comment(
        github_client=client,
        repository="owner/repo",
        pr_number=34,
        body="new-body",
    )
    assert action == "updated"
    assert client.updated == [("owner/repo", 12, "new-body")]
    assert client.created == []


def test_build_changed_line_map_uses_patch_data_only() -> None:
    changed_files = [
        ChangedFile("app/a.py", "modified", 2, 1, 3, patch="@@ -1 +1 @@\n-a\n+b"),
        ChangedFile("app/b.py", "modified", 1, 1, 2, patch=None),
    ]
    line_map = build_changed_line_map(changed_files)
    assert "app/a.py" in line_map
    assert "app/b.py" not in line_map
