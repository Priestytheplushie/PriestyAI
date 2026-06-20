
import asyncio
import logging
import time
from typing import Optional
from discord.ext import voice_recv
import discord

from core.voice.audio_utils import (
    resample_48_stereo_to_16_mono,
    mix_mono_pcm_frames,
    DISCORD_FRAME_MS,
    GEMINI_MONO_FRAME_BYTES
)
from core.voice.playback import BufferedAudioSource
from core.voice.barge_in import LocalBargeInController
from core.voice.connection import GeminiLiveSessionManager

logger = logging.getLogger("VoiceBridge")

class GeminiAudioSink(voice_recv.AudioSink):
    def __init__(self, loop: asyncio.AbstractEventLoop, mic_queue: asyncio.Queue):
        super().__init__()
        self.loop = loop
        self.mic_queue = mic_queue

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        if not data.pcm:
            return
            
        resampled_mono = resample_48_stereo_to_16_mono(data.pcm)
        if not resampled_mono:
            return
            
        self.loop.call_soon_threadsafe(
            self.mic_queue.put_nowait, 
            (user, resampled_mono)
        )

    def cleanup(self) -> None:
        logger.debug("GeminiAudioSink cleaned up successfully.")


class DiscordGeminiVoiceBridge:
    def __init__(
        self,
        voice_client: voice_recv.VoiceRecvClient,
        guild_id: int,
        text_channel: discord.TextChannel,
        gemini_key: str,
        model_name: str,
        voice_name: str,
        system_prompt: str
    ):
        self.vc = voice_client
        self.guild_id = guild_id
        self.text_channel = text_channel
        
        self.loop = asyncio.get_running_loop()
        self.mic_queue = asyncio.Queue()
        
        self.audio_source = BufferedAudioSource()
        self.sink = GeminiAudioSink(self.loop, self.mic_queue)
        self.barge_in_controller = LocalBargeInController()
        
        self.connection = GeminiLiveSessionManager(
            api_key=gemini_key,
            model=model_name,
            voice_name=voice_name,
            system_prompt=system_prompt,
            on_message_callback=self._handle_server_message,
            on_reset_callback=self._handle_session_reset
        )
        
        self.is_running = False
        self.tasks: list[asyncio.Task] = []
        
        self.awaiting_server_ack = False
        self.awaiting_server_ack_since = 0.0
        self.server_interrupt_fallback_seconds = 1.2
        
        self.turn_sent_audio = False
        self.tts_text_buffer = ""

    async def start(self):
        self.is_running = True
        logger.info(f"Initializing voice session bridge for Guild ID {self.guild_id}")
        
        try:
            await self.connection.connect()
            
            self.vc.listen(self.sink)
            self.vc.play(self.audio_source)
            
            self.tasks.append(asyncio.create_task(self._audio_upload_loop()))
            self.tasks.append(asyncio.create_task(self._stale_gate_monitor_loop()))
            
            logger.info(f"Voice session bridge successfully started for Guild {self.guild_id}")
        except Exception as e:
            logger.error(f"Failed to start DiscordGeminiVoiceBridge: {e}", exc_info=True)
            await self.stop()

    async def stop(self):
        if not self.is_running:
            return
            
        self.is_running = False
        logger.info(f"Stopping DiscordGeminiVoiceBridge for Guild {self.guild_id}")
        
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        
        if self.vc:
            try:
                self.vc.stop()
                self.vc.stop_listening()
            except Exception:
                pass
            if self.vc.is_connected():
                await self.vc.disconnect()
                
        self.audio_source.clear()
        await self.connection.stop()
        logger.info(f"Voice session bridge stopped for Guild {self.guild_id}")

    def _handle_server_message(self, message):
        server_content = getattr(message, 'server_content', None)
        if server_content is None:
            return

        if getattr(server_content, 'interrupted', False):
            self._flush_tts_text(interrupted=True)
            self.awaiting_server_ack = False
            self.audio_source.clear()
            self.barge_in_controller.reset()
            self.turn_sent_audio = False
            logger.info("Received interrupt signal from server. Playout buffer flushed.")
            
        if getattr(server_content, 'turn_complete', False):
            self._flush_tts_text(interrupted=False)
            self.awaiting_server_ack = False
            self.turn_sent_audio = False

        text_transcript = ""
        output_transcription = getattr(server_content, 'output_transcription', None)
        if output_transcription and getattr(output_transcription, 'text', None):
            text_transcript = output_transcription.text
            self.tts_text_buffer += text_transcript
            
        input_transcription = getattr(server_content, 'input_transcription', None)
        if input_transcription and getattr(input_transcription, 'text', None):
            logger.info(f"[User STT Transcript]: {input_transcription.text}")

        if self.awaiting_server_ack:
            return

        model_turn = getattr(server_content, 'model_turn', None)
        if model_turn is not None:
            for part in model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    audio_bytes = part.inline_data.data
                    
                    if isinstance(audio_bytes, str):
                        import base64
                        try:
                            audio_bytes = base64.b64decode(audio_bytes)
                        except Exception:
                            continue
                            
                    self.audio_source.write(audio_bytes)

    def _flush_tts_text(self, interrupted: bool = False):
        if not self.tts_text_buffer:
            return
        suffix = " [interrupted]" if interrupted else ""
        logger.info(f"[Gemini TTS Transcript]: {self.tts_text_buffer}{suffix}")
        self.tts_text_buffer = ""

    async def _handle_session_reset(self, reason: str):
        logger.info(f"Gemini Live session reset callback triggered. Reason: {reason}")
        self.audio_source.clear()
        self.barge_in_controller.reset()
        self.awaiting_server_ack = False
        self.turn_sent_audio = False

    async def _audio_upload_loop(self):
        while self.is_running:
            start_time = time.perf_counter()
            
            mixed_frame = await self._collect_and_mix_mic_frames()
            
            if mixed_frame:
                playback_active = self.audio_source.is_playing()
                
                self.barge_in_controller.arm_if_playback_started_before_turn(
                    playback_active=playback_active,
                    turn_sent_audio=self.turn_sent_audio
                )
                
                if playback_active and self.barge_in_controller.barge_in_mode:
                    decision = self.barge_in_controller.evaluate_frame(
                        mixed_frame, 
                        speaker_ids=["user"]
                    )
                    
                    if decision["action"] == "barge_in":
                        self.audio_source.clear()
                        self.awaiting_server_ack = True
                        self.awaiting_server_ack_since = time.time()
                        
                        logger.info("Local barge-in triggered! Flushing playout buffer and forwarding pre-roll buffer.")
                        
                        for chunk in decision["pre_roll_chunks"]:
                            await self.connection.send_audio(chunk["pcm_data"])
                        
                        await self.connection.send_audio(mixed_frame)
                        self.turn_sent_audio = True
                        continue
                        
                    elif decision["action"] == "hold":
                        pass
                
                await self.connection.send_audio(mixed_frame)
                self.turn_sent_audio = True
            else:
                await self.connection.send_audio(b'\x00' * GEMINI_MONO_FRAME_BYTES)

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.001, (DISCORD_FRAME_MS / 1000.0) - elapsed)
            await asyncio.sleep(sleep_time)

    async def _collect_and_mix_mic_frames(self) -> Optional[bytes]:
        frames = []
        try:
            while True:
                user, pcm_frame = self.mic_queue.get_nowait()
                frames.append(pcm_frame)
                self.mic_queue.task_done()
        except asyncio.QueueEmpty:
            pass
            
        if not frames:
            return None
            
        return mix_mono_pcm_frames(frames)

    async def _stale_gate_monitor_loop(self):
        while self.is_running:
            await asyncio.sleep(0.5)
            if self.awaiting_server_ack:
                elapsed = time.time() - self.awaiting_server_ack_since
                if elapsed >= self.server_interrupt_fallback_seconds:
                    logger.warning("Interruption acknowledgment lock timeout reached. Releasing gate.")
                    self.awaiting_server_ack = False