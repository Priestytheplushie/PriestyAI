
import collections
import logging
from core.voice.audio_utils import compute_mono_pcm_rms, mono_pcm_duration_ms, GEMINI_INPUT_SAMPLE_RATE

logger = logging.getLogger("BargeInController")

class LocalBargeInController:
    def __init__(self, rms_threshold: int = 1700, consecutive_frames: int = 3, pre_roll_ms: int = 240):
        self.rms_threshold = rms_threshold
        self.consecutive_frames = consecutive_frames
        self.pre_roll_ms = pre_roll_ms
        self.barge_in_mode = False
        self.above_threshold_frames = 0
        self.pre_roll_queue = collections.deque()
        
    def reset(self):
        self.barge_in_mode = False
        self.above_threshold_frames = 0
        self.pre_roll_queue.clear()
        
    def arm_if_playback_started_before_turn(self, playback_active: bool, turn_sent_audio: bool):
        if not turn_sent_audio and playback_active:
            self.barge_in_mode = True
            
    def clear_detection_window(self):
        self.above_threshold_frames = 0
        self.pre_roll_queue.clear()
        
    def evaluate_frame(self, pcm_data: bytes, speaker_ids: list[str]) -> dict:
        if not self.barge_in_mode:
            return {"action": "pass"}
            
        chunk_dur_ms = mono_pcm_duration_ms(pcm_data, GEMINI_INPUT_SAMPLE_RATE)
        max_pre_roll = max(1, int(self.pre_roll_ms / chunk_dur_ms))
        
        self.pre_roll_queue.append({"pcm_data": pcm_data, "speaker_ids": speaker_ids})
        if len(self.pre_roll_queue) > max_pre_roll:
            self.pre_roll_queue.popleft()
            
        rms = compute_mono_pcm_rms(pcm_data)
        if rms >= self.rms_threshold:
            self.above_threshold_frames += 1
        else:
            self.above_threshold_frames = 0
            
        if self.above_threshold_frames < self.consecutive_frames:
            return {"action": "hold"}
            
        sequential_pre_roll = list(self.pre_roll_queue)
        frames_count = self.above_threshold_frames
        
        self.clear_detection_window()
        self.barge_in_mode = False
        
        logger.info(f"Qualified barge-in detected from {speaker_ids} (RMS: {int(rms)}, Consecutive Frames: {frames_count})")
        
        return {
            "action": "barge_in",
            "pre_roll_chunks": sequential_pre_roll,
            "rms": rms,
            "frames": frames_count
        }