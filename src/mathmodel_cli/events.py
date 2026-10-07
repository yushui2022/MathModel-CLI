from __future__ import annotations

import json
import re

SESSION_ID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


class Events:
    def __init__(self):
        self.session_id = None
        self.completed = False
        self.failed = False
        self.usage: list[dict] = []
        self.event_count = 0

    def accept(self, line: str) -> None:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        self.event_count += 1
        kind = event.get("type")
        if kind == "thread.started" and SESSION_ID.fullmatch(str(event.get("thread_id", ""))):
            self.session_id = event["thread_id"]
        if kind == "turn.started":
            self.completed = False
        if kind == "turn.completed":
            self.completed = True
            usage = event.get("usage")
            if isinstance(usage, dict):
                self.usage.append({k: v for k, v in usage.items()
                                   if isinstance(v, int) and not isinstance(v, bool) and v >= 0})
        if kind == "turn.failed":
            self.failed = True


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
