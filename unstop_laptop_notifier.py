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
    """Checks challenges.json or localhost API to get today's task and streak."""
    try:
        res = requests.get("http://localhost:3000/api/today", timeout=2)
        if res.status_code == 200:
            data = res.json()
            return {
                "challenge": data.get("challenge", {}),
                "stats": data.get("stats", {}),
                "user": data.get("user", {})
            }
    except Exception:
        pass

    if DB_PATH.exists():
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            user = {}
            raw_user = db.get("settings", {}).get("user_profile")
            if raw_user:
                try:
                    user = json.loads(raw_user)
                except Exception:
                    pass

            today = today_date_str()
            # 1. Check if any challenge was solved TODAY
            solved_today = next((c for c in challenges if (c.get("solved_at") or "").split("T")[0] == today and c.get("status") == "solved"), None)
            if solved_today:
                pending = solved_today
            else:
                pending = next((c for c in challenges if c.get("status") in ("pending", "upcoming")), None)
                if not pending:
                    max_solved = max([c.get("day", 0) for c in challenges if c.get("status") == "solved"], default=0)
                    next_day = min(max_solved + 1, 30)
                    pending = next((c for c in challenges if c.get("day") == next_day), {"day": next_day, "status": "pending"})

            solved_days = {c.get("day") for c in challenges if c.get("status") == "solved"}
            streak = 0
            cur_day = pending.get("day", 1)
            start_day = cur_day if pending.get("status") == "solved" else cur_day - 1
            for d in range(start_day, 0, -1):
                if d in solved_days:
                    streak += 1
                else:
                    break

            return {
                "challenge": pending,
                "stats": {
                    "streak": streak,
                    "best_streak": db.get("settings", {}).get("best_streak", streak),
                    "solved": len(solved_days)
                },
                "user": user or {"name": "Animesh Goswami"}
            }
        except Exception as e:
            print(f"Error reading DB: {e}")

    return {
        "challenge": {"day": 1, "status": "pending", "title": "Daily Coding Challenge"},
        "stats": {"streak": 0, "solved": 0},
        "user": {"name": "Animesh Goswami"}
    }

def send_laptop_toast(title: str, body: str, link: str):
    """Sends native Windows Toast notification."""
    try:
        safe_title = title.replace('"', '`"').replace("'", "''")
        safe_body = body.replace('"', '`"').replace("'", "''")
        safe_link = link or "https://unstop.com/practice/coding"

        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xmlString = @"
<toast launch="{safe_link}" activationType="protocol">
    <visual>
        <binding template="ToastGeneric">
            <text>{safe_title}</text>
            <text>{safe_body}</text>
            <text>Click to open in Unstop.</text>
        </binding>
    </visual>
    <actions>
        <action content="Open Task in Chrome" arguments="{safe_link}" activationType="protocol" />
    </actions>
</toast>
"@

$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDoc.LoadXml($xmlString)
$appId = "Tracker Unstop"
$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDoc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True,
            timeout=8,
            creationflags=creationflags,
            startupinfo=startupinfo
        )
    except Exception as e:
        print(f"Toast error: {e}")

def show_gui_popup(day: int, title: str, streak: int, link: str, user_name: str = "Animesh", is_first_open: bool = True):
    """Displays a sleek modern interactive popup card right on the laptop home screen."""
    global popup_active
    with popup_lock:
        if popup_active:
            return
        popup_active = True

    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Unstop Daily Challenge Alert")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#0c101d")

        # Responsive modern dimensions
        win_w, win_h = 470, 350
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        pos_x = screen_w - win_w - 30
        pos_y = screen_h - win_h - 60
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

        # Outer card frame with amber/gold glowing border
        card = tk.Frame(root, bg="#131a2e", highlightthickness=2, highlightbackground="#ff9600")
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # Header Frame
        header = tk.Frame(card, bg="#131a2e")
        header.pack(fill="x", padx=16, pady=(12, 6))

        badge_text = "☀️ FIRST LAPTOP OPEN OF THE DAY" if is_first_open else "⚡ UNSTOP CODING QUEST"
        badge_lbl = tk.Label(header, text=badge_text, font=("Segoe UI", 9, "bold"), fg="#ff9600", bg="#1f2842", padx=8, pady=3)
        badge_lbl.pack(side="left")

        def close_win():
            global popup_active
            popup_active = False
            try:
                root.destroy()
            except Exception:
                pass

        close_btn = tk.Label(header, text="✕", font=("Segoe UI", 12, "bold"), fg="#718096", bg="#131a2e", cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: close_win())

        # Main Content Frame
        content = tk.Frame(card, bg="#131a2e")
        content.pack(fill="both", expand=True, padx=18, pady=4)

        # Flame & Streak row
        flame_row = tk.Frame(content, bg="#131a2e")
        flame_row.pack(anchor="w", pady=(2, 6))

        flame_icon = tk.Label(flame_row, text="🔥", font=("Segoe UI Emoji", 24), bg="#131a2e")
        flame_icon.pack(side="left", padx=(0, 10))

        streak_txt = f"{streak}-Day Streak Active!" if streak > 0 else "Start Your 30-Day Flame!"
        streak_lbl = tk.Label(flame_row, text=streak_txt, font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#131a2e")
        streak_lbl.pack(side="left")

        # Problem Details Box
        problem_box = tk.Frame(content, bg="#0d1424", padx=12, pady=8, highlightthickness=1, highlightbackground="#24304c")
        problem_box.pack(fill="x", pady=(2, 8))

        day_lbl = tk.Label(problem_box, text=f"Day {day}: {title}", font=("Segoe UI", 11, "bold"), fg="#ffba08", bg="#0d1424", wraplength=410, justify="left")
        day_lbl.pack(anchor="w")

        # Motivational Copy
        if is_first_open:
            note_text = f"Hey {user_name}! You just opened your laptop for the first time today. Knock out today's quest now to protect your {streak}-day streak before midnight!"
        else:
            note_text = f"Hey {user_name}! Today's coding challenge is still waiting. Keep your streak alive by completing Day {day}!"

        desc_lbl = tk.Label(
            content,
            text=note_text,
            font=("Segoe UI", 9),
            fg="#cbd5e0",
            bg="#131a2e",
            wraplength=410,
            justify="left"
        )
        desc_lbl.pack(anchor="w", pady=(0, 10))

        # Button Row
        btn_row = tk.Frame(content, bg="#131a2e")
        btn_row.pack(fill="x", pady=(2, 8))

        def open_challenge():
            try:
                open_btn.config(text="Opening Chrome...", state="disabled")
            except Exception:
                pass
            open_url_in_chrome(link or "https://unstop.com/practice/coding")
            # Wait 400ms before destroying GUI so Windows command handoff completes cleanly
            root.after(400, close_win)

        def mark_as_done():
            try:
                solve_btn.config(text="Saving...", state="disabled")
            except Exception:
                pass
            success = False
            try:
                # 1. Try local server API
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
                send_laptop_toast(
                    "🎉 Challenge Solved!",
                    f"Awesome job {user_name}! Day {day} is complete and your {streak + 1}-day streak is secured! 🔥",
                    link
                )
            root.after(300, close_win)

        def snooze():
            snooze_ts = time.time() + (30 * 60)
            state = load_notifier_state()
            state["snooze_until"] = snooze_ts
            save_notifier_state(state)
            close_win()

        open_btn = tk.Button(
            btn_row,
            text="🚀 Open Task in Chrome",
            font=("Segoe UI", 9, "bold"),
            bg="#10b981",
            fg="#ffffff",
            activebackground="#059669",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=7,
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
            padx=10,
            pady=7,
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
            pady=7,
            cursor="hand2",
            command=snooze
        )
        snooze_btn.pack(side="left")

        root.lift()
        root.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"GUI Popup error: {e}")
    finally:
        popup_active = False

def trigger_laptop_open_alert(force: bool = False, reason: str = "Laptop Open") -> bool:
    """
    Called whenever the laptop is opened, unlocked, or woken from sleep.
    If today's challenge is not yet solved, pops up the desktop alert card
    and delivers the Windows notification banner.
    """
    today = today_date_str()
    state = load_notifier_state()
    now_ts = time.time()

    # 1. Check snooze
    if not force and now_ts < state.get("snooze_until", 0):
        remaining = int(state.get("snooze_until", 0) - now_ts)
        print(f"⏰ [Laptop Open Check] Snoozed ({remaining}s remaining). Skipping popup.")
        return False

    # 2. Check cooldown between popups (2 minutes) to prevent duplicate overlapping windows
    last_popup_time = state.get("last_popup_time", 0)
    if not force and (now_ts - last_popup_time) < 120:
        print(f"⏳ [Laptop Open Check] Popup already triggered recently ({int(now_ts - last_popup_time)}s ago). Skipping duplicate.")
        return False

    data = get_today_status()
    challenge = data.get("challenge", {})
    stats = data.get("stats", {})
    user = data.get("user", {})

    status = challenge.get("status", "pending")
    day = challenge.get("day", 1)
    title = challenge.get("title") or f"Day {day} Challenge"
    streak = stats.get("streak", 0)
    user_name = user.get("name", "Animesh").split()[0]
    link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    if status == "solved" and not force:
        print(f"✅ [Laptop Open Check] Day {day} is already SOLVED! Home screen popup completely suppressed.")
        state["last_first_open_date"] = today
        state["first_open_resolved_at"] = datetime.now(IST).isoformat()
        save_notifier_state(state)
        return False

    is_first_open = (state.get("last_first_open_date") != today)
    print(f"☀️ [Laptop Open Alert] Laptop opened ({reason})! Day {day} is pending. Launching home screen popup & toast!")

    state["last_first_open_date"] = today
    state["last_popup_time"] = now_ts
    state["first_open_shown_at"] = datetime.now(IST).isoformat()
    save_notifier_state(state)

    # 1. Native Windows Toast
    if is_first_open:
        toast_title = f"☀️ Good Day {user_name}! Day {day} Challenge Waiting!"
        toast_body = f"First laptop open of the day! Complete Day {day} to keep your {streak}-day streak alive! 🚀"
    else:
        toast_title = f"⚡ Unstop Challenge Day {day} Pending!"
        toast_body = f"Hey {user_name}! Day {day} is not solved yet. Keep your {streak}-day streak alive! 🚀"

    send_laptop_toast(toast_title, toast_body, link)

    # 2. Interactive Desktop Home Screen Card Popup
    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
    script_path = str(Path(__file__).resolve())
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
        subprocess.Popen([
            pythonw_exe,
            script_path,
            "--popup-window",
            str(day),
            str(streak),
            title,
            link,
            user_name
        ], creationflags=creationflags)
    except Exception:
        threading.Thread(target=show_gui_popup, args=(day, title, streak, link, user_name, is_first_open), daemon=True).start()

    return True

# Alias for backwards compatibility
check_first_open_of_day = trigger_laptop_open_alert

def check_and_notify(force: bool = False, reason: str = "Opening Check"):
    """Checks if today's task is done; triggers popup & toast."""
    trigger_laptop_open_alert(force=force, reason=reason)

def ntfy_stream_listener():
    """Listens in real time to ntfy.sh topic and alerts laptop screen when message arrives."""
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
                        st = get_today_status()
                        if st.get("challenge", {}).get("status") == "solved":
                            print(f"✅ [ntfy Stream] Today's challenge is already solved! Suppressing notification.")
                        else:
                            trigger_laptop_open_alert(force=False, reason="ntfy alert received")
        except Exception:
            time.sleep(10)

def periodic_wake_checker():
    """
    Actively detects laptop open events:
    1. System wake / lid open (time jump > 3s while sleeping 1s)
    2. Workstation unlock (screen unlocked by user)
    3. Periodic escalation check (every 5 mins)
    """
    last_check_time = time.time()
    last_tick = time.time()
    was_locked = is_workstation_locked()

    while True:
        try:
            time.sleep(1)
            now = time.time()
            elapsed_since_tick = now - last_tick
            last_tick = now

            # Detection 1: Did system wake from sleep/hibernate/lid close?
            # A 1-second sleep taking > 3 seconds means system suspended / lid was closed!
            is_wake = (elapsed_since_tick > 3.0)

            # Detection 2: Did the user unlock their workstation / open screen?
            now_locked = is_workstation_locked()
            is_unlock = (was_locked and not now_locked)
            was_locked = now_locked

            if is_wake:
                print(f"💻 [Wake Detector] Laptop lid opened / woke from sleep (slept ~{int(elapsed_since_tick)}s).")
                time.sleep(1)
                trigger_laptop_open_alert(force=False, reason="Laptop Lid Opened / Woke from Sleep")
                last_check_time = time.time()

            elif is_unlock:
                print("🔓 [Unlock Detector] Workstation unlocked / laptop screen opened!")
                time.sleep(1)
                trigger_laptop_open_alert(force=False, reason="Workstation Unlocked")
                last_check_time = time.time()

            elif now - last_check_time >= 300:
                # 5-minute periodic check
                trigger_laptop_open_alert(force=False, reason="5-Min Periodic Interval")
                last_check_time = now

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
        show_gui_popup(day, title, streak, link, user_name, is_first_open=True)
        sys.exit(0)
    elif "--first-open" in args:
        force_flag = "--force" in args or "-f" in args
        print("\n☀️ [Laptop Open Trigger] Checking laptop open alert trigger...")
        triggered = trigger_laptop_open_alert(force=force_flag, reason="CLI Test Trigger")
        if triggered:
            print("✅ Home screen popup card and Windows Toast launched successfully!\n")
        else:
            print("ℹ️ Check completed. (No popup required or already completed for today).\n")
            print("💡 Use '--force' to test the popup card regardless of status: python unstop_laptop_notifier.py --first-open --force\n")
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
        send_laptop_toast(f"⚡ Unstop Challenge Day {day}", f"Hey {user_name}! Day {day} challenge is waiting! Keep your streak alive! 🚀", link)
        show_gui_popup(day, title, streak, link, user_name, is_first_open=False)
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
        print("✅ Notification and popup delivered to your laptop home screen!\n")
        sys.exit(0)
    elif "--check" in args:
        trigger_laptop_open_alert(force=False, reason="Command line check")
        time.sleep(10)
        sys.exit(0)

    print("🚀 [Laptop Notifier] Starting Unstop Laptop Home Screen Notifier...")
    time.sleep(2)  # Wait 2s on initial launch so Windows Explorer / Action Center is ready

    # Initial check on startup / logon
    trigger_laptop_open_alert(force=False, reason="Laptop Startup / Logon")

    t_ntfy = threading.Thread(target=ntfy_stream_listener, daemon=True)
    t_ntfy.start()

    periodic_wake_checker()

