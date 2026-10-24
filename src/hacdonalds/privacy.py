"""Remove credentials from public output, including echoed server text."""

import re

SENSITIVE_KEYS = {"authorization", "token", "access_token", "refresh_token", "mcd_mcp_token", "cookie", "password", "api_key"}


def redact(value, token: str = ""):
    if isinstance(value, dict):
        return {redact(str(key), token): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact(item, token)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, token) for item in value]
    if isinstance(value, str):
        if token:
            value = value.replace(token, "[REDACTED]")
        return re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value)
    return value
