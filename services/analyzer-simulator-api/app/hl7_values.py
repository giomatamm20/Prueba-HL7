"""Keep structural composites, but escape delimiter/control injection in values."""
import re


def escaped(value, scalar=False):
    text = str(value)
    # Preserve valid escapes already received from HL7; encode other backslashes.
    tokens = re.split(r"(\\(?:[FSRTE]|X[0-9A-Fa-f]+)\\)", text)
    for index in range(0, len(tokens), 2):
        token = tokens[index].replace("\\", "\\E\\")
        token = token.replace("|", "\\F\\").replace("\r", "\\X0D\\").replace("\n", "\\X0A\\")
        token = token.replace("\x0b", "\\X0B\\").replace("\x1c", "\\X1C\\").replace("\x00", "\\X00\\")
        if scalar:
            token = token.replace("^", "\\S\\").replace("~", "\\R\\").replace("&", "\\T\\")
        tokens[index] = token
    return "".join(tokens)


def safe_order(order):
    return {key: escaped(value) if isinstance(value, str) else
            [escaped(item) if isinstance(item, str) else item for item in value] if isinstance(value, list) else value
            for key, value in order.items()}


def declare_utf8(message):
    """Declare UTF-8 when non-ASCII data needs it (canonical separators)."""
    if message.isascii():
        return message
    rows = message.split("\r")
    header = rows[0].split("|")
    while len(header) <= 17:
        header.append("")
    if header[17] and header[17] not in {"UNICODE UTF-8", "UTF-8"}:
        raise ValueError("Non-ASCII output requires a UTF-8 character set in MSH-18")
    header[17] = "UNICODE UTF-8"
    rows[0] = "|".join(header)
    return "\r".join(rows)
