"""Normalize segment line endings without decoding clinical HL7 escape sequences."""
import re


def normalize_hl7(raw: str) -> str:
    text = raw.replace("\r\n", "\r").replace("\n", "\r").strip("\ufeff \t\r\n")
    if text.startswith("MSH") and len(text) > 3:
        separator = re.escape(text[3])
        # Pasted logs sometimes contain literal \\r / \\n or /r between segments.
        # Restrict conversion to segment boundaries; preserve URLs and HL7 \\R\\.
        endings = r"(?:\\r\\n|\\r|\\n|/r/n|/r|/n)"
        text = re.sub(endings + rf"(?=[A-Z0-9]{{3}}{separator})", "\r", text)
        text = re.sub(endings + r"$", "\r", text)
    return text.strip("\r") + "\r" if text else ""
