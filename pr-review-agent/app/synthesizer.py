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
_BLOCKING_PRECHECK_CODES = {"merge_conflict_marker", "python_syntax", "json_parse"}
_WARNING_PRECHECK_CODES = {"missing_patch"}
_STRUCTURAL_PRECHECK_CODES = {"risky_file_path", "large_diff"}
_REAL_ISSUE_CATEGORIES = {"correctness", "logic", "regression", "security", "edge_case", "tests"}
_LOW_SIGNAL_FACT_PREFIXES = (
    "no concrete correctness findings were identified",
    "ai review fallback used",
    "insufficient content available for analysis",
    "binary file change detected",
)


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
        "fallback_purpose_of_pr": fallback.purpose_of_pr,
        "fallback_behavior_before": fallback.behavior_before,
        "fallback_behavior_after": fallback.behavior_after,
        "fallback_core_logic_changes": fallback.core_logic_changes,
        "fallback_implementation_changes": fallback.implementation_changes,
        "fallback_real_issues_only": fallback.real_issues_only,
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
    final_decision = determine_final_decision(risk_level, key_issues, prechecks)
    purpose_of_pr = derive_purpose_of_pr(pr_metadata, changed_files)
    behavior_changes = collect_behavior_changes(file_reviews, changed_files)
    behavior_before = derive_behavior_before(changed_files)
    behavior_after = derive_behavior_after(behavior_changes, changed_files)
    core_logic_changes = derive_core_logic_changes(file_reviews, changed_files)
    implementation_changes = derive_implementation_changes(changed_files)
    summary_of_changes = build_summary_of_changes(
        behavior_after=behavior_after,
        core_logic_changes=core_logic_changes,
        implementation_changes=implementation_changes,
    )
    real_issues_only = collect_real_issues_only(prechecks, key_issues)
    business_context = derive_business_context(pr_metadata, purpose_of_pr)
    scope_of_review = build_scope_statement(changed_files, prechecks, file_reviews)
    files_changed_summary = summarize_files(changed_files, include_counts=True)

    executive_summary = build_executive_summary(
        behavior_after=behavior_after,
        risk_level=risk_level,
        final_decision=final_decision,
    )
    reasoning = build_reasoning(risk_level, real_issues_only)

    return FinalReview(
        executive_summary=executive_summary,
        summary_of_changes=summary_of_changes,
        risk_level=risk_level,
        key_issues=key_issues,
        behavior_changes=behavior_after,
        final_decision=final_decision,
        reasoning=reasoning,
        business_functional_context=business_context,
        scope_of_review=scope_of_review,
        files_changed_summary=files_changed_summary,
        purpose_of_pr=purpose_of_pr,
        behavior_before=behavior_before,
        behavior_after=behavior_after,
        core_logic_changes=core_logic_changes,
        implementation_changes=implementation_changes,
        real_issues_only=real_issues_only,
        final_recommendation=final_decision,
    )


def collect_key_issues(file_reviews: list[FileReview], limit: int = 10) -> list[FileIssue]:
    """Aggregate and sort issues across file reviews."""
    issues: list[FileIssue] = []
    for review in file_reviews:
        issues.extend(review.issues)
    issues.sort(key=lambda issue: _SEVERITY_WEIGHT.get(issue.severity, 0), reverse=True)
    return issues[:limit]


def determine_risk_level(prechecks: PrecheckResult, key_issues: Iterable[FileIssue]) -> str:
    """Compute overall risk level from evidence-backed issues only."""
    high_issue_count = sum(1 for issue in key_issues if _counts_for_risk(issue) and issue.severity == "high")
    medium_issue_count = sum(
        1 for issue in key_issues if _counts_for_risk(issue) and issue.severity == "medium"
    )
    low_issue_count = sum(1 for issue in key_issues if _counts_for_risk(issue) and issue.severity == "low")
    blocking_precheck_count = sum(
        1 for issue in prechecks.issues if issue.code in _BLOCKING_PRECHECK_CODES
    )
    warning_precheck_count = sum(
        1 for issue in prechecks.issues if issue.code in _WARNING_PRECHECK_CODES
    )

    if high_issue_count > 0 or blocking_precheck_count > 0:
        return "High"
    if medium_issue_count > 0 or warning_precheck_count > 0 or low_issue_count >= 3:
        return "Medium"
    return "Low"


def determine_final_decision(
    risk_level: str,
    key_issues: Iterable[FileIssue],
    prechecks: PrecheckResult | None = None,
) -> str:
    """Compute final merge recommendation."""
    has_high = any(_counts_for_risk(issue) and issue.severity == "high" for issue in key_issues)
    has_blocking_precheck = bool(
        prechecks and any(issue.code in _BLOCKING_PRECHECK_CODES for issue in prechecks.issues)
    )
    if risk_level == "High" or has_high or has_blocking_precheck:
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
    """Collect behavior changes from AI findings and deterministic file data."""
    behavior_changes: list[str] = []
    for review in file_reviews:
        for fact in review.facts:
            if _is_low_signal_fact(fact):
                continue
            if fact not in behavior_changes:
                behavior_changes.append(fact)

    if not behavior_changes:
        behavior_changes = summarize_files(changed_files, include_counts=True)[:8]

    return behavior_changes[:12]


def derive_business_context(pr_metadata: PRMetadata, purpose_of_pr: str) -> str:
    """Build business/functional context from PR description when possible."""
    body = pr_metadata.body.strip()
    if body:
        return body
    return purpose_of_pr


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
    risk_level: str, real_issues_only: list[str]
) -> str:
    """Create concise deterministic reasoning focused on concrete issues."""
    if not real_issues_only:
        return (
            f"Risk {risk_level}: no evidence-backed correctness, security, logic, or "
            "regression defects were identified from the reviewed context."
        )
    return (
        f"Risk {risk_level}: based on {len(real_issues_only)} concrete issue(s) with direct "
        "evidence in diffs or deterministic checks."
    )


def _merge_llm_output(fallback: FinalReview, llm_output: dict) -> FinalReview:
    """Merge LLM synthesis output into deterministic fallback with safeguards."""
    purpose_of_pr = str(
        llm_output.get("purpose_of_pr")
        or llm_output.get("business_functional_context")
        or fallback.purpose_of_pr
    ).strip()
    summary_of_changes = _to_string_list(llm_output.get("summary_of_changes")) or fallback.summary_of_changes
    behavior_before = _to_string_list(llm_output.get("behavior_before")) or fallback.behavior_before
    behavior_after = (
        _to_string_list(llm_output.get("behavior_after"))
        or _to_string_list(llm_output.get("behavior_changes"))
        or fallback.behavior_after
    )
    core_logic_changes = _to_string_list(llm_output.get("core_logic_changes")) or fallback.core_logic_changes
    implementation_changes = (
        _to_string_list(llm_output.get("implementation_changes")) or fallback.implementation_changes
    )
    real_issues_only = _to_string_list(llm_output.get("real_issues_only")) or fallback.real_issues_only

    risk_level = fallback.risk_level
    final_decision = fallback.final_decision

    executive_summary = build_executive_summary(
        behavior_after=behavior_after,
        risk_level=risk_level,
        final_decision=final_decision,
    )
    business_context = str(
        llm_output.get("business_functional_context") or fallback.business_functional_context
    ).strip()
    scope = str(llm_output.get("scope_of_review") or fallback.scope_of_review).strip()
    reasoning = build_reasoning(risk_level, real_issues_only)
    behavior_changes = behavior_after or fallback.behavior_changes

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
        purpose_of_pr=purpose_of_pr or fallback.purpose_of_pr,
        behavior_before=behavior_before,
        behavior_after=behavior_after,
        core_logic_changes=core_logic_changes,
        implementation_changes=implementation_changes,
        real_issues_only=real_issues_only,
        final_recommendation=final_decision,
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


def derive_purpose_of_pr(pr_metadata: PRMetadata, changed_files: list[ChangedFile]) -> str:
    """Derive likely PR purpose from metadata without speculation."""
    body = " ".join(pr_metadata.body.split()).strip()
    if body:
        return _first_sentence(body)
    title = " ".join(pr_metadata.title.split()).strip()
    if title:
        return title
    if changed_files:
        return (
            f"Update {len(changed_files)} file(s) to adjust existing behavior and implementation."
        )
    return "Purpose was not described in the provided PR metadata."


def derive_behavior_before(changed_files: list[ChangedFile]) -> list[str]:
    """Build concise statements describing baseline behavior before the PR."""
    added = sum(1 for file_item in changed_files if file_item.status == "added")
    removed = sum(1 for file_item in changed_files if file_item.status == "removed")
    modified = sum(1 for file_item in changed_files if file_item.status == "modified")

    behavior_before: list[str] = []
    if added:
        behavior_before.append("Newly added components did not exist before this PR.")
    if removed:
        behavior_before.append("Removed components were part of the previous behavior path.")
    if modified:
        behavior_before.append("Modified files followed their previous logic and output patterns.")
    if not behavior_before:
        behavior_before.append("Prior behavior can only be inferred from the provided base content.")
    return behavior_before[:6]


def derive_behavior_after(behavior_changes: list[str], changed_files: list[ChangedFile]) -> list[str]:
    """Build concise statements describing behavior after the PR."""
    behavior_after = _dedupe_preserve_order(behavior_changes)
    if behavior_after:
        return behavior_after[:8]
    if changed_files:
        return summarize_files(changed_files, include_counts=True)[:8]
    return ["Post-change behavior is not observable from available context."]


def derive_core_logic_changes(
    file_reviews: list[FileReview], changed_files: list[ChangedFile]
) -> list[str]:
    """Extract core logic changes from AI facts first, then deterministic summaries."""
    logic_changes = _dedupe_preserve_order(
        fact for review in file_reviews for fact in review.facts
    )
    if logic_changes:
        return logic_changes[:8]
    return summarize_files(changed_files)[:8]


def derive_implementation_changes(changed_files: list[ChangedFile]) -> list[str]:
    """Capture concise implementation-level details without overwhelming the summary."""
    implementation_changes = [
        f"{file_item.status.upper()} {file_item.filename} (+{file_item.additions} / -{file_item.deletions})"
        for file_item in changed_files
    ]
    return implementation_changes[:12]


def collect_real_issues_only(prechecks: PrecheckResult, key_issues: list[FileIssue]) -> list[str]:
    """Collect only concrete defects and evidence-backed review gaps."""
    issues: list[str] = []

    for issue in key_issues:
        if not _counts_for_risk(issue):
            continue
        issues.append(f"{issue.severity.upper()} {issue.category}: {issue.description}")

    for precheck_issue in prechecks.issues:
        if precheck_issue.code in _STRUCTURAL_PRECHECK_CODES:
            continue
        if precheck_issue.code in _BLOCKING_PRECHECK_CODES | _WARNING_PRECHECK_CODES:
            issues.append(
                f"{precheck_issue.severity.upper()} {precheck_issue.code}: {precheck_issue.message}"
            )

    return _dedupe_preserve_order(issues)[:12]


def build_summary_of_changes(
    behavior_after: list[str],
    core_logic_changes: list[str],
    implementation_changes: list[str],
) -> list[str]:
    """Build concise PR summary with behavior-first ordering."""
    summary = _dedupe_preserve_order(
        [*behavior_after[:4], *core_logic_changes[:3], *implementation_changes[:2]]
    )
    return summary[:10] or ["No concrete change summary available from provided context."]


def build_executive_summary(behavior_after: list[str], risk_level: str, final_decision: str) -> str:
    """Create behavior-first executive summary."""
    lead = behavior_after[0] if behavior_after else "Functional behavior changed in this PR."
    lead = _ensure_sentence(lead)
    return f"{lead} Risk: {risk_level}. Recommendation: {final_decision}."


def _counts_for_risk(issue: FileIssue) -> bool:
    """Return True when an issue should influence overall risk."""
    if issue.category in _REAL_ISSUE_CATEGORIES:
        return True
    return issue.severity == "high"


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _first_sentence(text: str) -> str:
    for separator in (". ", "\n", "; "):
        if separator in text:
            return text.split(separator, 1)[0].strip().rstrip(".") + "."
    return _ensure_sentence(text)


def _ensure_sentence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    return cleaned if cleaned[-1] in ".!?" else f"{cleaned}."


def _is_low_signal_fact(fact: str) -> bool:
    normalized = fact.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _LOW_SIGNAL_FACT_PREFIXES)
