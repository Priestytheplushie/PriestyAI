
import asyncio
import logging
import random
import time
from typing import Callable, Optional
from google import genai
from google.genai import types

logger = logging.getLogger("LiveSessionManager")

class GeminiLiveSessionManager:
    def __init__(
        self,
        api_key: str,
        model: str,
        voice_name: str,
        system_prompt: str,
        on_message_callback: Callable[[types.LiveServerMessage], None],
        on_reset_callback: Callable[[str], None],
    ):
        self.api_key = api_key
        self.model = model
        self.voice_name = voice_name
        self.system_prompt = system_prompt
        self.on_message = on_message_callback
        self.on_reset = on_reset_callback

        self.client = genai.Client(api_key=api_key)
        self.session: Optional[genai.aio.live.AsyncSession] = None
        self.is_connected = False
        self.is_destroyed = False
        
        self._connection_task: Optional[asyncio.Task] = None
        
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 6
        self.base_delay_seconds = 1.0
        self.max_delay_seconds = 30.0

    def build_live_config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=self.system_prompt)]
            ),
            thinking_config=types.ThinkingConfig(
                thinking_level="MINIMAL"
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=120,
                    silence_duration_ms=350,
                )
            )
        )

    async def connect(self):
        if self.is_destroyed:
            return
        if self._connection_task and not self._connection_task.done():
            return
            
        self._connection_task = asyncio.create_task(self._session_loop())

    async def _session_loop(self):
        while not self.is_destroyed:
            try:
                config = self.build_live_config()
                logger.info(f"Connecting stateful Gemini Live session (Model: {self.model})")
                
                async with self.client.aio.live.connect(model=self.model, config=config) as session:
                    self.session = session
                    self.is_connected = True
                    
                    logger.info("Gemini Live connection opened and handshaking completed.")
                    
                    if self.reconnect_attempts == 0:
                        try:
                            await session.send_client_content(
                                turns=[
                                    types.Content(
                                        role="user",
                                        parts=[types.Part.from_text(text="System: You have joined the voice call. Greet your friends naturally and briefly.")]
                                    )
                                ],
                                turn_complete=True
                            )
                        except Exception as greet_err:
                            logger.error(f"Failed to send initial system greeting prompt: {greet_err}")
                    
                    self.reconnect_attempts = 0
                    
                    while not self.is_destroyed and self.is_connected:
                        try:
                            async for message in session.receive():
                                if self.is_destroyed:
                                    break
                                    
                                try:
                                    self.on_message(message)
                                except Exception as e:
                                    logger.error(f"Error executing message callback: {e}", exc_info=True)
                        except asyncio.CancelledError:
                            raise
                        except Exception as rx_exc:
                            logger.debug(f"Session receiver stream exited due to transport closure: {rx_exc}")
                            break
                            
                        await asyncio.sleep(0.02)
                            
                logger.info("Gemini Live WebSocket loop exited naturally.")
            except asyncio.CancelledError:
                logger.info("Gemini Live connection task cancelled.")
                break
            except Exception as e:
                logger.warning(f"Error in Gemini Live connection runner: {e}")
                
            self.is_connected = False
            self.session = None
            
            if self.is_destroyed:
                break
                
            self.reconnect_attempts += 1
            if self.reconnect_attempts > self.max_reconnect_attempts:
                logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached. Stopping live session.")
                break
                
            delay = min(
                self.max_delay_seconds,
                self.base_delay_seconds * (2 ** (self.reconnect_attempts - 1)) * (0.5 + random.random())
            )
            logger.info(f"Reconnecting to Gemini Live (Attempt {self.reconnect_attempts}/{self.max_reconnect_attempts}) in {delay:.2f} seconds...")
            await asyncio.sleep(delay)
            
            try:
                await self.on_reset(f"reconnect_attempt_{self.reconnect_attempts}")
            except Exception as e:
                logger.error(f"Error executing on_reset callback: {e}")

    async def send_audio(self, pcm_mono_16k_data: bytes):
        if not self.is_connected or not self.session:
            return
        try:
            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_mono_16k_data,
                    mime_type="audio/pcm;rate=16000"
                )
            )
        except Exception as e:
            logger.error(f"Failed to transmit audio bytes over Gemini Live WebSocket: {e}")
            await self.trigger_reconnect()

    async def send_text_prompt(self, text: str, end_of_turn: bool = True):
        if not self.is_connected or not self.session:
            return
        try:
            await self.session.send_client_content(
                turns=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=text)]
                    )
                ],
                turn_complete=end_of_turn
            )
        except Exception as e:
            logger.error(f"Failed to send text prompt: {e}")

    async def send_realtime_control(self, payload: types.LiveClientRealtimeInput):
        if not self.is_connected or not self.session:
            return
        try:
            await self.session.send(input=payload)
        except Exception as e:
            logger.error(f"Failed to transmit control payload: {e}")

    async def trigger_reconnect(self):
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
        self.is_connected = False
        self.session = None
        await self.on_reset("force_reconnect")
        await self.connect()

    async def stop(self):
        self.is_destroyed = True
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        self.is_connected = False
        self.session = None
        logger.info("Gemini Live connection manager successfully stopped.")