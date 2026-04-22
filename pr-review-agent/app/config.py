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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        """Build config from environment variables."""
        source = env or os.environ
        github_token = (source.get("github_token") or source.get("GITHUB_TOKEN") or "").strip()
        openai_api_key = (source.get("OPENAI_API_KEY") or "").strip()
        llm_api_url = (source.get("LLM_API_URL") or "").strip()
        llm_model = (source.get("LLM_MODEL") or "").strip()

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
        )
