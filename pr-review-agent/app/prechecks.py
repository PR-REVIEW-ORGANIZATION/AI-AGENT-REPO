"""Deterministic prechecks executed before AI review."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.models import ChangedFile, PrecheckIssue, PrecheckResult

LARGE_CHANGE_THRESHOLD = 500
LARGE_PATCH_LINE_THRESHOLD = 300

_RISK_PATTERNS: dict[str, re.Pattern[str]] = {
    "auth": re.compile(
        r"(^|/)(auth|authentication|authorization|oauth|jwt|iam|permission|rbac)($|/|_|-|\.)",
        re.IGNORECASE,
    ),
    "infra": re.compile(
        r"(^|/)(infra|infrastructure|terraform|k8s|helm|docker|ansible|deployment)($|/|_|-|\.)",
        re.IGNORECASE,
    ),
    "workflow": re.compile(r"(^|/)\.github/workflows/.*\.ya?ml$", re.IGNORECASE),
}

_DEPENDENCY_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "pom.xml",
    "build.gradle",
    "pipfile",
    "pipfile.lock",
}


def run_prechecks(
    changed_files: list[ChangedFile], head_contents: dict[str, str | None]
) -> PrecheckResult:
    """Run deterministic prechecks and return structured findings."""
    result = PrecheckResult()

    for changed_file in changed_files:
        risk_categories = detect_risk_categories(changed_file.filename)
        if risk_categories:
            result.risky_files.append(changed_file.filename)
            severity = "high" if {"auth", "workflow"} & set(risk_categories) else "medium"
            result.add_issue(
                PrecheckIssue(
                    code="risky_file_path",
                    severity=severity,
                    message=(
                        f"File is in a risky area ({', '.join(risk_categories)}): "
                        f"{changed_file.filename}"
                    ),
                    filename=changed_file.filename,
                )
            )

        if _is_large_diff(changed_file):
            result.large_diff_files.append(changed_file.filename)
            result.add_issue(
                PrecheckIssue(
                    code="large_diff",
                    severity="medium",
                    message=(
                        f"Large diff detected with {changed_file.changes} line changes in "
                        f"{changed_file.filename}"
                    ),
                    filename=changed_file.filename,
                )
            )

        if changed_file.patch is None and not changed_file.is_binary and changed_file.status != "removed":
            result.missing_patch_files.append(changed_file.filename)
            result.add_issue(
                PrecheckIssue(
                    code="missing_patch",
                    severity="high",
                    message=f"Patch is missing for non-binary file: {changed_file.filename}",
                    filename=changed_file.filename,
                )
            )

        if changed_file.patch and _has_conflict_markers(changed_file.patch):
            result.add_issue(
                PrecheckIssue(
                    code="merge_conflict_marker",
                    severity="high",
                    message=f"Possible unresolved conflict markers in {changed_file.filename}",
                    filename=changed_file.filename,
                )
            )

        syntax_issues = _run_syntax_heuristics(
            changed_file=changed_file,
            head_content=head_contents.get(changed_file.filename),
        )
        for issue in syntax_issues:
            result.add_issue(issue)

    result.risky_files = sorted(set(result.risky_files))
    result.large_diff_files = sorted(set(result.large_diff_files))
    result.missing_patch_files = sorted(set(result.missing_patch_files))
    return result


def detect_risk_categories(filename: str) -> list[str]:
    """Classify risky files by path and dependency indicators."""
    normalized = filename.replace("\\", "/")
    categories: list[str] = []
    for category, pattern in _RISK_PATTERNS.items():
        if pattern.search(normalized):
            categories.append(category)
    if Path(normalized).name.lower() in _DEPENDENCY_FILES:
        categories.append("dependency")
    return sorted(set(categories))


def _is_large_diff(changed_file: ChangedFile) -> bool:
    if changed_file.changes >= LARGE_CHANGE_THRESHOLD:
        return True
    if changed_file.patch:
        patch_lines = len(changed_file.patch.splitlines())
        if patch_lines >= LARGE_PATCH_LINE_THRESHOLD:
            return True
    return False


def _has_conflict_markers(patch: str) -> bool:
    return any(token in patch for token in ("<<<<<<<", "=======", ">>>>>>>"))


def _run_syntax_heuristics(changed_file: ChangedFile, head_content: str | None) -> list[PrecheckIssue]:
    issues: list[PrecheckIssue] = []
    filename = changed_file.filename
    lower_name = filename.lower()

    if lower_name.endswith(".py") and head_content:
        try:
            compile(head_content, filename, "exec")
        except SyntaxError as exc:
            issues.append(
                PrecheckIssue(
                    code="python_syntax",
                    severity="high",
                    message=f"Python syntax error in {filename}: {exc.msg} (line {exc.lineno})",
                    filename=filename,
                )
            )

    if lower_name.endswith(".json") and head_content:
        try:
            json.loads(head_content)
        except json.JSONDecodeError as exc:
            issues.append(
                PrecheckIssue(
                    code="json_parse",
                    severity="high",
                    message=f"JSON parse issue in {filename}: {exc.msg} (line {exc.lineno})",
                    filename=filename,
                )
            )

    if lower_name.endswith((".yaml", ".yml")) and head_content and "\t" in head_content:
        issues.append(
            PrecheckIssue(
                code="yaml_syntax",
                severity="low",
                message=f"YAML contains tab indentation in {filename}, which may break parsing",
                filename=filename,
            )
        )

    if changed_file.patch and _has_unbalanced_delimiters(changed_file.patch):
        issues.append(
            PrecheckIssue(
                code="delimiter_balance",
                severity="low",
                message=f"Possible unbalanced delimiters in patch for {filename}",
                filename=filename,
            )
        )

    return issues


def _has_unbalanced_delimiters(patch: str) -> bool:
    added_lines = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    text = "\n".join(added_lines)
    checks = [("(", ")"), ("{", "}"), ("[", "]")]
    for left, right in checks:
        if abs(text.count(left) - text.count(right)) > 2:
            return True
    return False
