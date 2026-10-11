"""
Unstop CLI Companion (Python) - Hardened with API Authentication
Easily check progress, mark challenges as solved, or test phone alerts from the terminal.

Usage:
  python unstop_cli.py status      - View today's status & streak
  python unstop_cli.py sync        - Fetch Unstop challenge from browser & auto-mark solved
  python unstop_cli.py solve       - Mark today's challenge as solved
  python unstop_cli.py complete    - Test celebratory completion notification (screen + phone)
  python unstop_cli.py open        - Open today's challenge directly in Google Chrome
  python unstop_cli.py first-open  - Test first laptop open of the day popup
  python unstop_cli.py alert       - Send instant test alert to phone
  python unstop_cli.py laptop      - Send test toast to laptop
  python unstop_cli.py roadmap     - View the 30-day task list
  python unstop_cli.py backup      - Trigger secure snapshot backup
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
import subprocess

IST = timezone(timedelta(hours=5, minutes=30))

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
        from unstop_sync import auto_sync_and_mark_completed
        auto_sync_and_mark_completed()
    except Exception:
        pass

    try:
        res = requests.get(f"{BASE_URL}/today", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            c = data["challenge"]
            s = data["stats"]
            u = data["user"]
            streak = s.get("streak", 0)
            best_streak = max(s.get("best_streak", 16), 16)
            is_broken = s.get("is_broken", False) or (streak == 0 and (s.get("last_broken_streak", 0) > 0 or s.get("last_broken_day") or s.get("missed", 0) > 0))
            streak_started_date = s.get("streak_started_date")
            streak_started_day = s.get("streak_started_day")
            streak_days_running = s.get("streak_days_running", streak)
            
            # Tasks remaining for 30-day streak:
            # If streak broke or is 0, resets to 30!
            # If streak is running, remaining = 30 - streak
            streak_tasks_remaining = s.get("remaining", 30 if (streak == 0 or is_broken) else max(0, 30 - streak))
            quests_remaining = max(0, 30 - s.get("solved", 0))

            print("\n" + "=" * 54)
            print(f"👤 Coder: {u['name']} (@{u['username']})")
            print(f"📧 User ID: {u['email']}")
            print(f"📅 Today: Day {c['day']} of 30")
            print(f"⚡ Status: {c['status'].upper()}")

            if streak == 0 or is_broken:
                broken_day = s.get("last_broken_day") or (c['day'] - 1 if c['day'] > 1 else 1)
                lost_str = f" (Lost {s.get('last_broken_streak', 0)}d streak)" if s.get('last_broken_streak') else ""
                print(f"🔥 Current Streak: 0 Days (RESET TO 0: Streak Broken after Day {broken_day}{lost_str})")
            else:
                started_str = f" (Started: {streak_started_date} on Day {streak_started_day} • {streak_days_running} Day{'s' if streak_days_running != 1 else ''} running)" if streak_started_date else f" ({streak_days_running} Days running)"
                print(f"🔥 Current Streak: {streak} Days{started_str}")

            print(f"🏆 Best Streak: {best_streak} Days (Preserved All-Time Record)")
            print(f"✅ Solved Quests: {s['solved']} / 30")
            print(f"💔 Missed Days: {s.get('missed', 0)}")

            if streak == 0 or is_broken:
                print(f"🎯 Tasks Remaining: {streak_tasks_remaining} (RESET TO 30: Streak Broken - Restart from Day {c['day']})")
            else:
                print(f"🎯 Tasks Remaining: {streak_tasks_remaining} for 30-Day Streak ({quests_remaining} Quests Left)")

            if s.get("streak_at_risk"):
                print("=" * 54)
                print("🚨 WARNING: YOUR STREAK IS AT RISK BEFORE MIDNIGHT!")
                print("⚡ Complete today's task now to prevent auto-resetting to 0!")
            print("=" * 54 + "\n")
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
            settings = db.get("settings", {})
            solved_count = sum(1 for c in challenges if c.get("status") == "solved")
            missed_count = sum(1 for c in challenges if c.get("status") == "missed")
            solved_days = {c.get("day") for c in challenges if c.get("status") == "solved"}
            pending = [c for c in challenges if c.get("status") in ("pending", "upcoming")]
            today_c = pending[0] if pending else (challenges[-1] if challenges else {"day": 1, "status": "solved"})
            cur_day = today_c.get("day", 1)
            start_day = cur_day if today_c.get("status") == "solved" else cur_day - 1
            streak = 0
            chain = []
            for d in range(start_day, 0, -1):
                if d in solved_days:
                    streak += 1
                    chain.append(d)
                else:
                    break
            best_streak = max(settings.get("best_streak", 16), 16, streak)
            is_broken = (streak == 0 and (settings.get("last_broken_streak", 0) > 0 or missed_count > 0 or cur_day > 1))
            streak_remaining = 30 if (streak == 0 or is_broken) else max(0, 30 - streak)
            oldest_day = chain[-1] if chain else None
            streak_started_date = None
            if oldest_day:
                for ch in challenges:
                    if ch.get("day") == oldest_day:
                        raw_d = ch.get("solved_at") or ch.get("created_at") or ""
                        streak_started_date = raw_d.split("T")[0] if "T" in raw_d else raw_d
                        break
            streak_days_running = streak

            print("\n" + "=" * 54)
            print(f"👤 Coder: Animesh Goswami (@animegos31002)")
            print(f"📅 Today: Day {cur_day} of 30")
            print(f"⚡ Status: {today_c.get('status', 'pending').upper()}")
            if streak == 0 or is_broken:
                broken_day = settings.get("last_broken_day") or (cur_day - 1 if cur_day > 1 else 1)
                print(f"🔥 Current Streak: 0 Days (RESET TO 0: Streak Broken after Day {broken_day})")
            else:
                started_str = f" (Started: {streak_started_date} on Day {oldest_day} • {streak_days_running} Days running)" if streak_started_date else ""
                print(f"🔥 Current Streak: {streak} Days{started_str}")
            print(f"🏆 Best Streak: {best_streak} Days (Preserved All-Time Record)")
            print(f"✅ Solved Quests: {solved_count} / 30")
            print(f"💔 Missed Days: {missed_count}")
            if streak == 0 or is_broken:
                print(f"🎯 Tasks Remaining: {streak_remaining} (RESET TO 30: Streak Broken)")
            else:
                print(f"🎯 Tasks Remaining: {streak_remaining} for 30-Day Streak ({30 - solved_count} Quests Left)")
            print("=" * 54 + "\n")
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
                # After completing, do not show any notification per policy
                pass
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

def send_completion_alert():
    """Manually test or trigger celebratory completion notifications for laptop screen and phone."""
    print("\n🎉 Triggering completion notification for screen and mobile...")
    try:
        res = requests.post(f"{BASE_URL}/test-complete", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            print("✅ Server delivered completion notification to phone & web push!")
    except Exception:
        pass

    try:
        from unstop_sync import send_completion_notification
        is_done, day = is_today_completed()
        title = f"Day {day} Coding Challenge"
        streak = 1
        db_f = DATA_DIR / "challenges.json"
        if not db_f.exists():
            db_f = BASE_DIR / "challenges.json"
        if db_f.exists():
            try:
                with open(db_f, "r", encoding="utf-8") as f:
                    _d = json.load(f)
                streak = sum(1 for c in _d.get("challenges", []) if c.get("status") == "solved")
            except Exception:
                pass
        send_completion_notification(day, title, streak, "https://unstop.com/practice/coding")
        print(f"🎉 Completion notification delivered to laptop screen & https://ntfy.sh/{NTFY_TOPIC}!\n")
    except Exception as ex:
        print(f"❌ Failed to trigger completion alert: {ex}\n")

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
        from unstop_laptop_notifier import send_laptop_toast
        title = f"⚡ Day {day} Unstop Challenge Alert!"
        body = f"Hey {first_name}! Day {day} Challenge is waiting! Keep your 30-day streak alive! 🚀"
        subtext = "Click to open in Unstop."
        send_laptop_toast(title, body, link, subtext=subtext, force=True)
        print(f"\n💻 Native laptop toast notification fired completely silently to your screen for Day {day}!\n")
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

def sync_unstop():
    print("\n🔍 Scanning Google Chrome & Edge history for Unstop coding practice...")
    try:
        from unstop_sync import auto_sync_and_mark_completed, fetch_unstop_activity_for_date
        act = fetch_unstop_activity_for_date()
        if act:
            print(f"✅ Found Unstop challenge activity in {act.get('browser', 'Chrome')}:")
            print(f"   📌 Problem:  {act.get('title')}")
            print(f"   🔗 URL:      {act.get('url')}")
            print(f"   ⏰ Time:     {act.get('time_iso')}")
            print(f"   ⏱️ Duration: {act.get('duration_sec', 0):.1f}s")
        else:
            print("ℹ️ No Unstop practice activity detected in browser history for today yet.")

        res = auto_sync_and_mark_completed()
        if res.get("synced"):
            print(f"\n🎉 Successfully Synced Directly From Unstop!")
            print(f"   📅 Day:    Day {res.get('day')}")
            print(f"   ⚡ Status: SOLVED (Auto-Detected)")
            print(f"   🔥 Streak: {res.get('streak')} Days")
            print(f"   🔕 Result: Popups and repeat notifications are completely suppressed for today!")
        else:
            print(f"ℹ️ Status: {res.get('reason', 'Database is up to date.')}")

        # Display Live Stats
        print("\n" + "=" * 56)
        print("🔥 LIVE UNSTOP STREAK & STATS (REAL-TIME)")
        print("=" * 56)
        print_status()

    except Exception as e:
        print(f"❌ Sync error: {e}\n")

def print_streak_details():
    try:
        res = requests.get(f"{BASE_URL}/streak-details", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            s = data["stats"]
            c = data["challenge"]
            breaks = data.get("streak_breaks", [])
            print("\n" + "=" * 56)
            print("🔥 UNSTOP 30-DAY STREAK & HEALTH DIAGNOSTICS")
            print("=" * 56)

            streak = s.get("streak", 0)
            best_streak = max(s.get("best_streak", 16), 16)
            status = s.get("streak_status", "active").upper()
            is_broken = s.get("is_broken", False) or (streak == 0 and (s.get("last_broken_streak", 0) > 0 or s.get("last_broken_day") or s.get("missed", 0) > 0))
            streak_started_date = s.get("streak_started_date")
            streak_started_day = s.get("streak_started_day")
            streak_days_running = s.get("streak_days_running", streak)
            streak_remaining = s.get("remaining", 30 if (streak == 0 or is_broken) else max(0, 30 - streak))

            if is_broken or status == "BROKEN" or streak == 0:
                print(f"💔 Streak Status:      BROKEN / RESET TO 0")
                print(f"🔥 Active Streak:      0 Days (Reset to 0)")
                print(f"🏆 Best Streak Record:  {best_streak} Days (Safely Preserved!)")
                print(f"⚡ Last Broken Streak:  {s.get('last_broken_streak', 0)} Days")
                print(f"📅 Broken At:          {s.get('last_broken_at') or 'Recent deadline'}")
                print(f"🎯 Tasks Remaining:    30 Tasks (RESET TO 30 for Streak Restart)")
                print(f"🎯 Recovery Mission:   Complete Day {c.get('day')} today to restart your streak!")
            elif status == "AT_RISK":
                print(f"🚨 Streak Status:      AT RISK BEFORE MIDNIGHT!")
                print(f"🔥 Active Streak:      {streak} Days")
                if streak_started_date:
                    print(f"📅 Streak Started:     {streak_started_date} (Day {streak_started_day}) • {streak_days_running} Days Running")
                print(f"🏆 Best Streak Record:  {best_streak} Days (Safely Preserved!)")
                print(f"🎯 Tasks Remaining:    {streak_remaining} Tasks Remaining for 30-Day Goal")
                print(f"⚡ Action Required:    Solve Day {c.get('day')} before midnight to save streak!")
            else:
                print(f"🔥 Streak Status:      ACTIVE & STRONG")
                print(f"🔥 Active Streak:      {streak} Days")
                if streak_started_date:
                    print(f"📅 Streak Started:     {streak_started_date} (Day {streak_started_day}) • {streak_days_running} Days Running")
                print(f"🏆 Best Streak Record:  {best_streak} Days (Safely Preserved!)")
                print(f"🎯 Tasks Remaining:    {streak_remaining} Tasks Remaining to Complete 30-Day Goal")

            print("-" * 56)
            print(f"✅ Total Solved:       {s.get('solved', 0)} / 30")
            print(f"💔 Total Missed Days:  {s.get('missed', 0)}")
            if s.get("missed_days"):
                print(f"📋 Missed Days List:   {s.get('missed_days')}")
            print(f"📅 Last Solved Date:   {s.get('last_solved_date') or 'None'}")
            print(f"🔄 Total Break Events:  {s.get('total_streak_breaks', len(breaks))}")

            if breaks:
                print("\n📜 Past Streak Break History:")
                for b in breaks[-3:]:
                    print(f"   • Day {b.get('day')}: Lost {b.get('streak_lost', 0)}-Day Streak on {b.get('broken_at', '')[:10]}")

            print("=" * 56 + "\n")
            return
    except Exception as e:
        print(f"⚠️ Could not fetch streak details from server: {e}")

def test_streak_break(day=None):
    try:
        url = f"{BASE_URL}/streak/test-break"
        if day:
            url += f"?day={day}"
        res = requests.post(url, headers=get_headers(), timeout=5)
        data = res.json()
        print(f"\n💔 {data.get('message')}")
        print(f"🔥 Active Streak is now: {data.get('stats', {}).get('streak', 0)} Days")
        print(f"🏆 Best Streak is preserved at: {data.get('stats', {}).get('best_streak', 0)} Days\n")
    except Exception as e:
        print(f"❌ Failed to test streak break: {e}")

def restore_streak(day=None):
    try:
        url = f"{BASE_URL}/streak/restore"
        if day:
            url += f"?day={day}"
        res = requests.post(url, headers=get_headers(), timeout=5)
        data = res.json()
        print(f"\n✅ {data.get('message')}")
        print(f"🔥 Active Streak is now: {data.get('stats', {}).get('streak', 0)} Days\n")
    except Exception as e:
        print(f"❌ Failed to restore streak: {e}")

def print_roadmap():
    try:
        res = requests.get(f"{BASE_URL}/roadmap", headers=get_headers(), timeout=4)
        if res.status_code == 200:
            data = res.json()
            roadmap = data.get("roadmap", [])
            s = data.get("stats", {})
            u = data.get("user", {})
            streak = s.get("streak", 0)
            best_streak = max(s.get("best_streak", 16), 16)
            is_broken = s.get("is_broken", False) or (streak == 0 and (s.get("last_broken_streak", 0) > 0 or s.get("last_broken_day") or s.get("missed", 0) > 0))
            streak_tasks_remaining = s.get("remaining", 30 if (streak == 0 or is_broken) else max(0, 30 - streak))

            print("\n" + "=" * 76)
            print(f"🗺️  UNSTOP 30-DAY QUEST TRACKER & STREAK AUDIT — {u.get('name', 'Coder')}")
            print("=" * 76)
            if streak == 0 or is_broken:
                broken_day = s.get("last_broken_day") or "recent day"
                print(f"🔥 Active Streak:       0 Days (RESET TO 0 after Day {broken_day})")
            else:
                started_date = s.get("streak_started_date")
                started_day = s.get("streak_started_day")
                days_run = s.get("streak_days_running", streak)
                print(f"🔥 Active Streak:       {streak} Days (Started: {started_date} on Day {started_day} • {days_run}d active)")
            print(f"🏆 Best Streak Record:  {best_streak} Days (All-Time Record Preserved)")
            print(f"✅ Solved Quests:       {s.get('solved', 0)} / 30")
            print(f"💔 Missed Days:         {s.get('missed', 0)}")
            print(f"🎯 Tasks Remaining:     {streak_tasks_remaining} for 30-Day Streak ({30 - s.get('solved', 0)} Quests Left)")
            print("-" * 76)
            print(f"{'DAY':<8} {'STATUS':<14} {'DATE':<14} {'PROBLEM / QUEST TITLE'}")
            print("-" * 76)

            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            today_c = next((item for item in roadmap if (item.get("assigned_date") == today_str or (item.get("solved_at") or "").split("T")[0] == today_str)), None)
            if not today_c:
                today_c = next((item for item in roadmap if item.get("status") == "pending"), None)
            if not today_c:
                today_c = roadmap[-1] if roadmap else {}
            cur_day = today_c.get("day", 1)
            cur_status = today_c.get("status", "pending")

            for item in roadmap:
                d = item.get("day", 0)
                st = item.get("status", "upcoming").upper()
                raw_d = item.get("solved_at") or item.get("assigned_date") or item.get("missed_at") or ""
                date_str = raw_d.split("T")[0] if "T" in raw_d else (raw_d or "-")
                title = item.get("title") or f"Day {d} Challenge"
                if len(title) > 36:
                    title = title[:33] + "..."

                if st == "SOLVED":
                    st_icon = "✅ SOLVED"
                elif st == "MISSED":
                    st_icon = "💔 MISSED"
                elif st == "PENDING":
                    st_icon = "⚡ PENDING"
                else:
                    st_icon = "⏳ UPCOMING"

                # Emphasized tags & markers
                tag = ""
                if d == cur_day:
                    tag = f" ⭐ [TODAY • {st}] 🔥" if st == "SOLVED" else " ⚡ [TODAY • PENDING]"
                elif st == "MISSED":
                    tag = " 💔 [STREAK RESET TO 0]"
                elif d == cur_day + 1 and st != "SOLVED":
                    tag = f" 🎯 [NEXT: {streak + 1}-DAY STREAK]"
                elif d == 30:
                    tag = " 🏆 [CHAMPION FINALE]"
                elif st == "SOLVED" and d >= (s.get("streak_started_day") or 999):
                    tag = " 🔥 [STREAK ACTIVE]"

                day_str = f"👉 Day {d:<2}" if d == cur_day else f"   Day {d:<2}"
                print(f"{day_str} {st_icon:<14} {date_str:<14} {title}{tag}")

            print("=" * 84)
            print("🌟 EMPHASIZED ITEMS SUMMARY:")
            cur_status_text = "SOLVED ✅ (Protected & Secured)" if cur_status == "solved" else "PENDING ⚡ (Awaiting Solve)"
            print(f"  ⭐ TODAY'S QUEST (DAY {cur_day}):      {cur_status_text}")
            started_d = s.get('streak_started_date') or today_str
            started_day_num = s.get('streak_started_day') or cur_day
            print(f"  🔥 ACTIVE STREAK RUN:          {streak} Days (Started {started_d} on Day {started_day_num})")
            if s.get("last_broken_day"):
                print(f"  💔 STREAK BREAK POINT (DAY {s.get('last_broken_day')}): MISSED (Reset to 0 Days • 30 Tasks Remaining)")
            if cur_day < 30:
                print(f"  🎯 NEXT TARGET (DAY {cur_day + 1}):        Upcoming (Solve to reach {streak + 1}-Day Streak)")
            print(f"  🏆 ALL-TIME BEST RECORD:        {best_streak} Days (Preserved permanent high)")
            print("=" * 84 + "\n")
            return
    except Exception as e:
        print(f"⚠️ Could not fetch roadmap from server: {e}")

    # Fallback to local DB
    db_file = DATA_DIR / "challenges.json"
    if not db_file.exists():
        db_file = BASE_DIR / "challenges.json"
    if db_file.exists():
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                db = json.load(f)
            challenges = db.get("challenges", [])
            settings = db.get("settings", {})
            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            today_c = next((item for item in challenges if (item.get("assigned_date") == today_str or (item.get("solved_at") or "").split("T")[0] == today_str)), None)
            if not today_c:
                today_c = next((item for item in challenges if item.get("status") == "pending"), None)
            if not today_c:
                today_c = challenges[-1] if challenges else {}
            cur_day = today_c.get("day", 1)
            cur_status = today_c.get("status", "pending")

            print("\n" + "=" * 84)
            print("🗺️  UNSTOP 30-DAY QUEST TRACKER (LOCAL DB AUDIT)")
            print("=" * 84)
            print(f"{'DAY':<11} {'STATUS':<14} {'DATE':<14} {'PROBLEM / QUEST TITLE'}")
            print("-" * 84)
            for item in sorted(challenges, key=lambda x: x.get("day", 0)):
                d = item.get("day", 0)
                st = item.get("status", "upcoming").upper()
                raw_d = item.get("solved_at") or item.get("assigned_date") or item.get("missed_at") or ""
                date_str = raw_d.split("T")[0] if "T" in raw_d else (raw_d or "-")
                title = item.get("title") or f"Day {d} Challenge"
                if len(title) > 36:
                    title = title[:33] + "..."
                st_icon = "✅ SOLVED" if st == "SOLVED" else ("💔 MISSED" if st == "MISSED" else "⏳ " + st)

                tag = ""
                if d == cur_day:
                    tag = f" ⭐ [TODAY • {st}] 🔥" if st == "SOLVED" else " ⚡ [TODAY • PENDING]"
                elif st == "MISSED":
                    tag = " 💔 [STREAK RESET TO 0]"
                elif d == cur_day + 1 and st != "SOLVED":
                    tag = " 🎯 [NEXT TARGET]"
                elif d == 30:
                    tag = " 🏆 [CHAMPION FINALE]"

                prefix = "👉 " if d == cur_day else "   "
                print(f"{prefix}Day {d:<2}  {st_icon:<14} {date_str:<14} {title}{tag}")

            print("=" * 84)
            print("🌟 EMPHASIZED ITEMS SUMMARY:")
            print(f"  ⭐ TODAY (DAY {cur_day}):      {cur_status.upper()} {'✅' if cur_status == 'solved' else '⚡'}")
            if settings.get("last_broken_day"):
                print(f"  💔 BREAK POINT (DAY {settings.get('last_broken_day')}): MISSED (Reset to 0 • Tasks remaining reset to 30)")
            print(f"  🏆 BEST RECORD:         {settings.get('best_streak', 16)} Days (Preserved all-time record)")
            print("=" * 84 + "\n")
        except Exception as ex:
            print(f"❌ Failed to load local database: {ex}")

if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print_status()
    elif cmd in ("streak", "streak-details", "streaks", "health"):
        print_streak_details()
    elif cmd in ("break-test", "test-break", "break"):
        test_day = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        test_streak_break(test_day)
    elif cmd in ("restore", "unbreak", "restore-streak"):
        test_day = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        restore_streak(test_day)
    elif cmd in ("sync", "fetch", "auto-sync"):
        sync_unstop()
    elif cmd == "solve":
        mark_solved()
    elif cmd in ("complete", "completed", "notify-complete", "test-complete", "completion-alert"):
        send_completion_alert()
    elif cmd in ("alert", "phone"):
        force_flag = "--force" in sys.argv or "-f" in sys.argv
        send_alert(force=force_flag)
    elif cmd in ("laptop", "notify", "test"):
        force_flag = "--force" in sys.argv or "-f" in sys.argv
        send_laptop_notification(force=force_flag)
    elif cmd in ("first-open", "first", "firstopen", "test-first-open"):
        force_flag = "--force" in sys.argv or "-f" in sys.argv
        notifier_script = BASE_DIR / "unstop_laptop_notifier.py"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        args = [sys.executable, str(notifier_script), "--first-open"]
        if force_flag:
            args.append("--force")
        subprocess.run(args, creationflags=creationflags, startupinfo=startupinfo)
    elif cmd in ("open", "chrome", "open-task", "solve-now"):
        from unstop_laptop_notifier import open_url_in_chrome
        try:
            res = requests.get(f"{BASE_URL}/today", headers=get_headers(), timeout=3)
            data = res.json()
            c = data.get("challenge", {})
            link = c.get("link") or data.get("user", {}).get("practiceUrl") or "https://unstop.com/practice/coding"
            day = c.get("day", 1)
        except Exception:
            link = "https://unstop.com/practice/coding"
            day = 1
        open_url_in_chrome(link)
        print(f"\n🚀 Redirected Day {day} challenge directly to Google Chrome!\n🔗 URL: {link}\n")
    elif cmd in ("popup", "test-popup", "card"):
        notifier_script = BASE_DIR / "unstop_laptop_notifier.py"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        subprocess.Popen([sys.executable, str(notifier_script), "--popup"], creationflags=creationflags, startupinfo=startupinfo)
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
        print("Unknown command. Use: status | solve | alert | laptop | first-open | popup | roadmap | server | autostart | server-stop | backup | potd <day> <title> <link> [status]")


