# MEGA → Telegram Uploader

Upload named folders from MEGA (or your PC) to a Telegram group as media albums, with each folder's name as the caption.

- Posts each folder as albums of up to 10 photos/videos, captioned with the folder name
- Downloads straight from a public MEGA folder link, or uses folders already on disk
- Signs in as your own Telegram account, so files up to 2 GB are supported
- Resumes where it left off, waits out Telegram rate limits automatically, and skips damaged files instead of crashing
- Simple desktop app: enter your settings in the window, no file editing needed

## Requirements

| Item | Needed? | Notes |
| --- | --- | --- |
| Python 3.10+ | Yes | On Windows, tick **Add python.exe to PATH** when installing |
| `telethon`, `cryptg`, `hachoir` | Yes | `pip install -r requirements.txt` |
| Telegram API ID + API Hash | Yes | From [my.telegram.org](https://my.telegram.org), see below |
| Telegram account in the target group | Yes | Must be allowed to send media |
| [MEGAcmd](https://mega.io/cmd) | Only for MEGA links | Not needed if the folders are already on disk |

## Installation

```bash
git clone https://github.com/r11r33/MEGA-to-TELEGRAM.git
cd MEGA-to-TELEGRAM
pip install -r requirements.txt
```

Or click **Code → Download ZIP** on GitHub and extract it.

On Windows, use `py -m pip install -r requirements.txt` if `pip` isn't found.

## Getting a Telegram API ID and Hash

1. Sign in at [my.telegram.org](https://my.telegram.org) with your phone number. The code arrives in the Telegram app, not by SMS.
2. Open **API development tools**.
3. Create an application: any title, a short name of 5+ letters, platform **Desktop**.
4. Copy **App api_id** and **App api_hash**.

If the page shows a plain `ERROR` when creating the app, disable any VPN or ad-blocker and retry. Keep the hash private.

## Usage

```bash
python mega_to_telegram_gui.py      # Windows: py mega_to_telegram_gui.py
```

All settings are entered in the app window. You don't need to edit any file.

| Field | Value |
| --- | --- |
| API ID / API Hash | From my.telegram.org |
| Group | Invite link, `@username`, or numeric chat id |
| MEGA link | Public folder link, or leave empty to use local folders |
| Local folder | Parent folder that holds your named folders |
| Put folder name on every album | Off = caption on the first album of each folder only |

Click **Start**. On the first run, pop-ups ask for your phone number, the Telegram login code, and your 2FA password if you have one. Settings are saved for next time.

**Stop** finishes the current album and stops. **Reset upload history** makes everything upload again.

## How uploads are organised

Every folder containing files becomes one set of posts, sent in alphabetical order.

```
MegaUpload/
├── Beach Trip/        → albums captioned "Beach Trip"
│   ├── 001.jpg
│   └── clip.mp4
└── Wedding/           → albums captioned "Wedding"
    ├── a.jpg
    └── notes.pdf      → sent as a document after the albums
```

| Sent as | File types |
| --- | --- |
| Photo in album | `.jpg` `.jpeg` `.png` `.webp` |
| Video in album | `.mp4` `.mov` `.m4v` |
| Document | Everything else, including `.mkv`, `.webm`, `.pdf`, `.zip` |

- If Telegram rejects an album, its files are retried one at a time, then as documents. Files that still fail are skipped and listed at the end.
- Empty (0 KB) files, hidden files, `desktop.ini` and `Thumbs.db` are skipped.

## Resuming and rate limits

- A folder is marked done in `uploaded.json` only after all its files are sent. Rerun to continue; a folder interrupted midway starts again from its first album.
- When Telegram rate-limits the account (`Telegram asked to wait 1133s…`), the app sleeps for that long and continues. Keep it running.

## Platforms

Built and tested for Windows. It also runs on macOS and Linux desktops with Python and Tk installed (`python3 mega_to_telegram_gui.py`). It needs a screen, so it won't run in headless environments like Cloud Shell or iSH.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `python` opens the Microsoft Store | Use `py`, or reinstall Python with PATH ticked |
| `No module named 'telethon'` | `py -m pip install -r requirements.txt` |
| `No module named 'tkinter'` | Re-run the Python installer → Modify → tick **tcl/tk and IDLE** |
| `Album rejected (MediaEmptyError)` | Handled automatically; bad files are skipped |
| `✗ Skipped <file>` | The file is empty or damaged; download it again |
| `MEGAcmd not found` | Install MEGAcmd, or download the folders yourself and clear the MEGA link |

## Files created at runtime

| File | Contents |
| --- | --- |
| `uploader_config.json` | Saved GUI settings, **including your API hash** |
| `uploaded.json` | Folders already uploaded |
| `mega_uploader.session` | Your Telegram login — treat it like a password |

All three are listed in `.gitignore`. Never commit them. To sign out fully, delete the session file and end the session in Telegram → Settings → Devices.

## Disclaimer

This tool uses your personal Telegram account through the official MTProto API. Only upload content you have the right to share, and follow Telegram's Terms of Service.
