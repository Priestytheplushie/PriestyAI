// core/voice_service/index.js

import express from 'express';
import { DiscordGeminiVoiceBridge } from './services/discord-gemini-voice-bridge.js';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

// Resolve .env relative to this file's folder location [index.js]
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const app = express();
app.use(express.json());

// Cache active bridge adapters and session managers [entersState, connection-ready.js]
const activeSessions = new Map();
const activeAdapters = new Map();

// Custom Gateway Adapter Creator mapping directly to Python's raw socket events [entersState, connection-ready.js]
function createCustomGatewayAdapter(guildId) {
  return (methods) => {
    activeAdapters.set(guildId, methods);
    return {
      sendPayload(data) {
        // No-op! Python has already triggered the voice state transition on the gateway.
      },
      destroy() {
        activeAdapters.delete(guildId);
      }
    };
  };
}

// Local API Endpoints triggered by Python Proxy
app.post('/join', async (req, res) => {
  const { guild_id, channel_id, system_prompt, voice_name, gemini_api_key, model_name } = req.body;

  if (!guild_id || !channel_id) {
    return res.status(400).send("guild_id and channel_id are required.");
  }

  try {
    // Terminate existing session for this guild if active
    if (activeSessions.has(guild_id)) {
      console.log(`[Node Voice Service] Terminating old session for guild ${guild_id}`);
      await activeSessions.get(guild_id).stop();
      activeSessions.delete(guild_id);
    }

    const bridge = new DiscordGeminiVoiceBridge({
      guildId: guild_id,
      channelId: channel_id,
      adapterCreator: createCustomGatewayAdapter(guild_id),
      systemPrompt: system_prompt,
      voiceName: voice_name,
      geminiApiKey: gemini_api_key || process.env.GEMINI_API_KEY,
      modelName: model_name || "gemini-3.1-flash-live-preview"
    });

    activeSessions.set(guild_id, bridge);
    await bridge.start();

    console.log(`[Node Voice Service] Bridge registered successfully for guild ${guild_id}`);
    res.status(200).send("Bridge registered.");
  } catch (err) {
    console.error(`[Node Voice Service] Failed to register bridge:`, err);
    res.status(500).send(err.message);
  }
});

// Intercepts raw gateway state packets from Python and feeds them to @discordjs/voice [entersState, connection-ready.js]
app.post('/gateway-packet', (req, res) => {
  const { guild_id, type, data } = req.body;
  const adapter = activeAdapters.get(guild_id);

  if (adapter) {
    if (type === "VOICE_STATE_UPDATE") {
      adapter.onVoiceStateUpdate(data);
    } else if (type === "VOICE_SERVER_UPDATE") {
      adapter.onVoiceServerUpdate(data);
    }
    res.status(200).send("Packet processed.");
  } else {
    res.status(404).send(`No active voice adapter found for guild ${guild_id}`);
  }
});

app.post('/leave', async (req, res) => {
  const { guild_id } = req.body;

  if (!guild_id) {
    return res.status(400).send("guild_id is required.");
  }

  const bridge = activeSessions.get(guild_id);
  if (bridge) {
    try {
      await bridge.stop();
      activeSessions.delete(guild_id);
      activeAdapters.delete(guild_id);
      console.log(`[Node Voice Service] Bridge stopped cleanly for guild ${guild_id}`);
      res.status(200).send("Bridge stopped.");
    } catch (err) {
      console.error(`[Node Voice Service] Error stopping bridge:`, err);
      res.status(500).send(err.message);
    }
  } else {
    res.status(404).send("No active session found for this guild.");
  }
});

const PORT = process.env.NODE_VOICE_API_PORT || 3000;
app.listen(PORT, () => {
  console.log(`[Node Voice Service] Control Server listening on http://localhost:${PORT}`);
});