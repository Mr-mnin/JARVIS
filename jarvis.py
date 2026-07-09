#!/usr/bin/env python3
"""
Jarvis: Autonomous Cyberpunk Agent Terminal with Stream-Based Audio Routines,
Dual-Mode AI Agent Toolkits (Online/Offline), and Hardware-Accelerated Tkinter HUD.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

import numpy as np
import sounddevice as sd
import tkinter as tk
from tkinter import ttk, scrolledtext

# Speech recognition initialization
try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False

# Third-party tracking libraries for macro mechanics
try:
    from pynput import mouse, keyboard
except ImportError:
    mouse, keyboard = None, None

# --- Tuning Knobs -----------------------------------------------------------
SAMPLE_RATE = 44100       
BLOCK_MS = 20             
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_MS / 1000)

SPIKE_RATIO = 5.0         
MIN_RMS = 0.01            
COOLDOWN_S = 1.5          
MIN_DOUBLE_GAP_S = 0.12   
MAX_DOUBLE_GAP_S = 0.85
NOISE_FLOOR_ALPHA = 0.992 
RETRIGGER_RATIO = 0.4

# --- Python TTS Settings ----------------------------------------------------
JARVIS_WELCOME_PHRASE = "Jarvis online. Cybernetic HUD terminals loaded. Voice matrix online, sir."
MACRO_STORAGE_FILE = Path(__file__).resolve().parent / "learned_macros.json"

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent / "voice.env", override=False)

# --- AI Gateways ------------------------------------------------------------
FREETHEAI_ENDPOINT = "https://api.freetheai.xyz/v1/chat/completions"
FREETHEAI_MODEL    = "deepseek-r1-distill-qwen-32b"  
FREETHEAI_API_KEY  = os.getenv("FREETHEAI_API_KEY", "").strip()

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/messages"
OPENROUTER_MODEL    = "openrouter/free"
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "").strip()

# --- Google Search Settings ------------------------------------------------
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX", "").strip()

# Synchronized Communication Queues
gui_log_queue = queue.Queue()
audio_visualization_queue = queue.Queue()
SYSTEM_ACTIVE = True
tracer = None
command_lock = threading.Lock()  

# --- Cyberpunk Visual Palette Constants ------------------------------------
BG_MAIN = "#0A0A12"       
BG_PANEL = "#121222"      
FG_TEXT = "#00F0FF"       
FG_ACCENT = "#FF0055"     
FG_DIM = "#557799"        


class QueueLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        gui_log_queue.put(log_entry)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis_system")
queue_handler = QueueLogHandler()
queue_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
log.addHandler(queue_handler)


# --- Python Native TTS Dispatcher -----------------------------------------
_tts_engine = None

def _init_tts_engine():
    global _tts_engine
    try:
        import pyttsx3
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", 160)  
        _tts_engine.setProperty("volume", 0.9)  
        voices = _tts_engine.getProperty("voices")
        if voices:
            preferred_names = ["cortana", "david", "zira", "emily", "mark"]
            selected_voice = voices[0]  
            for voice in voices:
                voice_name = voice.name.lower()
                if any(pref in voice_name for pref in preferred_names):
                    selected_voice = voice
                    log.info(f"[TTS] Using system voice configuration: {voice.name}")
                    break
            _tts_engine.setProperty("voice", selected_voice.id)
        return True
    except Exception as e:
        log.error(f"[TTS] Engine failed to bind: {e}")
        return False


def say(text: str) -> None:
    log.info(f"Jarvis: {text}")
    global _tts_engine
    if _tts_engine is None:
        if not _init_tts_engine(): return
    try:
        _tts_engine.say(text)
        _tts_engine.runAndWait()
    except Exception as e:
        log.error(f"[TTS] Playback interruption: {e}")


def is_online() -> bool:
    try:
        socket.setdefaulttimeout(1.5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False


# --- Hybrid Engine Tools (Fixes 403 Crashes By Running Locally First) -------
def execute_command(cmd_text: str) -> str:
    """Interceptors to catch local commands before sending requests to the cloud."""
    cmd_lower = cmd_text.lower().strip()
    
    # 1. Directory Scanning
    if "list" in cmd_lower and ("folder" in cmd_lower or "directory" in cmd_lower or "file" in cmd_lower):
        try:
            items = list(Path(".").iterdir())[:12]
            return "Active workspace manifest: " + ", ".join([i.name for i in items])
        except Exception as e:
            return f"Cannot read local folder map: {e}"
    
    # 2. Local Application Orchestration
    if any(act in cmd_lower for act in ["open", "launch", "start", "run"]):
        if "calculator" in cmd_lower or "calc" in cmd_lower:
            subprocess.Popen("calc.exe" if sys.platform == "win32" else "gnome-calculator")
            return "Local calculating processor launched."
            
        if "notepad" in cmd_lower or "notes" in cmd_lower:
            subprocess.Popen("notepad.exe" if sys.platform == "win32" else "gedit")
            return "Notepad notepad terminal initialized."
            
        if "file" in cmd_lower or "explorer" in cmd_lower or "folder" in cmd_lower or "files" in cmd_lower:
            path = os.environ.get("USERPROFILE", os.path.expanduser("~"))
            if sys.platform == "win32": os.startfile(path)
            else: subprocess.Popen(["xdg-open", path])
            return "File array index deployed into workspace view."

        if "discord" in cmd_lower or "dicord" in cmd_lower:
            appdata = os.environ.get("LOCALAPPDATA", "")
            discord_paths = list(Path(appdata).glob("Discord/app-*/Discord.exe"))
            if discord_paths:
                subprocess.Popen(str(discord_paths[0]))
                return "Discord communications platform online."
            else:
                try:
                    subprocess.Popen("discord")
                    return "Broadcasting global execution call to application path for Discord."
                except Exception:
                    return "Target application binary not found in standard system directory scopes."

    # 3. Dynamic Browser Search Matrix
    if any(x in cmd_lower for x in ["search", "look up", "find", "google", "what is"]):
        words = cmd_lower.split()
        query = " ".join(words[1:]) if len(words) > 1 else cmd_lower
        open_google_search(query)
        result = google_search(query)
        return f"Query dispatched to web engine instance for '{query}'. {result}"
    
    # 4. Temporal Core Status
    if "time" in cmd_lower:
        from datetime import datetime
        return f"System time is: {datetime.now().strftime('%H:%M:%S')}."
    if "date" in cmd_lower:
        from datetime import datetime
        return f"Standard timeline calendar reads: {datetime.now().strftime('%A, %B %d, %Y')}."
    
    # 5. Remote AI Fallback Engine
    ai_response = query_ai_fallback(cmd_text)
    if ai_response:
        return ai_response
    
    return f"Directive parsed but unmapped locally: '{cmd_text}'. System is currently experiencing API drops."


def google_search(query: str) -> str:
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_CX:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
                if snippets:
                    text = " ".join([re.sub(r"<[^>]+>", "", s) for s in snippets[:3]])
                    return f"Index results: {text[:300]}"
                return "No records matched query."
        except Exception as e:
            return f"Scraper matrix offline: {e}"
    try:
        url = f"https://www.googleapis.com/customsearch/v1?q={urllib.parse.quote(query)}&key={GOOGLE_SEARCH_API_KEY}&cx={GOOGLE_SEARCH_CX}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("items"):
                result = res["items"][0]
                return f"{result.get('title', '')}: {result.get('snippet', '')[:200]}"
            return "No matching search instances found."
    except Exception as e:
        return "Search framework unavailable."


def open_google_search(query: str) -> None:
    try:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        if sys.platform == "win32": os.startfile(url)
        else: subprocess.Popen(["xdg-open", url])
    except Exception as e:
        log.error(f"[Browser Matrix] Failed to build link frame: {e}")


def query_ai_fallback(prompt: str) -> str | None:
    if not is_online(): return None
    if FREETHEAI_API_KEY:
        try:
            payload = json.dumps({"model": FREETHEAI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}).encode("utf-8")
            req = urllib.request.Request(FREETHEAI_ENDPOINT, data=payload, headers={"Authorization": f"Bearer {FREETHEAI_API_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"].strip()
        except Exception: pass
    if OPENROUTER_API_KEY:
        try:
            payload = json.dumps({"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
            req = urllib.request.Request(OPENROUTER_ENDPOINT, data=payload, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"].strip()
        except Exception: pass
    return None


def agent_reasoning_loop(initial_prompt: str) -> None:
    log.info(f"[Command Received] {initial_prompt}")
    with command_lock:
        response = execute_command(initial_prompt)
    if response:
        say(response)


# --- Re-Implemented Speech Recognition Engine (Fixed Definition Bug) ---------
def listen_for_command() -> str | None:
    """Verbal package mapping logic. Fallback to raw Sounddevice audio blocks if PyAudio fails."""
    if not SPEECH_RECOGNITION_AVAILABLE:
        log.warning("[Speech] speech_recognition dependency is missing.")
        return None

    recognizer = sr.Recognizer()
    
    # Hybrid validation: try PyAudio extraction, gracefully fall back to sounddevice arrays
    try:
        with sr.Microphone() as source:
            log.info("[Speech Calibration] Profiling background acoustics...")
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
            log.info("[Uplink Matrix Active] Speak directive now...")
            audio = recognizer.listen(source, timeout=4, phrase_time_limit=5)
            text = recognizer.recognize_google(audio)
            log.info(f"[Speech Registration Success] Parsed string: '{text}'")
            return text
    except (sr.UnknownValueError, sr.WaitTimeoutError):
        log.warning("[Speech Matrix] Audio track unclear or recording window timed out.")
    except Exception as pyaudio_err:
        log.warning(f"[Speech Matrix] PyAudio hook dropped ({pyaudio_err}). Attempting native Sounddevice bridge...")
        try:
            # Sounddevice fallback stream frame mapping
            duration = 4
            fs = 16000
            recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
            sd.wait()
            
            # Pack binary array chunks into the structural format speech_recognition expects
            audio_data = sr.AudioData(recording.tobytes(), fs, 2)
            text = recognizer.recognize_google(audio_data)
            log.info(f"[Sounddevice Bypass Uplink] Parsed: '{text}'")
            return text
        except Exception as sd_err:
            log.error(f"[Acoustic Critical Failure] Both audio backend options failed: {sd_err}")
    return None


# --- Desktop Input Macro Tracer ---------------------------------------------
class DesktopInteractionTracer:
    def __init__(self):
        self.events, self.anchor_time, self.tracking_active = [], 0, False
        self._mouse_hook, self._key_hook = None, None

    def begin_trace(self):
        if not mouse or not keyboard: return False
        self.events, self.anchor_time, self.tracking_active = [], time.time(), True
        self._mouse_hook = mouse.Listener(on_click=self._on_click)
        self._key_hook = keyboard.Listener(on_press=self._on_press)
        self._mouse_hook.start(); self._key_hook.start()
        log.info("Macro matrix tracking online. Recording coordinate space inputs...")
        return True

    def end_trace(self, macro_name: str) -> str:
        self.tracking_active = False
        if self._mouse_hook: self._mouse_hook.stop()
        if self._key_hook: self._key_hook.stop()
        if not self.events: return "No interface interactions caught inside time index block."
        
        stored = {}
        if MACRO_STORAGE_FILE.exists():
            try: stored = json.loads(MACRO_STORAGE_FILE.read_text())
            except Exception: pass
        stored[macro_name.lower()] = self.events
        MACRO_STORAGE_FILE.write_text(json.dumps(stored, indent=4))
        return f"Macro array frame saved. Profile assigned to database: '{macro_name}'."

    def _on_click(self, x, y, button, pressed):
        if pressed and self.tracking_active:
            self.events.append({"type": "click", "x": x, "y": y, "button": str(button), "delay": time.time() - self.anchor_time})
            self.anchor_time = time.time()

    def _on_press(self, key):
        if self.tracking_active:
            try: k_str = key.char
            except AttributeError: k_str = str(key)
            self.events.append({"type": "keypress", "key": k_str, "delay": time.time() - self.anchor_time})
            self.anchor_time = time.time()

tracer = DesktopInteractionTracer()


def route_command_intent(command_text: str) -> None:
    global SYSTEM_ACTIVE
    cmd_clean = command_text.lower().strip()
    
    if any(keyword in cmd_clean for keyword in ["listen", "voice input", "speech", "hear me"]):
        recognized_text = listen_for_command()
        if recognized_text: route_command_intent(recognized_text)
        return

    if any(term in cmd_clean for term in ["terminate", "shutdown", "exit", "go to sleep"]):
        say("Terminating structural HUD matrix loops. Operational status offline.")
        SYSTEM_ACTIVE = False
        return

    if tracer.tracking_active and "stop learning" in cmd_clean:
        target = cmd_clean.replace("stop learning", "").strip() or "custom_workflow"
        say(tracer.end_trace(target))
        return

    if "start learning task" in cmd_clean:
        target = cmd_clean.replace("start learning task", "").strip()
        if not target: say("Specify clear macro database target key."); return
        if tracer.begin_trace(): say(f"Tracking frame active. Context mapped to profile: '{target}'. Run 'stop learning' to commit.")
        return

    threading.Thread(target=agent_reasoning_loop, args=(command_text,), daemon=True).start()


# --- Stream-Based Audio Extraction Worker -----------------------------------
def hardware_audio_stream_worker():
    global SYSTEM_ACTIVE
    audio_buffer = queue.Queue()
    
    def callback(indata, frames, time_info, status):
        audio_buffer.put(indata.copy())
        audio_visualization_queue.put(indata[::2, 0])

    log.info(f"Opening low-latency audio capture stream vector frame at {SAMPLE_RATE}Hz...")
    noise_floor = MIN_RMS
    first_clap_time = None
    last_logged_double = 0.0
    in_retrigger = False

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1, callback=callback):
            while SYSTEM_ACTIVE:
                try: block = audio_buffer.get(timeout=0.2)
                except queue.Empty: continue

                rms_val = np.sqrt(np.mean(block**2))
                noise_floor = (NOISE_FLOOR_ALPHA * noise_floor) + ((1.0 - NOISE_FLOOR_ALPHA) * rms_val)
                threshold = max(MIN_RMS, noise_floor * SPIKE_RATIO)

                if in_retrigger:
                    if rms_val < (threshold * RETRIGGER_RATIO): in_retrigger = False
                    continue

                if rms_val > threshold:
                    now = time.time()
                    if (now - last_logged_double) < COOLDOWN_S: continue
                    in_retrigger = True
                    
                    if first_clap_time is None:
                        first_clap_time = now
                    else:
                        gap = now - first_clap_time
                        if MIN_DOUBLE_GAP_S <= gap <= MAX_DOUBLE_GAP_S:
                            first_clap_time = None
                            last_logged_double = now
                            log.info(f"[Acoustic Wake Event] Double-Pulse Transient Spike Registered ({gap:.3f}s)")
                            
                            say("System listening, state your directive.")
                            v_command = listen_for_command()
                            if v_command: route_command_intent(v_command)
                        else:
                            first_clap_time = now
    except Exception as e:
        log.error(f"Hardware sound device audio stream exception: {e}")


# --- Cyberpunk UI Terminal HUD ----------------------------------------------
class CyberpunkHUD(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("J.A.R.V.I.S // CORE INTENT ENGINE INTERFACE")
        self.geometry("950x620")
        self.configure(bg=BG_MAIN)
        self.build_hud_layout()
        self.enforce_foreground_activation()
        self.refresh_ui_loop()

    def build_hud_layout(self):
        header = tk.Frame(self, bg=BG_PANEL, height=45, bd=1, relief="flat")
        header.pack(fill="x", side="top", padx=8, pady=5)
        
        title_lbl = tk.Label(header, text="⚡ JARVIS AUTOMATION AGENT // COGNITIVE MATRIX HUD", font=("Consolas", 12, "bold"), bg=BG_PANEL, fg=FG_TEXT)
        title_lbl.pack(side="left", padx=10, pady=10)
        
        self.status_lbl = tk.Label(header, text="CORE_GRID: SECURE", font=("Consolas", 10, "bold"), bg=BG_PANEL, fg="#00FF66")
        self.status_lbl.pack(side="right", padx=10)

        middle_pane = tk.Frame(self, bg=BG_MAIN)
        middle_pane.pack(fill="both", expand=True, padx=8, pady=2)

        viz_frame = tk.LabelFrame(middle_pane, text=" [ HIGH-FIDELITY STREAM ACOUSTIC SPECTRUM ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_ACCENT, bd=1, labelanchor="nw")
        viz_frame.pack(fill="x", side="top", pady=4)
        self.canvas = tk.Canvas(viz_frame, height=60, bg=BG_MAIN, highlightthickness=0)
        self.canvas.pack(fill="x", padx=5, pady=5)

        log_frame = tk.LabelFrame(middle_pane, text=" [ COGNITIVE SYSTEM ENGINE ACTIVITY LOGS ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_TEXT, bd=1, labelanchor="nw")
        log_frame.pack(fill="both", expand=True, side="bottom", pady=4)
        
        self.log_area = scrolledtext.ScrolledText(log_frame, font=("Consolas", 10), bg=BG_MAIN, fg=FG_TEXT, insertbackground=FG_TEXT, bd=0, highlightthickness=0)
        self.log_area.pack(fill="both", expand=True, padx=6, pady=6)

        input_frame = tk.LabelFrame(self, text=" [ TRANSMIT DIRECT SYSTEM ARCHITECTURE COMMAND ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_TEXT, bd=1, labelanchor="nw")
        input_frame.pack(fill="x", side="bottom", padx=8, pady=8)

        self.cmd_entry = tk.Entry(input_frame, font=("Consolas", 11), bg=BG_MAIN, fg=FG_TEXT, insertbackground=FG_TEXT, bd=0, highlightthickness=0)
        self.cmd_entry.pack(fill="x", side="left", expand=True, padx=8, pady=8)
        self.cmd_entry.bind("<Return>", self.dispatch_text_command)

        send_btn = tk.Button(input_frame, text="TRANSMIT_ ", font=("Consolas", 10, "bold"), bg=BG_PANEL, fg=FG_ACCENT, activebackground=FG_ACCENT, activeforeground=BG_MAIN, bd=1, relief="flat", command=self.dispatch_text_command)
        send_btn.pack(side="right", padx=6, pady=4)

    def enforce_foreground_activation(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def dispatch_text_command(self, event=None):
        raw_cmd = self.cmd_entry.get().strip()
        if raw_cmd:
            log.info(f"[HUD Command Terminal Input] -> '{raw_cmd}'")
            route_command_intent(raw_cmd)
            self.cmd_entry.delete(0, tk.END)

    def refresh_ui_loop(self):
        while not gui_log_queue.empty():
            try:
                line = gui_log_queue.get_nowait()
                self.log_area.insert(tk.END, line + "\n")
                self.log_area.see(tk.END)
            except queue.Empty: break

        while not audio_visualization_queue.empty():
            try:
                wave_data = audio_visualization_queue.get_nowait()
                self.canvas.delete("wave")
                w = self.canvas.winfo_width()
                h = self.canvas.winfo_height()
                points = []
                step = max(1, w / len(wave_data))
                for i, val in enumerate(wave_data):
                    x = i * step
                    raw_y = (h / 2) + (val * h * 15.0)
                    y = max(2, min(h - 2, raw_y))
                    points.extend([x, y])
                if len(points) >= 4:
                    self.canvas.create_line(points, fill=FG_ACCENT, width=1, tags="wave")
            except queue.Empty: break

        if not SYSTEM_ACTIVE:
            self.destroy()
            return
        self.after(25, self.refresh_ui_loop)


def main() -> int:
    threading.Thread(target=say, args=(JARVIS_WELCOME_PHRASE,), daemon=True).start()
    
    # Audio capture thread re-activated
    audio_hardware_thread = threading.Thread(target=hardware_audio_stream_worker, daemon=True)
    audio_hardware_thread.start()
            
    hud_window = CyberpunkHUD()
    hud_window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())