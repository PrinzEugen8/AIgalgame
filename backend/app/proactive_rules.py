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
Foreground: send only when input_active is false and (idle_seconds >= 30, or dialogue_state is reply_waiting and idle_seconds >= 5).
Respect user_availability (sleep and future school/work routines), recent delivery, and unread retention.
proactive_frequency_preference is a soft hint only; do not mechanically cap message count.
appointment + on_time + due: must send (user explicitly asked for this time).
appointment + advance: send when 30-120 minutes before event_at and user is not sleeping.
appointment + follow_up: never send during busy_start..busy_end; after busy_end send a caring check-in tone.
appointment + on_time missed window: send as missed_reminder with apologetic tone.
on_time appointments may reach the user even during sleep/work routines.
news/weather/memory/schedule: prefer sending when user is available; skip if user_availability.is_unavailable unless very important.
Output shape:
{"should_send":false,"selected_event_id":"","reason":"short reason"}
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
