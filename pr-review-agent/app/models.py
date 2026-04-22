"""Core typed models used across the PR review pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_SEVERITIES = {"low", "medium", "high"}
VALID_RISK_LEVELS = {"Low", "Medium", "High"}
VALID_DECISIONS = {"Merge", "Do Not Merge"}
VALID_CONFIDENCE = {"low", "medium", "high"}


def _to_string_list(value: Any) -> list[str]:
    """Normalize unknown list-like values into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


@dataclass
class PRMetadata:
    """Pull request metadata fetched from GitHub."""

    repository: str
    number: int
    title: str
    body: str
    author: str
    state: str
    base_branch: str
    head_branch: str
    commit_sha: str
    created_at: str
    updated_at: str
    changed_files_count: int
    additions: int
    deletions: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChangedFile:
    """Metadata for a changed file in a PR."""

    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None
    previous_filename: str | None = None
    is_binary: bool = False
    is_large: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrecheckIssue:
    """Structured deterministic finding identified before AI review."""

    code: str
    severity: str
    message: str
    filename: str | None = None

    def __post_init__(self) -> None:
        self.severity = self.severity.lower().strip()
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {self.severity}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrecheckResult:
    """Output from deterministic precheck execution."""

    risky_files: list[str] = field(default_factory=list)
    large_diff_files: list[str] = field(default_factory=list)
    missing_patch_files: list[str] = field(default_factory=list)
    syntax_warnings: list[PrecheckIssue] = field(default_factory=list)
    issues: list[PrecheckIssue] = field(default_factory=list)

    def add_issue(self, issue: PrecheckIssue) -> None:
        self.issues.append(issue)
        if issue.code.endswith("syntax") or issue.code.endswith("parse"):
            self.syntax_warnings.append(issue)

    def issues_for_file(self, filename: str) -> list[PrecheckIssue]:
        return [issue for issue in self.issues if issue.filename == filename]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risky_files": list(self.risky_files),
            "large_diff_files": list(self.large_diff_files),
            "missing_patch_files": list(self.missing_patch_files),
            "syntax_warnings": [issue.to_dict() for issue in self.syntax_warnings],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class FileIssue:
    """Issue reported by AI during per-file review."""

    severity: str
    category: str
    description: str
    recommendation: str
    evidence: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        self.severity = self.severity.lower().strip()
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {self.severity}")
        self.category = self.category.strip() or "general"
        self.description = self.description.strip()
        self.recommendation = self.recommendation.strip()
        if not self.description:
            raise ValueError("Issue description must not be empty")
        if not self.recommendation:
            raise ValueError("Issue recommendation must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileReview:
    """Per-file AI review response."""

    filename: str
    facts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    issues: list[FileIssue] = field(default_factory=list)
    test_gaps: list[str] = field(default_factory=list)
    maintainability_notes: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None
    confidence: str = "medium"

    def __post_init__(self) -> None:
        self.facts = _to_string_list(self.facts)
        self.risks = _to_string_list(self.risks)
        self.test_gaps = _to_string_list(self.test_gaps)
        self.maintainability_notes = _to_string_list(self.maintainability_notes)
        confidence = self.confidence.lower().strip()
        self.confidence = confidence if confidence in VALID_CONFIDENCE else "medium"
        if self.skipped and not self.skip_reason:
            self.skip_reason = "Review skipped by policy."

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "facts": list(self.facts),
            "risks": list(self.risks),
            "issues": [issue.to_dict() for issue in self.issues],
            "test_gaps": list(self.test_gaps),
            "maintainability_notes": list(self.maintainability_notes),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "confidence": self.confidence,
        }


@dataclass
class FinalReview:
    """Final, PR-level synthesized review."""

    executive_summary: str
    summary_of_changes: list[str]
    risk_level: str
    key_issues: list[FileIssue]
    behavior_changes: list[str]
    final_decision: str
    reasoning: str
    business_functional_context: str
    scope_of_review: str
    files_changed_summary: list[str]

    def __post_init__(self) -> None:
        self.summary_of_changes = _to_string_list(self.summary_of_changes)
        self.behavior_changes = _to_string_list(self.behavior_changes)
        self.files_changed_summary = _to_string_list(self.files_changed_summary)
        self.risk_level = self.risk_level.strip().title()
        if self.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(f"Invalid risk level: {self.risk_level}")
        self.final_decision = self.final_decision.strip()
        if self.final_decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid final decision: {self.final_decision}")
        self.executive_summary = self.executive_summary.strip()
        self.reasoning = self.reasoning.strip()
        self.business_functional_context = self.business_functional_context.strip()
        self.scope_of_review = self.scope_of_review.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "executive_summary": self.executive_summary,
            "summary_of_changes": list(self.summary_of_changes),
            "risk_level": self.risk_level,
            "key_issues": [issue.to_dict() for issue in self.key_issues],
            "behavior_changes": list(self.behavior_changes),
            "final_decision": self.final_decision,
            "reasoning": self.reasoning,
            "business_functional_context": self.business_functional_context,
            "scope_of_review": self.scope_of_review,
            "files_changed_summary": list(self.files_changed_summary),
        }
