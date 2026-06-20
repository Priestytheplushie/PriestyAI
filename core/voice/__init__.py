
import logging
import discord.opus

logger = logging.getLogger("VoicePackageInit")

try:
    _original_decode = discord.opus.Decoder.decode

    def _safe_decode(self, data, fec=False):
        try:
            return _original_decode(self, data, fec=fec)
        except discord.opus.OpusError as e:
            logger.debug(f"Intercepted corrupt packet stream: {e}. Injecting silent frame to preserve event loops.")
            return bytes(3840)
        except Exception as e:
            logger.debug(f"Unhandled decoder exception: {e}. Injecting fallback silence.")
            return bytes(3840)

    discord.opus.Decoder.decode = _safe_decode
    logger.info("Voice stream decoder isolation patch successfully applied.")
except Exception as patch_err:
    logger.error(f"Failed to apply voice stream decoder isolation patch: {patch_err}")

from core.voice.audio_utils import (
    resample_48_stereo_to_16_mono,
    resample_24_mono_to_48_stereo,
    mix_mono_pcm_frames,
    compute_mono_pcm_rms,
    mono_pcm_duration_ms
)
from core.voice.playback import BufferedAudioSource
from core.voice.barge_in import LocalBargeInController
from core.voice.connection import GeminiLiveSessionManager
from core.voice.bridge import DiscordGeminiVoiceBridge, GeminiAudioSink