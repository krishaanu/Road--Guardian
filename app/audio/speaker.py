import os
import time
import threading
import logging
from gtts import gTTS
import pygame

logger = logging.getLogger("AudioSpeaker")


class AudioSpeaker:
    """Manages MP3 alerts and text-to-speech warnings without blocking video processing."""

    def __init__(self, audio_dir: str = "data/audio", cooldown_sec: float = 3.5):
        self.audio_dir = audio_dir
        os.makedirs(self.audio_dir, exist_ok=True)
        self.mixer_initialized = False
        try:
            pygame.mixer.init()
            self.mixer_initialized = True
            logger.info("Pygame mixer initialized successfully.")
        except Exception as e:
            logger.warning(f"Pygame mixer initialization failed (audio will be muted): {e}")
            self.mixer_initialized = False

        self.last_played_time = {}
        self.last_sound_played_timestamp = 0.0
        self.cooldown_sec = max(3.0, float(cooldown_sec))  # Enforce minimum 3.0-4.0 seconds between played sounds

    def _play_file(self, filepath: str):
        if not self.mixer_initialized:
            return

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
                self.mixer_initialized = True

            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            while pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                time.sleep(0.1)
        except Exception as e:
            logger.debug(f"[AudioSpeaker Playback Error] {e}")

    def speak_alert(self, text: str):
        """Generates and plays an MP3 voice warning asynchronously with cooldown protection."""
        if not self.mixer_initialized:
            # Check if mixer can now be initialized
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                    self.mixer_initialized = True
            except Exception:
                return

        now = time.time()
        # Global cooldown between any played sounds to prevent repetitive audio spam
        if (now - self.last_sound_played_timestamp) < self.cooldown_sec:
            return

        # Per-alert text cooldown
        if text in self.last_played_time and (now - self.last_played_time[text]) < self.cooldown_sec:
            return

        self.last_sound_played_timestamp = now
        self.last_played_time[text] = now

        def run():
            try:
                filename = "".join(c for c in text if c.isalnum() or c == ' ').strip().replace(" ", "_").lower() + ".mp3"
                filepath = os.path.join(self.audio_dir, filename)

                if not os.path.exists(filepath):
                    tts = gTTS(text=text, lang='en')
                    tts.save(filepath)

                self._play_file(filepath)
            except Exception as e:
                logger.debug(f"[AudioSpeaker Generation Error] {e}")

        threading.Thread(target=run, daemon=True).start()
