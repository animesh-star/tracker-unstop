"""
Unstop Laptop Home Screen Notifier & First-in-Day Reminder
Automatically checks when the laptop is opened or unlocked:
- Detects laptop boot / Windows startup / logon
- Detects laptop lid open / wake from sleep / hibernate
- Detects workstation unlock (when user unlocks their laptop)
- If today's challenge is not completed, pops up an interactive desktop alert card & Windows Toast on the home screen
- Direct one-click launch in Google Chrome for the coding challenge
- Streams ntfy.sh topic alerts in real-time
"""

import os
import sys
import json
import time
import threading
import subprocess
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

# Reconfigure stdout/stderr for Windows console UTF-8 support
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "challenges.json"
if not DB_PATH.exists():
    DB_PATH = BASE_DIR / "challenges.json"

STATE_FILE = DATA_DIR / "notifier_state.json"
ENV_FILE = BASE_DIR / ".env"
NTFY_TOPIC = "animesh_unstop_streak"
API_SECRET = ""

if ENV_FILE.exists():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NTFY_TOPIC="):
                    NTFY_TOPIC = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("API_SECRET="):
                    API_SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

IST = timezone(timedelta(hours=5, minutes=30))

try:
    from unstop_sync import auto_sync_and_mark_completed, fetch_unstop_activity_for_date
except ImportError:
    auto_sync_and_mark_completed = None
    fetch_unstop_activity_for_date = None

popup_active = False
popup_lock = threading.Lock()
snooze_until = 0

def today_date_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")

def is_workstation_locked() -> bool:
    """Checks whether the Windows workstation / screen is currently locked."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x0100) # DESKTOP_SWITCHDESKTOP
        if hdesk:
            user32.CloseDesktop(hdesk)
            return False
        return True
    except Exception:
        return False

def is_popup_window_open() -> bool:
    """Checks whether the GUI popup window is already active on screen."""
    lock_file = DATA_DIR / "popup_open.lock"
    if not lock_file.exists():
        return False
    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        else:
            lock_file.unlink(missing_ok=True)
            return False
    except Exception:
        return False

def get_chrome_path() -> str:
    """Finds Google Chrome executable path on Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p

    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if val and os.path.isfile(val):
                        return val
            except Exception:
                pass
    except Exception:
        pass

    import shutil
    w = shutil.which("chrome")
    if w and os.path.isfile(w):
        return w

    return ""

def open_url_in_chrome(url: str):
    """
    Directly opens the given URL in Google Chrome specifically for this task,
    without changing the user's system-wide default browser.
    """
    target_url = url or "https://unstop.com/practice/coding"
    chrome_path = get_chrome_path()

    # Method 1: Windows shell start with direct chrome path (detached, brings window to front)
    if chrome_path and os.path.isfile(chrome_path):
        try:
            cmd = f'start "" "{chrome_path}" "{target_url}"'
            subprocess.Popen(cmd, shell=True)
            print(f"🚀 [Chrome Launcher] Launched via path: {chrome_path}")
            return True
        except Exception as e:
            print(f"Error launching via chrome path: {e}")

    # Method 2: Windows shell start with registered chrome command
    try:
        cmd = f'start chrome "{target_url}"'
        subprocess.Popen(cmd, shell=True)
        print("🚀 [Chrome Launcher] Launched via 'start chrome'")
        return True
    except Exception as e:
        print(f"Error launching via start chrome: {e}")

    # Method 3: Direct subprocess invocation with DETACHED flags
    if chrome_path and os.path.isfile(chrome_path):
        try:
            subprocess.Popen([chrome_path, target_url], creationflags=0x00000008 | 0x00000200)
            return True
        except Exception:
            pass

    # Method 4: Fallback to webbrowser
    try:
        webbrowser.open(target_url)
        return True
    except Exception:
        pass

    return False

def load_notifier_state() -> dict:
    """Reads persistent state for first-open-of-day tracking and snoozing."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_first_open_date": "",
        "first_open_shown_at": "",
        "last_popup_time": 0,
        "last_wake_at": "",
        "snooze_until": 0
    }

def save_notifier_state(state: dict):
    """Saves persistent notifier state atomically."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_file = DATA_DIR / "notifier_state.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        if tmp_file.exists():
            tmp_file.replace(STATE_FILE)
    except Exception as e:
        print(f"Error saving notifier state: {e}")

def get_today_status() -> dict:
    """Checks challenges.json or localhost API to get today's task, streak, and broken streak details."""
    # First: Auto-sync from browser activity (detect if task was solved on Unstop without clicking notification)
    if auto_sync_and_mark_completed:
        try:
            auto_sync_and_mark_completed()
        except Exception:
            pass

    data = None
    try:
        res = requests.get("http://localhost:3000/api/today", timeout=2)
        if res.status_code == 200:
            data = res.json()
    except Exception:
        pass

    if data and "challenge" in data and "stats" in data:
        c = data.get("challenge", {})
        s = data.get("stats", {})
        u = data.get("user", {})

        cur_day = c.get("day", 1)
        cur_day = c.get("day", 1)
        remaining_days = max(1, 30 - cur_day + 1)
        remaining_range = f"Day {cur_day} to 30"
        streak = s.get("streak", 0)
        best_streak = max(s.get("best_streak", 16), 16)
        missed = s.get("missed", 0)
        streak_breaks = s.get("streak_breaks", [])

        is_broken = s.get("is_broken")
        if is_broken is None:
            is_broken = (streak == 0 and (s.get("last_broken_streak", 0) > 0 or s.get("last_broken_day") is not None or missed > 0 or best_streak > 0 or cur_day > 1))

        last_broken_day = s.get("last_broken_day")
        if last_broken_day is None:
            if streak_breaks:
                last_broken_day = streak_breaks[-1].get("day")
            elif s.get("missed_days"):
                last_broken_day = s.get("missed_days")[-1]
            elif is_broken and cur_day > 1:
                last_broken_day = cur_day - 1

        last_broken_streak = s.get("last_broken_streak", 0)
        if not last_broken_streak and streak_breaks:
            last_broken_streak = streak_breaks[-1].get("streak_lost", best_streak)
        if not last_broken_streak and is_broken:
            last_broken_streak = best_streak

        streak_remaining = 30 if (streak == 0 or is_broken) else max(0, 30 - streak)

        s["is_broken"] = bool(is_broken)
        s["last_broken_day"] = last_broken_day
        s["last_broken_streak"] = last_broken_streak
        s["remaining_days"] = remaining_days
        s["remaining_range"] = remaining_range
        s["total_days"] = 30
        s["best_streak"] = best_streak
        s["streak_remaining"] = streak_remaining
        s["remaining"] = streak_remaining

        return {
            "challenge": c,
            "stats": s,
            "user": u or {"name": "Animesh Goswami"}
        }

    # Fallback to local challenges.json
    db_file = DB_PATH if DB_PATH.exists() else (BASE_DIR / "challenges.json")
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            settings = db.get("settings", {})
            user = {}
            raw_user = settings.get("user_profile")
            if raw_user:
                try:
                    user = json.loads(raw_user)
                except Exception:
                    pass

            today = today_date_str()
            # 1. Check if any challenge was solved TODAY
            solved_today = next(
                (c for c in challenges if c.get("status") == "solved" and (
                    (c.get("solved_at") or "").split("T")[0] == today or c.get("assigned_date") == today
                )),
                None
            )
            if solved_today:
                pending = solved_today
            else:
                pending = next((c for c in challenges if c.get("status") == "pending" and (not c.get("assigned_date") or c.get("assigned_date") <= today)), None)
                if not pending:
                    pending = next((c for c in challenges if c.get("status") in ("pending", "upcoming")), None)
                if not pending:
                    max_solved = max([c.get("day", 0) for c in challenges if c.get("status") == "solved"], default=0)
                    next_day = min(max_solved + 1, 30)
                    pending = next((c for c in challenges if c.get("day") == next_day), {"day": next_day, "status": "pending"})

            solved_days = {c.get("day") for c in challenges if c.get("status") == "solved"}
            missed_days = [c.get("day") for c in challenges if c.get("status") == "missed"]
            streak = 0
            cur_day = pending.get("day", 1)
            start_day = cur_day if pending.get("status") == "solved" else cur_day - 1
            chain = []
            for d in range(start_day, 0, -1):
                if d in solved_days:
                    streak += 1
                    chain.append(d)
                else:
                    break

            best_streak = max(settings.get("best_streak", 16), 16, streak)
            streak_breaks = settings.get("streak_breaks", [])
            last_broken_streak = settings.get("last_broken_streak", 0)
            last_broken_day = settings.get("last_broken_day")

            is_broken = (streak == 0 and (last_broken_streak > 0 or last_broken_day is not None or len(missed_days) > 0 or best_streak > 0 or cur_day > 1))
            if not last_broken_day and is_broken:
                last_broken_day = cur_day - 1 if cur_day > 1 else 1
            if not last_broken_streak and is_broken:
                last_broken_streak = best_streak

            streak_started_day = chain[-1] if chain else None
            streak_started_date = None
            if streak_started_day:
                for ch in challenges:
                    if ch.get("day") == streak_started_day:
                        raw_d = ch.get("solved_at") or ch.get("created_at") or ""
                        streak_started_date = raw_d.split("T")[0] if "T" in raw_d else (raw_d or today_date_str())
                        break
            streak_days_running = streak
            streak_remaining = 30 if (streak == 0 or is_broken) else max(0, 30 - streak)
            remaining_days = max(1, 30 - cur_day + 1)
            remaining_range = f"Day {cur_day} to 30"

            return {
                "challenge": pending,
                "stats": {
                    "streak": streak,
                    "best_streak": best_streak,
                    "solved": len(solved_days),
                    "missed": len(missed_days),
                    "total": 30,
                    "remaining": streak_remaining,
                    "remaining_days": remaining_days,
                    "remaining_range": remaining_range,
                    "streak_remaining": streak_remaining,
                    "streak_started_day": streak_started_day,
                    "streak_started_date": streak_started_date,
                    "streak_days_running": streak_days_running,
                    "quest_remaining": max(0, 30 - len(solved_days)),
                    "is_broken": is_broken,
                    "last_broken_day": last_broken_day,
                    "last_broken_streak": last_broken_streak,
                    "streak_breaks": streak_breaks,
                    "missed_days": missed_days
                },
                "user": user or {"name": "Animesh Goswami"}
            }
        except Exception as e:
            print(f"Error reading DB: {e}")

    return {
        "challenge": {"day": 1, "status": "pending", "title": "Daily Coding Challenge"},
        "stats": {
            "streak": 0, "best_streak": 0, "solved": 0, "missed": 0,
            "total": 30, "remaining_days": 30, "remaining_range": "Day 1 to 30",
            "is_broken": False, "last_broken_day": None, "last_broken_streak": 0
        },
        "user": {"name": "Animesh Goswami"}
    }

def is_today_completed() -> bool:
    """
    Returns True ONLY if today's challenge is confirmed completed / solved.
    If today's challenge is pending or upcoming, returns False so reminders fire properly.
    """
    today = today_date_str()
    # Check database
    for path in (DB_PATH, BASE_DIR / "challenges.json"):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    db = json.load(f)
                challenges = db.get("challenges", [])
                # If today has a pending challenge, it is NOT completed!
                today_pending = next((c for c in challenges if (c.get("assigned_date") == today or not c.get("assigned_date")) and c.get("status") in ("pending", "upcoming")), None)
                if today_pending:
                    return False

                for c in challenges:
                    if c.get("status") == "solved":
                        solved_date = (c.get("solved_at") or "").split("T")[0]
                        assigned_date = c.get("assigned_date") or ""
                        if solved_date == today or assigned_date == today:
                            return True
            except Exception:
                pass

    return False

def is_notification_allowed(force: bool = False) -> tuple:
    """
    Strict Notification Policy:
    1. Notification will ONLY pop up on and after 00:00 (midnight IST).
    2. After completing today's challenge, it does NOT show ANY notification.
    3. Before time (before 00:00 of the active challenge day), NO notification will show.
    """
    if force:
        return True, "Force flag enabled."

    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")

    # Rule 1 & 2: If today's challenge is completed, do NOT show any notification!
    if is_today_completed():
        return False, f"Today ({today}) challenge is already COMPLETED. All notifications suppressed."

    # Rule 3: Before time check - verify challenge status
    data = get_today_status()
    challenge = data.get("challenge", {})
    status = challenge.get("status", "pending")
    day = challenge.get("day", 1)

    if status == "solved":
        return False, f"Day {day} is already SOLVED. Notification completely suppressed."

    assigned_date = challenge.get("assigned_date")
    if assigned_date and assigned_date > today:
        return False, f"Day {day} is scheduled for future date {assigned_date}. Before time (00:00): no notification allowed."

    if status == "upcoming" and (not assigned_date or assigned_date > today):
        return False, f"Day {day} is UPCOMING. Before time (00:00): no notification allowed."

    return True, "On or after 00:00 and challenge is pending."

_last_toast_time = 0
_toast_lock = threading.Lock()

def _run_silent_powershell(args, timeout: int = 20):
    """Executes notification powershell completely silently with zero console/terminal window pop-ups."""
    vbs_path = BASE_DIR / "send_notification_silent.vbs"
    if sys.platform == "win32" and vbs_path.exists():
        try:
            return subprocess.run(
                ["wscript.exe", "//B", "//Nologo", str(vbs_path)] + args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
        except Exception:
            pass

    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    full_args = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
    ] + args

    return subprocess.run(
        full_args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=creationflags,
        startupinfo=startupinfo
    )

def send_laptop_toast(title: str, body: str, link: str, subtext: str = "", force: bool = False):
    """Sends native Windows Toast notification asynchronously and 100% silently (zero command shell windows).
    Enforces strict policy: only on and after 00:00, never after completed, never before time."""
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed:
            print(f"🔕 [Toast Suppressed] {reason}")
            return False

    global _last_toast_time
    now = time.time()
    with _toast_lock:
        if not force and (now - _last_toast_time) < 5:
            return False
        _last_toast_time = now

    def _run():
        try:
            ps1_path = BASE_DIR / "send_laptop_notification.ps1"
            safe_link = link or "https://unstop.com/practice/coding"

            if ps1_path.exists():
                args = [
                    "-File", str(ps1_path),
                    "-Title", title,
                    "-Body", body,
                    "-Link", safe_link
                ]
                if subtext:
                    args.extend(["-SubText", subtext])
                _run_silent_powershell(args, timeout=20)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return True

def launch_gui_popup(popup_data: dict, force: bool = False):
    """
    Pops up the interactive desktop card directly on the laptop screen.
    Uses independent pythonw process so the background wake checker
    keeps running uninterrupted.
    Enforces strict policy: only on and after 00:00, never after completed, never before time.
    """
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed:
            print(f"🔕 [Popup Suppressed] {reason}")
            return

    if is_popup_window_open():
        print("ℹ️ [Popup Alert] Desktop popup card is already active on screen.")
        return

    try:
        data_json = json.dumps(popup_data)
        pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
        notifier_script = Path(__file__).resolve()

        subprocess.Popen(
            [pythonw_exe, str(notifier_script), "--popup-data", data_json],
            cwd=str(BASE_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        print("🖥️ [Popup Alert] Directly popped up interactive alert card on laptop screen!")
    except Exception as e:
        print(f"Error launching popup subprocess: {e}")
        # Thread fallback
        threading.Thread(target=lambda: show_gui_popup(**popup_data, force=force), daemon=True).start()

def show_gui_popup(
    day: int = 1,
    title: str = "Daily Coding Challenge",
    streak: int = 0,
    link: str = "https://unstop.com/practice/coding",
    user_name: str = "Animesh",
    is_first_open: bool = False,
    is_broken: bool = False,
    last_broken_day: int = None,
    last_broken_streak: int = 0,
    remaining_days: int = 1,
    total_days: int = 30,
    best_streak: int = 16,
    streak_started_date: str = "",
    streak_started_day: int = None,
    streak_days_running: int = 0,
    streak_remaining: int = 30,
    status: str = "pending",
    solved_count: int = 12,
    missed_count: int = 1,
    force: bool = False
):
    """Displays a sleek modern interactive popup card right on the laptop home screen."""
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed or status == "solved":
            print(f"🔕 [Popup Suppressed] {reason if not allowed else 'Day ' + str(day) + ' is already solved. No popup shown.'}")
            return
    lock_file = DATA_DIR / "popup_open.lock"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Unstop Daily Challenge Alert")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#0c101d")

        # Responsive modern dimensions
        win_w, win_h = 500, 390
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        pos_x = max(10, screen_w - win_w - 30)
        pos_y = max(10, screen_h - win_h - 60)
        root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

        # Drag-and-move window functionality
        def start_move(event):
            root.x = event.x
            root.y = event.y

        def do_move(event):
            deltax = event.x - root.x
            deltay = event.y - root.y
            x = root.winfo_x() + deltax
            y = root.winfo_y() + deltay
            root.geometry(f"+{x}+{y}")

        root.bind("<ButtonPress-1>", start_move)
        root.bind("<B1-Motion>", do_move)

        def close_win():
            try:
                if lock_file.exists():
                    lock_file.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass

        # Outer card frame: emerald if solved, crimson if broken/0, amber if pending
        if status == "solved":
            card_border_color = "#10b981"
        elif is_broken or streak == 0:
            card_border_color = "#ef4444"
        else:
            card_border_color = "#ff9600"

        card = tk.Frame(root, bg="#131a2e", highlightthickness=2, highlightbackground=card_border_color)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # Header Frame
        header = tk.Frame(card, bg="#131a2e")
        header.pack(fill="x", padx=16, pady=(12, 6))

        if status == "solved":
            badge_text = f"✅ DAY {day} SOLVED • STREAK SECURED"
            badge_bg = "#064e3b"
            badge_fg = "#6ee7b7"
        elif is_broken or streak == 0:
            badge_text = f"💔 STREAK BROKEN (RESET TO 0) • RESTART FROM DAY {day}"
            badge_bg = "#450a0a"
            badge_fg = "#fca5a5"
        elif is_first_open:
            badge_text = "☀️ FIRST LAPTOP OPEN OF THE DAY"
            badge_bg = "#1f2842"
            badge_fg = "#ff9600"
        else:
            badge_text = f"⚡ UNSTOP CODING QUEST • DAY {day}"
            badge_bg = "#1f2842"
            badge_fg = "#ff9600"

        badge_lbl = tk.Label(header, text=badge_text, font=("Segoe UI", 9, "bold"), fg=badge_fg, bg=badge_bg, padx=8, pady=3)
        badge_lbl.pack(side="left")

        close_btn = tk.Label(header, text="✕", font=("Segoe UI", 12, "bold"), fg="#718096", bg="#131a2e", cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: close_win())

        # Main Content Frame
        content = tk.Frame(card, bg="#131a2e")
        content.pack(fill="both", expand=True, padx=18, pady=4)

        # Flame & Streak row
        flame_row = tk.Frame(content, bg="#131a2e")
        flame_row.pack(anchor="w", pady=(2, 4))

        safe_best = max(best_streak, 16)
        if status == "solved":
            flame_icon = tk.Label(flame_row, text="🔥", font=("Segoe UI Emoji", 20), bg="#131a2e")
            flame_icon.pack(side="left", padx=(0, 8))

            streak_txt = f"{streak}-Day Streak Active & Protected!"
            streak_lbl = tk.Label(flame_row, text=streak_txt, font=("Segoe UI", 13, "bold"), fg="#34d399", bg="#131a2e")
            streak_lbl.pack(side="left")

            started_info = f"Started {streak_started_date or ('Day ' + str(streak_started_day))} • " if (streak_started_date or streak_started_day) else ""
            best_txt = f" ({started_info}{streak_days_running}d active • Best: {safe_best}d)"
            streak_sub = tk.Label(flame_row, text=best_txt, font=("Segoe UI", 9), fg="#94a3b8", bg="#131a2e")
            streak_sub.pack(side="left", padx=(4, 0), pady=(3, 0))
        elif is_broken or streak == 0:
            flame_icon = tk.Label(flame_row, text="💔", font=("Segoe UI Emoji", 20), bg="#131a2e")
            flame_icon.pack(side="left", padx=(0, 8))

            streak_txt = "Streak Broken (0 Days)"
            streak_lbl = tk.Label(flame_row, text=streak_txt, font=("Segoe UI", 13, "bold"), fg="#f87171", bg="#131a2e")
            streak_lbl.pack(side="left")

            sub_broken = f" (Lost {last_broken_streak}d streak • Best: {safe_best}d)" if last_broken_streak else f" (Best: {safe_best}d)"
            streak_sub = tk.Label(flame_row, text=sub_broken, font=("Segoe UI", 9), fg="#94a3b8", bg="#131a2e")
            streak_sub.pack(side="left", padx=(4, 0), pady=(3, 0))
        else:
            flame_icon = tk.Label(flame_row, text="🔥", font=("Segoe UI Emoji", 20), bg="#131a2e")
            flame_icon.pack(side="left", padx=(0, 8))

            streak_txt = f"{streak}-Day Streak Active!"
            streak_lbl = tk.Label(flame_row, text=streak_txt, font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#131a2e")
            streak_lbl.pack(side="left")

            started_info = f"Started {streak_started_date or ('Day ' + str(streak_started_day))} • " if (streak_started_date or streak_started_day) else ""
            best_txt = f" ({started_info}{streak_days_running}d active • Best: {safe_best}d)"
            streak_sub = tk.Label(flame_row, text=best_txt, font=("Segoe UI", 9), fg="#94a3b8", bg="#131a2e")
            streak_sub.pack(side="left", padx=(4, 0), pady=(3, 0))

        # Remaining Tasks / Streak Progress Highlight Box
        rem_box = tk.Frame(content, bg="#0f172a", padx=10, pady=5, highlightthickness=1, highlightbackground="#334155")
        rem_box.pack(fill="x", pady=(2, 6))

        if status == "solved":
            rem_label_text = f"🎯 TARGET PROGRESS: {streak_remaining} Tasks Remaining for 30-Day Streak ({30 - solved_count} Quests Left)"
            rem_fg = "#34d399"
        elif is_broken or streak == 0:
            rem_label_text = f"📍 RESET TO 30: {streak_remaining} Tasks Remaining for 30-Day Streak • Day {day} to {total_days}"
            rem_fg = "#f87171"
        else:
            rem_label_text = f"🎯 STREAK TARGET: {streak_remaining} Tasks Remaining to Complete 30-Day Streak (Day {day} to {total_days})"
            rem_fg = "#38bdf8"

        rem_lbl = tk.Label(rem_box, text=rem_label_text, font=("Segoe UI", 9, "bold"), fg=rem_fg, bg="#0f172a")
        rem_lbl.pack(anchor="w")

        # Problem Details Box
        problem_box = tk.Frame(content, bg="#0d1424", padx=10, pady=6, highlightthickness=1, highlightbackground="#24304c")
        problem_box.pack(fill="x", pady=(2, 6))

        prob_prefix = f"Day {day} [SOLVED]: " if status == "solved" else f"Day {day}: "
        prob_fg = "#34d399" if status == "solved" else "#fde047"
        day_lbl = tk.Label(problem_box, text=f"{prob_prefix}{title}", font=("Segoe UI", 10, "bold"), fg=prob_fg, bg="#0d1424", wraplength=430, justify="left")
        day_lbl.pack(anchor="w")

        # Motivational Copy
        if status == "solved":
            note_text = f"Awesome consistency, {user_name}! Day {day} is already solved and your {streak}-day streak is secured. Popups are completely suppressed today. Day 14 opens tomorrow!"
        elif is_broken:
            broken_after_str = f"Day {last_broken_day}" if last_broken_day else "earlier"
            note_text = f"Hey {user_name}! Your streak broke after {broken_after_str}, but your 30-day challenge is alive! Tasks remaining reset to 30. Complete Day {day} now to restart your streak!"
        elif is_first_open:
            note_text = f"Hey {user_name}! You just opened your laptop for the first time today. Knock out Day {day} now to protect your {streak}-day streak before midnight!"
        else:
            note_text = f"Hey {user_name}! Day {day} is not completed yet! Keep your {streak}-day streak alive by solving today's challenge now."

        desc_lbl = tk.Label(
            content,
            text=note_text,
            font=("Segoe UI", 9),
            fg="#cbd5e0",
            bg="#131a2e",
            wraplength=430,
            justify="left"
        )
        desc_lbl.pack(anchor="w", pady=(0, 8))

        # Button Row
        btn_row = tk.Frame(content, bg="#131a2e")
        btn_row.pack(fill="x", pady=(2, 6))

        def open_challenge():
            try:
                open_btn.config(text="Opening Chrome...", state="disabled")
            except Exception:
                pass
            open_url_in_chrome(link or "https://unstop.com/practice/coding")
            root.after(400, close_win)

        def mark_as_done():
            try:
                solve_btn.config(text="Saving...", state="disabled")
            except Exception:
                pass
            success = False
            try:
                headers = {}
                if API_SECRET:
                    headers["x-api-key"] = API_SECRET
                res = requests.post("http://localhost:3000/api/solve", headers=headers, timeout=2)
                if res.status_code == 200:
                    success = True
            except Exception:
                pass

            if not success and DB_PATH.exists():
                try:
                    with open(DB_PATH, "r", encoding="utf-8") as f:
                        db = json.load(f)
                    for c in db.get("challenges", []):
                        if c.get("day") == day:
                            c["status"] = "solved"
                            c["solved_at"] = datetime.now(IST).isoformat()
                            success = True
                            break
                    if success:
                        with open(DB_PATH, "w", encoding="utf-8") as f:
                            json.dump(db, f, indent=2)
                except Exception as ex:
                    print(f"Direct DB solve error: {ex}")

            if success:
                state = load_notifier_state()
                state["last_first_open_date"] = today_date_str()
                state["first_open_resolved_at"] = datetime.now(IST).isoformat()
                save_notifier_state(state)
            root.after(300, close_win)

        def snooze():
            snooze_ts = time.time() + (30 * 60)
            state = load_notifier_state()
            state["snooze_until"] = snooze_ts
            save_notifier_state(state)
            close_win()

        open_btn = tk.Button(
            btn_row,
            text=f"🚀 Open Day {day} in Chrome",
            font=("Segoe UI", 9, "bold"),
            bg="#10b981",
            fg="#ffffff",
            activebackground="#059669",
            activeforeground="#ffffff",
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            command=open_challenge
        )
        open_btn.pack(side="left", padx=(0, 6))

        solve_btn = tk.Button(
            btn_row,
            text="✅ Mark Solved",
            font=("Segoe UI", 9, "bold"),
            bg="#3b82f6",
            fg="#ffffff",
            activebackground="#2563eb",
            activeforeground="#ffffff",
            bd=0,
            padx=9,
            pady=6,
            cursor="hand2",
            command=mark_as_done
        )
        solve_btn.pack(side="left", padx=(0, 6))

        snooze_btn = tk.Button(
            btn_row,
            text="⏰ Remind in 30m",
            font=("Segoe UI", 9),
            bg="#273142",
            fg="#cbd5e0",
            activebackground="#374151",
            activeforeground="#ffffff",
            bd=0,
            padx=8,
            pady=6,
            cursor="hand2",
            command=snooze
        )
        snooze_btn.pack(side="left")

        # Live Background Poll: If user completes task in Chrome/Unstop WITHOUT clicking this popup,
        # automatically detect it, show celebration, and auto-close!
        def poll_auto_solved():
            if not lock_file.exists():
                return
            if auto_sync_and_mark_completed:
                try:
                    sync_res = auto_sync_and_mark_completed()
                    if sync_res.get("synced") and (sync_res.get("status") == "solved" or sync_res.get("newly_solved")):
                        day_lbl.config(text=f"✅ {sync_res.get('title', title)}", fg="#10b981")
                        new_streak_calc = 1 if is_broken else streak + 1
                        streak_txt_updated = f"{sync_res.get('streak', new_streak_calc)}-Day Streak Secured! 🔥"
                        streak_lbl.config(text=streak_txt_updated, fg="#10b981")
                        desc_lbl.config(text="🎉 Awesome job! We automatically detected your completion on Unstop! Marked as SOLVED. Closing alert now...", fg="#a7f3d0")
                        for widget in btn_row.winfo_children():
                            widget.pack_forget()
                        auto_badge = tk.Label(btn_row, text="✨ AUTO-DETECTED ON UNSTOP • SOLVED!", font=("Segoe UI", 9, "bold"), fg="#10b981", bg="#131a2e")
                        auto_badge.pack(side="left", pady=4)
                        root.after(2200, close_win)
                        return
                except Exception:
                    pass
            root.after(3500, poll_auto_solved)

        root.after(3500, poll_auto_solved)

        root.lift()
        root.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"GUI Popup error: {e}")
    finally:
        try:
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)
        except Exception:
            pass

def trigger_laptop_open_alert(force: bool = False, reason: str = "Laptop Open") -> bool:
    """
    Called whenever the laptop is opened, unlocked, or woken from sleep.
    If today's challenge is not yet completed, directly pops up the desktop alert card
    and delivers the Windows notification banner.
    """
    allowed, notif_reason = is_notification_allowed(force=force)
    if not allowed and not force:
        print(f"🔕 [Laptop Open Check] {notif_reason} (Reason: {reason})")
        return False

    today = today_date_str()

    # Step 1: Auto-fetch & sync from browser history FIRST
    if auto_sync_and_mark_completed:
        try:
            sync_res = auto_sync_and_mark_completed()
            if sync_res.get("synced") and sync_res.get("newly_solved"):
                print(f"🎉 [Auto-Sync] Detected completion on Unstop! '{sync_res.get('title')}' auto-marked as SOLVED.")
        except Exception:
            pass

    state = load_notifier_state()
    now_ts = time.time()

    # 1. Check snooze
    if not force and now_ts < state.get("snooze_until", 0):
        remaining = int(state.get("snooze_until", 0) - now_ts)
        print(f"⏰ [Laptop Open Check] Snoozed ({remaining}s remaining). Skipping popup.")
        return False

    # 2. Debounce cooldown (8 seconds) to prevent duplicate overlapping windows from same lid open
    last_popup_time = state.get("last_popup_time", 0)
    if not force and (now_ts - last_popup_time) < 8:
        print(f"⏳ [Laptop Open Check] Debounced ({int(now_ts - last_popup_time)}s ago). Skipping duplicate.")
        return False

    data = get_today_status()
    challenge = data.get("challenge", {})
    stats = data.get("stats", {})
    user = data.get("user", {})

    status = challenge.get("status", "pending")
    day = challenge.get("day", 1)
    title = challenge.get("title") or f"Day {day} Challenge"
    streak = stats.get("streak", 0)
    best_streak = stats.get("best_streak", streak)
    is_broken = stats.get("is_broken", False)
    last_broken_day = stats.get("last_broken_day")
    last_broken_streak = stats.get("last_broken_streak", 0)
    remaining_days = stats.get("remaining_days", max(1, 30 - day + 1))
    remaining_range = stats.get("remaining_range", f"Day {day} to 30")
    total_days = stats.get("total_days", 30)

    user_name = user.get("name", "Animesh").split()[0]
    link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    if status == "solved" and not force:
        print(f"✅ [Laptop Open Check] Day {day} is already SOLVED! Notification completely suppressed.")
        state["last_first_open_date"] = today
        state["first_open_resolved_at"] = datetime.now(IST).isoformat()
        save_notifier_state(state)
        return False

    is_first_open = (state.get("last_first_open_date") != today)
    print(f"☀️ [Laptop Open Alert] Laptop opened ({reason})! Day {day} is NOT completed. Directly popping up notification!")

    state["last_first_open_date"] = today
    state["last_popup_time"] = now_ts
    state["first_open_shown_at"] = datetime.now(IST).isoformat()
    save_notifier_state(state)

    # 1. Native Windows Toast
    clean_title = title.split(":", 1)[1].strip() if ":" in title else title
    safe_best = max(best_streak, 16)
    streak_started_date = stats.get("streak_started_date")
    streak_started_day = stats.get("streak_started_day")
    streak_days_running = stats.get("streak_days_running", streak)
    streak_remaining = stats.get("streak_remaining", 30 if (streak == 0 or is_broken) else max(0, 30 - streak))

    if is_broken or streak == 0:
        broken_day_str = f"Day {last_broken_day}" if last_broken_day else "earlier day"
        toast_title = f"💔 Streak Broken! Reset to 0 (30 Tasks Remaining)"
        toast_body = f"Streak broke after {broken_day_str}! Current streak is 0 Days. Tasks remaining reset to 30. Complete Day {day}: '{clean_title}' to restart your streak!"
        toast_subtext = f"📍 Streak: 0 Days • 30 Tasks Remaining • Best: {safe_best}d"
    elif is_first_open:
        started_str = f"Started {streak_started_date} (Day {streak_started_day})" if streak_started_date else ""
        toast_title = f"☀️ Good Day {user_name}! Day {day} Waiting! ({streak_remaining} Tasks Remaining)"
        toast_body = f"First laptop open of the day! Complete Day {day} to keep your {streak}-day streak alive! {streak_remaining} tasks left for 30-day goal."
        toast_subtext = f"🔥 Streak: {streak}d ({started_str}) • {streak_remaining} Tasks Remaining • Best: {safe_best}d"
    else:
        started_str = f"Started {streak_started_date} (Day {streak_started_day})" if streak_started_date else ""
        toast_title = f"⚡ Day {day} Challenge Pending! ({streak_remaining} Tasks Remaining)"
        toast_body = f"Hey {user_name}! Day {day} is not solved yet. Keep your {streak}-day streak alive! {streak_remaining} tasks left for 30-day goal."
        toast_subtext = f"🔥 Streak: {streak}d ({started_str}) • {streak_remaining} Tasks Remaining • Best: {safe_best}d"

    # Push exactly ONE notification at once
    send_laptop_toast(toast_title, toast_body, link, subtext=toast_subtext, force=force)

    return True

# Alias for backwards compatibility
check_first_open_of_day = trigger_laptop_open_alert

def check_and_notify(force: bool = False, reason: str = "Opening Check"):
    """Checks if today's task is done; triggers popup & toast."""
    trigger_laptop_open_alert(force=force, reason=reason)

def ntfy_stream_listener():
    """Listens in real time to ntfy.sh topic for telemetry and sync."""
    topic = NTFY_TOPIC or "animesh_unstop_streak"
    url = f"https://ntfy.sh/{topic}/raw"
    print(f"📱 [ntfy Listener] Connected to stream: https://ntfy.sh/{topic}")

    while True:
        try:
            with requests.get(url, stream=True, timeout=90) as resp:
                for line in resp.iter_lines():
                    if line:
                        msg = line.decode("utf-8", errors="replace").strip()
                        print(f"🔔 [ntfy Stream Received]: {msg}")
                        # Strict policy: Notifications ONLY pop up upon opening the laptop lid.
                        # Never pop up from network stream while laptop is actively in use.
        except Exception:
            time.sleep(10)

def periodic_wake_checker():
    """
    Actively monitors laptop lid open / wake events.
    Strict Policy:
    1. Notification will ONLY pop up ONCE every time the laptop lid is opened.
    2. Notification will ONLY pop up IF today's task is NOT completed.
    3. If today's task IS completed, notification is completely SUPPRESSED (zero popups/toasts).
    4. While the laptop remains open and in use, NO repeat notifications or periodic timers will pop up.
    """
    last_tick = time.time()
    last_sync_time = 0
    was_locked = is_workstation_locked()
    lid_open_pending = False

    print("👀 [Lid Monitor] Active: Monitoring laptop lid open events...")

    while True:
        try:
            time.sleep(0.5)
            now = time.time()
            elapsed_since_tick = now - last_tick
            last_tick = now

            # Background Auto-Sync: Check browser history every 15 seconds to ensure solved state is fresh
            if now - last_sync_time >= 15:
                last_sync_time = now
                if auto_sync_and_mark_completed:
                    try:
                        sync_res = auto_sync_and_mark_completed()
                        if sync_res.get("synced") and sync_res.get("newly_solved"):
                            print(f"🎉 [Auto-Sync Background] Detected completed challenge on Unstop! '{sync_res.get('title')}' auto-marked as SOLVED.")
                    except Exception:
                        pass

            # Detect System Wake / Lid Open:
            # When the laptop lid is closed, Windows enters Modern Standby or Sleep.
            # While suspended, the 0.5s sleep freezes.
            # Upon opening the laptop lid, elapsed_since_tick jumps (> 2.0s).
            is_system_wake = (elapsed_since_tick > 2.0)

            if is_system_wake:
                print(f"💻 [Lid Open Event] Laptop lid opened / woke from sleep (suspended ~{int(elapsed_since_tick)}s).")
                lid_open_pending = True

            # Track workstation lock status & unlock transitions
            now_locked = is_workstation_locked()
            is_unlock_event = (was_locked and not now_locked)
            was_locked = now_locked

            if is_unlock_event:
                print("🔓 [Unlock Event] Workstation unlocked by user! Laptop is active.")
                lid_open_pending = True

            # When laptop lid is opened or workstation unlocked:
            # Wait until workstation is NOT locked (user is actively at the desktop)
            if lid_open_pending and not now_locked:
                time.sleep(0.5)  # Brief pause for desktop shell to stabilize
                allowed, reason = is_notification_allowed(force=False)
                if not allowed:
                    print(f"🔕 [Lid Open Check] Challenge already completed / suppressed: {reason}")
                else:
                    print("🔔 [Lid Open Check] Today's task is NOT completed! Popping up reminder once...")
                    trigger_laptop_open_alert(force=False, reason="Laptop Lid Opened / Screen Unlocked")

                # Once popped up, clear pending flag so it does NOT fire again while laptop remains open
                lid_open_pending = False

        except Exception as e:
            time.sleep(5)

def install_startup():
    """Configures automatic launch on Windows login / laptop open."""
    startup_dir = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    if not startup_dir.exists():
        print(f"❌ Startup directory not found: {startup_dir}")
        return False

    vbs_path = startup_dir / "UnstopLaptopNotifier.vbs"
    script_path = Path(__file__).resolve()
    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")

    vbs_content = f'CreateObject("Wscript.Shell").Run """{pythonw_exe}"" ""{script_path}""", 0, False\n'
    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print(f"✅ Startup launcher installed to: {vbs_path}")

        cmd = f'schtasks /create /tn "UnstopLaptopNotifier" /tr "\"{pythonw_exe}\" \"{script_path}\"" /sc onlogon /f'
        subprocess.run(cmd, shell=True, capture_output=True)
        print("✅ Windows Scheduled Task 'UnstopLaptopNotifier' registered!")
        return True
    except Exception as e:
        print(f"❌ Failed to install startup launcher: {e}")
        return False

def uninstall_startup():
    startup_dir = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    vbs_path = startup_dir / "UnstopLaptopNotifier.vbs"
    if vbs_path.exists():
        vbs_path.unlink()
        print("🗑️ Removed Startup launcher.")
    subprocess.run('schtasks /delete /tn "UnstopLaptopNotifier" /f', shell=True, capture_output=True)
    print("🗑️ Removed Scheduled Task.")

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--install" in args:
        install_startup()
        sys.exit(0)
    elif "--uninstall" in args:
        uninstall_startup()
        sys.exit(0)
    elif "--popup-data" in args:
        try:
            data_idx = args.index("--popup-data") + 1
            payload = json.loads(args[data_idx])
            show_gui_popup(
                day=payload.get("day", 1),
                title=payload.get("title", "Daily Coding Challenge"),
                streak=payload.get("streak", 0),
                link=payload.get("link", "https://unstop.com/practice/coding"),
                user_name=payload.get("user_name", "Animesh"),
                is_first_open=payload.get("is_first_open", False),
                is_broken=payload.get("is_broken", False),
                last_broken_day=payload.get("last_broken_day"),
                last_broken_streak=payload.get("last_broken_streak", 0),
                remaining_days=payload.get("remaining_days", 1),
                total_days=payload.get("total_days", 30),
                best_streak=payload.get("best_streak", 16),
                streak_started_date=payload.get("streak_started_date", ""),
                streak_started_day=payload.get("streak_started_day"),
                streak_days_running=payload.get("streak_days_running", 0),
                streak_remaining=payload.get("streak_remaining", 30),
                status=payload.get("status", "pending"),
                solved_count=payload.get("solved_count", 12),
                missed_count=payload.get("missed_count", 1)
            )
        except Exception as e:
            print(f"Error launching popup from --popup-data: {e}")
        sys.exit(0)
    elif "--popup-window" in args:
        # Args: --popup-window <day> <streak> <title> <link> <user_name>
        try:
            day = int(args[1]) if len(args) > 1 else 1
            streak = int(args[2]) if len(args) > 2 else 0
            title = args[3] if len(args) > 3 else f"Day {day} Challenge"
            link = args[4] if len(args) > 4 else "https://unstop.com/practice/coding"
            user_name = args[5] if len(args) > 5 else "Animesh"
        except Exception:
            data = get_today_status()
            c = data.get("challenge", {})
            st = data.get("stats", {})
            u = data.get("user", {})
            day = c.get("day", 1)
            title = c.get("title") or f"Day {day} Challenge"
            streak = st.get("streak", 0)
            link = c.get("link") or u.get("practiceUrl") or "https://unstop.com/practice/coding"
            user_name = u.get("name", "Animesh").split()[0]
        data_full = get_today_status()
        st_data = data_full.get("stats", {})
        c_data = data_full.get("challenge", {})
        show_gui_popup(
            day=day,
            title=title,
            streak=streak,
            link=link,
            user_name=user_name,
            is_first_open=True,
            is_broken=st_data.get("is_broken", False),
            last_broken_day=st_data.get("last_broken_day"),
            last_broken_streak=st_data.get("last_broken_streak", 0),
            remaining_days=st_data.get("remaining_days", max(1, 30 - day + 1)),
            total_days=st_data.get("total_days", 30),
            best_streak=max(st_data.get("best_streak", 16), 16),
            streak_started_date=st_data.get("streak_started_date", ""),
            streak_started_day=st_data.get("streak_started_day"),
            streak_days_running=st_data.get("streak_days_running", streak),
            streak_remaining=st_data.get("streak_remaining", 30 if st_data.get("is_broken") else max(0, 30 - streak)),
            status=c_data.get("status", "pending"),
            solved_count=st_data.get("solved", 12),
            missed_count=st_data.get("missed", 1)
        )
        sys.exit(0)
    elif "--first-open" in args:
        force_flag = "--force" in args or "-f" in args
        print("\n☀️ [Laptop Open Trigger] Checking laptop open alert trigger...")
        triggered = trigger_laptop_open_alert(force=force_flag, reason="CLI Test Trigger")
        if triggered:
            print("✅ Alert delivered successfully (Popup & Toast)!\n")
        else:
            print("ℹ️ Check completed. (No notification required or already completed for today).\n")
            print("💡 Use '--force' to test the notification regardless of status: python unstop_laptop_notifier.py --first-open --force\n")
        sys.exit(0)
    elif "--popup" in args:
        data = get_today_status()
        c = data.get("challenge", {})
        st = data.get("stats", {})
        u = data.get("user", {})
        day = c.get("day", 1)
        title = c.get("title") or f"Day {day} Challenge"
        streak = st.get("streak", 0)
        link = c.get("link") or u.get("practiceUrl") or "https://unstop.com/practice/coding"
        user_name = u.get("name", "Animesh").split()[0]
        show_gui_popup(
            day=day,
            title=title,
            streak=streak,
            link=link,
            user_name=user_name,
            is_first_open=False,
            is_broken=st.get("is_broken", False),
            last_broken_day=st.get("last_broken_day"),
            last_broken_streak=st.get("last_broken_streak", 0),
            remaining_days=st.get("remaining_days", max(1, 30 - day + 1)),
            total_days=st.get("total_days", 30),
            best_streak=max(st.get("best_streak", 16), 16),
            streak_started_date=st.get("streak_started_date", ""),
            streak_started_day=st.get("streak_started_day"),
            streak_days_running=st.get("streak_days_running", streak),
            streak_remaining=st.get("streak_remaining", 30 if st.get("is_broken") else max(0, 30 - streak)),
            status=c.get("status", "pending"),
            solved_count=st.get("solved", 12),
            missed_count=st.get("missed", 1)
        )
        sys.exit(0)
    elif "--wake" in args or "--test-wake" in args:
        force_flag = "--force" in args
        print("\n💻 [Laptop Wake Check] Checking challenge status on laptop open...")
        time.sleep(1)
        data = get_today_status()
        c = data.get("challenge", {})
        st = data.get("stats", {})
        u = data.get("user", {})
        day = c.get("day", 1)
        status = c.get("status", "pending")
        streak = st.get("streak", 0)
        link = c.get("link") or u.get("practiceUrl") or "https://unstop.com/practice/coding"
        user_name = u.get("name", "Animesh").split()[0]

        if status == "solved" and not force_flag:
            print(f"✅ Day {day} is already completed & marked as SOLVED!")
            print("🔕 Active Tracking: Laptop wake notification is completely SUPPRESSED.")
            print("✨ Zero popups or banners will appear on your home screen.")
            print("💡 (To force-test: python unstop_laptop_notifier.py --wake --force)\n")
            sys.exit(0)

        print(f"⚡ Day {day} is NOT completed yet ({status.upper()})! Firing home screen notification...")
        trigger_laptop_open_alert(force=force_flag, reason="Wake Test")
        print("✅ Notification delivered to your laptop home screen!\n")
        sys.exit(0)
    elif "--check" in args:
        trigger_laptop_open_alert(force=False, reason="Command line check")
        time.sleep(10)
        sys.exit(0)

    print("🚀 [Laptop Notifier] Starting Unstop Laptop Home Screen Notifier...")
    time.sleep(2)  # Wait 2s on initial launch so Windows Explorer / Action Center is ready

    # Initial check on startup / logon
    if is_notification_allowed()[0]:
        trigger_laptop_open_alert(force=False, reason="Laptop Startup / Logon")
    else:
        print("🔕 [Startup Check] Notifications suppressed per policy (challenge completed or before 00:00).")

    t_ntfy = threading.Thread(target=ntfy_stream_listener, daemon=True)
    t_ntfy.start()

    periodic_wake_checker()

