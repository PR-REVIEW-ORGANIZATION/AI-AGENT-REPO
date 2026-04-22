# AI PR Review Agent

Production-ready AI pull request review system with reusable GitHub Actions workflow, deterministic prechecks, AI per-file review, PR-level synthesis, and one final DOCX report per PR.

## Architecture

Flow:
1. Target repository PR event triggers `.github/workflows/ai-pr-review.yml`.
2. Target workflow calls this repository's reusable workflow `.github/workflows/review.yml`.
3. `app/main.py` runs pipeline:
   - Fetch PR metadata + files + patch + base/head content from GitHub.
   - Run deterministic prechecks.
   - Run AI review per changed file.
   - Synthesize final PR review.
   - Generate exactly one DOCX plus one JSON artifact.

Key modules:
- `app/github_api.py`: GitHub PR/file/diff/content integration with pagination.
- `app/prechecks.py`: Risk and syntax heuristics.
- `app/ai_review.py`: LLM client + per-file review.
- `app/synthesizer.py`: PR-level synthesis and decision logic.
- `app/doc_writer.py`: DOCX + JSON artifact generation.

## Folder Structure

```text
<agent-repo-root>/
  .github/
    workflows/
      review.yml
  pr-review-agent/
    app/
      main.py
      config.py
      models.py
      github_api.py
      prechecks.py
      ai_review.py
      synthesizer.py
      doc_writer.py
      prompts/
        system_prompt.txt
        file_review_prompt.txt
        final_review_prompt.txt
    tests/
      test_models.py
      test_prechecks.py
      test_synthesizer.py
    requirements.txt
    README.md
    .env.example
```

## Environment Variables

Set all required variables (no hardcoded secrets):

- `github_token`
- `OPENAI_API_KEY`
- `LLM_API_URL`
- `LLM_MODEL`

Expected values:
- `LLM_API_URL=https://genai-sharedservice-americas.pwc.com`
- `LLM_MODEL=azure.gpt-4o-mini`

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set environment variables (example):

```bash
export github_token=...
export OPENAI_API_KEY=...
export LLM_API_URL=https://genai-sharedservice-americas.pwc.com
export LLM_MODEL=azure.gpt-4o-mini
```

Run:

```bash
python app/main.py --repository owner/repo --pr-number 1
```

Artifacts:
- `outputs/Detailed_PR_Review_PR_<number>.docx`
- `outputs/Detailed_PR_Review_PR_<number>.json`

## Reusable Workflow (Agent Repository)

File: `.github/workflows/review.yml`

This workflow uses `workflow_call` and requires:
- Inputs: `repository`, `pr_number`, `base_branch`, `head_branch`, `commit_sha`
- Optional inputs: `agent_repository`, `agent_ref` (used for checkout of agent source)
- Secrets: `OPENAI_API_KEY`, `LLM_API_URL`, `LLM_MODEL`
- Optional secret: `GH_TOKEN` (recommended PAT for private cross-repo checkout)

The job installs dependencies, runs the pipeline, and uploads DOCX/JSON artifacts.

## Target Repository Integration

Place this file in the **target repository** at:
- `.github/workflows/ai-pr-review.yml`

Connection steps:
1. Push this agent repository (including `.github/workflows/review.yml`) to GitHub.
2. In each target repo, add `.github/workflows/ai-pr-review.yml`.
3. Ensure target repo secrets include:
   - `OPENAI_API_KEY`
   - `LLM_API_URL`
   - `LLM_MODEL`
4. Add optional `GH_TOKEN` secret in target repo if agent repo is private or cross-org (PAT with read access).
5. Use this caller workflow:

```yaml
name: AI PR Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  review:
    uses: PR-REVIEW-ORGANIZATION/AI-AGENT-REPO/.github/workflows/review.yml@main
    permissions:
      contents: read
      pull-requests: read
    with:
      repository: ${{ github.repository }}
      pr_number: ${{ github.event.pull_request.number }}
      base_branch: ${{ github.event.pull_request.base.ref }}
      head_branch: ${{ github.event.pull_request.head.ref }}
      commit_sha: ${{ github.event.pull_request.head.sha }}
      agent_repository: PR-REVIEW-ORGANIZATION/AI-AGENT-REPO
      agent_ref: main
    secrets:
      GH_TOKEN: ${{ secrets.GH_TOKEN }}
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      LLM_API_URL: ${{ secrets.LLM_API_URL }}
      LLM_MODEL: ${{ secrets.LLM_MODEL }}
```

6. Open/update PR in target repo; workflow will generate artifact files for that PR.

## Testing

Run:

```bash
pytest
```

Included tests:
- `tests/test_models.py`
- `tests/test_prechecks.py`
- `tests/test_synthesizer.py`

## Limitations

- AI output quality depends on diff/context size and model behavior.
- Very large files may have missing patch/context from GitHub API.
- Current syntax heuristics are intentionally lightweight and language-limited.
- Final recommendation is safeguarded by deterministic logic and may be conservative.
