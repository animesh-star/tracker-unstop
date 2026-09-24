"""
Unstop Laptop Home Screen Notifier & ntfy.sh Listener
Automatically checks on laptop startup / login / lid open:
If today's challenge is not completed, pops up an interactive desktop alert
and Windows Toast notification directly on the laptop home screen.
Also streams ntfy.sh topic alerts in real-time.
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
DB_PATH = DATA_DIR / "challenges.json"
if not DB_PATH.exists():
    DB_PATH = BASE_DIR / "challenges.json"

ENV_FILE = BASE_DIR / ".env"
NTFY_TOPIC = "animesh_unstop_streak"
if ENV_FILE.exists():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NTFY_TOPIC="):
                    NTFY_TOPIC = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

IST = timezone(timedelta(hours=5, minutes=30))

popup_active = False
popup_lock = threading.Lock()
snooze_until = 0

def today_date_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")

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
                pending = next((c for c in challenges if c.get("status") == "pending"), None)
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
        <action content="Open Challenge" arguments="{safe_link}" activationType="protocol" />
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

def show_gui_popup(day: int, title: str, streak: int, link: str, user_name: str = "Animesh"):
    """Displays a sleek modern popup right on the laptop home screen."""
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

        win_w, win_h = 440, 310
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        pos_x = screen_w - win_w - 30
        pos_y = screen_h - win_h - 60
        root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

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

        card = tk.Frame(root, bg="#131a2e", highlightthickness=2, highlightbackground="#ff9600")
        card.pack(fill="both", expand=True, padx=2, pady=2)

        header = tk.Frame(card, bg="#131a2e")
        header.pack(fill="x", padx=16, pady=(14, 6))

        logo_lbl = tk.Label(header, text="⚡ UNSTOP CODING QUEST", font=("Segoe UI", 10, "bold"), fg="#ff9600", bg="#131a2e")
        logo_lbl.pack(side="left")

        def close_win():
            global popup_active
            popup_active = False
            root.destroy()

        close_btn = tk.Label(header, text="✕", font=("Segoe UI", 12, "bold"), fg="#718096", bg="#131a2e", cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: close_win())

        content = tk.Frame(card, bg="#131a2e")
        content.pack(fill="both", expand=True, padx=18, pady=4)

        flame_row = tk.Frame(content, bg="#131a2e")
        flame_row.pack(anchor="w", pady=(2, 6))

        flame_icon = tk.Label(flame_row, text="🔥", font=("Segoe UI Emoji", 24), bg="#131a2e")
        flame_icon.pack(side="left", padx=(0, 10))

        streak_txt = f"{streak}-Day Streak Active!" if streak > 0 else "Start Your 30-Day Flame!"
        streak_lbl = tk.Label(flame_row, text=streak_txt, font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#131a2e")
        streak_lbl.pack(side="left")

        title_lbl = tk.Label(content, text=f"Day {day}: {title}", font=("Segoe UI", 12, "bold"), fg="#ffba08", bg="#131a2e", wraplength=390, justify="left")
        title_lbl.pack(anchor="w", pady=(0, 6))

        desc_lbl = tk.Label(
            content,
            text=f"Hey {user_name}! You haven't completed today's coding quest yet. Complete it now to protect your streak from resetting at midnight!",
            font=("Segoe UI", 9),
            fg="#a0aec0",
            bg="#131a2e",
            wraplength=390,
            justify="left"
        )
        desc_lbl.pack(anchor="w", pady=(0, 12))

        btn_row = tk.Frame(content, bg="#131a2e")
        btn_row.pack(fill="x", pady=(4, 10))

        def open_challenge():
            webbrowser.open(link or "https://unstop.com/practice/coding")
            close_win()

        def snooze():
            global snooze_until
            snooze_until = time.time() + (30 * 60)
            close_win()

        open_btn = tk.Button(
            btn_row,
            text="🚀 Open Challenge Now",
            font=("Segoe UI", 10, "bold"),
            bg="#58cc02",
            fg="#ffffff",
            activebackground="#46a302",
            activeforeground="#ffffff",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            command=open_challenge
        )
        open_btn.pack(side="left", padx=(0, 10))

        snooze_btn = tk.Button(
            btn_row,
            text="⏰ Remind in 30m",
            font=("Segoe UI", 9),
            bg="#2d3748",
            fg="#cbd5e0",
            activebackground="#4a5568",
            activeforeground="#ffffff",
            bd=0,
            padx=10,
            pady=8,
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

def check_and_notify(force=False, reason="Opening Check"):
    """Checks if today's task is done; if not, triggers toast & home screen popup."""
    global snooze_until
    if not force and time.time() < snooze_until:
        return

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
        print(f"✅ [Laptop Alert] Day {day} is already solved! Notification and popup suppressed.")
        return

    if status != "solved" or force:
        print(f"⚡ [Laptop Alert] Task pending for Day {day}! Delivering smooth toast notification ({reason})...")
        send_laptop_toast(
            f"⚡ Unstop Challenge Day {day} Pending!",
            f"Hey {user_name}! Day {day} is not solved yet. Keep your {streak}-day streak alive!",
            link
        )
    else:
        print(f"✅ [Laptop Alert] Day {day} already solved! No notification needed.")

def ntfy_stream_listener():
    """Listens in real time to ntfy.sh topic and pops up on laptop screen when alert arrives."""
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
                            print(f"✅ [ntfy Stream] Today's challenge is already solved! Suppressing notification & popup.")
                        else:
                            check_and_notify(force=False, reason="ntfy alert received")
        except Exception:
            time.sleep(10)

def periodic_wake_checker():
    """
    Checks periodically (every 5 min) AND immediately whenever the laptop
    wakes from sleep, hibernate, or the lid is opened.
    """
    last_check_time = time.time()
    last_tick = time.time()

    while True:
        try:
            time.sleep(5)
            now = time.time()
            elapsed_since_tick = now - last_tick
            last_tick = now

            # If time jumped by more than 20 seconds during a 5-second sleep,
            # the laptop was asleep / lid was closed and just opened!
            if elapsed_since_tick > 20:
                print(f"💻 [Wake Detector] Laptop lid opened / woke from sleep (slept ~{int(elapsed_since_tick)}s).")
                time.sleep(2)  # Short pause to ensure desktop is ready
                check_and_notify(force=False, reason="Laptop Lid Opened / Woke from Sleep")
                last_check_time = time.time()
            elif now - last_check_time >= 300:
                # Regular 5-minute escalation check
                check_and_notify(force=False, reason="5-Min Periodic Interval")
                last_check_time = now

        except Exception as e:
            time.sleep(10)

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
        show_gui_popup(day, title, streak, link, user_name)
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
            print("💡 (To force-test the banner preview anyway: python unstop_laptop_notifier.py --wake --force)\n")
            sys.exit(0)

        print(f"⚡ Day {day} is NOT completed yet ({status.upper()})! Firing home screen notification...")
        send_laptop_toast(
            f"⚡ Unstop Challenge Day {day} Pending!",
            f"Hey {user_name}! Day {day} is not solved yet. Keep your {streak}-day streak alive! 🚀",
            link
        )
        print("✅ Notification delivered to your laptop home screen!\n")
        sys.exit(0)
    elif "--check" in args:
        check_and_notify(force=False, reason="Command line check")
        time.sleep(10)
        sys.exit(0)

    print("🚀 [Laptop Notifier] Starting Unstop Laptop Home Screen Notifier...")
    time.sleep(3)  # Wait 3s on initial launch so Windows Explorer / Action Center is ready
    check_and_notify(force=False, reason="Laptop Open / Startup")

    t_ntfy = threading.Thread(target=ntfy_stream_listener, daemon=True)
    t_ntfy.start()

    periodic_wake_checker()
