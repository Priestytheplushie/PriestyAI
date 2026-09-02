import os
import time
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
import discord
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.PollManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

class PollManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_polls (
                    poll_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    guild_id TEXT,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    expiry_timestamp INTEGER NOT NULL,
                    concluded INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_polls_expiry ON active_polls(expiry_timestamp, concluded)")
            conn.commit()

    def register_poll(
        self,
        poll_id: str,
        message_id: str | int,
        channel_id: str | int,
        guild_id: str | int | None,
        question: str,
        options: list[str],
        duration_hours: int
    ):
        expiry = int(time.time()) + int(duration_hours * 3600)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO active_polls (
                    poll_id, message_id, channel_id, guild_id,
                    question, options_json, expiry_timestamp, concluded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                str(poll_id),
                str(message_id),
                str(channel_id),
                str(guild_id) if guild_id else None,
                question,
                json.dumps(options),
                expiry
            ))
            conn.commit()
        logger.info(f"[PollManager] Registered poll #{poll_id} (Expires in {duration_hours}h)")

    async def poll_watchdog_tick(self, bot: discord.Client):
        now_ts = int(time.time())
        expired_polls = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM active_polls WHERE expiry_timestamp <= ? AND concluded = 0",
                (now_ts,)
            )
            expired_polls = [dict(r) for r in cursor.fetchall()]

        for poll_data in expired_polls:
            poll_id = poll_data["poll_id"]
            chan_id = int(poll_data["channel_id"])
            msg_id = int(poll_data["message_id"])

            try:
                channel = bot.get_channel(chan_id) or await bot.fetch_channel(chan_id)
                if not channel:
                    self._mark_concluded(poll_id)
                    continue

                msg = await channel.fetch_message(msg_id)
                if not msg or not msg.poll:
                    self._mark_concluded(poll_id)
                    continue

                results_summary = []
                total_votes = 0
                for answer in msg.poll.answers:
                    count = answer.vote_count
                    total_votes += count
                    results_summary.append(f"- **{answer.text}**: {count} vote(s)")

                tally_text = "\n".join(results_summary)
                question = poll_data["question"]

                prompt = (
                    f"A Discord poll has concluded in this channel!\n\n"
                    f"Poll Question: \"{question}\"\n"
                    f"Total Votes: {total_votes}\n"
                    f"Results:\n{tally_text}\n\n"
                    f"Write a short, engaging, witty 2-3 sentence wrap-up announcing the winning result!"
                )

                client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
                if client:
                    res = await client.aio.models.generate_content(model=active_model, contents=prompt)
                    if res.text:
                        await channel.send(
                            content=f"📊 **Poll Concluded: {question}**\n\n{res.text.strip()}",
                            reference=msg,
                            mention_author=False
                        )

            except Exception as e:
                logger.warning(f"Failed to process expired poll #{poll_id}: {e}")
            finally:
                self._mark_concluded(poll_id)

    def _mark_concluded(self, poll_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE active_polls SET concluded = 1 WHERE poll_id = ?", (str(poll_id),))
            conn.commit()

poll_manager = PollManager()