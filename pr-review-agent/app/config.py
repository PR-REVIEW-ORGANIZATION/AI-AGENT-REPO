"""Configuration loading for the PR review agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(ValueError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class AppConfig:
    """Environment-backed runtime configuration."""

    github_token: str
    openai_api_key: str
    llm_api_url: str
    llm_model: str
    post_pr_summary_comment: bool
    post_inline_comments: bool
    fail_on_comment_error: bool
    max_inline_comments: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        """Build config from environment variables."""
        source = env or os.environ
        github_token = (source.get("github_token") or source.get("GITHUB_TOKEN") or "").strip()
        openai_api_key = (source.get("OPENAI_API_KEY") or "").strip()
        llm_api_url = (source.get("LLM_API_URL") or "").strip()
        llm_model = (source.get("LLM_MODEL") or "").strip()
        post_pr_summary_comment = _parse_bool(source.get("POST_PR_SUMMARY_COMMENT"), default=True)
        post_inline_comments = _parse_bool(source.get("POST_INLINE_COMMENTS"), default=False)
        fail_on_comment_error = _parse_bool(source.get("FAIL_ON_COMMENT_ERROR"), default=False)
        max_inline_comments = _parse_positive_int(source.get("MAX_INLINE_COMMENTS"), default=5)

        missing: list[str] = []
        if not github_token:
            missing.append("github_token")
        if not openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not llm_api_url:
            missing.append("LLM_API_URL")
        if not llm_model:
            missing.append("LLM_MODEL")

        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            github_token=github_token,
            openai_api_key=openai_api_key,
            llm_api_url=llm_api_url,
            llm_model=llm_model,
            post_pr_summary_comment=post_pr_summary_comment,
            post_inline_comments=post_inline_comments,
            fail_on_comment_error=fail_on_comment_error,
            max_inline_comments=max_inline_comments,
        )


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
