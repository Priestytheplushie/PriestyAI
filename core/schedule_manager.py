import os
import re
import time
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
import dateparser
import discord
from google.genai import types

from core.client_manager import client_manager
from core.config_manager import config_manager
from core.branch_manager import branch_manager
from tools.registry import tool_registry, ToolExecutionContext
from parsers.artifact_parser import ArtifactStreamParser
from handlers.stream_handler import DiscordStreamDispatcher
from agent.constants import OCTICONS_MAP
from core.engine import SYSTEM_INSTRUCTION_TEMPLATE

logger = logging.getLogger("PriestyAI.ScheduleManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

DAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6
}

def parse_schedule_time_expression(raw_expr: str) -> tuple[int, str, int, str]:
    expr = raw_expr.strip().lower()
    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    interval_match = re.match(r'^every\s+(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days)$', expr)
    if interval_match:
        qty = int(interval_match.group(1))
        unit = interval_match.group(2)
        
        if unit.startswith("m"):
            sec = qty * 60
            unit_name = "minute" if qty == 1 else "minutes"
        elif unit.startswith("h"):
            sec = qty * 3600
            unit_name = "hour" if qty == 1 else "hours"
        else:
            sec = qty * 86400
            unit_name = "day" if qty == 1 else "days"

        if sec < 300:
            sec = 300

        next_ts = now_ts + sec
        return next_ts, "interval", sec, f"Every {qty} {unit_name}"

    daily_match = re.match(r'^(?:every\s+day|daily|everyday)(?:\s+at\s+(.+))?$', expr)
    if daily_match:
        time_part = daily_match.group(1) or "08:00"
        parsed_time = dateparser.parse(time_part, settings={'TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})
        if not parsed_time:
            parsed_time = dateparser.parse(f"today at {time_part}", settings={'TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})

        if parsed_time:
            target_dt = datetime(
                year=now.year, month=now.month, day=now.day,
                hour=parsed_time.hour, minute=parsed_time.minute, second=0,
                tzinfo=timezone.utc
            )
            if target_dt <= now:
                target_dt += timedelta(days=1)
            
            next_ts = int(target_dt.timestamp())
            time_str = target_dt.strftime("%I:%M %p UTC")
            return next_ts, "daily", 86400, f"Daily at {time_str}"

    weekly_match = re.match(r'^every\s+([a-zA-Z]+)(?:\s+at\s+(.+))?$', expr)
    if weekly_match:
        day_str = weekly_match.group(1).lower()
        time_part = weekly_match.group(2) or "09:00"

        if day_str in DAY_MAP:
            target_weekday = DAY_MAP[day_str]
            parsed_time = dateparser.parse(time_part, settings={'TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True})
            target_hour = parsed_time.hour if parsed_time else 9
            target_min = parsed_time.minute if parsed_time else 0

            days_ahead = (target_weekday - now.weekday()) % 7
            target_dt = datetime(
                year=now.year, month=now.month, day=now.day,
                hour=target_hour, minute=target_min, second=0,
                tzinfo=timezone.utc
            ) + timedelta(days=days_ahead)

            if target_dt <= now:
                target_dt += timedelta(days=7)

            next_ts = int(target_dt.timestamp())
            day_name = day_str.capitalize()
            time_str = target_dt.strftime("%I:%M %p UTC")
            return next_ts, "weekly", 604800, f"Every {day_name} at {time_str}"

    parsed_dt = dateparser.parse(
        raw_expr,
        settings={
            'PREFER_DATES_FROM': 'future',
            'TIMEZONE': 'UTC',
            'RETURN_AS_TIMEZONE_AWARE': True
        }
    )
    if parsed_dt:
        next_ts = int(parsed_dt.timestamp())
        if next_ts > now_ts:
            return next_ts, "once", 0, "One-Time"

    raise ValueError(f"Could not parse '{raw_expr}'. Try natural expressions like 'in 2 hours', 'tomorrow at 9am', 'every day at 8am', or 'every friday at 12pm'.")


class ScheduleManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    guild_id TEXT,
                    channel_id TEXT,
                    scope TEXT DEFAULT 'personal',
                    prompt_text TEXT NOT NULL,
                    time_expression TEXT NOT NULL,
                    summary_schedule TEXT NOT NULL,
                    next_run_timestamp INTEGER NOT NULL,
                    interval_type TEXT DEFAULT 'once',
                    interval_seconds INTEGER DEFAULT 0,
                    dm_delivery TEXT DEFAULT 'channel_only',
                    is_active INTEGER DEFAULT 1,
                    last_run_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_tasks(next_run_timestamp, is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sched_guild ON scheduled_tasks(guild_id, scope)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sched_user ON scheduled_tasks(user_id, scope)")
            conn.commit()

    def create_task(
        self,
        task_id: str,
        user_id: str | int,
        user_name: str,
        guild_id: str | int | None,
        channel_id: str | int | None,
        scope: str,
        prompt_text: str,
        time_expression: str,
        summary_schedule: str,
        next_run_timestamp: int,
        interval_type: str = "once",
        interval_seconds: int = 0,
        dm_delivery: str = "channel_only"
    ) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scheduled_tasks (
                    task_id, user_id, user_name, guild_id, channel_id, scope,
                    prompt_text, time_expression, summary_schedule, next_run_timestamp,
                    interval_type, interval_seconds, dm_delivery, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                str(task_id),
                str(user_id),
                user_name.strip(),
                str(guild_id) if guild_id else None,
                str(channel_id) if channel_id else None,
                scope.lower().strip(),
                prompt_text.strip(),
                time_expression.strip(),
                summary_schedule.strip(),
                int(next_run_timestamp),
                interval_type,
                int(interval_seconds),
                dm_delivery
            ))
            conn.commit()

        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_tasks WHERE task_id = ?", (str(task_id),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_tasks_for_guild(self, guild_id: str | int) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scheduled_tasks WHERE guild_id = ? AND scope = 'server' AND is_active = 1 ORDER BY next_run_timestamp ASC",
                (str(guild_id),)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_tasks_for_user(self, user_id: str | int) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scheduled_tasks WHERE user_id = ? AND scope = 'personal' AND is_active = 1 ORDER BY next_run_timestamp ASC",
                (str(user_id),)
            )
            return [dict(r) for r in cursor.fetchall()]

    def update_task(self, task_id: str, **kwargs) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False

        task.update(kwargs)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks
                SET prompt_text = ?, time_expression = ?, summary_schedule = ?,
                    next_run_timestamp = ?, interval_type = ?, interval_seconds = ?,
                    channel_id = ?, dm_delivery = ?, is_active = ?
                WHERE task_id = ?
            """, (
                task.get("prompt_text"),
                task.get("time_expression"),
                task.get("summary_schedule"),
                int(task.get("next_run_timestamp")),
                task.get("interval_type"),
                int(task.get("interval_seconds", 0)),
                str(task.get("channel_id")) if task.get("channel_id") else None,
                task.get("dm_delivery", "channel_only"),
                int(bool(task.get("is_active", 1))),
                str(task_id)
            ))
            conn.commit()
            return cursor.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (str(task_id),))
            conn.commit()
            return cursor.rowcount > 0

    async def schedule_watchdog_tick(self, bot: discord.Client):
        now_ts = int(time.time())
        due_tasks = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scheduled_tasks WHERE next_run_timestamp <= ? AND is_active = 1",
                (now_ts,)
            )
            due_tasks = [dict(r) for r in cursor.fetchall()]

        if not due_tasks:
            return

        for task in due_tasks:
            asyncio.create_task(self._execute_due_task(bot, task))

    async def _execute_due_task(self, bot: discord.Client, task: dict[str, Any]):
        task_id = task["task_id"]
        scope = task.get("scope", "personal")
        prompt = task["prompt_text"]
        user_id = int(task["user_id"])
        dm_delivery = task.get("dm_delivery", "channel_only")
        summary_sched = task.get("summary_schedule", "Scheduled")
        interval_type = task.get("interval_type", "once")
        interval_sec = int(task.get("interval_seconds", 0))

        logger.info(f"[ScheduleWatchdog] Executing {scope} task #{task_id} ('{prompt[:40]}...')")

        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())

        if interval_type == "once":
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE scheduled_tasks SET is_active = 0, last_run_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                    (task_id,)
                )
                conn.commit()
        else:
            if interval_type == "daily":
                next_ts = now_ts + 86400
            elif interval_type == "weekly":
                next_ts = now_ts + 604800
            elif interval_type == "interval" and interval_sec > 0:
                next_ts = now_ts + interval_sec
            else:
                next_ts = now_ts + 86400

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE scheduled_tasks SET next_run_timestamp = ?, last_run_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                    (next_ts, task_id)
                )
                conn.commit()

        target_channel: discord.abc.Messageable | None = None
        target_guild: discord.Guild | None = None
        user_obj: discord.User | discord.Member | None = None

        if task.get("guild_id"):
            try:
                g_id = int(task["guild_id"])
                target_guild = bot.get_guild(g_id)
            except Exception:
                pass

        if task.get("channel_id") and scope == "server":
            try:
                c_id = int(task["channel_id"])
                target_channel = bot.get_channel(c_id) or await bot.fetch_channel(c_id)
            except Exception as ex:
                logger.warning(f"[ScheduleWatchdog] Could not fetch channel #{task.get('channel_id')}: {ex}")

        try:
            user_obj = bot.get_user(user_id) or await bot.fetch_user(user_id)
        except Exception:
            user_obj = None

        effective_channel_id = getattr(target_channel, "id", None)
        resolved_cfg = config_manager.resolve_effective_config(
            task.get("guild_id"),
            effective_channel_id,
            user_id
        )

        tool_context = ToolExecutionContext(
            channel=target_channel or (user_obj if user_obj else None),
            guild=target_guild,
            author=user_obj,
            bot=bot
        )
        tool_declarations = tool_registry.get_tool_declarations(disabled_tools=resolved_cfg.get("disabled_tools", []))

        current_date_str = now.strftime("%A, %B %d, %Y")
        current_year_str = str(now.year)
        custom_instructions = resolved_cfg.get("combined_system_prompt", "")
        preferred_name_note = f"\nThe user's preferred name is '{resolved_cfg['preferred_name']}'. Address them by this name." if resolved_cfg.get("preferred_name") else ""

        formatted_sys_instruction = (
            SYSTEM_INSTRUCTION_TEMPLATE
            .replace("{current_date}", current_date_str)
            .replace("{current_year}", current_year_str)
            .replace("<@BOT_ID>", f"<@{bot.user.id}>")
        )
        if custom_instructions:
            formatted_sys_instruction += f"\n\n[Active Server/Channel Directives]:\n{custom_instructions}"
        if preferred_name_note:
            formatted_sys_instruction += preferred_name_note

        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash", fallback=True)
        if not client:
            logger.error("[ScheduleWatchdog] AI client unavailable to generate scheduled output.")
            return

        stream_dispatcher = DiscordStreamDispatcher(
            guild=target_guild,
            target_channel=target_channel or (user_obj if user_obj else None),
            show_reply_button=False
        )
        artifact_parser = ArtifactStreamParser(
            stream_dispatcher,
            tool_context,
            channel_id=getattr(target_channel, "id", "global")
        )

        conversation_contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        try:
            for turn in range(5):
                config = types.GenerateContentConfig(
                    system_instruction=formatted_sys_instruction,
                    tools=tool_declarations,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    temperature=0.7
                )

                res = await client.aio.models.generate_content(
                    model=active_model,
                    contents=conversation_contents,
                    config=config
                )

                model_parts = []
                fcalls = []

                if res.candidates and res.candidates[0].content:
                    for part in res.candidates[0].content.parts:
                        model_parts.append(part)
                        if part.text:
                            await artifact_parser.feed(part.text)
                        elif part.function_call:
                            fcalls.append(part.function_call)

                if model_parts:
                    conversation_contents.append(types.Content(role="model", parts=model_parts))

                if not fcalls:
                    break

                fres_parts = []
                for fc in fcalls:
                    f_name = fc.name
                    f_args = dict(fc.args) if fc.args else {}
                    tool_res = await tool_registry.execute(f_name, f_args, tool_context)
                    fres_parts.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=f_name,
                                response=tool_res
                            )
                        )
                    )

                conversation_contents.append(types.Content(role="user", parts=fres_parts))

            await artifact_parser.finish()

        except Exception as gen_err:
            logger.error(f"[ScheduleWatchdog] AI generation failed for task #{task_id}: {gen_err}")
            await stream_dispatcher.append_text(f"\n\n⚠️ Automated task execution error: `{gen_err}`")

        footer_text = f"\n\n-# {OCTICONS_MAP['oct_calendar']} Scheduled Task ({summary_sched}) • Requested by <@{user_id}>"
        await stream_dispatcher.append_text(footer_text)

        delivered = False
        if target_channel and scope == "server" and dm_delivery != "dm_only":
            try:
                await stream_dispatcher.finalize(
                    staged_artifacts=tool_context.staged_artifacts,
                    staged_components=tool_context.staged_components
                )
                delivered = True
            except Exception as e:
                logger.warning(f"[ScheduleWatchdog] Failed to dispatch to channel #{target_channel.id}: {e}")

        if user_obj and (scope == "personal" or dm_delivery in ("dm_only", "channel_and_dm") or not delivered):
            try:
                if scope == "personal" or dm_delivery == "dm_only":
                    await stream_dispatcher.finalize(
                        staged_artifacts=tool_context.staged_artifacts,
                        staged_components=tool_context.staged_components
                    )
                else:
                    raw_text = stream_dispatcher.get_accumulated_text()
                    await user_obj.send(content=raw_text[:2000])
                delivered = True
            except Exception as e:
                logger.warning(f"[ScheduleWatchdog] Failed to send DM to user {user_id}: {e}")

        if interval_type == "once":
            self.delete_task(task_id)
            logger.info(f"[ScheduleWatchdog] One-time task #{task_id} self-destructed after execution.")
        elif delivered:
            logger.info(f"[ScheduleWatchdog] Recurring task #{task_id} successfully delivered.")

schedule_manager = ScheduleManager()