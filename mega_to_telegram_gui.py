"""
MEGA -> Telegram uploader (GUI)

Each folder is posted to your Telegram group as albums, captioned with the folder name.

Setup (once, in PowerShell):
    pip install telethon cryptg hachoir
Install MEGAcmd from https://mega.io/cmd if you want the app to download from a MEGA link.

Run:
    python mega_to_telegram_gui.py
"""

import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "uploader_config.json"
PROGRESS_FILE = APP_DIR / "uploaded.json"
SESSION = str(APP_DIR / "mega_uploader")

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}      # .mkv/.webm aren't allowed in albums; they go as files
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
ALBUM_SIZE = 10


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class App:
    def __init__(self, root):
        self.root = root
        root.title("MEGA → Telegram Uploader")
        root.geometry("720x640")
        root.minsize(560, 500)

        self.v = {k: tk.StringVar() for k in ("api_id", "api_hash", "group", "mega_link", "local_dir")}
        self.caption_all = tk.BooleanVar(value=False)
        self.q = queue.Queue()
        self.stop_flag = False
        self.proc = None
        self.running = False
        self.skipped = []
        self.last_error = ""

        self._build_ui()
        self._load_config()
        root.after(100, self._drain_queue)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        rows = [
            ("API ID", "api_id", None),
            ("API Hash", "api_hash", "*"),
            ("Group", "group", None),
            ("MEGA link", "mega_link", None),
        ]
        for r, (label, key, show) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=self.v[key], show=show or "").grid(
                row=r, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(frm, text="Local folder").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self.v["local_dir"]).grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=4, column=2, padx=(6, 0))

        hint = ("API ID/Hash: my.telegram.org → API development tools.   "
                "Group: link, @username or chat id.\n"
                "MEGA link is optional — leave empty to upload folders already on your PC.")
        ttk.Label(frm, text=hint, foreground="#666").grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 6))

        ttk.Checkbutton(frm, text="Put folder name on every album (not just the first)",
                        variable=self.caption_all).grid(row=6, column=0, columnspan=3, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=3, sticky="w", pady=8)
        self.start_btn = ttk.Button(btns, text="Start", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Reset upload history", command=self._reset_history).pack(side="left", padx=6)

        self.overall_lbl = ttk.Label(frm, text="Overall: –")
        self.overall_lbl.grid(row=8, column=0, columnspan=3, sticky="w")
        self.overall_bar = ttk.Progressbar(frm, maximum=100)
        self.overall_bar.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 6))

        self.current_lbl = ttk.Label(frm, text="Current: –")
        self.current_lbl.grid(row=10, column=0, columnspan=3, sticky="w")
        self.current_bar = ttk.Progressbar(frm, maximum=100)
        self.current_bar.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(2, 8))

        self.log_box = ScrolledText(frm, height=12, state="disabled", font=("Consolas", 9))
        self.log_box.grid(row=12, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(12, weight=1)

    def _browse(self):
        d = filedialog.askdirectory(title="Folder containing your named folders")
        if d:
            self.v["local_dir"].set(d)

    # ---------------- config / history ----------------
    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, var in self.v.items():
                    var.set(data.get(k, ""))
                self.caption_all.set(data.get("caption_all", False))
            except Exception:
                pass
        if not self.v["local_dir"].get():
            self.v["local_dir"].set(str(APP_DIR / "mega_download"))

    def _save_config(self):
        data = {k: var.get().strip() for k, var in self.v.items()}
        data["caption_all"] = self.caption_all.get()
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _reset_history(self):
        if messagebox.askyesno("Reset", "Forget which folders were already uploaded?\n"
                                        "Next run will upload everything again."):
            PROGRESS_FILE.unlink(missing_ok=True)
            self.log("Upload history cleared.")

    @staticmethod
    def _load_done():
        if PROGRESS_FILE.exists():
            return set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8")))
        return set()

    @staticmethod
    def _save_done(done):
        PROGRESS_FILE.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")

    # ---------------- thread-safe UI updates ----------------
    def log(self, msg):
        self.q.put(("log", msg))

    def _drain_queue(self):
        try:
            while True:
                kind, *args = self.q.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", args[0] + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "overall":
                    done, total = args
                    self.overall_bar["value"] = (done / total * 100) if total else 0
                    self.overall_lbl.config(text=f"Overall: {done}/{total} files")
                elif kind == "current":
                    text, pct = args
                    self.current_bar["value"] = pct
                    self.current_lbl.config(text=f"Current: {text}")
                elif kind == "finished":
                    self.running = False
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def ask(self, prompt, secret=False):
        """Called from the worker thread; shows a dialog on the UI thread and waits."""
        box, ev = {}, threading.Event()

        def _ask():
            box["v"] = simpledialog.askstring("Telegram login", prompt, parent=self.root,
                                              show="*" if secret else None)
            ev.set()

        self.root.after(0, _ask)
        ev.wait()
        return (box.get("v") or "").strip()

    # ---------------- start / stop ----------------
    def _start(self):
        if not self.v["api_id"].get().strip().isdigit():
            messagebox.showerror("Missing", "API ID must be a number.")
            return
        for key, name in (("api_hash", "API Hash"), ("group", "Group"), ("local_dir", "Local folder")):
            if not self.v[key].get().strip():
                messagebox.showerror("Missing", f"{name} is required.")
                return
        self._save_config()
        self.stop_flag = False
        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self._worker, daemon=True).start()

    def _stop(self):
        self.stop_flag = True
        self.log("Stopping after the current upload…")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _on_close(self):
        if self.running and not messagebox.askyesno("Quit", "An upload is running. Quit anyway?"):
            return
        self.root.destroy()

    def _worker(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            self.log(f"ERROR: {type(e).__name__}: {e}")
        finally:
            self.q.put(("finished",))

    # ---------------- MEGA download ----------------
    def _download(self, link, dest):
        dest.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.environ["PATH"] += os.pathsep + os.path.join(os.environ.get("LOCALAPPDATA", ""), "MEGAcmd")
        mega_get = shutil.which("mega-get")
        if mega_get:
            cmd = [mega_get, link, str(dest)]
        elif shutil.which("megatools"):
            cmd = ["megatools", "dl", "--path", str(dest), link]
        else:
            raise RuntimeError("MEGAcmd not found. Install it from mega.io/cmd, "
                               "or download the folders yourself and clear the MEGA link.")

        self.log(f"Downloading from MEGA into {dest} …")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, errors="replace", creationflags=flags)
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            m = re.search(r"([\d.]+)\s*%", line)
            if m:
                self.q.put(("current", "Downloading from MEGA", float(m.group(1))))
            else:
                self.log(line)
        self.proc.wait()
        if self.stop_flag:
            raise RuntimeError("Stopped during download.")
        if self.proc.returncode != 0:
            raise RuntimeError(f"MEGA download failed (exit code {self.proc.returncode}).")
        self.log("Download finished.")

    # ---------------- Telegram ----------------
    async def _login(self, client):
        await client.connect()
        if await client.is_user_authorized():
            return
        phone = await asyncio.to_thread(self.ask, "Phone number with country code (e.g. +9607xxxxxx):")
        if not phone:
            raise RuntimeError("Login cancelled.")
        await client.send_code_request(phone)
        code = await asyncio.to_thread(self.ask, "Enter the login code Telegram sent you:")
        if not code:
            raise RuntimeError("Login cancelled.")
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            pw = await asyncio.to_thread(self.ask, "Two-step verification password:", True)
            await client.sign_in(password=pw)
        self.log("Logged in to Telegram.")

    async def _try_send(self, client, chat, files, caption, as_doc, label):
        """One send attempt, retrying only on flood waits. Returns True on success."""
        def progress(sent, total):
            self.q.put(("current", label, sent / total * 100 if total else 0))

        payload = [str(f) for f in files] if len(files) > 1 else str(files[0])
        while True:
            try:
                await client.send_file(chat, payload, caption=caption,
                                       force_document=as_doc, supports_streaming=not as_doc,
                                       progress_callback=progress)
                return True
            except FloodWaitError as e:
                self.log(f"  Telegram asked to wait {e.seconds}s…")
                await asyncio.sleep(e.seconds + 2)
            except RPCError as e:
                self.last_error = e.__class__.__name__
                return False

    async def _send(self, client, chat, files, caption, as_doc, label):
        """Send an album; if Telegram rejects it, fall back to one file at a time and skip bad files."""
        if await self._try_send(client, chat, files, caption, as_doc, label):
            return
        self.log(f"  Album rejected ({self.last_error}), sending these {len(files)} files one by one…")

        for f in files:
            if self.stop_flag:
                return
            name = f"{label} – {f.name}"
            if not as_doc and await self._try_send(client, chat, [f], caption, False, name):
                caption = None
                continue
            if await self._try_send(client, chat, [f], caption, True, name):
                caption = None
                continue
            self.log(f"  ✗ Skipped {f.name} ({self.last_error}) — file may be empty or damaged")
            self.skipped.append(str(f))

    async def _run(self):
        cfg = {k: var.get().strip() for k, var in self.v.items()}
        local = Path(cfg["local_dir"])

        if cfg["mega_link"]:
            await asyncio.to_thread(self._download, cfg["mega_link"], local)

        if not local.exists():
            raise RuntimeError(f"Folder not found: {local}")

        def wanted(p):
            return p.is_file() and p.name.lower() not in SKIP_NAMES and not p.name.startswith(".")

        empty = [p for p in local.rglob("*") if wanted(p) and p.stat().st_size == 0]
        for p in empty:
            self.log(f"  ✗ Skipping empty file: {p.relative_to(local)}")
        empty_set = set(empty)
        _wanted = wanted
        wanted = lambda p: _wanted(p) and p not in empty_set
        self.skipped = []

        folders = sorted({p.parent for p in local.rglob("*") if wanted(p)}, key=lambda p: str(p).lower())
        done = self._load_done()
        todo = [f for f in folders if str(f.resolve()) not in done]
        if not todo:
            self.log("Nothing to upload (all folders already done, or folder is empty).")
            return

        total = sum(1 for f in todo for p in f.iterdir() if wanted(p))
        sent_count = 0
        self.q.put(("overall", 0, total))
        self.log(f"{len(todo)} folder(s), {total} file(s) to upload.")

        client = TelegramClient(SESSION, int(cfg["api_id"]), cfg["api_hash"])
        try:
            await self._login(client)
            chat = await client.get_entity(cfg["group"])

            for folder in todo:
                if self.stop_flag:
                    break
                files = sorted((p for p in folder.iterdir() if wanted(p)), key=lambda p: p.name.lower())
                media = [f for f in files if f.suffix.lower() in PHOTO_EXT | VIDEO_EXT]
                others = [f for f in files if f not in media]
                self.log(f"▶ {folder.name}  ({len(media)} media, {len(others)} other)")

                first = True
                for group, as_doc in [(g, False) for g in chunks(media, ALBUM_SIZE)] + \
                                     [(g, True) for g in chunks(others, ALBUM_SIZE)]:
                    if self.stop_flag:
                        break
                    cap = folder.name if (first or self.caption_all.get()) else None
                    await self._send(client, chat, group, cap, as_doc, f"{folder.name} ({len(group)} files)")
                    first = False
                    sent_count += len(group)
                    self.q.put(("overall", sent_count, total))

                if not self.stop_flag:
                    done.add(str(folder.resolve()))
                    self._save_done(done)
                    self.log(f"✓ {folder.name} done")
        finally:
            await client.disconnect()

        self.q.put(("current", "–", 0))
        if self.skipped:
            self.log(f"{len(self.skipped)} file(s) couldn't be sent:")
            for f in self.skipped:
                self.log(f"   {f}")
        self.log("Stopped." if self.stop_flag else "All done.")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
