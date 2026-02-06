import re

def normalize_sender(from_value: str) -> str:
    s = (from_value or "").strip().lower()

    if s.startswith("whatsapp:"):
        s = s[len("whatsapp:"):]

    s = re.sub(r"[^\d+]", "", s)

    # If it's digits only, assume US and add +
    if s and not s.startswith("+"):
        s = "+" + s

    return s
