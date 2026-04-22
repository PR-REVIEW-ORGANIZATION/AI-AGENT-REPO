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
- Secrets: `GH_TOKEN`, `OPENAI_API_KEY`, `LLM_API_URL`, `LLM_MODEL`

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
4. The target workflow passes `secrets.GITHUB_TOKEN` to reusable workflow as `GH_TOKEN` (the reusable workflow maps it to runtime env var `github_token`).
5. Open/update PR in target repo; workflow will generate artifact files for that PR.

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
