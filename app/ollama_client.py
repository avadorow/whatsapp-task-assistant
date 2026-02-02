import os
import json
import httpx

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()

def _build_suggest_prompt(payload: dict) -> str:
    # Extract valid IDs to explicitly constrain the model
    open_items = payload.get("open_items", [])
    valid_ids = [str(x.get("id")) for x in open_items if "id" in x]

def _build_suggest_prompt(payload: dict) -> str:
    open_items = payload.get("open_items", [])
    task_lines = "\n".join(
        [f"- {t['id']}: {t['text']}" for t in open_items]
    ) or "(none)"

    return (
        "You are an advisory task assistant.\n\n"
        "RULES (you must follow ALL of these):\n"
        "1) You MUST ONLY reference task IDs that appear in the task list below.\n"
        "2) Never invent task IDs, list IDs, names, or example values.\n"
        "3) Do NOT use placeholders such as '...', 'TBD', or 'etc.'.\n"
        "4) Be concise and concrete.\n"
        "5) In the Reminder section, you must print the command list EXACTLY as provided. "
        "Do not add example arguments or extra text.\n"
        "6) Each schedule line must explicitly mention the task ID(s) it addresses.\n"
        "7) If there are fewer than 3 tasks, list only the available ones.\n\n"
        "TASK LIST (only these IDs are valid):\n"
        f"{task_lines}\n\n"
        "OUTPUT FORMAT (use exactly this structure):\n\n"
        "Top priorities:\n"
        "- <id>: <one short reason>\n"
        "- <id>: <one short reason>\n"
        "- <id>: <one short reason>\n\n"
        "Suggested schedule:\n"
        "- Morning (2 hours): <specific plan mentioning task ID(s)>\n"
        "- Afternoon (2 hours): <specific plan mentioning task ID(s)>\n"
        "- Evening (1 hour): <specific plan mentioning task ID(s)>\n\n"
        "Reminder (print exactly these commands, no examples, no extra text):\n"
        "/lists\n"
        "/newlist <name>\n"
        "/use <list_id>\n"
        "/todo <text>\n"
        "/list\n"
        "/all\n"
        "/done <item_id>\n"
        "/suggest\n"
    )


async def ollama_suggest(payload: dict) -> str:
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model:
        return "Ollama model not configured. Set OLLAMA_MODEL in .env to the exact name from /api/tags."

    url = f"{OLLAMA_BASE_URL}/api/generate"
    req = {
        "model": model,
        "prompt": _build_suggest_prompt(payload),
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 220,
            "top_p": 0.9,
        },
    }

    timeout = httpx.Timeout(120.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=req)
            r.raise_for_status()
            data = r.json()

        return (data.get("response") or "").strip() or "Suggestion engine returned empty output."

    except Exception as e:
        return f"Suggestion engine error: {type(e).__name__}: {e}"
