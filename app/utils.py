import re

def normalize_sender(from_value: str) -> str:
    """
    Normalize sender identifiers from Twilio / web UI into a stable key.
    Examples:
      "whatsapp:+16036607136" -> "+16036607136"
      "+1 (603) 660-7136"     -> "+16036607136"
    """
    s = (from_value or "").strip().lower()

    if s.startswith("whatsapp:"):
        s = s[len("whatsapp:"):]

    # Keep only digits and leading +
    s = re.sub(r"[^\d+]", "", s)

    return s