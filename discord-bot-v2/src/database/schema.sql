-- Enable WAL mode for high concurrency
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- 1. Channel Message Buffer (Rolling context for active conversations)
CREATE TABLE IF NOT EXISTS channel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    guild_id TEXT,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    content TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_messages_channel_time 
ON channel_messages(channel_id, created_at DESC);

-- 2. User Memories (Facts, preferences, and personal context per user)
CREATE TABLE IF NOT EXISTS user_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created_at INTEGER NOT NULL,
    last_accessed_at INTEGER NOT NULL,
    UNIQUE(user_id, fact)
);

CREATE INDEX IF NOT EXISTS idx_user_memories_user_id 
ON user_memories(user_id);

-- 3. Server Lore & Knowledge (Slang, inside jokes, memes, community rules)
CREATE TABLE IF NOT EXISTS server_lore (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    topic TEXT NOT NULL,          -- e.g. 'slang', 'meme', 'member_info', 'rule'
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,  -- 0.0 to 1.0 confidence score
    last_observed_at INTEGER NOT NULL,
    UNIQUE(guild_id, topic, content)
);

CREATE INDEX IF NOT EXISTS idx_server_lore_guild_topic 
ON server_lore(guild_id, topic);

-- 4. Server Vibe Profiles (Evolving tone digest for persona alignment)
CREATE TABLE IF NOT EXISTS server_vibes (
    guild_id TEXT PRIMARY KEY,
    vibe_summary TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 5. Watched Channels (For autonomous event listening and temporary presence)
CREATE TABLE IF NOT EXISTS watched_channels (
    channel_id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    watch_until INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL
);