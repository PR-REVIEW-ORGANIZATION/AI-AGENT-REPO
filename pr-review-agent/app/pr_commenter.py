"""PR comment publishing utilities (summary + optional inline comments)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.github_api import GitHubClient
from app.models import ChangedFile, FileReview, FinalReview, InlineComment, PRMetadata

LOGGER = logging.getLogger(__name__)

SUMMARY_MARKER = "<!-- ai-pr-review-agent -->"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class CommentingResult:
    """Outcome for PR comment publishing."""

    summary_action: str
    inline_posted: int
    inline_candidates: int
    inline_skipped: int


def publish_pr_comments(
    github_client: GitHubClient,
    repository: str,
    pr_number: int,
    metadata: PRMetadata,
    changed_files: list[ChangedFile],
    final_review: FinalReview,
    file_reviews: list[FileReview],
    post_summary_comment: bool = True,
    post_inline_comments: bool = False,
    fail_on_comment_error: bool = False,
    max_inline_comments: int = 5,
) -> CommentingResult:
    """Publish one summary comment and optional inline comments."""
    summary_action = "skipped"
    inline_posted = 0
    inline_candidates = 0
    inline_skipped = 0

    if post_summary_comment:
        summary_body = build_summary_comment(metadata, changed_files, final_review, file_reviews)
        try:
            summary_action = upsert_summary_comment(
                github_client=github_client,
                repository=repository,
                pr_number=pr_number,
                body=summary_body,
            )
        except Exception:
            LOGGER.exception("Failed to create/update PR summary comment.")
            if fail_on_comment_error:
                raise
            summary_action = "error"

    if post_inline_comments:
        changed_line_map = build_changed_line_map(changed_files)
        existing_comments = github_client.list_pr_inline_comments(repository, pr_number)
        candidates = select_inline_comments(
            final_review.inline_comments,
            changed_line_map=changed_line_map,
            existing_comments=existing_comments,
            limit=max_inline_comments,
        )
        inline_candidates = len(candidates)
        for candidate in candidates:
            try:
                github_client.create_pr_inline_comment(
                    repository=repository,
                    pr_number=pr_number,
                    commit_sha=metadata.commit_sha,
                    path=candidate.path,
                    line=candidate.line,
                    body=candidate.body,
                )
                inline_posted += 1
            except Exception:
                inline_skipped += 1
                LOGGER.exception(
                    "Failed to post inline comment for %s:%s", candidate.path, candidate.line
                )
                if fail_on_comment_error:
                    raise

    return CommentingResult(
        summary_action=summary_action,
        inline_posted=inline_posted,
        inline_candidates=inline_candidates,
        inline_skipped=inline_skipped,
    )


def build_summary_comment(
    metadata: PRMetadata,
    changed_files: list[ChangedFile],
    final_review: FinalReview,
    file_reviews: list[FileReview],
) -> str:
    """Render a single PR summary comment body."""
    decision = final_review.final_recommendation or final_review.final_decision
    if decision not in {"Merge", "Do Not Merge"}:
        decision = "Review Blocked"

    what_changed = _select_what_changed(final_review)
    key_findings = _select_key_findings(final_review)
    testing_recommendations = _select_testing_recommendations(file_reviews)

    lines = [
        SUMMARY_MARKER,
        "",
        "## AI PR Review Summary",
        "",
        f"**Decision:** {decision}  ",
        f"**Risk Level:** {final_review.risk_level}  ",
        f"**Files Changed:** {metadata.changed_files_count or len(changed_files)}  ",
        f"**Lines Added / Removed:** +{metadata.additions} / -{metadata.deletions}",
        "",
        "### What changed",
    ]
    lines.extend(f"- {item}" for item in what_changed)
    lines.append("")
    lines.append("### Key findings")
    lines.extend(f"- {item}" for item in key_findings)
    lines.append("")
    lines.append("### Testing recommendations")
    lines.extend(f"- {item}" for item in testing_recommendations)
    lines.append("")
    lines.append("### Detailed report")
    lines.append("A DOCX review report was generated in the workflow artifacts.")
    return "\n".join(lines).strip()


def upsert_summary_comment(
    github_client: GitHubClient,
    repository: str,
    pr_number: int,
    body: str,
) -> str:
    """Create or update the single marker-based summary comment."""
    issue_comments = github_client.list_issue_comments(repository, pr_number)
    existing = None
    for comment in issue_comments:
        if SUMMARY_MARKER in str(comment.get("body") or ""):
            existing = comment

    if existing and isinstance(existing.get("id"), int):
        github_client.update_issue_comment(repository, int(existing["id"]), body)
        return "updated"

    github_client.create_issue_comment(repository, pr_number, body)
    return "created"


def select_inline_comments(
    inline_comments: list[InlineComment],
    changed_line_map: dict[str, set[int]],
    existing_comments: list[dict],
    limit: int,
) -> list[InlineComment]:
    """Filter inline comments to concrete, deduplicated, line-valid comments."""
    existing_keys = _existing_inline_keys(existing_comments)
    selected: list[InlineComment] = []
    seen: set[tuple[str, int, str]] = set()
    max_items = max(0, int(limit))

    for comment in inline_comments:
        if len(selected) >= max_items:
            break
        if not comment.is_eligible:
            continue
        changed_lines = changed_line_map.get(comment.path)
        if not changed_lines:
            continue
        if comment.line not in changed_lines:
            continue
        key = (comment.path, comment.line, _normalize_comment_text(comment.body))
        if key in seen or key in existing_keys:
            continue
        seen.add(key)
        selected.append(comment)

    return selected


def build_changed_line_map(changed_files: list[ChangedFile]) -> dict[str, set[int]]:
    """Map file paths to changed RIGHT-side line numbers from patch hunks."""
    mapping: dict[str, set[int]] = {}
    for changed_file in changed_files:
        if not changed_file.patch:
            continue
        lines = extract_changed_right_lines(changed_file.patch)
        if lines:
            mapping[changed_file.filename] = lines
    return mapping


def extract_changed_right_lines(patch: str) -> set[int]:
    """Extract added/modified RIGHT-side line numbers from a unified diff patch."""
    changed_lines: set[int] = set()
    current_right_line: int | None = None

    for raw_line in patch.splitlines():
        if raw_line.startswith("@@"):
            match = _HUNK_RE.match(raw_line)
            current_right_line = int(match.group(1)) if match else None
            continue
        if current_right_line is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            changed_lines.add(current_right_line)
            current_right_line += 1
            continue
        if raw_line.startswith(" "):
            current_right_line += 1
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        if raw_line.startswith("\\"):
            continue

    return changed_lines


def _select_what_changed(final_review: FinalReview) -> list[str]:
    entries = final_review.behavior_after or final_review.summary_of_changes
    cleaned = _dedupe_non_empty(entries)
    if cleaned:
        return cleaned[:4]
    return ["Behavior-focused change summary is limited by available diff context."]


def _select_key_findings(final_review: FinalReview) -> list[str]:
    findings = _dedupe_non_empty(final_review.real_issues_only)
    if findings:
        return findings[:5]
    return ["No evidence-based medium/high defects were identified in reviewed changes."]


def _select_testing_recommendations(file_reviews: list[FileReview]) -> list[str]:
    gathered: list[str] = []
    for review in file_reviews:
        gathered.extend(review.test_gaps)

    recommendations = _dedupe_non_empty(gathered)
    if recommendations:
        return recommendations[:5]
    return [
        "Run unit/integration tests covering the updated behavior paths.",
        "Validate regression scenarios around modified files before merge.",
    ]


def _existing_inline_keys(existing_comments: list[dict]) -> set[tuple[str, int, str]]:
    keys: set[tuple[str, int, str]] = set()
    for comment in existing_comments:
        path = str(comment.get("path") or "").strip()
        line = comment.get("line")
        body = _normalize_comment_text(str(comment.get("body") or ""))
        if not path or not body:
            continue
        try:
            parsed_line = int(line)
        except (TypeError, ValueError):
            continue
        keys.add((path, parsed_line, body))
    return keys


def _dedupe_non_empty(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in items:
        text = str(raw).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _normalize_comment_text(text: str) -> str:
    return " ".join(text.split()).strip().lower()
