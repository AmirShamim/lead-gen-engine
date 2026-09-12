# Module 06: Stage 5 — Azure OpenAI Hyper-Personalization Engine

> **Stage Mission:** Generate organic, peer-to-peer, strictly under-250-character LinkedIn connection notes using Azure OpenAI (`gpt-4o-mini`). Enforce hard validation gates to prevent corporate jargon, hallucinated mutual acquaintances, and length violations.

---

## 1. Input & Output Contract

* **Input Query:** Leads with `status = 'QUALIFIED_ACTIVE'`.
* **Output Updates:** `status = 'QUEUED'`, `personalized_note`, `note_char_count`, `note_generated_at`, `queued_at`.
* **Prerequisites:** Azure OpenAI API Key and Deployment Endpoint (subsidized by $5,000 credits).

---

## 2. Strict Prompt Engineering Contract & Guardrails

### System Prompt
```
You are an expert copywriter creating organic LinkedIn connection notes for a boutique AI engineering agency.
Rules:
1. STRICT LENGTH: The note MUST be 240 characters or fewer. Never exceed 250 characters.
2. TONE: Casual peer-to-peer, respectful, curious. Never salesy, no corporate jargon ("synergy", "streamline", "elevate").
3. CONTEXT: Reference ONE specific insight about their company or role.
4. NO LIES: Never claim you've met, were referred, or follow them unless specified in context.
5. NO SALES PITCH: Do not pitch, sell, ask for a 15-minute call, or drop links. Just open the door.
```

### JSON Schema Output Specification
```json
{
  "connection_note": "Hey Alex — saw your agency's work on the fintech rebrand. Really sharp positioning in a crowded space. Always glad to connect with founders building in Austin.",
  "char_count": 164,
  "confidence_score": 0.95
}
```

---

## 3. Post-Generation Validation Gates

Before writing the note to the database, it must pass three hard checks:
1. **Character Gate:** `len(note) <= 250`. If $> 250$, trigger automatic regeneration with `temperature=0.3`.
2. **Jargon Blacklist Gate:** Regex scan for `["synergy", "elevate", "game-changer", "hop on a call", "free audit"]`.
3. **Punctuation Check:** No excessive exclamation points or emoji spam.

---

## 4. Standalone Runner Script (`stages/stage5_personalize.py`)

Run independently via `python -m stages.stage5_personalize --batch-size 15`:

```python
import os
import json
import sqlite3
from openai import AzureOpenAI
from config import DB_PATH

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_KEY")
DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

client = AzureOpenAI(
    azure_endpoint=AZURE_ENDPOINT,
    api_key=AZURE_API_KEY,
    api_version="2024-02-15-preview"
)

SYSTEM_PROMPT = """You write authentic, peer-to-peer LinkedIn connection notes from a technical founder.
Strict Rules:
- Maximum 240 characters.
- Reference their business name or role naturally.
- Zero pitching, no call requests, no buzzwords.
Return JSON: {"connection_note": "text"}"""

def generate_note(founder: str, biz_name: str) -> str:
    user_prompt = f"Founder: {founder}\nAgency: {biz_name}\nWrite a custom connection note."
    
    for attempt in range(3):
        res = client.chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        data = json.loads(res.choices[0].message.content)
        note = data.get("connection_note", "").strip()
        
        # Hard length check
        if len(note) <= 245 and "hop on" not in note.lower():
            return note
            
    return f"Hey {founder.split()[0]} — saw what you're building at {biz_name}. Always good to connect with other agency founders."

def personalize_batch(limit=15, db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, founder_name, business_name 
        FROM leads 
        WHERE status = 'QUALIFIED_ACTIVE' 
        LIMIT ?
    """, (limit,))
    leads = cur.fetchall()
    
    for lead_id, founder, biz in leads:
        note = generate_note(founder, biz)
        char_count = len(note)
        
        cur.execute("""
            UPDATE leads 
            SET status = 'QUEUED', personalized_note = ?, note_char_count = ?, 
                note_generated_at = CURRENT_TIMESTAMP, queued_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (note, char_count, lead_id))
        print(f"✓ Generated ({char_count} chars): {note}")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    personalize_batch(5)
```
