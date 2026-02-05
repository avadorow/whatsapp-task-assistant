import os
import json
import httpx

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()

def _build_suggest_prompt(payload: dict) -> str:
    open_items = payload.get("open_items", []) or []
    cmds = payload.get("supported_commands") or [
        "/lists", "/newlist <name>", "/use <list_id>", "/todo <text>",
        "/list", "/all", "/done <item_id>", "/suggest", "/suggest_result"
    ]

    task_lines = "\n".join(f"- {t['id']}: {t['text']}" for t in open_items) or "(none)"
    cmd_lines = "\n".join(cmds)

    return (
        "You are an advisory task assistant. Do NOT invent any IDs.\n"
        "Only use task IDs that appear in TASKS.\n"
        "No placeholders. Be concise.\n\n"
        "TASKS:\n"
        f"{task_lines}\n\n"
        "Write exactly 3 sections in this exact format:\n\n"
        "Top priorities:\n"
        "- <id>: <reason>\n"
        "- <id>: <reason>\n"
        "- <id>: <reason>\n\n"
        "Suggested work blocks (NO times, NO durations):\n"
        "- Block 1: <plan mentioning task ID(s)>\n"
        "- Block 2: <plan mentioning task ID(s)>\n"
        "- Block 3: <plan mentioning task ID(s)>\n\n"
        "Reminder:\n"
        f"{cmd_lines}\n"
    )


async def ollama_suggest(payload: dict) -> str:
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model:
        return "Ollama model not configured. Set OLLAMA_MODEL in .env to the exact name from /api/tags."

    url = f"{OLLAMA_BASE_URL}/api/generate"

    # Hard cap output. Smaller = faster.
    req = {
        "model": model,
        "prompt": _build_suggest_prompt(payload),
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_predict": 110,     
            "num_ctx": 2048,        
        },
    }

    # Don’t wait 120s—if it’s not responding, fail fast and try again.
    timeout = httpx.Timeout(45.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=req)
            r.raise_for_status()
            data = r.json()
        return (data.get("response") or "").strip() or "Suggestion engine returned empty output."
    except Exception as e:
        return f"Suggestion engine error: {type(e).__name__}: {e}"