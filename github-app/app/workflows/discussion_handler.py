import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.discussions")


async def handle_discussion_opened(payload: Dict[str, Any]) -> None:
    discussion = payload.get("discussion", {})
    discussion_id = discussion.get("node_id") or discussion.get("id")
    title = discussion.get("title", "")
    body = discussion.get("body", "")
    category = discussion.get("category", {}).get("name", "")
    sender = payload.get("sender", {}).get("login", "")
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    installation_id = payload.get("installation", {}).get("id")

    if sender == settings.BOT_USERNAME:
        return

    bot_tag = f"@{settings.BOT_USERNAME}".lower()
    is_mentioned = bot_tag in body.lower()
    is_qa = category.lower() in ("q&a", "q & a", "questions", "help")

    if not is_mentioned and not is_qa:
        logger.info(
            f"Discussion #{discussion.get('number')} opened in category '{category}'. Skipping auto-response."
        )
        return

    logger.info(
        f"Triaging new discussion #{discussion.get('number')} ('{title}') in category '{category}'..."
    )

    app_client = AppInstallationClient(installation_id)
    default_branch_info = await app_client.get_default_branch_sha(owner, repo)
    base_sha = default_branch_info["sha"]

    file_tree = await app_client.get_repository_tree(owner, repo, base_sha)
    readme = await app_client.get_file_content(owner, repo, "README.md", base_sha) or ""

    qa_prompt = f"""You are PriestyAI, an engineer teammate participating in a GitHub Discussion.
User @{sender} started a new discussion in the '{category}' category.
Tone: Friendly, helpful developer. Answer directly with code snippets or instructions where appropriate. No emoji spam, no robotic boilerplate.

DISCUSSION TITLE: {title}
DISCUSSION BODY:
{body}

REPOSITORY CONTEXT:
Files: {file_tree[:50]}
README:
{readme[:1200]}

INSTRUCTIONS:
1. Provide a direct, helpful technical answer based on the actual repository files.
2. If this is a bug report that belongs in Issues, politely offer to track it in an issue.
3. Keep the response concise, clear, and actionable in natural markdown.
"""

    answer_text = await llm_client.generate(
        prompt=qa_prompt,
        system_prompt="You are a collaborative senior developer helping community members in GitHub Discussions.",
        model_tier="routing",
    )

    if discussion_id:
        await machine_client.add_discussion_comment(
            discussion_id=discussion_id, body=answer_text
        )
        logger.info(
            f"Successfully posted answer to Discussion #{discussion.get('number')}"
        )


async def handle_discussion_comment(payload: Dict[str, Any]) -> None:
    comment = payload.get("comment", {})
    body = comment.get("body", "")
    sender = payload.get("sender", {}).get("login", "")
    discussion = payload.get("discussion", {})
    discussion_id = discussion.get("node_id") or discussion.get("id")
    discussion_number = discussion.get("number")
    comment_node_id = comment.get("node_id")
    parent_id = comment.get("parent_id")
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    installation_id = payload.get("installation", {}).get("id")

    if sender == settings.BOT_USERNAME:
        return

    bot_tag = f"@{settings.BOT_USERNAME}".lower()
    is_explicitly_tagged = bot_tag in body.lower()

    has_other_mentions = bool(
        re.search(
            r"@(?!" + re.escape(settings.BOT_USERNAME) + r"\b)\w+",
            body,
            re.IGNORECASE,
        )
    )

    if has_other_mentions and not is_explicitly_tagged:
        return

    reply_target_node_id = comment_node_id
    if parent_id is not None:

        root_node_id = await get_discussion_comment_node_id(
            owner, repo, discussion_number, parent_id
        )
        if root_node_id:
            reply_target_node_id = root_node_id

    logger.info(
        f"Processing discussion thread reply from '{sender}' in Discussion #{discussion_number} (target: {reply_target_node_id})..."
    )

    app_client = AppInstallationClient(installation_id)
    default_branch_info = await app_client.get_default_branch_sha(owner, repo)
    base_sha = default_branch_info["sha"]

    file_tree = await app_client.get_repository_tree(owner, repo, base_sha)
    available_labels = await app_client.get_repo_labels(owner, repo)

    router_prompt = f"""You are an intent router for a GitHub AI teammate named @{settings.BOT_USERNAME} in a GitHub Discussion thread.

USER COMMENT:
\"{body}\"

DISCUSSION TITLE: {discussion.get('title')}

POSSIBLE INTENTS:
- CREATE_ISSUE: The user is asking to create or track an issue from this discussion (e.g. "create an issue for this", "spin off an issue").
- GENERAL_QA: The user is asking a follow-up question, seeking code examples, or continuing the conversation.
- SUMMARIZE: The user wants a summary of the discussion.
- NONE: Spam, troll, insults, or meaningless comment.

Return JSON:
{{
  "intent": "CREATE_ISSUE" | "GENERAL_QA" | "SUMMARIZE" | "NONE",
  "reason": "short explanation"
}}
"""

    route_res = await llm_client.generate_json(
        prompt=router_prompt,
        system_prompt="You are a fast intent router for discussions. Output valid JSON only.",
        model_tier="routing",
    )

    intent = route_res.get("intent", "GENERAL_QA")
    logger.info(f"Discussion comment intent: '{intent}'")

    if intent == "CREATE_ISSUE":
        issue_prompt = f"""Write a clear, concise Issue based on this discussion request from @{sender}.
User request: "{body}"
Discussion Title: {discussion.get('title')}
Discussion Body: {discussion.get('body')}

Return JSON:
{{
  "title": "feat: <title> or fix: <title>",
  "body": "Markdown issue description linking to discussion #{discussion_number}...",
  "selected_labels": ["enhancement"],
  "reply_text": "I've created Issue #ISSUE_NUMBER to track this from our discussion!"
}}
"""
        issue_res = await llm_client.generate_json(
            prompt=issue_prompt,
            system_prompt="You are a senior developer writing clear GitHub issues.",
            model_tier="routing",
        )
        title = issue_res.get(
            "title", f"Follow-up from Discussion #{discussion_number}"
        )
        issue_body = issue_res.get(
            "body", f"Spun off from Discussion #{discussion_number}"
        )
        selected_labels = [
            l for l in issue_res.get("selected_labels", []) if l in available_labels
        ]
        reply_template = issue_res.get(
            "reply_text", "I've opened Issue #ISSUE_NUMBER to track this."
        )

        new_issue = await machine_client.create_issue(
            owner=owner,
            repo=repo,
            title=title,
            body=issue_body,
            labels=selected_labels if selected_labels else None,
        )
        new_issue_number = new_issue["number"]
        reply_text = reply_template.replace("#ISSUE_NUMBER", f"#{new_issue_number}")

        if discussion_id:
            if reply_target_node_id:
                await machine_client.add_discussion_reply(
                    discussion_id=discussion_id,
                    reply_to_id=reply_target_node_id,
                    body=reply_text,
                )
            else:
                await machine_client.add_discussion_comment(
                    discussion_id=discussion_id, body=reply_text
                )

    elif intent in ("GENERAL_QA", "SUMMARIZE"):
        answer_prompt = f"""You are PriestyAI, an engineer teammate continuing a conversation in a GitHub Discussion thread.
User @{sender} replied in the discussion thread.
Tone: Friendly, conversational teammate. Answer directly and concisely with clear code examples where appropriate.

DISCUSSION TITLE: {discussion.get('title')}
DISCUSSION OP:
{discussion.get('body')}

LATEST REPLY FROM @{sender}:
\"{body}\"

FILES IN REPO:
{file_tree[:40]}
"""
        answer_text = await llm_client.generate(
            prompt=answer_prompt,
            system_prompt="You are a senior developer teammate. Speak naturally.",
            model_tier="routing",
        )

        if discussion_id:
            if reply_target_node_id:

                await machine_client.add_discussion_reply(
                    discussion_id=discussion_id,
                    reply_to_id=reply_target_node_id,
                    body=answer_text,
                )
            else:
                await machine_client.add_discussion_comment(
                    discussion_id=discussion_id, body=answer_text
                )


async def get_discussion_comment_node_id(
    owner: str, repo: str, discussion_number: int, database_id: int
) -> Optional[str]:
    query = """
    query($owner: String!, $repo: String!, $num: Int!) {
      repository(owner: $owner, name: $repo) {
        discussion(number: $num) {
          comments(first: 50) {
            nodes {
              id
              databaseId
            }
          }
        }
      }
    }
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.github.com/graphql",
            headers=machine_client.headers,
            json={
                "query": query,
                "variables": {"owner": owner, "repo": repo, "num": discussion_number},
            },
        )
        data = resp.json()
        nodes = (
            data.get("data", {})
            .get("repository", {})
            .get("discussion", {})
            .get("comments", {})
            .get("nodes", [])
        )
        for node in nodes:
            if node.get("databaseId") == database_id:
                return node.get("id")
    return None
