# Notion Topic Researcher

Automatically researches topics you jot down in Notion and writes back a
fully formatted, collapsed knowledge note  every 3 hours via GitHub Actions.

---

## How it works

You write a topic title in Notion (Status = New)
        ↓
GitHub Actions runs every 3 hours
        ↓
Script fetches every "New" page from your database
        ↓
Claude researches each topic (Core Concepts, How It Works,
Practical Applications, Misconceptions, Related Topics)
        ↓
Results written back as collapsible toggle blocks in the same page
Status updated to "Done"

---

## 1 — Set up the Notion Database

1. Go to **notion.so** → create a new **full-page database** (not inline).
2. Add these properties:

   | Property | Type   | Notes                        |
   |----------|--------|------------------------------|
   | Name     | Title  | Your topic headline (default)|
   | Status   | Select | Add options: **New**, **Done**, **Error** |

3. Copy the **database ID** from the URL:
   `notion.so/YOUR_WORKSPACE/<DATABASE_ID>?v=...`

---

## 2 — Create a Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration** → name it (e.g. "Researcher Bot")
3. Capabilities needed: **Read content**, **Update content**, **Insert content**
4. Copy the **Internal Integration Token** (starts with `secret_`)
5. Back in your database → click **...** (top right) → **Connect to** → select your integration

---

## 3 — Get an Anthropic API Key

1. Go to [console.anthropic.com]
2. **API Keys** → **Create Key**
3. Copy it

---

## 4 — Set up the GitHub repo

1. Create a new GitHub repository
2. Push the files into it

3. Go to **Settings → Secrets and variables → Actions → New repository secret**
   and add all three:

   | Secret name        | Value                         |
   |--------------------|-------------------------------|
   | ANTHROPIC_API_KEY  | your Anthropic key            |
   | NOTION_API_KEY     | your Notion integration token |
   | NOTION_DATABASE_ID | your Notion database ID       |

---

## 5 — First run

- Go to **Actions** tab → **Research Notion Topics** → **Run workflow**
- Watch the logs — each topic takes ~10–15 seconds

After that it runs automatically every 3 hours.

---

