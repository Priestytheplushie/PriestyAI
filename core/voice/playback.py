
import threading
import logging
import discord
from core.voice.audio_utils import resample_24_mono_to_48_stereo, DISCORD_PLAYBACK_FRAME_BYTES

logger = logging.getLogger("Playback")

class BufferedAudioSource(discord.AudioSource):
    def __init__(self, buffer_start_threshold: int = 15360):
        super().__init__()
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.buffer_start_threshold = buffer_start_threshold
        self.has_started_playing = False
        self.is_active = False

    def write(self, gemini_mono_24k_bytes: bytes):
        if not gemini_mono_24k_bytes:
            return
        try:
            stereo_48k = resample_24_mono_to_48_stereo(gemini_mono_24k_bytes)
            if stereo_48k:
                with self.lock:
                    self.buffer.extend(stereo_48k)
                    self.is_active = True
        except Exception as e:
            logger.error(f"Error upsampling/writing to playback buffer: {e}")

    def read(self) -> bytes:
        with self.lock:
            if not self.has_started_playing:
                if len(self.buffer) >= self.buffer_start_threshold:
                    self.has_started_playing = True
                else:
                    return b'\x00' * DISCORD_PLAYBACK_FRAME_BYTES

            if len(self.buffer) >= DISCORD_PLAYBACK_FRAME_BYTES:
                chunk = self.buffer[:DISCORD_PLAYBACK_FRAME_BYTES]
                del self.buffer[:DISCORD_PLAYBACK_FRAME_BYTES]
                return bytes(chunk)
            else:
                self.has_started_playing = False
                self.is_active = False
                return b'\x00' * DISCORD_PLAYBACK_FRAME_BYTES

    def clear(self):
        with self.lock:
            self.buffer.clear()
            self.has_started_playing = False
            self.is_active = False
            logger.debug("Playout buffer successfully cleared.")

    def is_playing(self) -> bool:
        with self.lock:
            return self.is_active or len(self.buffer) >= DISCORD_PLAYBACK_FRAME_BYTES