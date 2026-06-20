// core/voice_service/services/discord-gemini-voice-bridge.js

import { 
  joinVoiceChannel, 
  entersState, 
  VoiceConnectionStatus, 
  createAudioPlayer, 
  createAudioResource, 
  StreamType, 
  EndBehaviorType 
} from '@discordjs/voice';
import OpusScript from 'opusscript';
import { GoogleGenAI } from '@google/genai';
import { Readable } from 'stream';

const DISCORD_FRAME_MS = 20;
const GEMINI_INPUT_SAMPLE_RATE = 16000;
const GEMINI_MONO_FRAME_BYTES = 640;

// High-performance downsampling (48kHz Stereo -> 16kHz Mono) [pcm.js]
function downsample48StereoTo16Mono(pcm48Stereo) {
  const decimationFactor = 3; 
  const outputLength = Math.floor(pcm48Stereo.length / (2 * decimationFactor));
  const pcm16Mono = Buffer.alloc(outputLength);
  
  let outIdx = 0;
  for (let i = 0; i < pcm48Stereo.length; i += 2 * decimationFactor) {
    const left = pcm48Stereo.readInt16LE(i);
    const right = pcm48Stereo.readInt16LE(i + 2);
    const mono = Math.round((left + right) / 2);
    
    pcm16Mono.writeInt16LE(mono, outIdx);
    outIdx += 2;
  }
  return pcm16Mono;
}

// High-performance upsampling (24kHz Mono -> 48kHz Stereo) [pcm.js]
function upsample24MonoTo48Stereo(pcm24Mono) {
  const outputBuffer = Buffer.alloc(pcm24Mono.length * 4);
  let outOffset = 0;
  for (let i = 0; i < pcm24Mono.length; i += 2) {
    const sample = pcm24Mono.readInt16LE(i);
    
    // Duplicate 2x for sampling and write left/right
    outputBuffer.writeInt16LE(sample, outOffset);
    outputBuffer.writeInt16LE(sample, outOffset + 2);
    outputBuffer.writeInt16LE(sample, outOffset + 4);
    outputBuffer.writeInt16LE(sample, outOffset + 6);
    outOffset += 8;
  }
  return outputBuffer;
}

// Standard math average mixer for overlapping speakers [pcm.js]
function mixMonoPcmFrames(frames) {
  if (frames.length === 0) return Buffer.alloc(0);
  if (frames.length === 1) return frames[0];

  const mixed = Buffer.alloc(640);
  const attenuation = Math.sqrt(frames.length);

  for (let i = 0; i < 640; i += 2) {
    let sum = 0;
    for (const frame of frames) {
      sum += frame.readInt16LE(i);
    }
    const sample = Math.max(-32768, Math.min(32767, Math.round(sum / attenuation)));
    mixed.writeInt16LE(sample, i);
  }
  return mixed;
}

export class DiscordGeminiVoiceBridge {
  constructor({ guildId, channelId, adapterCreator, systemPrompt, voiceName, geminiApiKey, modelName }) {
    this.guildId = guildId;
    this.channelId = channelId;
    this.adapterCreator = adapterCreator;
    this.systemPrompt = systemPrompt;
    this.voiceName = voiceName;
    this.modelName = modelName;

    this.ai = new GoogleGenAI({ apiKey: geminiApiKey });
    this.session = null;
    this.connection = null;
    
    this.opusDecoder = new OpusScript(48000, 2, OpusScript.Application.AUDIO);
    this.micQueue = [];
    
    // Playback and mixing states
    this.audioPlayer = createAudioPlayer();
    this.pcmPlayoutStream = new Readable({ read() {} });
    this.isPlayoutActive = false;
    
    this.is_running = false;
    this.uploadLoop = null;
    this.turn_sent_audio = false;
    
    // Interruption / Barge-in States [local-barge-in-controller.js]
    this.barge_in_armed = false;
    this.above_threshold_frames = 0;
    this.rms_threshold = 1700;
    this.consecutive_frames = 3;
    this.pre_roll_buffer = [];
    
    this.awaiting_server_ack = false;
  }

  async start() {
    this.is_running = true;

    // 1. Establish Discord connection utilizing the custom Python-bridged gateway adapter [entersState, connection-ready.js]
    this.connection = joinVoiceChannel({
      channelId: this.channelId,
      guildId: this.guildId,
      adapterCreator: this.adapterCreator,
      selfDeaf: false,
      selfMute: false
    });

    // Start entersState as a background task. Since Python is initiating the handshake,
    // ready transition resolves asynchronously as soon as Python forwards the raw gateway state packets.
    entersState(this.connection, VoiceConnectionStatus.Ready, 20000)
      .then(() => {
         console.log(`[Bridge:${this.guildId}] @discordjs/voice successfully connected and Ready.`);
         this.connection.subscribe(this.audioPlayer);
      })
      .catch((err) => {
         console.error(`[Bridge:${this.guildId}] Handshake Ready state timed out:`, err);
      });

    // 2. Build and connect the Gemini live socket [live-session-manager.js]
    const liveConfig = {
      responseModalities: ["AUDIO"],
      speechConfig: {
        voiceConfig: {
          prebuiltVoiceConfig: { voiceName: this.voiceName }
        }
      },
      systemInstruction: {
        parts: [{ text: this.systemPrompt }]
      },
      thinkingConfig: { thinkingLevel: "minimal" },
      realtimeInputConfig: {
        automaticActivityDetection: {
          disabled: false,
          startOfSpeechSensitivity: "START_SENSITIVITY_HIGH",
          endOfSpeechSensitivity: "END_SENSITIVITY_HIGH",
          prefixPaddingMs: 120,
          silenceDurationMs: 350
        }
      }
    };

    this.session = await this.ai.live.connect({
      model: this.modelName,
      config: liveConfig,
      callbacks: {
        onopen: async () => {
          console.log(`[Bridge:${this.guildId}] Gemini Live connection established.`);
          // Fire initial text-based greeting turn once socket completes handshake
          try {
            await this.session.sendClient_content({
              turns: [{
                role: "user",
                parts: [{ text: "System: You have joined the voice call. Greet your friends naturally and briefly." }]
              }],
              turnComplete: true
            });
          } catch (e) {
            console.error("Failed to send text greeting prompt", e);
          }
        },
        onmessage: (msg) => this._handle_server_message(msg),
        onclose: (event) => console.log(`[Bridge:${this.guildId}] Gemini Live socket closed:`, event),
        onerror: (err) => console.error(`[Bridge:${this.guildId}] Gemini Live error:`, err)
      }
    });

    // 3. Register voice listener [multi-user-frame-mixer.js]
    const receiver = this.connection.receiver;
    receiver.speaking.on('start', (userId) => {
      if (!this.is_running) return;
      
      const opusStream = receiver.subscribe(userId, {
        end: {
          behavior: EndBehaviorType.AfterSilence,
          duration: 350
        }
      });

      opusStream.on('data', (packet) => {
        if (!this.is_running) return;
        try {
          const decodedRaw = this.opusDecoder.decode(packet);
          const pcm48 = Buffer.from(decodedRaw);
          const pcm16 = downsample48StereoTo16Mono(pcm48);
          this.micQueue.push(pcm16);
        } catch (err) {
          // Silent catch to handle corrupted Opus streams cleanly
        }
      });
    });

    // 4. Start the 20ms Continuous frame clock loop
    this._start_upload_loop();
  }

  _handle_server_message(message) {
    const serverContent = message.serverContent;
    if (!serverContent) return;

    // Clear buffer instantly if interrupted [model-audio-gate.js, pcm-playback-queue.js]
    if (serverContent.interrupted) {
      console.log(`[Bridge:${this.guildId}] Interrupted by server. Playout buffer cleared.`);
      this.pcmPlayoutStream = new Readable({ read() {} });
      this.isPlayoutActive = false;
      this.awaiting_server_ack = false;
      this.barge_in_armed = false;
      this.turn_sent_audio = false;
    }

    if (serverContent.turnComplete) {
      this.awaiting_server_ack = false;
      this.turn_sent_audio = false;
    }

    // Suppress audio while gating barge-in confirmation
    if (this.awaiting_server_ack) return;

    const parts = serverContent.modelTurn?.parts || [];
    for (const part of parts) {
      const inlineData = part.inlineData;
      if (inlineData?.data) {
        const raw24Mono = Buffer.from(inlineData.data, 'base64');
        const raw48Stereo = upsample24MonoTo48Stereo(raw24Mono);

        if (!this.isPlayoutActive) {
          this.pcmPlayoutStream = new Readable({ read() {} });
          const resource = createAudioResource(this.pcmPlayoutStream, { inputType: StreamType.Raw });
          this.audioPlayer.play(resource);
          this.isPlayoutActive = true;
        }
        this.pcmPlayoutStream.push(raw48Stereo);
      }
    }
  }

  _start_upload_loop() {
    const tick = async () => {
      if (!this.is_running) return;
      const startTime = process.hrtime.bigint();

      // Mix frame from queue
      const activeFrames = [...this.micQueue];
      this.micQueue = [];

      if (activeFrames.length > 0) {
        const mixed = mixMonoPcmFrames(activeFrames);
        const isAIPlaying = this.isPlayoutActive;

        // Arm barge-in checking if the bot is currently speaking
        if (isAIPlaying && !this.turn_sent_audio) {
          this.barge_in_armed = true;
        }

        if (isAIPlaying && this.barge_in_armed) {
          // Push current frame into a rolling pre-roll buffer (keep last 12 frames / 240ms)
          this.pre_roll_buffer.push(mixed);
          if (this.pre_roll_buffer.length > 12) this.pre_roll_buffer.shift();

          // Calculate RMS of the current frame [local-barge-in-controller.js]
          let sumSquares = 0;
          for (let i = 0; i < mixed.length; i += 2) {
            const val = mixed.readInt16LE(i);
            sumSquares += val * val;
          }
          const rms = Math.sqrt(sumSquares / (mixed.length / 2));

          if (rms >= this.rms_threshold) {
            this.above_threshold_frames++;
          } else {
            this.above_threshold_frames = 0;
          }

          if (this.above_threshold_frames >= this.consecutive_frames) {
            // Local Interruption Triggered!
            console.log(`[Bridge:${this.guildId}] Local Barge-In triggered! Clearing playout.`);
            this.pcmPlayoutStream = new Readable({ read() {} });
            this.isPlayoutActive = false;
            this.awaiting_server_ack = true;
            this.barge_in_armed = false;
            this.above_threshold_frames = 0;

            // Push pre-roll frames to server instantly to prevent chopped speech
            for (const pr of this.pre_roll_buffer) {
              await this.session.sendRealtime_input({
                audio: { data: pr.toString('base64'), mimeType: `audio/pcm;rate=${GEMINI_INPUT_SAMPLE_RATE}` }
              });
            }
            this.pre_roll_buffer = [];

            // Forward the active trigger frame
            await this.session.sendRealtime_input({
              audio: { data: mixed.toString('base64'), mimeType: `audio/pcm;rate=${GEMINI_INPUT_SAMPLE_RATE}` }
            });
            this.turn_sent_audio = true;
            
            scheduleNextTick();
            return;
          }
        }

        // Active speech frame forward
        await this.session.sendRealtime_input({
          audio: { data: mixed.toString('base64'), mimeType: `audio/pcm;rate=${GEMINI_INPUT_SAMPLE_RATE}` }
        });
        this.turn_sent_audio = true;
      } else {
        // Comfort Noise Pump: Stream zeroed silence frames continuously [pcm.js]
        // to feed Gemini's server-side VAD silence calculations
        const silence = Buffer.alloc(GEMINI_MONO_FRAME_BYTES);
        await this.session.sendRealtime_input({
          audio: { data: silence.toString('base64'), mimeType: `audio/pcm;rate=${GEMINI_INPUT_SAMPLE_RATE}` }
        });
      }

      // Self-correcting clock to keep precise 20ms timing boundaries
      const scheduleNextTick = () => {
        const endTime = process.hrtime.bigint();
        const elapsedMs = Number(endTime - startTime) / 1000000;
        const delay = Math.max(1, DISCORD_FRAME_MS - elapsedMs);
        this.uploadLoop = setTimeout(tick, delay);
      };

      scheduleNextTick();
    };

    this.uploadLoop = setTimeout(tick, DISCORD_FRAME_MS);
  }

  async stop() {
    this.is_running = false;
    if (this.uploadLoop) {
      clearTimeout(this.uploadLoop);
    }

    if (this.audioPlayer) {
      this.audioPlayer.stop();
    }

    if (this.connection) {
      try {
        this.connection.destroy();
      } catch (err) {}
    }

    if (this.session) {
      try {
        await this.session.close();
      } catch (err) {}
    }

    if (this.opusDecoder) {
      try {
        this.opusDecoder.delete();
      } catch (err) {}
    }
  }
}