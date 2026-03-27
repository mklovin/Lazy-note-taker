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
        time.sleep(0.3)   # stay inside Notion rate limits


# ── Block builders ────────────────────────────────────────────────────────────

def rich(text: str, bold: bool = False, italic: bool = False, color: str = "default") -> dict:
    """Single rich-text span. Notion caps text objects at 2 000 chars."""
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
    Turn the structured research JSON into a list of Notion blocks:

        ─────────────────────────────────
        📋  Summary callout
        📌  Key Takeaways  (toggle)
            • …
        [Section 1 heading]  (toggle)
            Paragraph(s)…
        [Section 2 heading]  (toggle)
            …
        📚  Further Reading  (toggle)
            • …
        🕐  Timestamp (italic, gray)
        ─────────────────────────────────
    """
    blocks: list[dict] = [divider_block()]

    # Summary
    blocks.append(callout_block(data.get("summary", ""), "📋", "blue_background"))

    # Key Takeaways toggle
    takeaway_children = [bullet_block(t) for t in data.get("key_takeaways", [])]
    if takeaway_children:
        blocks.append(toggle_block("📌  Key Takeaways", "orange", takeaway_children))

    # Sections
    for idx, section in enumerate(data.get("sections", [])):
        color = SECTION_COLORS[idx % len(SECTION_COLORS)]
        # Support multi-paragraph content separated by blank lines
        paragraphs = [p.strip() for p in section["content"].split("\n\n") if p.strip()]
        children = [paragraph_block(p) for p in paragraphs] or [paragraph_block(section["content"])]
        blocks.append(toggle_block(section["heading"], color, children))

    # Further Reading toggle
    fr_children = [bullet_block(r) for r in data.get("further_reading", [])]
    if fr_children:
        blocks.append(toggle_block("📚  Further Reading", "gray", fr_children))

    # Timestamp
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
You are a thorough research assistant. Your job is to produce a detailed, well-structured
summary on the topic below that someone can use as a personal knowledge note.

Topic: "{topic}"

Return ONLY a raw JSON object — no markdown fences, no preamble, no trailing text.

Schema:
{{
  "title": "Clean display title",
  "summary": "2–3 sentence executive summary",
  "sections": [
    {{
      "heading": "Section title",
      "content": "Detailed content. Use blank lines to separate paragraphs."
    }}
  ],
  "key_takeaways": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "further_reading": ["Book / article / search query 1", "Resource 2", "Resource 3"]
}}

Include exactly 5 sections:
1. Core Concepts
2. How It Works (or Historical Background if more relevant)
3. Practical Applications / Real-world Examples
4. Common Misconceptions
5. Related Topics & Next Steps

Each section content should be 2–4 paragraphs (150–300 words). Be educational and precise.\
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

    # Strip accidental markdown fences
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

        time.sleep(1)   # brief pause between topics

    logger.info("Research run complete")
    logger.info("─" * 60)


if __name__ == "__main__":
    main()
