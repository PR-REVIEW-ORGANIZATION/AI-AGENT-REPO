"""Entry point for PR review pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.ai_review import LLMClient, load_prompt, review_file
from app.config import AppConfig
from app.doc_writer import write_review_docx, write_review_json
from app.github_api import GitHubClient
from app.prechecks import run_prechecks
from app.synthesizer import synthesize_pr_review

LOGGER = logging.getLogger("pr-review-agent")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="AI PR Review Agent")
    parser.add_argument("--repository", required=True, help="Target repository (owner/repo)")
    parser.add_argument("--pr-number", required=True, type=int, help="Pull request number")
    parser.add_argument("--base-branch", default="", help="Optional base branch override")
    parser.add_argument("--head-branch", default="", help="Optional head branch override")
    parser.add_argument("--commit-sha", default="", help="Optional head commit SHA override")
    parser.add_argument("--output-dir", default="outputs", help="Artifact output directory")
    return parser.parse_args()


def main() -> int:
    """Execute full review pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    args = parse_args()
    config = AppConfig.from_env()

    github_client = GitHubClient(token=config.github_token)
    llm_client = LLMClient(
        api_key=config.openai_api_key,
        base_url=config.llm_api_url,
        model=config.llm_model,
    )

    LOGGER.info("Fetching PR metadata for %s #%s", args.repository, args.pr_number)
    metadata = github_client.fetch_pr_metadata(args.repository, args.pr_number)
    if args.base_branch:
        metadata.base_branch = args.base_branch
    if args.head_branch:
        metadata.head_branch = args.head_branch
    if args.commit_sha:
        metadata.commit_sha = args.commit_sha

    LOGGER.info("Fetching changed files")
    changed_files = github_client.fetch_pr_files(args.repository, args.pr_number)

    LOGGER.info("Fetching base/head contents when available")
    base_contents: dict[str, str | None] = {}
    head_contents: dict[str, str | None] = {}
    for changed_file in changed_files:
        base_content, head_content = github_client.fetch_file_versions(
            repository=args.repository,
            changed_file=changed_file,
            base_ref=metadata.base_branch,
            head_ref=metadata.commit_sha,
        )
        base_contents[changed_file.filename] = base_content
        head_contents[changed_file.filename] = head_content

    LOGGER.info("Running deterministic prechecks")
    prechecks = run_prechecks(changed_files, head_contents)

    prompt_dir = Path(__file__).resolve().parent / "prompts"
    system_prompt = load_prompt(prompt_dir / "system_prompt.txt")
    file_review_prompt = load_prompt(prompt_dir / "file_review_prompt.txt")
    final_review_prompt = load_prompt(prompt_dir / "final_review_prompt.txt")

    LOGGER.info("Running AI review for each file")
    file_reviews = []
    for changed_file in changed_files:
        file_prechecks = prechecks.issues_for_file(changed_file.filename)
        file_reviews.append(
            review_file(
                llm_client=llm_client,
                system_prompt=system_prompt,
                file_prompt_template=file_review_prompt,
                pr_metadata=metadata,
                changed_file=changed_file,
                base_content=base_contents.get(changed_file.filename),
                head_content=head_contents.get(changed_file.filename),
                precheck_issues=file_prechecks,
            )
        )

    LOGGER.info("Synthesizing PR-level review")
    final_review = synthesize_pr_review(
        llm_client=llm_client,
        system_prompt=system_prompt,
        final_prompt_template=final_review_prompt,
        pr_metadata=metadata,
        changed_files=changed_files,
        prechecks=prechecks,
        file_reviews=file_reviews,
    )

    LOGGER.info("Writing output artifacts")
    docx_path = write_review_docx(
        review=final_review,
        metadata=metadata,
        changed_files=changed_files,
        prechecks=prechecks,
        file_reviews=file_reviews,
        output_dir=args.output_dir,
    )
    json_path = write_review_json(
        review=final_review,
        metadata=metadata,
        prechecks=prechecks,
        file_reviews=file_reviews,
        output_dir=args.output_dir,
    )

    print(f"DOCX artifact generated: {docx_path}")
    print(f"JSON artifact generated: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
