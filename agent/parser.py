import re
import logging
from typing import Any
from bs4 import BeautifulSoup

logger = logging.getLogger("PriestyAI.Agent.Parser")

def parse_agent_questions_from_text(text: str) -> list[dict[str, Any]]:
    questions = []
    q_matches = re.finditer(
        r'<question\s+id=["\']([^"\'\n>]+)["\']\s+label=["\']([^"\'\n>]+)["\']\s*>(.*?)</question>',
        text,
        re.DOTALL | re.IGNORECASE
    )

    for m in q_matches:
        q_id = m.group(1).strip()
        q_label = m.group(2).strip()
        body = m.group(3).strip()

        options = []
        opt_matches = re.finditer(
            r'<option\s+value=["\']([^"\'\n>]+)["\']\s+label=["\']([^"\'\n>]+)["\'](?:\s+description=["\']([^"\'\n>]+)["\'])?\s*\/?>',
            body,
            re.IGNORECASE
        )
        for opt in opt_matches:
            options.append({
                "value": opt.group(1).strip(),
                "label": opt.group(2).strip(),
                "description": (opt.group(3) or "").strip()
            })

        if options:
            questions.append({
                "id": q_id,
                "label": q_label,
                "options": options
            })

    return questions

def parse_finalize_artifact(text: str) -> tuple[str, str]:
    fn_match = re.search(r'<finalize_artifact\s+[^>]*(?:filename|name)=["\']([^"\'\n>]+)["\']', text, re.IGNORECASE)
    title_match = re.search(r'<finalize_artifact\s+[^>]*title=["\']([^"\'\n>]+)["\']', text, re.IGNORECASE)
    
    filename = fn_match.group(1).strip() if fn_match else ""
    title = title_match.group(1).strip() if title_match else ""
    return filename, title

def parse_agent_citations_from_text(text: str) -> list[str]:
    match = re.search(r'<citations>(.*?)</citations>', text, re.DOTALL | re.IGNORECASE)
    if not match:
        return []

    body = match.group(1).strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    citations = []

    for line in lines:
        clean_line = re.sub(r'^[•\-\*]\s*', '', line).strip()
        if clean_line:
            citations.append(clean_line)

    return citations

def extract_citations_from_html_or_markdown(content: str) -> list[str]:
    if not content or not content.strip():
        return []

    citations: list[str] = []

    if "<html" in content.lower() or "<div" in content.lower() or "<ol" in content.lower():
        try:
            soup = BeautifulSoup(content, "html.parser")
            ref_div = soup.find(class_=re.compile(r'(?:references|citations|footer-citations)', re.I)) or soup.find("ol")
            if ref_div:
                for idx, li in enumerate(ref_div.find_all("li")):
                    a_tag = li.find("a")
                    link_url = a_tag.get("href", "") if a_tag else ""
                    link_text = a_tag.get_text().strip() if a_tag else ""
                    full_text = li.get_text().strip()

                    if link_url and link_text:
                        desc = full_text.replace(link_text, "").strip(" —-:")
                        desc_str = f" — {desc}" if desc else ""
                        citations.append(f"[{idx + 1}] [{link_text}]({link_url}){desc_str}")
                    elif full_text:
                        citations.append(f"[{idx + 1}] {full_text}")
        except Exception as e:
            logger.debug(f"Failed to scrape HTML references: {e}")

    if not citations:
        md_matches = re.findall(r'\[(\d+)\]\s*\[([^\]]+)\]\(([^\)]+)\)(?:\s*[—\-:]\s*([^\n]+))?', content)
        for m in md_matches:
            num, title, url, desc = m
            desc_part = f" — {desc.strip()}" if desc else ""
            citations.append(f"[{num}] [{title}]({url}){desc_part}")

    return citations

def strip_agent_xml_tags(text: str) -> str:
    cleaned = re.sub(r'<question\s+[^>]*>.*?</question>', '', text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<citations>.*?</citations>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<finalize_artifact\s*[^>]*\/?>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<artifact\s+[^>]*>.*?</artifact>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()