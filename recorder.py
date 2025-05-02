import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pynput import mouse, keyboard
import pyautogui
import threading
import queue
import json
import os
from datetime import datetime
import sys
import logging
from typing import List, Tuple, Any, Optional

if sys.platform != 'win32':
    raise OSError("Questo programma funziona solo su Windows.")

import win32api
import win32con

# Costanti per gli eventi
EVENT_KEY_PRESS = 'key_press'
EVENT_KEY_RELEASE = 'key_release'
EVENT_MOUSE_MOVE = 'mouse_move'
EVENT_MOUSE_CLICK = 'mouse_click'

# Costanti per i messaggi
MSG_RECORDING_STOPPED = "Recording stopped"
MSG_PLAYBACK_FINISHED = "Playback finished"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class ActivityRecorder:
    """
    Registra e riproduce eventi di mouse e tastiera.
    Permette di salvare e caricare le registrazioni su file JSON.
    Thread-safe per l'accesso agli eventi registrati.
    """
    def __init__(self):
        self.recorded_events: List[Tuple[str, Any, float]] = []
        self.is_recording: bool = False
        self.is_playing: bool = False
        self.event_queue: queue.Queue = queue.Queue()
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None
        self.playback_thread: Optional[threading.Thread] = None
        self.start_time: Optional[float] = None
        self._lock = threading.Lock()
    
    def _serialize_event(self, event_type: str, event_data: Any, timestamp: float) -> Tuple[str, Any, float]:
        if event_type in (EVENT_KEY_PRESS, EVENT_KEY_RELEASE) and isinstance(event_data, keyboard.Key):
            event_data = f"<Key.{event_data._name_}>"
        return (event_type, event_data, timestamp)

    def _deserialize_event(self, event_type: str, event_data: Any, timestamp: float) -> Optional[Tuple[str, Any, float]]:
        if event_type in (EVENT_KEY_PRESS, EVENT_KEY_RELEASE) and isinstance(event_data, str) and event_data.startswith('<Key.'):
            key_name = event_data[5:-1]
            try:
                event_data = keyboard.Key[key_name]
            except (KeyError, AttributeError):
                logging.warning(f"Could not convert key {key_name}")
                return None
        return (event_type, event_data, timestamp)
    
    def on_press(self, key: Any) -> None:
        if self.is_recording:
            with self._lock:
                try:
                    self.recorded_events.append((EVENT_KEY_PRESS, key.char, time.time() - self.start_time))
                except AttributeError:
                    self.recorded_events.append((EVENT_KEY_PRESS, key, time.time() - self.start_time))
    
    def on_release(self, key: Any) -> Optional[bool]:
        if self.is_recording:
            with self._lock:
                try:
                    self.recorded_events.append((EVENT_KEY_RELEASE, key.char, time.time() - self.start_time))
                except AttributeError:
                    self.recorded_events.append((EVENT_KEY_RELEASE, key, time.time() - self.start_time))
            if key == keyboard.Key.esc:
                self.event_queue.put('stop_recording')
                self.stop_recording()
                return False
        return None
    
    def on_move(self, x: int, y: int) -> None:
        if self.is_recording:
            with self._lock:
                self.recorded_events.append((EVENT_MOUSE_MOVE, (x, y), time.time() - self.start_time))
    
    def on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if self.is_recording:
            with self._lock:
                self.recorded_events.append((EVENT_MOUSE_CLICK, (x, y, button.name, pressed), time.time() - self.start_time))
    
    def start_recording(self) -> None:
        """Avvia la registrazione degli eventi."""
        if self.is_recording:
            return
        with self._lock:
            self.recorded_events = []
        self.is_recording = True
        self.start_time = time.time()
        try:
            self.keyboard_listener = keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release)
            self.keyboard_listener.start()
            self.mouse_listener = mouse.Listener(
                on_move=self.on_move,
                on_click=self.on_click)
            self.mouse_listener.start()
        except Exception as e:
            logging.error(f"Error starting listeners: {e}")
            self.is_recording = False
            raise
    
    def stop_recording(self) -> None:
        """Ferma la registrazione degli eventi."""
        if not self.is_recording:
            return
        self.is_recording = False
        try:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
            if self.mouse_listener:
                self.mouse_listener.stop()
        except Exception as e:
            logging.error(f"Error stopping listeners: {e}")
        finally:
            self.keyboard_listener = None
            self.mouse_listener = None
    
    def play_recording(self, repeat_count: int = 1) -> None:
        """Riproduce la registrazione."""
        with self._lock:
            if not self.recorded_events:
                return
        if self.is_playing:
            return
        self.is_playing = True
        self.playback_thread = threading.Thread(target=self._playback, args=(repeat_count,))
        self.playback_thread.start()
        self.keyboard_listener = keyboard.Listener(on_release=self._on_playback_key)
        self.keyboard_listener.start()
    
    def _on_playback_key(self, key: Any) -> Optional[bool]:
        if key == keyboard.Key.esc:
            self.stop_playback()
            return False
        return None
    
    def _playback(self, repeat_count: int = 1) -> None:
        for iteration in range(repeat_count):
            if not self.is_playing:
                break
            start_time = time.time()
            with self._lock:
                events = list(self.recorded_events)
            for event in events:
                if not self.is_playing:
                    break
                event_type, event_data, timestamp = event
                current_time = time.time() - start_time
                sleep_time = timestamp - current_time
                if sleep_time > 0:
                    time.sleep(sleep_time)
                try:
                    if event_type == EVENT_KEY_PRESS:
                        if isinstance(event_data, keyboard.Key):
                            key_str = str(event_data).replace('Key.', '')
                            pyautogui.keyDown(key_str)
                        else:
                            pyautogui.keyDown(event_data)
                    elif event_type == EVENT_KEY_RELEASE:
                        if isinstance(event_data, keyboard.Key):
                            key_str = str(event_data).replace('Key.', '')
                            pyautogui.keyUp(key_str)
                        else:
                            pyautogui.keyUp(event_data)
                    elif event_type == EVENT_MOUSE_MOVE:
                        x, y = event_data
                        win32api.SetCursorPos((x, y))
                    elif event_type == EVENT_MOUSE_CLICK:
                        x, y, button, pressed = event_data
                        if pressed:
                            if button == 'left':
                                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                            elif button == 'right':
                                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, x, y, 0, 0)
                            elif button == 'middle':
                                win32api.mouse_event(win32con.MOUSEEVENTF_MIDDLEDOWN, x, y, 0, 0)
                        else:
                            if button == 'left':
                                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                            elif button == 'right':
                                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, x, y, 0, 0)
                            elif button == 'middle':
                                win32api.mouse_event(win32con.MOUSEEVENTF_MIDDLEUP, x, y, 0, 0)
                except Exception as e:
                    logging.error(f"Error during playback: {e}")
            if self.is_playing and iteration < repeat_count - 1:
                time.sleep(1)
        self.is_playing = False
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        self.event_queue.put('playback_finished')
    
    def stop_playback(self) -> None:
        """Ferma la riproduzione."""
        self.is_playing = False
        try:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
            if self.playback_thread:
                self.playback_thread.join()
        except Exception as e:
            logging.error(f"Error stopping playback: {e}")
        finally:
            self.keyboard_listener = None
            self.playback_thread = None
    
    def save_recording(self, filename: str) -> bool:
        """Salva la registrazione su file."""
        with self._lock:
            if not self.recorded_events:
                return False
            try:
                serializable_events = [self._serialize_event(*event) for event in self.recorded_events]
                with open(filename, 'w') as f:
                    json.dump(serializable_events, f)
                return True
            except Exception as e:
                logging.error(f"Error saving recording: {e}")
                return False
    
    def load_recording(self, filename: str) -> bool:
        """Carica una registrazione da file."""
        try:
            with open(filename, 'r') as f:
                loaded_events = json.load(f)
            events = []
            for event_type, event_data, timestamp in loaded_events:
                event = self._deserialize_event(event_type, event_data, timestamp)
                if event:
                    events.append(event)
            with self._lock:
                self.recorded_events = events
            return True
        except Exception as e:
            logging.error(f"Error loading recording: {e}")
            return False

class RecorderApp:
    """
    Interfaccia grafica per ActivityRecorder.
    Permette di registrare, riprodurre, salvare e caricare eventi di mouse e tastiera.
    """
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Activity Recorder")
        self.root.geometry("300x400")
        self.root.resizable(False, False)
        self.root.configure(bg='#f0f0f0')
        self.recorder = ActivityRecorder()
        main_frame = ttk.Frame(root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        title_label = ttk.Label(main_frame, text="Activity Recorder", 
                              font=('Helvetica', 14, 'bold'))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 20))
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        status_label = ttk.Label(status_frame, textvariable=self.status_var)
        status_label.grid(row=0, column=0, sticky=tk.W)
        self.event_count_var = tk.StringVar()
        self.event_count_var.set("Events: 0")
        event_label = ttk.Label(status_frame, textvariable=self.event_count_var)
        event_label.grid(row=0, column=1, sticky=tk.E)
        recording_frame = ttk.LabelFrame(main_frame, text="Recording", padding=10)
        recording_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        self.start_rec_btn = tk.Button(recording_frame, text="Start Recording", 
                                command=self.start_recording, bg='#007bff', fg='white',
                                relief=tk.RAISED)
        self.start_rec_btn.grid(row=0, column=0, padx=5, pady=5)
        self.stop_rec_btn = tk.Button(recording_frame, text="Stop Recording", 
                               command=self.stop_recording, bg='#007bff', fg='white',
                               relief=tk.RAISED)
        self.stop_rec_btn.grid(row=0, column=1, padx=5, pady=5)
        self.stop_rec_btn.config(state=tk.DISABLED)
        playback_frame = ttk.LabelFrame(main_frame, text="Playback", padding=10)
        playback_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        repeat_frame = ttk.Frame(playback_frame)
        repeat_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 5))
        repeat_label = ttk.Label(repeat_frame, text="Repetitions:")
        repeat_label.grid(row=0, column=0, padx=(0, 5))
        self.repeat_count = tk.StringVar(value="1")
        self.repeat_spinbox = ttk.Spinbox(repeat_frame, from_=1, to=100, width=5,
                                        textvariable=self.repeat_count)
        self.repeat_spinbox.grid(row=0, column=1)
        self.play_btn = tk.Button(playback_frame, text="Play Recording", 
                           command=self.play_recording, bg='#007bff', fg='white',
                           relief=tk.RAISED)
        self.play_btn.grid(row=1, column=0, padx=5, pady=5)
        self.stop_play_btn = tk.Button(playback_frame, text="Stop Playback", 
                                command=self.stop_playback, bg='#007bff', fg='white',
                                relief=tk.RAISED)
        self.stop_play_btn.grid(row=1, column=1, padx=5, pady=5)
        self.stop_play_btn.config(state=tk.DISABLED)
        file_frame = ttk.LabelFrame(main_frame, text="File Operations", padding=10)
        file_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E))
        self.save_btn = tk.Button(file_frame, text="Save Recording", 
                           command=self.save_recording, bg='#007bff', fg='white',
                           relief=tk.RAISED)
        self.save_btn.grid(row=0, column=0, padx=5, pady=5)
        self.load_btn = tk.Button(file_frame, text="Load Recording", 
                           command=self.load_recording, bg='#007bff', fg='white',
                           relief=tk.RAISED)
        self.load_btn.grid(row=0, column=1, padx=5, pady=5)
        for frame in [recording_frame, playback_frame, file_frame]:
            frame.columnconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        self.update_status()
        for btn in [self.start_rec_btn, self.stop_rec_btn, self.play_btn, self.stop_play_btn, self.save_btn, self.load_btn]:
            btn.bind('<Enter>', lambda e, b=btn: b.configure(bg='#0056b3'))
            btn.bind('<Leave>', lambda e, b=btn: b.configure(bg='#007bff'))
            btn.configure(width=15)

    def set_recording_buttons_state(self, recording: bool) -> None:
        self.start_rec_btn.config(state=tk.DISABLED if recording else tk.NORMAL)
        self.stop_rec_btn.config(state=tk.NORMAL if recording else tk.DISABLED)
        self.play_btn.config(state=tk.DISABLED if recording else tk.NORMAL)
        self.stop_play_btn.config(state=tk.DISABLED)
        self.save_btn.config(state=tk.DISABLED if recording else tk.NORMAL)
        self.load_btn.config(state=tk.DISABLED if recording else tk.NORMAL)

    def set_playback_buttons_state(self, playing: bool) -> None:
        self.play_btn.config(state=tk.DISABLED if playing else tk.NORMAL)
        self.stop_play_btn.config(state=tk.NORMAL if playing else tk.DISABLED)
        self.start_rec_btn.config(state=tk.DISABLED if playing else tk.NORMAL)
        self.stop_rec_btn.config(state=tk.DISABLED)
        self.save_btn.config(state=tk.DISABLED if playing else tk.NORMAL)
        self.load_btn.config(state=tk.DISABLED if playing else tk.NORMAL)

    def start_recording(self) -> None:
        try:
            self.recorder.start_recording()
            self.status_var.set("Recording... Press ESC to stop")
            self.set_recording_buttons_state(True)
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante l'avvio della registrazione: {e}")

    def stop_recording(self) -> None:
        try:
            self.recorder.stop_recording()
            self.status_var.set(MSG_RECORDING_STOPPED)
            self.set_recording_buttons_state(False)
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante l'arresto della registrazione: {e}")

    def play_recording(self) -> None:
        if not self.recorder.recorded_events:
            messagebox.showwarning("Warning", "No recording to play")
            return
        try:
            repeat_count = int(self.repeat_count.get())
            if repeat_count < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number of repetitions (1 or more)")
            return
        try:
            self.recorder.play_recording(repeat_count)
            self.status_var.set(f"Playing recording {repeat_count} time(s)...")
            self.set_playback_buttons_state(True)
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante la riproduzione: {e}")

    def stop_playback(self) -> None:
        try:
            self.recorder.stop_playback()
            self.status_var.set("Playback stopped")
            self.set_playback_buttons_state(False)
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante l'arresto della riproduzione: {e}")

    def save_recording(self) -> None:
        if not self.recorder.recorded_events:
            messagebox.showwarning("Warning", "No recording to save")
            return
        default_filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filename = filedialog.asksaveasfilename(
            title="Save recording as",
            initialfile=default_filename,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if not filename:
            return
        try:
            if self.recorder.save_recording(filename):
                messagebox.showinfo("Success", f"Recording saved as:\n{filename}")
            else:
                messagebox.showerror("Error", "Failed to save recording")
        except Exception as e:
            messagebox.showerror("Errore", f"Errore durante il salvataggio: {e}")

    def load_recording(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select recording file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if filename:
            try:
                if self.recorder.load_recording(filename):
                    self.status_var.set("Recording loaded")
                    self.play_btn.config(state=tk.NORMAL)
                    self.save_btn.config(state=tk.NORMAL)
                else:
                    messagebox.showerror("Error", "Failed to load recording")
            except Exception as e:
                messagebox.showerror("Errore", f"Errore durante il caricamento: {e}")

    def update_status(self) -> None:
        try:
            while True:
                message = self.recorder.event_queue.get_nowait()
                if message == 'stop_recording':
                    self.status_var.set(MSG_RECORDING_STOPPED)
                    self.set_recording_buttons_state(False)
                elif message == 'playback_finished':
                    self.status_var.set(MSG_PLAYBACK_FINISHED)
                    self.set_playback_buttons_state(False)
        except queue.Empty:
            pass
        count = len(self.recorder.recorded_events)
        self.event_count_var.set(f"Events: {count}")
        self.root.after(100, self.update_status)

def main() -> None:
    root = tk.Tk()
    app = RecorderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
