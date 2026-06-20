
import numpy as np

DISCORD_INPUT_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000
PCM_BYTES_PER_SAMPLE = 2
DISCORD_FRAME_MS = 20

DISCORD_PLAYBACK_FRAME_BYTES = 960 * DISCORD_CHANNELS * PCM_BYTES_PER_SAMPLE
GEMINI_MONO_FRAME_BYTES = int((GEMINI_INPUT_SAMPLE_RATE / 1000) * DISCORD_FRAME_MS * PCM_BYTES_PER_SAMPLE)

def resample_48_stereo_to_16_mono(pcm_data: bytes) -> bytes:
    if not pcm_data:
        return b""
    try:
        samples = np.frombuffer(pcm_data, dtype=np.int16)
        if len(samples) == 0:
            return b""
        
        stereo_samples = samples.reshape(-1, DISCORD_CHANNELS)
        
        mono_samples = (stereo_samples[:, 0].astype(np.int32) + stereo_samples[:, 1].astype(np.int32)) // 2
        
        resampled_samples = mono_samples[::3].astype(np.int16)
        
        return resampled_samples.tobytes()
    except Exception:
        return b""

def resample_24_mono_to_48_stereo(pcm_data: bytes) -> bytes:
    if not pcm_data:
        return b""
    try:
        mono_24k = np.frombuffer(pcm_data, dtype=np.int16)
        if len(mono_24k) == 0:
            return b""
        
        mono_48k = np.repeat(mono_24k, 2)
        
        stereo_48k = np.empty((len(mono_48k), 2), dtype=np.int16)
        stereo_48k[:, 0] = mono_48k
        stereo_48k[:, 1] = mono_48k
        
        return stereo_48k.flatten().tobytes()
    except Exception:
        return b""

def mix_mono_pcm_frames(frames: list[bytes]) -> bytes:
    if not frames:
        return b""
    if len(frames) == 1:
        return frames[0]
        
    try:
        np_frames = [np.frombuffer(f, dtype=np.int16).astype(np.int32) for f in frames if f]
        if not np_frames:
            return b""
            
        mixed = np.sum(np_frames, axis=0)
        
        attenuation = np.sqrt(len(np_frames))
        mixed = mixed / attenuation
        
        clamped = np.clip(mixed, -32768, 32767).astype(np.int16)
        return clamped.tobytes()
    except Exception:
        return b""

def compute_mono_pcm_rms(pcm_data: bytes) -> float:
    if not pcm_data:
        return 0.0
    try:
        samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))
    except Exception:
        return 0.0

def mono_pcm_duration_ms(pcm_data: bytes, sample_rate: int) -> int:
    if not pcm_data:
        return 0
    samples = len(pcm_data) // PCM_BYTES_PER_SAMPLE
    return int(max(1, round((samples / sample_rate) * 1000)))