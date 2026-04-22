"""PR-level synthesis for final recommendation output."""

from __future__ import annotations

import json
from typing import Iterable

from app.ai_review import LLMClient
from app.models import (
    ChangedFile,
    FileIssue,
    FileReview,
    FinalReview,
    PRMetadata,
    PrecheckResult,
)

_SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_RISK_WEIGHT = {"High": 3, "Medium": 2, "Low": 1}


def synthesize_pr_review(
    llm_client: LLMClient,
    system_prompt: str,
    final_prompt_template: str,
    pr_metadata: PRMetadata,
    changed_files: list[ChangedFile],
    prechecks: PrecheckResult,
    file_reviews: list[FileReview],
) -> FinalReview:
    """Build final review, using LLM synthesis with deterministic fallback."""
    fallback = build_fallback_review(pr_metadata, changed_files, prechecks, file_reviews)
    context_payload = {
        "metadata": pr_metadata.to_dict(),
        "prechecks": prechecks.to_dict(),
        "file_reviews": [review.to_dict() for review in file_reviews],
        "fallback_risk_level": fallback.risk_level,
        "fallback_decision": fallback.final_decision,
    }
    prompt = final_prompt_template.format(context_json=json.dumps(context_payload, indent=2))

    try:
        llm_output = llm_client.complete_json(system_prompt=system_prompt, user_prompt=prompt)
        return _merge_llm_output(fallback, llm_output)
    except Exception:
        return fallback


def build_fallback_review(
    pr_metadata: PRMetadata,
    changed_files: list[ChangedFile],
    prechecks: PrecheckResult,
    file_reviews: list[FileReview],
) -> FinalReview:
    """Deterministic synthesis used when LLM synthesis fails."""
    key_issues = collect_key_issues(file_reviews, limit=12)
    risk_level = determine_risk_level(prechecks, key_issues)
    final_decision = determine_final_decision(risk_level, key_issues)

    summary_of_changes = summarize_files(changed_files)
    behavior_changes = collect_behavior_changes(file_reviews, changed_files)
    business_context = derive_business_context(pr_metadata)
    scope_of_review = build_scope_statement(changed_files, prechecks, file_reviews)
    files_changed_summary = summarize_files(changed_files, include_counts=True)

    executive_summary = (
        f"PR #{pr_metadata.number} modifies {len(changed_files)} file(s). "
        f"Risk assessed as {risk_level}. Decision: {final_decision}."
    )
    reasoning = build_reasoning(risk_level, prechecks, key_issues)

    return FinalReview(
        executive_summary=executive_summary,
        summary_of_changes=summary_of_changes,
        risk_level=risk_level,
        key_issues=key_issues,
        behavior_changes=behavior_changes,
        final_decision=final_decision,
        reasoning=reasoning,
        business_functional_context=business_context,
        scope_of_review=scope_of_review,
        files_changed_summary=files_changed_summary,
    )


def collect_key_issues(file_reviews: list[FileReview], limit: int = 10) -> list[FileIssue]:
    """Aggregate and sort issues across file reviews."""
    issues: list[FileIssue] = []
    for review in file_reviews:
        issues.extend(review.issues)
    issues.sort(key=lambda issue: _SEVERITY_WEIGHT.get(issue.severity, 0), reverse=True)
    return issues[:limit]


def determine_risk_level(prechecks: PrecheckResult, key_issues: Iterable[FileIssue]) -> str:
    """Compute overall risk level from deterministic and AI findings."""
    high_issue_count = sum(1 for issue in key_issues if issue.severity == "high")
    medium_issue_count = sum(1 for issue in key_issues if issue.severity == "medium")
    high_precheck_count = sum(1 for issue in prechecks.issues if issue.severity == "high")
    medium_precheck_count = sum(1 for issue in prechecks.issues if issue.severity == "medium")

    if high_issue_count > 0 or high_precheck_count > 0 or prechecks.missing_patch_files:
        return "High"
    if (
        medium_issue_count >= 2
        or medium_precheck_count >= 2
        or len(prechecks.risky_files) >= 2
        or len(prechecks.large_diff_files) >= 2
    ):
        return "Medium"
    return "Low"


def determine_final_decision(risk_level: str, key_issues: Iterable[FileIssue]) -> str:
    """Compute final merge recommendation."""
    has_high = any(issue.severity == "high" for issue in key_issues)
    if risk_level == "High" or has_high:
        return "Do Not Merge"
    return "Merge"


def summarize_files(changed_files: list[ChangedFile], include_counts: bool = False) -> list[str]:
    """Create concise file change summaries."""
    summaries: list[str] = []
    for changed_file in changed_files:
        if include_counts:
            summaries.append(
                f"{changed_file.status.upper()} {changed_file.filename} "
                f"(+{changed_file.additions} / -{changed_file.deletions})"
            )
        else:
            summaries.append(f"{changed_file.status.upper()} {changed_file.filename}")
    return summaries


def collect_behavior_changes(
    file_reviews: list[FileReview], changed_files: list[ChangedFile]
) -> list[str]:
    """Collect behavior changes from AI findings and file summaries."""
    behavior_changes: list[str] = []
    for review in file_reviews:
        for fact in review.facts:
            if fact not in behavior_changes:
                behavior_changes.append(fact)

    if not behavior_changes:
        behavior_changes = summarize_files(changed_files, include_counts=True)[:8]

    return behavior_changes[:12]


def derive_business_context(pr_metadata: PRMetadata) -> str:
    """Build business/functional context from PR description when possible."""
    body = pr_metadata.body.strip()
    if body:
        return body
    return (
        "PR description does not explicitly describe business context. "
        "Context inferred from changed files and code deltas only."
    )


def build_scope_statement(
    changed_files: list[ChangedFile], prechecks: PrecheckResult, file_reviews: list[FileReview]
) -> str:
    """Create a scope statement using completed analysis steps."""
    reviewed_file_count = sum(1 for review in file_reviews if not review.skipped)
    skipped_file_count = sum(1 for review in file_reviews if review.skipped)
    return (
        f"Deterministic prechecks ran on {len(changed_files)} changed file(s). "
        f"AI file-level review completed for {reviewed_file_count} file(s), "
        f"with {skipped_file_count} skipped. "
        f"Risky files: {len(prechecks.risky_files)}, large diffs: {len(prechecks.large_diff_files)}, "
        f"missing patches: {len(prechecks.missing_patch_files)}."
    )


def build_reasoning(
    risk_level: str, prechecks: PrecheckResult, key_issues: list[FileIssue]
) -> str:
    """Create concise deterministic reasoning text."""
    issue_summary = ", ".join(
        f"{issue.severity}:{issue.category}" for issue in key_issues[:5]
    ) or "no major issues"
    return (
        f"Risk {risk_level} determined from {len(prechecks.issues)} deterministic findings and "
        f"{len(key_issues)} key AI issues ({issue_summary})."
    )


def _merge_llm_output(fallback: FinalReview, llm_output: dict) -> FinalReview:
    """Merge LLM synthesis output into deterministic fallback with safeguards."""
    executive_summary = str(llm_output.get("executive_summary") or fallback.executive_summary).strip()
    summary_of_changes = _to_string_list(llm_output.get("summary_of_changes")) or fallback.summary_of_changes
    behavior_changes = _to_string_list(llm_output.get("behavior_changes")) or fallback.behavior_changes
    business_context = str(
        llm_output.get("business_functional_context") or fallback.business_functional_context
    ).strip()
    scope = str(llm_output.get("scope_of_review") or fallback.scope_of_review).strip()
    reasoning = str(llm_output.get("reasoning") or fallback.reasoning).strip()

    llm_risk = str(llm_output.get("risk_level") or "").strip().title()
    risk_level = _max_risk(fallback.risk_level, llm_risk) if llm_risk in _RISK_WEIGHT else fallback.risk_level

    llm_decision = str(llm_output.get("final_decision") or "").strip()
    if llm_decision not in {"Merge", "Do Not Merge"}:
        final_decision = fallback.final_decision
    elif "Do Not Merge" in {fallback.final_decision, llm_decision}:
        final_decision = "Do Not Merge"
    else:
        final_decision = "Merge"

    return FinalReview(
        executive_summary=executive_summary,
        summary_of_changes=summary_of_changes,
        risk_level=risk_level,
        key_issues=fallback.key_issues,
        behavior_changes=behavior_changes,
        final_decision=final_decision,
        reasoning=reasoning,
        business_functional_context=business_context,
        scope_of_review=scope,
        files_changed_summary=fallback.files_changed_summary,
    )


def _to_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            if entry is None:
                continue
            text = str(entry).strip()
            if text:
                items.append(text)
        return items
    return [str(value).strip()] if str(value).strip() else []


def _max_risk(left: str, right: str) -> str:
    return left if _RISK_WEIGHT[left] >= _RISK_WEIGHT[right] else right
