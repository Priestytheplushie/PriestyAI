# PriestyAI for GitHub

[![Install GitHub App](https://img.shields.io/badge/GitHub_App-Install-blue?logo=github)](https://github.com/apps/priestyai)
[![Machine Account](https://img.shields.io/badge/Machine_User-@PriestyAI-181717?logo=github)](https://github.com/PriestyAI)

@PriestyAI is an autonomous AI teammate that works with your team as a pair programmer rather than an opaque black box. PriestyAI uses transparent planning, draft pull requests, sandboxed test verification, and human-in-the-loop review to build and maintain software collaboratively.

---

## QuickStart

Get up and running in three steps:

1. **Install the GitHub App:** Install [PriestyAI](https://github.com/apps/priestyai) on your repository or organization.
2. **Invite the Machine User:** Go to your repository **Settings** &rarr; **Collaborators** and invite `@PriestyAI` with write access. PriestyAI automatically accepts the invitation within 30 seconds.
3. **Assign Your First Issue:** Assign `@PriestyAI` to any open issue (or comment `@PriestyAI work on this`). PriestyAI will open a Draft PR with an atomic step-by-step plan for you to review.

---

## Using PriestyAI

PriestyAI integrates directly into your native GitHub workflow. You can interact with it using standard GitHub UI features (`Assignees`, `Reviewers`, reactions) or by `@mentioning` it in comments.

### 1. Autonomous Issue-to-PR Workflow

> [!TIP]
> The more context provided in an issue (reproduction steps, expected behavior, relevant file paths), the more accurate PriestyAI's plan and execution will be.

```
Assign Issue ──▶ Draft PR Opened ──▶ Review & Approve ──▶ Sandbox Verified ──▶ Ready for Review
```

1. **Assign:** Add `@PriestyAI` under **Assignees** on an issue (or comment `@PriestyAI work on this`).
2. **Review Plan:** PriestyAI analyzes your codebase, creates a clean feature branch, and opens a **Draft Pull Request** containing a checklist implementation plan.
3. **Approve:** Review the plan and approve by reacting with 👍, 🚀, or 👀, or comment `"approved"` / `"LGTM"`.
4. **Autonomous Build & Verification:** PriestyAI executes each step, verifies tests/linters inside an isolated Docker sandbox with self-healing passes, creates atomic commits, and publishes a native GitHub Check Run.
5. **Ready for Review:** Once all steps pass, PriestyAI converts the PR to **Ready for Review** and requests a final review from you.

---

### 2. Thoughtful Pull Request Reviews

Anyone on your team can request a code review from PriestyAI using GitHub's native Reviewers sidebar:

1. Add `@PriestyAI` under **Reviewers** on any open Pull Request.
2. PriestyAI inspects the diff against the base branch's `CONTRIBUTING.md`, runs the test suite, and submits a formal review with 1-click native GitHub suggestion blocks.
3. Reply to any inline comment to ask questions, explore alternatives, or discuss architectural trade-offs.
4. Apply feedback directly by commenting: `@PriestyAI fix <instructions>` or submitting a **Changes Requested** review.

---

### 3. Discussions & Community Support

PriestyAI participates in GitHub Discussions to assist maintainers and community members:

* **Auto-Answer Q&A:** When a user opens a new discussion in the **Q&A**, **Questions**, or **Help** category, PriestyAI reads the repository files to provide a code-backed teammate answer.
* **Spin Off Issues:** Tag `@PriestyAI create an issue for this` in any discussion thread to automatically open a tracking issue with appropriate repository labels.

---

## Command Cheatsheet

PriestyAI uses intent routing to understand natural developer instructions without requiring rigid slash commands:

| Goal | Example Command / Action | Where to Use |
| :--- | :--- | :--- |
| **Start Working on Issue** | Assign `@PriestyAI` or `@PriestyAI work on this` | Issues |
| **Approve Implementation Plan** | React with 👍, 🚀, 👀 or comment `"approved"` / `"LGTM"` | Draft PRs |
| **Decompose Broad Project** | `@PriestyAI split this up` or `@PriestyAI break this into sub-issues` | Issues |
| **Request Code Review** | Add `@PriestyAI` to **Reviewers** | Pull Requests |
| **Apply Review Fixes** | `@PriestyAI fix <instructions>` or submit *Changes Requested* | Pull Requests |
| **Resolve Merge Conflicts** | `@PriestyAI resolve conflicts` | Pull Requests |
| **Run Sandbox Tests** | `@PriestyAI run tests` | Pull Requests |
| **ChatOps Actions** | `@PriestyAI assign @alice`, `@PriestyAI close as completed`, `@PriestyAI squash and merge` | Issues & PRs |
| **Create Standalone Issue** | `@PriestyAI spin off an issue to track <task>` | Discussions, Issues, PRs |
| **Ask Technical Questions** | `@PriestyAI why is this failing?` or `@PriestyAI explain how this works` | Any Thread / Discussions |

---

## Key Capabilities

* **Automated Issue & PR Triage:** Analyzes incoming issues for duplicates and missing reproduction steps, labels them, and provides natural diff summaries on newly opened PRs.
* **Native Sub-Issue Decomposition:** Breaks large tasks into atomic child issues natively linked via GitHub's Sub-Issues API.
* **Self-Healing Test Loops:** Runs tests locally in Docker during implementation and fix passes, diagnosing test tracebacks up to 2 self-healing cycles.
* **True 2-Parent Merge Commits:** Resolves conflicts between the base branch and PR branch by constructing valid two-parent Git merge commits.
* **Native GitHub Check Runs:** Publishes status check results directly to PR commits (`PriestyAI Sandbox Verification`).

---

## Security & Sandboxing

* **Principle of Least Privilege:** Operates without administrative permissions; code modification requires maintainer status or author authorization.
* **Isolated Ephemeral Sandboxes:** Test suites execute in isolated Docker containers with strict timeouts and memory boundaries.
* **External Fork Protection:** Automated container execution is skipped on external untrusted forks unless explicitly authorized by a maintainer.
* **Base Branch Compliance:** Coding guidelines and rules are strictly pinned to the base branch's `CONTRIBUTING.md`.