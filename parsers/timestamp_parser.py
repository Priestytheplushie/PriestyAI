import re
import dateparser
from datetime import datetime

def parse_timestamps(text: str) -> str:
    pattern = r'<time:\s*([^>]+)>'

    def replace_time(match):
        time_str = match.group(1).strip()
        parsed_dt = dateparser.parse(time_str, settings={'PREFER_DATES_FROM': 'future'})
        if parsed_dt:
            unix_ts = int(parsed_dt.timestamp())
            return f"<t:{unix_ts}:F> (<t:{unix_ts}:R>)"
        return match.group(0)

    return re.sub(pattern, replace_time, text)