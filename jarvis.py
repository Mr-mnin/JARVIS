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

# Third-party tracking libraries for macro mechanics
try:
    from pynput import mouse, keyboard
except ImportError:
    mouse, keyboard = None, None

# --- Tuning Knobs (Imported from jarvis(gitvers).py) ------------------------
SAMPLE_RATE = 44100       # High-fidelity sample rate matching your audio hardware
BLOCK_MS = 20             # Snappier 20ms audio processing windows
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_MS / 1000)

SPIKE_RATIO = 5.0         # Transient sensitivity multiplier 
MIN_RMS = 0.01            # Ignore ambient background room hums
COOLDOWN_S = 1.5          # Debounce threshold between commands
MIN_DOUBLE_GAP_S = 0.12   # Double hit validation bounds
MAX_DOUBLE_GAP_S = 0.85
NOISE_FLOOR_ALPHA = 0.992 # Adaptive background noise calculation speed
RETRIGGER_RATIO = 0.4

# --- ElevenLabs TTS Settings ------------------------------------------------
ELEVENLABS_TTS_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_MODEL_ID     = "eleven_multilingual_v2"

JARVIS_WELCOME_PHRASE = "Jarvis online. Cybernetic HUD terminals loaded. ElevenLabs voice matrix is operational, sir."
MACRO_STORAGE_FILE = Path(__file__).resolve().parent / "learned_macros.json"

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent / "voice.env", override=False)

# Read ElevenLabs keys after dotenv loads
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

# Synchronized Communication Queues
gui_log_queue = queue.Queue()
audio_visualization_queue = queue.Queue()
SYSTEM_ACTIVE = True
tracer = None

# --- Cyberpunk Visual Palette Constants ------------------------------------
BG_MAIN = "#0A0A12"       # Deep Void Black
BG_PANEL = "#121222"      # Neon Carbon Shell
FG_TEXT = "#00F0FF"       # Cyber Cyan / Matrix Core
FG_ACCENT = "#FF0055"     # Neon Magenta / Alerts
FG_DIM = "#557799"        # Steel Gray / Timestamps


# --- Core System Log Redirector --------------------------------------------
class QueueLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        gui_log_queue.put(log_entry)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis_system")
queue_handler = QueueLogHandler()
queue_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
log.addHandler(queue_handler)


# --- ElevenLabs TTS Dispatcher ----------------------------------------------
def say(text: str) -> None:
    """
    Streams text-to-speech via ElevenLabs API and plays it through the default
    audio output device. Falls back to a log entry if keys are missing.
    """
    log.info(f"Jarvis: {text}")

    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        log.warning("[TTS] ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID not set — voice skipped.")
        return

    url = f"{ELEVENLABS_TTS_ENDPOINT}/{ELEVENLABS_VOICE_ID}"
    payload = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.85},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            audio_bytes = resp.read()

        import tempfile, wave as wv
        tmp_mp3 = Path(tempfile.gettempdir()) / "jarvis_tts.mp3"
        tmp_mp3.write_bytes(audio_bytes)
        pcm_file = Path(tempfile.gettempdir()) / "jarvis_tts.wav"
        converted = False

        # Try ffmpeg (most common on Windows/Linux)
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", "22050", "-ac", "1", "-f", "wav", str(pcm_file)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            if r.returncode == 0 and pcm_file.exists():
                converted = True
        except Exception:
            pass

        # Try mpg123 as fallback
        if not converted:
            try:
                r = subprocess.run(
                    ["mpg123", "-q", "-w", str(pcm_file), str(tmp_mp3)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
                if r.returncode == 0 and pcm_file.exists():
                    converted = True
            except Exception:
                pass

        if converted:
            with wv.open(str(pcm_file), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(audio_np, samplerate=rate)
            sd.wait()
            log.info("[TTS] ElevenLabs audio playback complete.")
        else:
            # Last resort: open mp3 with OS default player
            log.warning("[TTS] ffmpeg/mpg123 not found — opening with OS default player.")
            if sys.platform == "win32":
                os.startfile(str(tmp_mp3))
            else:
                subprocess.Popen(["xdg-open", str(tmp_mp3)])

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        log.error(f"[TTS] ElevenLabs HTTP {e.code}: {body[:200]}")
    except Exception as e:
        log.error(f"[TTS] ElevenLabs playback failure: {e}")


# --- Connectivity Prober ----------------------------------------------------
def is_online() -> bool:
    """Checks whether the agent has active internet access to use online tools."""
    try:
        socket.setdefaulttimeout(1.5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False


# --- Extensible Tool Matrices (Hybrid Online/Offline) ----------------------
def tool_web_search(query: str) -> str:
    """ONLINE TOOL: Performs a quick scraping search via fallback public APIs when internet is live."""
    log.info(f"Scanning deep-web matrices for: '{query}'")
    if not is_online():
        return "Error: System grid is currently offline. Web search tools unavailable."
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            html = response.read().decode('utf-8', errors='ignore')
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            if snippets:
                clean_text = " ".join([re.sub(r'<[^>]+>', '', s) for s in snippets[:3]])
                return f"Live findings: {clean_text[:400]}"
            return "No readable index blocks could be gathered from the web node query."
    except Exception as e:
        return f"Scraper matrix error: {e}"


def tool_file_system(operation: str, target_path: str, content: str = "") -> str:
    """OFFLINE/ONLINE TOOL: Directly reads, writes, or inspects files across your workspace."""
    log.info(f"Accessing local storage sector: [{operation}] on path '{target_path}'")
    try:
        resolved_path = Path(os.path.abspath(os.path.expandvars(target_path)))
        if operation == "write":
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(content, encoding="utf-8")
            return f"Data successfully committed to disk sector at: {resolved_path.name}."
        elif operation == "read":
            if resolved_path.exists():
                return f"File Contents:\n{resolved_path.read_text(encoding='utf-8')[:600]}"
            return f"Error: Specified sector {target_path} does not exist locally."
        elif operation == "list":
            search_dir = resolved_path if resolved_path.is_dir() else resolved_path.parent
            if search_dir.exists():
                items = [f"{'📁' if i.is_dir() else '📄'} {i.name}" for i in search_dir.iterdir()]
                return f"Directory Manifest for {search_dir.name}:\n" + "\n".join(items[:15])
            return "Error: target directory unreachable."
        return f"Invalid target operation: '{operation}'"
    except Exception as e:
        return f"File matrix exception: {e}"


def tool_system_automation(action: str, target: str) -> str:
    """OFFLINE/ONLINE TOOL: Executes OS directives, managing apps, processes, and runtime instances."""
    log.info(f"Routing system shell directive: {action} -> {target}")
    try:
        if action == "open_folder":
            path = os.path.abspath(os.path.expandvars(target)) if target else os.environ.get("USERPROFILE")
            if os.path.exists(path):
                os.startfile(path)
                return f"Opening system directory view for '{os.path.basename(path)}'."
            return "Target path not found in local system indexing."
        elif action == "run_program":
            shorthand = target.lower().strip()
            if shorthand in ["calculator", "calc"]: subprocess.Popen("calc.exe")
            elif shorthand in ["notepad", "notes"]: subprocess.Popen("notepad.exe")
            elif shorthand in ["cmd", "terminal"]: subprocess.Popen("cmd.exe")
            elif shorthand in ["explorer", "this pc"]: subprocess.Popen("explorer.exe")
            else: os.startfile(target)
            return f"Process initialization frame deployed for: {target}."
        elif action == "close_program":
            if not target.lower().endswith(".exe") and not target.lower() in ["calc", "notepad", "cmd"]:
                target += ".exe"
            if "calc" in target.lower(): target = "CalculatorApp.exe"
            subprocess.run(["taskkill", "/f", "/im", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Process loop for '{target}' has been forced to close."
        return "System action execution fell out of expected range."
    except Exception as e:
        return f"OS system automation tool runtime error: {e}"


# --- Command Response Engine (ElevenLabs Voice Only) ------------------------
def agent_reasoning_loop(initial_prompt: str) -> None:
    """
    Receives a command string and speaks it back via ElevenLabs TTS.
    No AI processing — Jarvis acts as a pure voice interface.
    Type or clap-trigger a command and Jarvis will voice-confirm it.
    """
    log.info(f"[Command Received] {initial_prompt}")
    # Echo the command back as a spoken confirmation
    say(f"Received: {initial_prompt}")


# --- Windows Desktop Observer (Click & Action Tracer) ------------------------
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
        log.info("Macro Matrix Recording Online. Processing hardware user space clicks...")
        return True

    def end_trace(self, macro_name: str) -> str:
        self.tracking_active = False
        if self._mouse_hook: self._mouse_hook.stop()
        if self._key_hook: self._key_hook.stop()
        if not self.events: return "No hardware user actions recorded during tracing frame."
        
        stored = {}
        if MACRO_STORAGE_FILE.exists():
            try: stored = json.loads(MACRO_STORAGE_FILE.read_text())
            except Exception: pass
        stored[macro_name.lower()] = self.events
        MACRO_STORAGE_FILE.write_text(json.dumps(stored, indent=4))
        return f"Macro array tracking completed. Saved context profile as '{macro_name}'."

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
    """Validates structural macro frames before throwing strings to the main Agent Loop."""
    global SYSTEM_ACTIVE
    cmd_clean = command_text.lower().strip()
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
        if not target: say("Specify a clear name profile for macro ingestion."); return
        if tracer.begin_trace(): say(f"Macro tracking initiated for profile '{target}'. Run 'stop learning' to save.")
        return

    threading.Thread(target=agent_reasoning_loop, args=(command_text,), daemon=True).start()


# --- Stream-Based Audio Extraction -------------------------------------------
def hardware_audio_stream_worker():
    """Reads raw hardware stream blocks using low latency blocks to prevent thread blocking."""
    global SYSTEM_ACTIVE
    
    audio_buffer = queue.Queue()
    def callback(indata, frames, time_info, status):
        audio_buffer.put(indata.copy())
        # Drop half the frames down the pipeline to reduce processing overhead inside the GUI thread
        audio_visualization_queue.put(indata[::2, 0])

    log.info(f"Opening low-latency audio stream frame at {SAMPLE_RATE}Hz...")
    
    # Adaptive audio processing loop parameters
    noise_floor = MIN_RMS
    first_clap_time = None
    last_logged_double = 0.0
    in_retrigger = False

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1, callback=callback):
            while SYSTEM_ACTIVE:
                try:
                    block = audio_buffer.get(timeout=0.2)
                except queue.Empty:
                    continue

                # Compute current Root-Mean-Square level of the audio block
                rms_val = np.sqrt(np.mean(block**2))
                
                # Dynamically calculate moving noise thresholds
                noise_floor = (NOISE_FLOOR_ALPHA * noise_floor) + ((1.0 - NOISE_FLOOR_ALPHA) * rms_val)
                threshold = max(MIN_RMS, noise_floor * SPIKE_RATIO)

                if in_retrigger:
                    if rms_val < (threshold * RETRIGGER_RATIO):
                        in_retrigger = False
                    continue

                if rms_val > threshold:
                    now = time.time()
                    if (now - last_logged_double) < COOLDOWN_S:
                        continue

                    in_retrigger = True
                    
                    if first_clap_time is None:
                        first_clap_time = now
                    else:
                        gap = now - first_clap_time
                        if gap < MIN_DOUBLE_GAP_S:
                            pass
                        elif gap <= MAX_DOUBLE_GAP_S:
                            first_clap_time = None
                            last_logged_double = now
                            log.info(f"[Acoustic Event] Double Transient Spike Registered (Gap: {gap:.3f}s)")
                            route_command_intent("Online. Terminal loop listening, state your directive, sir.")
                        else:
                            first_clap_time = now

    except Exception as e:
        log.error(f"Hardware input device stream error: {e}")


# --- Cyberpunk Custom Tkinter UI Terminal HUD -------------------------------
class CyberpunkHUD(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("J.A.R.V.I.S // CORE INTENT ENGINE INTERFACE")
        self.geometry("900x600")
        self.configure(bg=BG_MAIN)
        self.build_hud_layout()
        self.enforce_foreground_activation()
        self.refresh_ui_loop()

    def build_hud_layout(self):
        # Top Neon Title Bar Frame
        header = tk.Frame(self, bg=BG_PANEL, height=45, bd=1, relief="flat")
        header.pack(fill="x", side="top", padx=8, pady=5)
        title_lbl = tk.Label(header, text="⚡ JARVIS AUTOMATION AGENT // COGNITIVE MATRIX HUD", font=("Consolas", 12, "bold"), bg=BG_PANEL, fg=FG_TEXT)
        title_lbl.pack(side="left", padx=10, pady=10)
        
        self.status_lbl = tk.Label(header, text="CORE_GRID: SECURE", font=("Consolas", 10, "bold"), bg=BG_PANEL, fg="#00FF66")
        self.status_lbl.pack(side="right", padx=10)

        # Middle Section: Audio Wave Matrix + Log Monitor
        middle_pane = tk.Frame(self, bg=BG_MAIN)
        middle_pane.pack(fill="both", expand=True, padx=8, pady=2)

        # Audio Matrix Visualizer Frame
        viz_frame = tk.LabelFrame(middle_pane, text=" [ HIGH-FIDELITY STREAM ACOUSTIC SPECTRUM ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_ACCENT, bd=1, labelanchor="nw")
        viz_frame.pack(fill="x", side="top", pady=4)
        self.canvas = tk.Canvas(viz_frame, height=60, bg=BG_MAIN, highlightthickness=0)
        self.canvas.pack(fill="x", padx=5, pady=5)

        # Log Matrix Scrolled Text Area
        log_frame = tk.LabelFrame(middle_pane, text=" [ COGNITIVE SYSTEM ENGINE ACTIVITY LOGS ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_TEXT, bd=1, labelanchor="nw")
        log_frame.pack(fill="both", expand=True, side="bottom", pady=4)
        
        self.log_area = scrolledtext.ScrolledText(log_frame, font=("Consolas", 10), bg=BG_MAIN, fg=FG_TEXT, insertbackground=FG_TEXT, bd=0, highlightthickness=0)
        self.log_area.pack(fill="both", expand=True, padx=6, pady=6)

        # Bottom Frame: User Prompt Entry Matrix
        input_frame = tk.LabelFrame(self, text=" [ TRANSMIT DIRECT SYSTEM ARCHITECTURE COMMAND ] ", font=("Consolas", 9, "bold"), bg=BG_PANEL, fg=FG_TEXT, bd=1, labelanchor="nw")
        input_frame.pack(fill="x", side="bottom", padx=8, pady=8)

        self.cmd_entry = tk.Entry(input_frame, font=("Consolas", 11), bg=BG_MAIN, fg=FG_TEXT, insertbackground=FG_TEXT, bd=0, highlightthickness=0)
        self.cmd_entry.pack(fill="x", side="left", expand=True, padx=8, pady=8)
        self.cmd_entry.bind("<Return>", self.dispatch_text_command)

        send_btn = tk.Button(input_frame, text="TRANSMIT_ ", font=("Consolas", 10, "bold"), bg=BG_PANEL, fg=FG_ACCENT, activebackground=FG_ACCENT, activeforeground=BG_MAIN, bd=1, relief="flat", command=self.dispatch_text_command)
        send_btn.pack(side="right", padx=6, pady=4)

    def enforce_foreground_activation(self):
        """Forces the window manager window stack to launch HUD window immediately into user view."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))

    def dispatch_text_command(self, event=None):
        raw_cmd = self.cmd_entry.get().strip()
        if raw_cmd:
            log.info(f"[HUD Command Terminal Input] -> '{raw_cmd}'")
            route_command_intent(raw_cmd)
            self.cmd_entry.delete(0, tk.END)

    def refresh_ui_loop(self):
        # 1. Drain logs into the visualizer screen area
        while not gui_log_queue.empty():
            try:
                line = gui_log_queue.get_nowait()
                self.log_area.insert(tk.END, line + "\n")
                self.log_area.see(tk.END)
            except queue.Empty: break

        # 2. Render smooth real-time acoustic visualizer tracking waves
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
                    # Calculate raw Y position
                    raw_y = (h / 2) + (val * h * 15.0)
                    # Force boundary clamp keeping the lines cleanly inside the canvas layout bounds
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
    
    audio_hardware_thread = threading.Thread(target=hardware_audio_stream_worker, daemon=True)
    audio_hardware_thread.start()
            
    hud_window = CyberpunkHUD()
    hud_window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())