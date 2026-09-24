"""
Unstop CLI Companion (Python) - Hardened with API Authentication
Easily check progress, mark challenges as solved, or test phone alerts from the terminal.

Usage:
  python unstop_cli.py status      - View today's status & streak
  python unstop_cli.py solve       - Mark today's challenge as solved
  python unstop_cli.py alert       - Send instant test alert to phone
  python unstop_cli.py roadmap     - View the 30-day task list
  python unstop_cli.py backup      - Trigger secure snapshot backup
"""

import os
import sys
import json
from pathlib import Path
import requests
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"

# Load API_SECRET & NTFY_TOPIC from .env if present
API_SECRET = os.getenv("API_SECRET", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "animesh_unstop_streak")

if ENV_FILE.exists():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("API_SECRET=") and not API_SECRET:
                    API_SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("NTFY_TOPIC=") and NTFY_TOPIC == "animesh_unstop_streak":
                    NTFY_TOPIC = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

BASE_URL = "http://localhost:3000/api"

def get_headers():
    headers = {"Content-Type": "application/json"}
    if API_SECRET:
        headers["x-api-key"] = API_SECRET
    return headers

def print_status():
    try:
        res = requests.get(f"{BASE_URL}/today", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            c = data["challenge"]
            s = data["stats"]
            u = data["user"]
            print("\n" + "=" * 52)
            print(f"👤 Coder: {u['name']} (@{u['username']})")
            print(f"📧 User ID: {u['email']}")
            print(f"📅 Today: Day {c['day']} of 30")
            print(f"⚡ Status: {c['status'].upper()}")
            print(f"🔥 Current Streak: {s['streak']} Days")
            print(f"🏆 Best Streak: {s.get('best_streak', s['streak'])} Days")
            print(f"✅ Solved Quests: {s['solved']} / 30")
            print(f"💔 Missed Days: {s.get('missed', 0)}")
            print(f"🎯 Tasks Remaining: {s['remaining']}")
            if s.get("streak_at_risk"):
                print("=" * 52)
                print("🚨 WARNING: YOUR STREAK IS AT RISK BEFORE MIDNIGHT!")
                print("⚡ Complete today's task now to prevent auto-resetting to 0!")
            print("=" * 52 + "\n")
            return
        else:
            print(f"❌ Server returned error {res.status_code}: {res.text}")
            return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline.")
        print("💡 Showing stats from local database (start server with 'python unstop_tracker.py'):")
    except Exception as e:
        print(f"⚠️ Could not connect to tracker server: {e}")

    # Offline fallback reading local file
    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
                challenges = db.get("challenges", [])
                solved_count = sum(1 for c in challenges if c.get("status") == "solved")
                missed_count = sum(1 for c in challenges if c.get("status") == "missed")
                pending = [c for c in challenges if c.get("status") in ("pending", "upcoming")]
                today_c = pending[0] if pending else (challenges[-1] if challenges else {"day": 1, "status": "solved"})
                print("\n" + "=" * 52)
                print(f"👤 Coder: Animesh Goswami (@animegos31002)")
                print(f"📅 Today: Day {today_c.get('day', 1)} of 30")
                print(f"⚡ Status: {today_c.get('status', 'pending').upper()}")
                print(f"✅ Tasks Done: {solved_count} / 30")
                print(f"💔 Missed Days: {missed_count}")
                print(f"🎯 Tasks Remaining: {30 - solved_count}")
                print("=" * 52 + "\n")
        except Exception as ex:
            print(f"❌ Failed to load local database: {ex}")

def mark_solved():
    try:
        res = requests.post(f"{BASE_URL}/solve", headers=get_headers(), timeout=5)
        data = res.json()
        if res.status_code == 200 and data.get("success"):
            print(f"\n🎉 {data.get('message')}\n")
            return
        else:
            print(f"❌ Error ({res.status_code}): {data.get('error') or data.get('detail')}")
            return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline.")
        print("⚡ Marking today's challenge as solved directly in local database...")
    except Exception as e:
        print(f"❌ Could not connect to server: {e}")

    # Offline fallback update
    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            updated = False
            for c in challenges:
                if c.get("status") in ("pending", "upcoming"):
                    c["status"] = "solved"
                    from datetime import datetime, timezone, timedelta
                    IST = timezone(timedelta(hours=5, minutes=30))
                    c["solved_at"] = datetime.now(IST).isoformat()
                    updated = True
                    print(f"\n🎉 Day {c['day']} marked as SOLVED in local database!\n")
                    break
            if updated:
                with open(db_file, "w", encoding="utf-8") as f:
                    json.dump(db, f, indent=2)
            else:
                print("\nℹ️ All current challenges are already marked as solved.\n")
        except Exception as ex:
            print(f"❌ Failed to update local database: {ex}")

def is_today_completed():
    try:
        res = requests.get(f"{BASE_URL}/today", headers=get_headers(), timeout=3)
        if res.status_code == 200:
            data = res.json()
            ch = data.get("challenge") or {}
            if ch.get("status") == "solved":
                return True, ch.get("day", 1)
            else:
                return False, ch.get("day", 1)
    except Exception:
        pass

    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            from datetime import datetime, timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            today = datetime.now(IST).strftime("%Y-%m-%d")

            # 1. Check if any challenge was marked solved today
            for c in challenges:
                s_date = (c.get("solved_at") or "").split("T")[0]
                if s_date == today and c.get("status") == "solved":
                    return True, c.get("day", 1)

            # 2. Check active pending challenge
            pending = next((c for c in challenges if c.get("status") == "pending"), None)
            if pending:
                return False, pending.get("day", 1)

            # 3. Next sequential day
            max_solved = max([c.get("day", 0) for c in challenges if c.get("status") == "solved"], default=0)
            next_day = min(max_solved + 1, 30)
            match = next((c for c in challenges if c.get("day") == next_day), None)
            if match:
                return match.get("status") == "solved", match.get("day", next_day)
        except Exception:
            pass
    return False, 1

def send_alert(force: bool = False):
    solved, day = is_today_completed()
    if solved:
        print(f"\n✅ Day {day} is already completed & marked as SOLVED!")
        print("📲 Delivering test alert to phone to verify notification feature...")
    else:
        print(f"\n⚡ Day {day} is pending. Delivering reminder alert to your phone...")

    try:
        res = requests.post(f"{BASE_URL}/test-phone-notify", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                print(f"📱 Alert sent via server to phone topic: https://ntfy.sh/{data.get('topic')}\n")
                return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline.")
        print("⚡ Sending mobile push notification directly via ntfy.sh...")
    except Exception as e:
        print(f"⚠️ Could not reach server: {e}. Falling back to direct push...")

    # Direct push notification fallback
    topic = NTFY_TOPIC or "animesh_unstop_streak"
    clean_title = "Unstop Mobile Alert Check"
    if solved:
        body = f"Hey Animesh! Mobile alerts are verified and working properly! Day {day} is already solved. Keep your streak blazing! 🔥"
    else:
        body = f"Hey Animesh! Day {day} challenge is waiting! Keep your 30-day coding streak alive! 🚀"
    safe_link = "https://unstop.com/practice/coding"

    headers = {
        "Title": clean_title,
        "Priority": "5",
        "Tags": "fire,muscle,bell",
        "Click": safe_link,
        "Actions": f"view, Open Challenge, {safe_link}",
    }

    try:
        res = requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, timeout=10)
        if res.status_code == 200:
            print(f"📱 Direct alert delivered to mobile topic: https://ntfy.sh/{topic}\n")
        else:
            print(f"❌ ntfy returned status: {res.status_code}\n")
    except Exception as e:
        print(f"❌ Failed to send direct push: {e}\n")

def print_roadmap():
    try:
        res = requests.get(f"{BASE_URL}/roadmap", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            roadmap = res.json().get("roadmap", [])
            print("\n" + "=" * 50)
            print("📋 30-Day Task Roadmap:")
            print("=" * 50)
            for item in roadmap:
                status_icon = "✅" if item["status"] == "solved" else ("⏳" if item["status"] == "pending" else "⚪")
                print(f"Day {item['day']:02d}: {status_icon} {item['status'].capitalize()} - {item['title']}")
            print("=" * 50 + "\n")
            return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline.")
        print("💡 Showing roadmap from local database:")
    except Exception as e:
        print(f"⚠️ Could not connect to tracker server: {e}")

    # Local file fallback
    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            roadmap = db.get("challenges", [])
            print("\n" + "=" * 50)
            print("📋 30-Day Task Roadmap:")
            print("=" * 50)
            for item in roadmap:
                status_icon = "✅" if item.get("status") == "solved" else ("⏳" if item.get("status") == "pending" else "⚪")
                print(f"Day {item.get('day', 0):02d}: {status_icon} {item.get('status', 'upcoming').capitalize()} - {item.get('title')}")
            print("=" * 50 + "\n")
        except Exception as ex:
            print(f"❌ Failed to load roadmap: {ex}")

def trigger_backup():
    try:
        res = requests.post(f"{BASE_URL}/backup", headers=get_headers(), timeout=5)
        data = res.json()
        if res.status_code == 200 and data.get("success"):
            print(f"\n🛡️ Snapshot backup created: data/backups/{data.get('backup')}\n")
            return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline.")
        print("⚡ Creating snapshot backup directly...")
    except Exception as e:
        print(f"❌ Could not connect to server: {e}")

    # Offline backup creation
    try:
        db_file = DATA_DIR / "challenges.json"
        if not db_file.exists():
            db_file = BASE_DIR / "challenges.json"
        if db_file.exists():
            import shutil
            from datetime import datetime, timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
            backup_file = (DATA_DIR / "backups") / f"challenges_backup_{ts}.json"
            (DATA_DIR / "backups").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(db_file, backup_file)
            print(f"\n🛡️ Snapshot backup created: {backup_file.name}\n")
    except Exception as ex:
        print(f"❌ Backup creation failed: {ex}")

def send_laptop_notification(force: bool = False):
    solved, cur_day = is_today_completed()
    if solved and not force:
        print(f"\n✅ Day {cur_day} is already completed & marked as SOLVED!")
        print("🔕 Laptop notification will not pop up.")
        print("💡 (To force test anyway, run: python unstop_cli.py laptop --force)\n")
        return

    day = cur_day
    first_name = "Animesh"
    link = "https://unstop.com/practice/coding"

    try:
        res = requests.get(f"{BASE_URL}/today", headers=get_headers(), timeout=3)
        if res.status_code == 200:
            data = res.json()
            c = data.get("challenge") or {}
            u = data.get("user") or {}
            first_name = u.get("name", "Animesh").split()[0]
            day = c.get("day", 1)
            link = c.get("link") or u.get("practiceUrl") or link
    except Exception:
        db_file = DATA_DIR / "challenges.json"
        if not db_file.exists():
            db_file = BASE_DIR / "challenges.json"
        if db_file.exists():
            try:
                with open(db_file, "r", encoding="utf-8") as f:
                    db = json.load(f)
                pending = [c for c in db.get("challenges", []) if c.get("status") in ("pending", "upcoming")]
                if pending:
                    day = pending[0].get("day", 1)
                    link = pending[0].get("link") or link
            except Exception:
                pass

    try:
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xmlString = @"
<toast launch="{link}" activationType="protocol">
    <visual>
        <binding template="ToastGeneric">
            <text>⚡ Unstop 30-Day Challenge Alert!</text>
            <text>Hey {first_name}! Day {day} Challenge is waiting! Keep your 30-day streak alive! 🚀</text>
            <text>Click to open your daily challenge in Unstop.</text>
        </binding>
    </visual>
    <actions>
        <action content="Open Challenge" arguments="{link}" activationType="protocol" />
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
        print(f"\n💻 Native laptop toast notification fired quietly to your screen for Day {day}!\n")
    except Exception as e:
        print(f"❌ Failed to trigger laptop notification: {e}")

def update_potd(day, title, link, status="solved"):
    try:
        payload = {"day": day, "title": title, "link": link, "status": status}
        res = requests.post(f"{BASE_URL}/challenges/set-day", json=payload, headers=get_headers(), timeout=5)
        data = res.json()
        if res.status_code == 200 and data.get("success"):
            print(f"\n🔄 Day {day} updated successfully!")
            print(f"📌 Title: {title}")
            print(f"🔗 Link: {link}")
            print(f"⚡ Status: {status.upper()}\n")
            return
    except requests.exceptions.ConnectionError:
        print("⚠️ Tracker server (http://localhost:3000) is offline. Updating local database...")
    except Exception as e:
        print(f"❌ Could not connect to server: {e}")

    # Offline POTD update fallback
    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            match = None
            for c in challenges:
                if c.get("day") == day:
                    c["title"] = title
                    c["link"] = link
                    c["status"] = status
                    match = c
                    break
            if not match:
                # pyrefly: ignore [unknown-name]
                match = {"id": int(time.time()*1000), "day": day, "title": title, "link": link, "status": status}
                challenges.append(match)
                db["challenges"] = challenges
            with open(db_file, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=2)
            print(f"\n🔄 Day {day} updated in local database!")
            print(f"📌 Title: {title}")
            print(f"🔗 Link: {link}")
            print(f"⚡ Status: {status.upper()}\n")
        except Exception as ex:
            print(f"❌ Local database update failed: {ex}")

if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print_status()
    elif cmd == "solve":
        mark_solved()
    elif cmd in ("alert", "phone"):
        force_flag = "--force" in sys.argv or "-f" in sys.argv
        send_alert(force=force_flag)
    elif cmd in ("laptop", "notify", "test"):
        force_flag = "--force" in sys.argv or "-f" in sys.argv
        send_laptop_notification(force=force_flag)
    elif cmd in ("popup", "test-popup", "card"):
        notifier_script = BASE_DIR / "unstop_laptop_notifier.py"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen([sys.executable, str(notifier_script), "--popup"])
        print("\n✨ Home screen interactive popup card launched to your screen!\n")
    elif cmd == "roadmap":
        print_roadmap()
    elif cmd == "backup":
        trigger_backup()
    elif cmd in ("server", "server-start", "start-server", "start"):
        import unstop_service
        unstop_service.start_services()
    elif cmd in ("server-stop", "stop-server", "stop"):
        import unstop_service
        unstop_service.stop_services()
    elif cmd in ("server-status", "service-status", "check-server"):
        import unstop_service
        unstop_service.check_status()
    elif cmd in ("autostart", "install-autostart", "enable-autostart"):
        import unstop_service
        unstop_service.install_autostart()
        unstop_service.start_services()
    elif cmd in ("disable-autostart", "uninstall-autostart"):
        import unstop_service
        unstop_service.uninstall_autostart()
    else:
        print("Unknown command. Use: status | solve | alert | laptop | roadmap | server | autostart | server-stop | backup | potd <day> <title> <link> [status]")

