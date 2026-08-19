import pytest
from app.llm.models import (
    DecomposeEpicResult,
    FileChange,
    IssueTriageResult,
    PlanResult,
    PlanStep,
    ReviewComment,
    ReviewResult,
    RouteResult,
    SubIssueItem,
    TriageResult,
)


def test_review_models():
    comment = ReviewComment(
        path="src/app.py",
        line=12,
        body="Use `sys.exit(0)` here:\n```suggestion\nsys.exit(0)\n```",
    )
    assert comment.path == "src/app.py"

    review = ReviewResult(
        verdict="APPROVE",
        summary="Looks great!",
        comments=[comment],
    )
    assert review.verdict == "APPROVE"
    assert len(review.comments) == 1


def test_plan_models():
    change = FileChange(path="main.py", action="MODIFY", summary="Update routes")
    step = PlanStep(
        step_number=1,
        title="Refactor routes",
        files=[change],
        difficulty="standard",
    )
    plan = PlanResult(
        is_too_broad=False,
        branch_name="feature/routes",
        pr_title="feat: update routes",
        steps=[step],
    )
    assert plan.is_too_broad is False
    assert len(plan.steps) == 1
    assert plan.steps[0].files[0].action == "MODIFY"


def test_decompose_epic_models():
    item = SubIssueItem(
        title="Phase 1: DB Schema",
        body="Part of #10",
        selected_labels=["backend"],
    )
    result = DecomposeEpicResult(
        intro="Broken down into phases:",
        sub_issues=[item],
        outro="Let me know when to start.",
    )
    assert len(result.sub_issues) == 1
    assert result.sub_issues[0].title == "Phase 1: DB Schema"


def test_route_and_triage_models():
    route = RouteResult(intent="PLAN_APPROVED", reason="Maintainer approved")
    assert route.intent == "PLAN_APPROVED"

    triage = TriageResult(
        summary="Added authentication routes",
        selected_labels=["enhancement"],
    )
    assert "enhancement" in triage.selected_labels

    issue_triage = IssueTriageResult(
        is_duplicate=True,
        duplicate_issue_number=42,
        needs_info=False,
        followup_message="Duplicate of #42",
        selected_labels=["bug"],
    )
    assert issue_triage.duplicate_issue_number == 42
