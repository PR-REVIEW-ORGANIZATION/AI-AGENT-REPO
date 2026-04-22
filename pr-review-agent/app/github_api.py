"""GitHub API client utilities for PR data retrieval."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests

from app.models import ChangedFile, PRMetadata

LOGGER = logging.getLogger(__name__)

_BINARY_EXTENSIONS = {
    ".7z",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".svgz",
    ".tar",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}

_TEXTY_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
)


class GitHubApiError(RuntimeError):
    """Raised when GitHub API responses are unsuccessful."""


class GitHubClient:
    """Thin GitHub REST API client focused on pull request review data."""

    base_url = "https://api.github.com"

    def __init__(self, token: str, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "pr-review-agent",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def fetch_pr_metadata(self, repository: str, pr_number: int) -> PRMetadata:
        """Fetch pull request metadata."""
        data = self._request_json("GET", f"/repos/{repository}/pulls/{pr_number}")
        return PRMetadata(
            repository=repository,
            number=int(data["number"]),
            title=str(data.get("title") or "").strip(),
            body=str(data.get("body") or "").strip(),
            author=str(data.get("user", {}).get("login") or "unknown"),
            state=str(data.get("state") or "unknown"),
            base_branch=str(data.get("base", {}).get("ref") or ""),
            head_branch=str(data.get("head", {}).get("ref") or ""),
            commit_sha=str(data.get("head", {}).get("sha") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            changed_files_count=int(data.get("changed_files") or 0),
            additions=int(data.get("additions") or 0),
            deletions=int(data.get("deletions") or 0),
        )

    def fetch_pr_files(self, repository: str, pr_number: int) -> list[ChangedFile]:
        """Fetch changed files for a pull request, including pagination handling."""
        files_data = self._request_paginated(f"/repos/{repository}/pulls/{pr_number}/files")
        result: list[ChangedFile] = []
        for raw in files_data:
            filename = str(raw.get("filename") or "")
            patch = raw.get("patch")
            likely_binary = patch is None and _is_binary_filename(filename)
            likely_large = patch is None and not likely_binary and int(raw.get("changes") or 0) > 0
            result.append(
                ChangedFile(
                    filename=filename,
                    status=str(raw.get("status") or "unknown"),
                    additions=int(raw.get("additions") or 0),
                    deletions=int(raw.get("deletions") or 0),
                    changes=int(raw.get("changes") or 0),
                    patch=patch if isinstance(patch, str) else None,
                    previous_filename=raw.get("previous_filename"),
                    is_binary=likely_binary,
                    is_large=likely_large,
                )
            )
        return result

    def fetch_file_versions(
        self,
        repository: str,
        changed_file: ChangedFile,
        base_ref: str,
        head_ref: str,
    ) -> tuple[str | None, str | None]:
        """
        Fetch base/head file content where possible.

        Binary files are skipped. Removed files will not have head content. Added files will not have base content.
        """
        if changed_file.is_binary:
            return None, None

        base_content: str | None = None
        head_content: str | None = None

        if changed_file.status != "added":
            base_filename = changed_file.previous_filename or changed_file.filename
            base_content = self.fetch_file_content(repository, base_filename, base_ref)

        if changed_file.status != "removed":
            head_content = self.fetch_file_content(repository, changed_file.filename, head_ref)

        return base_content, head_content

    def fetch_file_content(self, repository: str, filepath: str, ref: str) -> str | None:
        """Fetch file content from a specific ref using GitHub contents API."""
        encoded_path = quote(filepath, safe="/")
        response = self._request(
            "GET",
            f"/repos/{repository}/contents/{encoded_path}",
            params={"ref": ref},
            expected_status={200, 404},
        )
        if response.status_code == 404:
            return None

        payload = response.json()
        if isinstance(payload, list):
            return None

        if payload.get("encoding") == "base64" and payload.get("content"):
            try:
                decoded = base64.b64decode(payload["content"], validate=False)
                return decoded.decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None

        download_url = payload.get("download_url")
        if download_url:
            raw_response = self._request(
                "GET",
                download_url,
                expected_status={200, 404},
                absolute=True,
            )
            if raw_response.status_code == 404:
                return None
            content_type = raw_response.headers.get("Content-Type", "")
            if not _is_text_content_type(content_type):
                return None
            raw_response.encoding = raw_response.encoding or "utf-8"
            return raw_response.text

        return None

    def _request_paginated(self, path: str, params: dict[str, object] | None = None) -> list[dict]:
        all_items: list[dict] = []
        page = 1
        while True:
            merged_params = dict(params or {})
            merged_params["per_page"] = 100
            merged_params["page"] = page
            data = self._request_json("GET", path, params=merged_params)
            if not isinstance(data, list):
                raise GitHubApiError(f"Expected list response for paginated endpoint: {path}")
            if not data:
                break
            all_items.extend(data)
            if len(data) < 100:
                break
            page += 1
        return all_items

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
    ) -> dict | list:
        response = self._request(method, path, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubApiError(f"Invalid JSON response from GitHub for {path}") from exc

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        expected_status: int | set[int] = 200,
        absolute: bool = False,
    ) -> requests.Response:
        url = path if absolute else f"{self.base_url}{path}"
        response = self.session.request(method, url, params=params, timeout=self.timeout_seconds)
        expected = expected_status if isinstance(expected_status, set) else {expected_status}
        if response.status_code not in expected:
            body_preview = response.text[:400].replace("\n", " ")
            raise GitHubApiError(
                f"GitHub API request failed ({response.status_code}) for {url}: {body_preview}"
            )
        return response


def _is_text_content_type(content_type: str) -> bool:
    lower_content_type = content_type.lower()
    return any(marker in lower_content_type for marker in _TEXTY_CONTENT_TYPES)


def _is_binary_filename(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix in _BINARY_EXTENSIONS
