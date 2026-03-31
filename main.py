import os
import json
import time
import logging
import anthropic
import requests
from datetime import datetime, timezone

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config from environment ───────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
NOTION_API_KEY     = os.environ["NOTION_API_KEY"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

NOTION_BASE    = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Notion helpers ────────────────────────────────────────────────────────────

def get_new_topics() -> list[dict]:
    """Return all database pages whose Status = New."""
    url = f"{NOTION_BASE}/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Status",
            "select": {"equals": "New"},
        }
    }
    resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_page_title(page: dict) -> str:
    """Pull the plain-text title out of a Notion page object."""
    props = page.get("properties", {})
    title_list = props.get("Name", {}).get("title", [])
    if title_list:
        return title_list[0].get("plain_text", "").strip()
    return ""


def update_page_status(page_id: str, status: str) -> None:
    """Set the Status select property on a page (New / Done / Error)."""
    url = f"{NOTION_BASE}/pages/{page_id}"
    resp = requests.patch(
        url,
        headers=NOTION_HEADERS,
        json={"properties": {"Status": {"select": {"name": status}}}},
    )
    resp.raise_for_status()


def append_blocks(page_id: str, blocks: list[dict]) -> None:
    """Append blocks to a page, batching at 100 (Notion's limit)."""
    url = f"{NOTION_BASE}/blocks/{page_id}/children"
    for i in range(0, len(blocks), 100):
        resp = requests.patch(
            url,
            headers=NOTION_HEADERS,
            json={"children": blocks[i : i + 100]},
        )
        resp.raise_for_status()
        time.sleep(0.3)


# ── Block builders ────────────────────────────────────────────────────────────

def rich(text: str, bold: bool = False, italic: bool = False, color: str = "default") -> dict:
    """Single rich-text span."""
    return {
        "type": "text",
        "text": {"content": text[:2000]},
        "annotations": {"bold": bold, "italic": italic, "color": color},
    }


def paragraph_block(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [rich(text)]}}


def bullet_block(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [rich(text)]}}


def toggle_block(label: str, color: str, children: list[dict]) -> dict:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [rich(label, bold=True, color=color)],
            "children": children,
        },
    }


def callout_block(text: str, emoji: str, bg: str) -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [rich(text)],
            "icon": {"emoji": emoji},
            "color": bg,
        },
    }


def divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


SECTION_COLORS = ["purple", "green", "blue", "red", "orange", "pink", "gray"]


def build_blocks(data: dict) -> list[dict]:
    """
    Notion page structure:

        ─────────────────────────────────
        🎯  30-second answer  (callout)
        📌  Key Takeaways     (toggle)
        [Section 1]           (toggle)
        [Section 2]           (toggle)
        [Section 3]           (toggle)
        [Section 4]           (toggle)
        📚  Further Reading   (toggle)
        🕐  Timestamp
        ─────────────────────────────────
    """
    blocks: list[dict] = [divider_block()]

    # 30-second interview answer callout
    blocks.append(callout_block(data.get("summary", ""), "🎯", "green_background"))

    takeaway_children = [bullet_block(t) for t in data.get("key_takeaways", [])]
    if takeaway_children:
        blocks.append(toggle_block("📌  Key Takeaways", "orange", takeaway_children))

    for idx, section in enumerate(data.get("sections", [])):
        color = SECTION_COLORS[idx % len(SECTION_COLORS)]
        paragraphs = [p.strip() for p in section["content"].split("\n\n") if p.strip()]
        children = [paragraph_block(p) for p in paragraphs] or [paragraph_block(section["content"])]
        blocks.append(toggle_block(section["heading"], color, children))

    fr_children = [bullet_block(r) for r in data.get("further_reading", [])]
    if fr_children:
        blocks.append(toggle_block("📚  Further Reading", "gray", fr_children))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [rich(f"🕐  Researched on {ts}", italic=True, color="gray")]
        },
    })

    blocks.append(divider_block())
    return blocks


# ── Claude research ───────────────────────────────────────────────────────────

RESEARCH_PROMPT = """\
You are a senior software engineer and technical interviewer with deep expertise across \
software architecture, backend systems, and modern development practices.

Your job is to prepare a thorough, interview-ready knowledge note on the topic below. \
The person reading this is a software engineer who wants to deeply understand the topic \
so they can answer interview questions confidently and apply the concept in real projects.

Topic: "{topic}"

Return ONLY a raw JSON object — no markdown fences, no preamble, no trailing text.

Schema:
{{
  "title": "Clean display title",
  "summary": "A 2-3 sentence answer you could give in the first 30 seconds of an interview. Clear, confident, and direct.",
  "sections": [
    {{
      "heading": "Section title",
      "content": "Detailed content. Use blank lines to separate paragraphs."
    }}
  ],
  "key_takeaways": [
    "Concise point an interviewer would love to hear",
    "Another strong point",
    "Another strong point",
    "Another strong point",
    "Another strong point"
  ],
  "further_reading": ["Resource 1", "Resource 2", "Resource 3"]
}}

Include exactly 4 sections in this order:

1. Core Concepts
   Explain the fundamental idea clearly. Define terms. Cover the why, not just the what.
   Write as if explaining to someone smart who has not used it before.

2. How It Works Under the Hood
   Go deeper — internals, mechanics, lifecycle, memory, threading, whatever is relevant.
   This is what separates a junior from a senior answer in an interview.

3. When to Use It (and When Not To)
   Real trade-offs. Compare with alternatives. When is this the right tool?
   When would you avoid it and why? Give concrete scenarios.

4. Common Interview Questions & Strong Answers
   Write 3 interview questions on this topic with strong, detailed answers.
   Format each as:
   Q: [question]
   A: [answer — 3 to 5 sentences, specific and technical]

Each section should be 200-350 words. Be technical, precise, and opinionated where appropriate.\
"""


def research_topic(topic: str) -> dict:
    """Ask Claude to research the topic and return parsed JSON."""
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": RESEARCH_PROMPT.format(topic=topic)}
        ],
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    return json.loads(raw)


# ── Orchestration ─────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("─" * 60)
    logger.info("Research run started")

    pages = get_new_topics()
    logger.info(f"New topics found: {len(pages)}")

    if not pages:
        logger.info("Nothing to do — exiting.")
        return

    for page in pages:
        page_id = page["id"]
        topic   = get_page_title(page)

        if not topic:
            logger.warning(f"Page {page_id} has no title — skipping")
            continue

        logger.info(f"  → Researching: {topic!r}")

        try:
            data   = research_topic(topic)
            blocks = build_blocks(data)
            append_blocks(page_id, blocks)
            update_page_status(page_id, "Done")
            logger.info(f"  ✅ Done: {topic!r}")

        except json.JSONDecodeError as exc:
            logger.error(f"  ❌ Claude returned invalid JSON for {topic!r}: {exc}")
            update_page_status(page_id, "Error")

        except requests.HTTPError as exc:
            logger.error(f"  ❌ Notion API error for {topic!r}: {exc.response.text}")
            update_page_status(page_id, "Error")

        except Exception as exc:
            logger.error(f"  ❌ Unexpected error for {topic!r}: {exc}")
            update_page_status(page_id, "Error")

        time.sleep(1)

    logger.info("Research run complete")
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
