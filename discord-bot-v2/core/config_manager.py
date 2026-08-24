import os
import json
import sqlite3
import logging
from typing import Any
import discord

logger = logging.getLogger("PriestyAI.ConfigManager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "priestyai.db")

class ConfigManager:
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
                CREATE TABLE IF NOT EXISTS server_configs (
                    guild_id TEXT PRIMARY KEY,
                    server_bio TEXT DEFAULT '',
                    system_prompt TEXT DEFAULT '',
                    override_user_instructions INTEGER DEFAULT 0,
                    access_behavior TEXT DEFAULT 'blacklist',
                    restricted_entities_json TEXT DEFAULT '[]',
                    permission_bypass_json TEXT DEFAULT '[]',
                    config_manager_role TEXT DEFAULT 'administrators',
                    disabled_tools_json TEXT DEFAULT '[]',
                    server_lore_policy TEXT DEFAULT 'read_write',
                    preferred_reasoning_level TEXT DEFAULT 'AUTO',
                    ai_channels_json TEXT DEFAULT '[]',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_configs (
                    channel_id TEXT PRIMARY KEY,
                    guild_id TEXT,
                    system_prompt TEXT DEFAULT '',
                    override_user_instructions INTEGER DEFAULT 0,
                    disabled_tools_json TEXT DEFAULT '[]',
                    memory_policy TEXT DEFAULT 'read_write',
                    preferred_reasoning_level TEXT DEFAULT 'AUTO',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_configs (
                    user_id TEXT PRIMARY KEY,
                    preferred_name TEXT DEFAULT '',
                    special_instructions TEXT DEFAULT '',
                    user_memory_policy TEXT DEFAULT 'read_write',
                    preferred_reasoning_level TEXT DEFAULT 'AUTO',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("PRAGMA table_info(server_configs)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "server_bio" not in columns and "guild_id" in columns:
                cursor.execute("ALTER TABLE server_configs ADD COLUMN server_bio TEXT DEFAULT ''")
            if "ai_channels_json" not in columns and "guild_id" in columns:
                cursor.execute("ALTER TABLE server_configs ADD COLUMN ai_channels_json TEXT DEFAULT '[]'")

            conn.commit()
        logger.info(f"Initialized configuration tables at absolute path: '{self.db_path}'")


    def get_server_config(self, guild_id: str | int) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM server_configs WHERE guild_id = ?", (str(guild_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["restricted_entities"] = json.loads(d.get("restricted_entities_json") or "[]")
                d["permission_bypass"] = json.loads(d.get("permission_bypass_json") or "[]")
                d["disabled_tools"] = json.loads(d.get("disabled_tools_json") or "[]")
                d["ai_channels"] = json.loads(d.get("ai_channels_json") or "[]")
                return d
        return {
            "guild_id": str(guild_id),
            "server_bio": "",
            "system_prompt": "",
            "override_user_instructions": 0,
            "access_behavior": "blacklist",
            "restricted_entities": [],
            "permission_bypass": [],
            "config_manager_role": "administrators",
            "disabled_tools": [],
            "server_lore_policy": "read_write",
            "preferred_reasoning_level": "AUTO",
            "ai_channels": []
        }

    def set_server_config(self, guild_id: str | int, **kwargs):
        current = self.get_server_config(guild_id)
        current.update(kwargs)
        
        re_json = json.dumps(current.get("restricted_entities", []))
        pb_json = json.dumps(current.get("permission_bypass", []))
        dt_json = json.dumps(current.get("disabled_tools", []))
        ac_json = json.dumps([str(cid) for cid in current.get("ai_channels", [])])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO server_configs (
                    guild_id, server_bio, system_prompt, override_user_instructions, access_behavior,
                    restricted_entities_json, permission_bypass_json, config_manager_role,
                    disabled_tools_json, server_lore_policy, preferred_reasoning_level, ai_channels_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(guild_id) DO UPDATE SET
                    server_bio = excluded.server_bio,
                    system_prompt = excluded.system_prompt,
                    override_user_instructions = excluded.override_user_instructions,
                    access_behavior = excluded.access_behavior,
                    restricted_entities_json = excluded.restricted_entities_json,
                    permission_bypass_json = excluded.permission_bypass_json,
                    config_manager_role = excluded.config_manager_role,
                    disabled_tools_json = excluded.disabled_tools_json,
                    server_lore_policy = excluded.server_lore_policy,
                    preferred_reasoning_level = excluded.preferred_reasoning_level,
                    ai_channels_json = excluded.ai_channels_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                str(guild_id),
                current.get("server_bio", ""),
                current.get("system_prompt", ""),
                int(bool(current.get("override_user_instructions", 0))),
                current.get("access_behavior", "blacklist"),
                re_json,
                pb_json,
                current.get("config_manager_role", "administrators"),
                dt_json,
                current.get("server_lore_policy", "read_write"),
                current.get("preferred_reasoning_level", "AUTO"),
                ac_json
            ))
            conn.commit()

    def is_ai_channel(self, guild_id: str | int | None, channel_id: str | int) -> bool:
        if not guild_id:
            return False
        s_cfg = self.get_server_config(guild_id)
        ai_channels = [str(cid) for cid in s_cfg.get("ai_channels", [])]
        return str(channel_id) in ai_channels

    def get_channel_config(self, channel_id: str | int) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channel_configs WHERE channel_id = ?", (str(channel_id),))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["disabled_tools"] = json.loads(d.get("disabled_tools_json") or "[]")
                return d
        return {
            "channel_id": str(channel_id),
            "guild_id": None,
            "system_prompt": "",
            "override_user_instructions": 0,
            "disabled_tools": [],
            "memory_policy": "read_write",
            "preferred_reasoning_level": "AUTO"
        }

    def set_channel_config(self, channel_id: str | int, guild_id: str | int | None = None, **kwargs):
        current = self.get_channel_config(channel_id)
        current.update(kwargs)
        if guild_id:
            current["guild_id"] = str(guild_id)

        dt_json = json.dumps(current.get("disabled_tools", []))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_configs (
                    channel_id, guild_id, system_prompt, override_user_instructions,
                    disabled_tools_json, memory_policy, preferred_reasoning_level, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    system_prompt = excluded.system_prompt,
                    override_user_instructions = excluded.override_user_instructions,
                    disabled_tools_json = excluded.disabled_tools_json,
                    memory_policy = excluded.memory_policy,
                    preferred_reasoning_level = excluded.preferred_reasoning_level,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                str(channel_id),
                current.get("guild_id"),
                current.get("system_prompt", ""),
                int(bool(current.get("override_user_instructions", 0))),
                dt_json,
                current.get("memory_policy", "read_write"),
                current.get("preferred_reasoning_level", "AUTO")
            ))
            conn.commit()

    def get_user_config(self, user_id: str | int) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_configs WHERE user_id = ?", (str(user_id),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return {
            "user_id": str(user_id),
            "preferred_name": "",
            "special_instructions": "",
            "user_memory_policy": "read_write",
            "preferred_reasoning_level": "AUTO"
        }

    def set_user_config(self, user_id: str | int, **kwargs):
        current = self.get_user_config(user_id)
        current.update(kwargs)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_configs (
                    user_id, preferred_name, special_instructions,
                    user_memory_policy, preferred_reasoning_level, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferred_name = excluded.preferred_name,
                    special_instructions = excluded.special_instructions,
                    user_memory_policy = excluded.user_memory_policy,
                    preferred_reasoning_level = excluded.preferred_reasoning_level,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                str(user_id),
                current.get("preferred_name", ""),
                current.get("special_instructions", ""),
                current.get("user_memory_policy", "read_write"),
                current.get("preferred_reasoning_level", "AUTO")
            ))
            conn.commit()

    def reset_config(self, scope: str, entity_id: str | int) -> bool:
        scope_clean = scope.lower().strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if scope_clean in ["server", "guild"]:
                cursor.execute("DELETE FROM server_configs WHERE guild_id = ?", (str(entity_id),))
            elif scope_clean == "channel":
                cursor.execute("DELETE FROM channel_configs WHERE channel_id = ?", (str(entity_id),))
            elif scope_clean in ["user", "dm", "bot_dm", "user_app", "global_dm"]:
                cursor.execute("DELETE FROM user_configs WHERE user_id = ?", (str(entity_id),))
            conn.commit()
            return cursor.rowcount > 0


    def check_permission(self, interaction: discord.Interaction, setting: str, scope: str) -> tuple[bool, str]:
        setting_clean = setting.lower().replace(" ", "_")
        scope_clean = scope.lower().replace(" ", "_")

        if setting_clean == "user_persona":
            if scope_clean not in ["user", "bot_dm", "user_app", "global_dm"]:
                return False, "User Persona is a personal setting and is only available in User, Bot DM, or User App scopes."
            return True, ""

        if scope_clean in ["user", "bot_dm", "user_app", "global_dm"]:
            return True, ""

        if not interaction.guild:
            return False, "This configuration scope requires a Discord server."

        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            return False, "Member context unavailable."

        is_owner = (interaction.guild.owner_id == interaction.user.id)
        is_admin = member.guild_permissions.administrator

        s_cfg = self.get_server_config(interaction.guild.id)
        bypass_list = s_cfg.get("permission_bypass", [])

        member_role_ids = [str(r.id) for r in member.roles]
        has_bypass = (str(interaction.user.id) in bypass_list) or any(rid in bypass_list for rid in member_role_ids)

        if is_owner or is_admin or has_bypass:
            return True, ""

        if setting_clean in ["permissions", "server_identity"] or (setting_clean == "reset" and scope_clean == "server"):
            return False, "🔒 This setting is restricted to Server Administrators and the Server Owner."

        manager_policy = s_cfg.get("config_manager_role", "administrators").lower()

        if manager_policy == "everyone":
            return True, ""
        elif manager_policy == "managers":
            if member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
                return True, ""
            return False, "🔒 This setting requires `Manage Server` or `Manage Channels` permissions."
        elif manager_policy == "owner_only":
            return False, "🔒 General settings on this server are restricted to the Server Owner."

        return False, "🔒 You do not have permission to modify server settings for PriestyAI."

    def resolve_effective_config(
        self,
        guild_id: str | int | None,
        channel_id: str | int | None,
        user_id: str | int | None
    ) -> dict[str, Any]:
        s_cfg = self.get_server_config(guild_id) if guild_id else {}
        c_cfg = self.get_channel_config(channel_id) if channel_id else {}
        u_cfg = self.get_user_config(user_id) if user_id else {}

        system_prompts = []
        override_user = bool(c_cfg.get("override_user_instructions") or s_cfg.get("override_user_instructions"))

        if s_cfg.get("system_prompt"):
            system_prompts.append(f"[Server Rule]: {s_cfg['system_prompt']}")

        if c_cfg.get("system_prompt"):
            system_prompts.append(f"[Channel Context]: {c_cfg['system_prompt']}")

        if not override_user and u_cfg.get("special_instructions"):
            system_prompts.append(f"[User Persona]: {u_cfg['special_instructions']}")

        reasoning = "AUTO"
        if c_cfg.get("preferred_reasoning_level") and c_cfg["preferred_reasoning_level"] != "AUTO":
            reasoning = c_cfg["preferred_reasoning_level"]
        elif u_cfg.get("preferred_reasoning_level") and u_cfg["preferred_reasoning_level"] != "AUTO":
            reasoning = u_cfg["preferred_reasoning_level"]
        elif s_cfg.get("preferred_reasoning_level") and s_cfg["preferred_reasoning_level"] != "AUTO":
            reasoning = s_cfg["preferred_reasoning_level"]

        disabled_tools = set(s_cfg.get("disabled_tools", []) + c_cfg.get("disabled_tools", []))

        return {
            "combined_system_prompt": "\n\n".join(system_prompts),
            "preferred_name": u_cfg.get("preferred_name", "").strip(),
            "reasoning_level": reasoning,
            "disabled_tools": list(disabled_tools),
            "user_memory_policy": u_cfg.get("user_memory_policy", "read_write"),
            "server_lore_policy": s_cfg.get("server_lore_policy", "read_write"),
            "channel_memory_policy": c_cfg.get("memory_policy", "read_write")
        }

config_manager = ConfigManager()