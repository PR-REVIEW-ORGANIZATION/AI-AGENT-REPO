"""Tests for environment-backed application configuration."""

from __future__ import annotations

from app.config import AppConfig


def _base_env() -> dict[str, str]:
    return {
        "github_token": "token",
        "OPENAI_API_KEY": "key",
        "LLM_API_URL": "https://example.test",
        "LLM_MODEL": "model-id",
    }


def test_commenting_flags_default_values() -> None:
    config = AppConfig.from_env(_base_env())
    assert config.post_pr_summary_comment is True
    assert config.post_inline_comments is False
    assert config.fail_on_comment_error is False
    assert config.max_inline_comments == 5


def test_commenting_flags_can_be_overridden() -> None:
    env = _base_env()
    env.update(
        {
            "POST_PR_SUMMARY_COMMENT": "false",
            "POST_INLINE_COMMENTS": "true",
            "FAIL_ON_COMMENT_ERROR": "true",
            "MAX_INLINE_COMMENTS": "7",
        }
    )
    config = AppConfig.from_env(env)
    assert config.post_pr_summary_comment is False
    assert config.post_inline_comments is True
    assert config.fail_on_comment_error is True
    assert config.max_inline_comments == 7
