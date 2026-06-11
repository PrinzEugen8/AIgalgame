from __future__ import annotations

import json
from typing import Any


JUDGE_SYSTEM_PROMPT = (
    "You are a JSON-only classifier. Do not reason. Do not explain. "
    "Return one compact JSON object and nothing else."
)

JUDGE_RULES = """
Pick at most one candidate id.
If not sending, selected_event_id must be "".
Foreground: send only when idle_seconds >= 30 and input_active is false.
Respect sleep, recent delivery, unread retention, and daily_limit.
next_check_after_minutes: integer 1..1440.
Output shape:
{"should_send":false,"selected_event_id":"","reason":"short reason","next_check_after_minutes":30}
"""


def build_judge_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Return the decision JSON now.\n"
                f"{JUDGE_RULES}\n"
                f"Context JSON:{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
            ),
        },
    ]
