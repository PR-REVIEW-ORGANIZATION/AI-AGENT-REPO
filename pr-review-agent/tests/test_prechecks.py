"""Tests for deterministic precheck logic."""

from __future__ import annotations

from app.models import ChangedFile
from app.prechecks import detect_risk_categories, run_prechecks


def test_detect_risk_categories_for_workflow_and_dependency() -> None:
    categories = detect_risk_categories(".github/workflows/review.yml")
    assert "workflow" in categories

    dep_categories = detect_risk_categories("requirements.txt")
    assert "dependency" in dep_categories


def test_prechecks_detect_missing_patch_and_conflicts() -> None:
    files = [
        ChangedFile(
            filename="src/auth/login.py",
            status="modified",
            additions=10,
            deletions=2,
            changes=12,
            patch="<<<<<<< HEAD\n+print('a')\n=======\n+print('b')\n>>>>>>>",
        ),
        ChangedFile(
            filename="src/service.py",
            status="modified",
            additions=8,
            deletions=3,
            changes=11,
            patch=None,
            is_binary=False,
        ),
    ]
    result = run_prechecks(files, head_contents={"src/auth/login.py": "print('ok')", "src/service.py": "print('x')"})
    codes = {issue.code for issue in result.issues}
    assert "merge_conflict_marker" in codes
    assert "missing_patch" in codes
    assert "src/auth/login.py" in result.risky_files


def test_prechecks_detect_python_syntax_error() -> None:
    file_item = ChangedFile(
        filename="app/bad.py",
        status="modified",
        additions=2,
        deletions=0,
        changes=2,
        patch="+def broken(:\n+    pass",
    )
    result = run_prechecks([file_item], head_contents={"app/bad.py": "def broken(:\n    pass"})
    assert any(issue.code == "python_syntax" for issue in result.issues)
