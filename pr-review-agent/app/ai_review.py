"""AI-powered, per-file review logic."""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from app.models import ChangedFile, FileIssue, FileReview, PRMetadata, PrecheckIssue

PATCH_CHAR_LIMIT = 12000
CONTENT_CHAR_LIMIT = 6000


class LLMApiError(RuntimeError):
    """Raised when the configured LLM endpoint fails."""


class LLMClient:
    """Minimal JSON-oriented LLM client for OpenAI-compatible chat endpoints."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout_seconds: int = 60) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Call the model and parse a JSON object from response content."""
        endpoints = [
            f"{self.base_url}/v1/chat/completions",
            f"{self.base_url}/chat/completions",
        ]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        errors: list[str] = []

        for endpoint in endpoints:
            for strict_json in (True, False):
                payload: dict[str, object] = {
                    "model": self.model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                if strict_json:
                    payload["response_format"] = {"type": "json_object"}
                try:
                    response = requests.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=self.timeout_seconds,
                    )
                except requests.RequestException as exc:
                    errors.append(f"{endpoint}: {exc}")
                    continue

                if response.status_code >= 400:
                    errors.append(f"{endpoint} -> {response.status_code}: {response.text[:200]}")
                    continue

                try:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    errors.append(f"{endpoint}: malformed response payload ({exc})")
                    continue

                try:
                    return _extract_json_object(content)
                except ValueError as exc:
                    errors.append(f"{endpoint}: {exc}")
                    continue

        raise LLMApiError("LLM call failed. " + " | ".join(errors[-4:]))


def load_prompt(path: Path) -> str:
    """Load prompt content from disk."""
    return path.read_text(encoding="utf-8").strip()


def review_file(
    llm_client: LLMClient,
    system_prompt: str,
    file_prompt_template: str,
    pr_metadata: PRMetadata,
    changed_file: ChangedFile,
    base_content: str | None,
    head_content: str | None,
    precheck_issues: list[PrecheckIssue],
) -> FileReview:
    """Run AI review on a single file and return structured output."""
    if changed_file.is_binary:
        return FileReview(
            filename=changed_file.filename,
            skipped=True,
            skip_reason="Binary file skipped from AI review.",
            facts=["Binary file change detected."],
        )

    if changed_file.patch is None and head_content is None and changed_file.status != "removed":
        return FileReview(
            filename=changed_file.filename,
            skipped=True,
            skip_reason="Patch and head content unavailable for AI review.",
            facts=["Insufficient content available for analysis."],
        )

    prompt = file_prompt_template.format(
        repository=pr_metadata.repository,
        pr_number=pr_metadata.number,
        pr_title=pr_metadata.title,
        filename=changed_file.filename,
        status=changed_file.status,
        additions=changed_file.additions,
        deletions=changed_file.deletions,
        patch=_clip(changed_file.patch or "", PATCH_CHAR_LIMIT),
        base_content=_clip(base_content or "", CONTENT_CHAR_LIMIT),
        head_content=_clip(head_content or "", CONTENT_CHAR_LIMIT),
        precheck_issues=json.dumps([issue.to_dict() for issue in precheck_issues], indent=2),
    )

    try:
        payload = llm_client.complete_json(system_prompt=system_prompt, user_prompt=prompt)
        return _parse_file_review_response(changed_file.filename, payload)
    except Exception as exc:  # pylint: disable=broad-except
        return FileReview(
            filename=changed_file.filename,
            facts=["AI review fallback used due to upstream model/API failure."],
            risks=[],
            issues=[
                FileIssue(
                    severity="medium",
                    category="automation",
                    description=f"LLM review failed for {changed_file.filename}: {exc}",
                    recommendation=(
                        "Run manual code review for this file before merging."
                    ),
                )
            ],
            test_gaps=["AI-generated test gap analysis unavailable for this file."],
            maintainability_notes=[],
            confidence="low",
        )


def _parse_file_review_response(filename: str, payload: dict) -> FileReview:
    facts = _to_string_list(payload.get("facts"))
    risks = _to_string_list(payload.get("risks"))
    test_gaps = _to_string_list(payload.get("test_gaps"))
    maintainability_notes = _to_string_list(payload.get("maintainability_notes"))
    confidence = str(payload.get("confidence") or "medium").lower().strip()

    raw_issues = payload.get("issues") or []
    issues: list[FileIssue] = []
    if isinstance(raw_issues, list):
        for raw_issue in raw_issues:
            if not isinstance(raw_issue, dict):
                continue
            description = str(raw_issue.get("description") or "").strip()
            recommendation = str(raw_issue.get("recommendation") or "").strip()
            if not description or not recommendation:
                continue
            try:
                issues.append(
                    FileIssue(
                        severity=str(raw_issue.get("severity") or "medium"),
                        category=str(raw_issue.get("category") or "general"),
                        description=description,
                        recommendation=recommendation,
                        evidence=_optional_text(raw_issue.get("evidence")),
                        line=_optional_int(raw_issue.get("line")),
                    )
                )
            except ValueError:
                continue

    if not facts:
        facts = ["No concrete correctness findings were identified from provided evidence."]

    return FileReview(
        filename=filename,
        facts=facts,
        risks=risks,
        issues=issues,
        test_gaps=test_gaps,
        maintainability_notes=maintainability_notes,
        confidence=confidence,
    )


def _extract_json_object(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("Model response does not contain a JSON object.")
    try:
        parsed = json.loads(match.group(0))
    except ValueError as exc:
        raise ValueError("Model response contains invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("JSON response must be an object.")
    return parsed


def _to_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                output.append(text)
        return output
    text = str(value).strip()
    return [text] if text else []


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + "\n...[truncated for token safety]..."
