import re
import time
import logging
from typing import Optional
import dateparser

logger = logging.getLogger("PriestyAI.Timestamps")

class TimestampParser:
    RELATIVE_TIME_REGEX = re.compile(
        r"\b(in \d+ (?:minute|hour|day|week|month|year)s?|tomorrow at \d+(?::\d+)?\s*(?:am|pm)?|next (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
        re.IGNORECASE
    )

    @classmethod
    def convert_relative_dates(cls, text: str) -> str:
        def replace_match(match: re.Match) -> str:
            raw_expr = match.group(0)
            parsed_dt = dateparser.parse(raw_expr, settings={"PREFER_DATES_FROM": "future"})
            if parsed_dt:
                unix_ts = int(parsed_dt.timestamp())
                return f"<t:{unix_ts}:R>"
            return raw_expr

        return cls.RELATIVE_TIME_REGEX.sub(replace_match, text)

    @staticmethod
    def to_discord_timestamp(unix_time: int, style: str = "R") -> str:
        return f"<t:{unix_time}:{style}>"