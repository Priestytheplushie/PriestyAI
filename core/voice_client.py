
import os
import asyncio
import logging
import threading
import discord
from discord.ext import voice_recv
from google import genai
from google.genai import types

logger = logging.getLogger("VoiceClient")

import discord.opus
_orig_decode = discord.opus.Decoder.decode

def _patched_decode(self, *args, **kwargs):
    try:
        return _orig_decode(self, *args, **kwargs)
    except Exception:
        return bytes(3840)

discord.opus.Decoder.decode = _patched_decode
logger.info("Decoder corruption safety handler applied.")


def resample_48_stereo_to_16_mono(data: bytes) -> bytes:
    """
    Downsamples Discord's 48kHz Stereo to Gemini's 16kHz Mono.
    We take 1 out of every 3 frames, and extract only the Left channel to avoid expensive division math.
    1 Frame of 48kHz Stereo = 4 bytes. 3 Frames = 12 bytes.
    """
    out = bytearray(len(data) // 6)
    out_idx = 0
    for i in range(0, len(data) - 11, 12):
        out[out_idx] = data[i]
        out[out_idx+1] = data[i+1]
        out_idx += 2
    return bytes(out)

def resample_24_mono_to_48_stereo(data: bytes) -> bytes:
    """
    Upsamples Gemini's 24kHz Mono to Discord's 48kHz Stereo.
    We double the frames (for sample rate) and duplicate mono into L/R channels (for stereo).
    1 input frame (2 bytes) -> 2 output frames (8 bytes).
    """
    out = bytearray(len(data) * 4)
    out_idx = 0
    for i in range(0, len(data) - 1, 2):
        b0 = data[i]
        b1 = data[i+1]
        out[out_idx] = b0; out[out_idx+1] = b1
        out[out_idx+2] = b0; out[out_idx+3] = b1
        out[out_idx+4] = b0; out[out_idx+5] = b1
        out[out_idx+6] = b0; out[out_idx+7] = b1
        out_idx += 8
    return bytes(out)


class BufferedAudioSource(discord.AudioSource):
    """A thread-safe audio source that Discord's vc.play() constantly reads from."""
    def __init__(self):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        
    def write(self, data: bytes):
        with self.lock:
            self.buffer.extend(data)
            
    def read(self) -> bytes:
        with self.lock:
            if len(self.buffer) >= 3840:
                chunk = self.buffer[:3840]
                del self.buffer[:3840]
                return bytes(chunk)
            else:
                return b'\x00' * 3840
                
    def clear(self):
        with self.lock:
            self.buffer.clear()


class GeminiAudioSink(voice_recv.AudioSink):
    """Receives raw PCM from Discord users and pushes it to our async queue."""
    def __init__(self, loop, queue):
        super().__init__()
        self.loop = loop
        self.queue = queue

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        if not data.pcm:
            return
        resampled = resample_48_stereo_to_16_mono(data.pcm)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, resampled)

    def cleanup(self) -> None:
        """Required finalizer implementation for the abstract AudioSink class."""
        logger.info("GeminiAudioSink has closed and cleaned up.")


class DiscordVoiceSession:
    def __init__(self, bot_instance, voice_client: voice_recv.VoiceRecvClient, guild_id: int, text_channel):
        self.bot = bot_instance
        self.vc = voice_client
        self.guild_id = guild_id
        self.text_channel = text_channel
        
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = os.getenv("LIVE_MODEL", "gemini-3.1-flash-live-preview")
        self.voice_name = os.getenv("GEMINI_VOICE_NAME", "Puck")
        
        prompt_path = os.path.join("config", "voice_prompt.md")
        try:
            with open(prompt_path, 'r', encoding='utf-8') as file:
                self.system_instruction = file.read()
        except FileNotFoundError:
            self.system_instruction = "You are a friend in a voice call."

        self.mic_queue = asyncio.Queue()
        self.audio_source = BufferedAudioSource()
        self.sink = GeminiAudioSink(asyncio.get_event_loop(), self.mic_queue)
        
        self.is_running = False
        self.tasks = []
        self.session_context = None
        self.gemini_session = None

    async def start(self):
        self.is_running = True
        logger.info(f"Starting Gemini Live Voice Session in Guild {self.guild_id}")
        
        config = types.LiveConnectConfig(
            system_instruction=types.Content(parts=[types.Part(text=self.system_instruction)]),
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            )
        )
        
        try:
            self.session_context = self.client.aio.live.connect(model=self.model, config=config)
            self.gemini_session = await self.session_context.__aenter__()
            
            self.vc.listen(self.sink)
            self.vc.play(self.audio_source)
            
            self.tasks.append(asyncio.create_task(self._send_audio_loop()))
            self.tasks.append(asyncio.create_task(self._receive_loop()))
            
            await self.gemini_session.send_realtime_input(
                text=f"System: You just joined the voice call in '{self.vc.channel.name}'. Say a casual, brief greeting out loud!"
            )
            
        except Exception as e:
            logger.error(f"Failed to establish Live session: {e}")
            await self.stop()

    async def stop(self):
        if not self.is_running:
            return
            
        self.is_running = False
        logger.info(f"Stopping Voice Session for Guild {self.guild_id}")
        
        for task in self.tasks:
            task.cancel()
            
        if self.vc and self.vc.is_connected():
            self.vc.stop()
            self.vc.stop_listening()
            await self.vc.disconnect()
            
        if self.session_context:
            try:
                await self.session_context.__aexit__(None, None, None)
            except Exception as context_err:
                logger.warning(f"Error releasing session context: {context_err}")
            self.session_context = None
            self.gemini_session = None
            
        if self.guild_id in self.bot.voice_sessions:
            del self.bot.voice_sessions[self.guild_id]

    async def _send_audio_loop(self):
        """Streams audio to Gemini Live in stable, balanced chunks of 150ms."""
        batch = bytearray()
        chunk_size_target = 4800
        
        while self.is_running:
            try:
                chunk = await asyncio.wait_for(self.mic_queue.get(), timeout=0.1)
                batch.extend(chunk)
            except asyncio.TimeoutError:
                if batch:
                    try:
                        logger.info(f"[Transmit] Flushing remaining {len(batch)} bytes of voice stream to Gemini...")
                        await self.gemini_session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(batch),
                                mime_type="audio/pcm;rate=16000"
                            )
                        )
                        batch.clear()
                    except Exception as e:
                        logger.error(f"Error during audio flush: {e}")
                        await self.stop()
                        break
                continue

            if len(batch) >= chunk_size_target:
                try:
                    logger.debug(f"[Transmit] Streaming {len(batch)} bytes of speech to Gemini...")
                    await self.gemini_session.send_realtime_input(
                        audio=types.Blob(
                            data=bytes(batch),
                            mime_type="audio/pcm;rate=16000"
                        )
                    )
                    batch.clear()
                except Exception as e:
                    logger.error(f"Error sending audio to Gemini: {e}")
                    await self.stop()
                    break

    async def _receive_loop(self):
        """Listens for the bot's generated voice chunks and interruptions."""
        try:
            async for response in self.gemini_session.receive():
                if not self.is_running:
                    break
                    
                server_content = response.server_content
                if server_content is not None:
                    if server_content.interrupted:
                        logger.info("[Interrupted] User spoke over bot! Clearing playback queue.")
                        self.audio_source.clear()
                    
                    model_turn = server_content.model_turn
                    if model_turn is not None:
                        for part in model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                logger.info(f"[Response] Received {len(part.inline_data.data)} bytes of spoken audio back from Gemini Live.")
                                audio_bytes = part.inline_data.data
                                upsampled = resample_24_mono_to_48_stereo(audio_bytes)
                                self.audio_source.write(upsampled)
                                
        except Exception as e:
            logger.error(f"Error in Gemini receive loop: {e}")
            await self.stop()