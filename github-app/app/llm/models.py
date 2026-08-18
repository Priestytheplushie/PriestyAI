from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ReviewComment(BaseModel):
    path: str = Field(description="Relative file path, e.g. src/task_vault/kv_store.py")
    line: int = Field(description="Line number in the diff where the comment applies")
    body: str = Field(description="Comment explanation and suggestion markdown block")


class ReviewResult(BaseModel):
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
    summary: str = Field(description="Teammate markdown review summary")
    comments: List[ReviewComment] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    action: Literal["CREATE", "MODIFY"]
    summary: str


class PlanStep(BaseModel):
    step_number: int
    title: str
    files: List[FileChange]
    difficulty: Literal["standard", "complex"] = "standard"


class PlanResult(BaseModel):
    is_too_broad: bool = False
    epic_explanation: Optional[str] = None
    proposed_phases: List[str] = Field(default_factory=list)
    branch_name: Optional[str] = None
    pr_title: Optional[str] = None
    pr_intro: Optional[str] = None
    pr_call_to_action: Optional[str] = None
    issue_comment: Optional[str] = None
    steps: List[PlanStep] = Field(default_factory=list)


class SubIssueItem(BaseModel):
    title: str
    body: str
    selected_labels: List[str] = Field(default_factory=list)


class DecomposeEpicResult(BaseModel):
    intro: str
    sub_issues: List[SubIssueItem]
    outro: str


class RouteResult(BaseModel):
    intent: Literal[
        "PLAN_APPROVED",
        "CHATOPS",
        "CREATE_SUB_ISSUES",
        "START_ISSUE",
        "RESOLVE_CONFLICTS",
        "CREATE_ISSUE",
        "FIX_REQUEST",
        "RUN_TESTS",
        "DEBUG_ANALYSIS",
        "GENERAL_QA",
        "SUMMARIZE",
        "NONE",
    ]
    reason: str


class TriageResult(BaseModel):
    summary: str
    selected_labels: List[str] = Field(default_factory=list)


class IssueTriageResult(BaseModel):
    is_duplicate: bool = False
    duplicate_issue_number: Optional[int] = None
    needs_info: bool = False
    followup_message: Optional[str] = None
    selected_labels: List[str] = Field(default_factory=list)
