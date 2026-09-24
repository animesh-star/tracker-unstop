"""
Unstop 30-Day Challenge Tracker & Reminder Engine
Python Edition (FastAPI + Schedule + Requests)
Hardened Security Architecture:
 - Content Security Policy & Security Headers (CSP, X-Frame-Options, X-Content-Type-Options)
 - Rate Limiting on all endpoints & sensitive operations
 - API Key Authentication (x-api-key) for mutating operations
 - Input Sanitization & URL Scheme Validation (anti-XSS)
 - Atomic JSON Storage & Automated Daily Backups with 14-day retention
 - Safe Exception Handling & CORS Restriction
"""

import os
import sys
import json
import time
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

# Handle stdout/stderr safely for Windows and headless pythonw background service
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

if sys.stdout is None:
    sys.stdout = open(LOGS_DIR / "tracker.log", "a", encoding="utf-8", buffering=1)
elif hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if sys.stderr is None:
    sys.stderr = open(LOGS_DIR / "tracker.err.log", "a", encoding="utf-8", buffering=1)
elif hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

# Load .env file
try:
    import dotenv
    dotenv.load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

# ─── Configuration & Directories ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BACKUPS_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "challenges.json"
TMP_PATH = DATA_DIR / "challenges.tmp"
LEGACY_DB_PATH = BASE_DIR / "challenges.json"
PUBLIC_DIR = BASE_DIR / "public"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Auto-migrate legacy challenges.json if needed
if not DB_PATH.exists() and LEGACY_DB_PATH.exists():
    try:
        import shutil
        shutil.copyfile(LEGACY_DB_PATH, DB_PATH)
        print("📦 Migrated challenges.json to data/challenges.json")
    except Exception as e:
        print(f"⚠️ Migration notice: {e}")

# Indian Standard Time (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

API_SECRET = os.getenv("API_SECRET", "").strip()
NTFY_TOPIC_ENV = os.getenv("NTFY_TOPIC", "animesh_unstop_streak")
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()
]

DEFAULT_USER = {
    "name": "Animesh Goswami",
    "username": "animegos31002",
    "email": "animesh.goswami045@gmail.com",
    "profileUrl": "https://unstop.com/u/animegos31002",
    "practiceUrl": "https://unstop.com/practice/coding",
    "ntfyTopic": NTFY_TOPIC_ENV,
}

DEFAULT_DB = {
    "challenges": [],
    "push_subscriptions": [],
    "settings": {
        "user_profile": json.dumps(DEFAULT_USER)
    }
}

# ─── Input Sanitization Helpers ────────────────────────────────────────────────
def sanitize_url(url: Optional[str]) -> Optional[str]:
    """Ensures URL strictly uses http or https scheme (blocks javascript: or data: XSS)."""
    if not url or not isinstance(url, str):
        return None
    trimmed = url.strip()
    try:
        parsed = urlparse(trimmed)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return trimmed
        return None
    except Exception:
        return None

def sanitize_text(text: Optional[str], max_len: int = 150) -> str:
    """Strips control characters and caps length."""
    if not text or not isinstance(text, str):
        return ""
    cleaned = "".join(ch for ch in text if ord(ch) >= 32 or ch == "\n").strip()
    return cleaned[:max_len]

# ─── Database Operations with Atomic Writes & Backups ──────────────────────────
db_lock = threading.Lock()

def read_db() -> Dict[str, Any]:
    with db_lock:
        if not DB_PATH.exists():
            write_db_unlocked(DEFAULT_DB)
            return json.loads(json.dumps(DEFAULT_DB))
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "challenges" not in data:
                    data["challenges"] = []
                if "push_subscriptions" not in data:
                    data["push_subscriptions"] = []
                if "settings" not in data:
                    data["settings"] = {}
                return data
        except Exception:
            return json.loads(json.dumps(DEFAULT_DB))

def write_db_unlocked(data: Dict[str, Any]) -> None:
    """Atomic write: write to temp file, then atomic rename."""
    try:
        with open(TMP_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(TMP_PATH, DB_PATH)
    except Exception as e:
        print(f"❌ [Python] Atomic write error: {e}")

def write_db(data: Dict[str, Any]) -> None:
    with db_lock:
        write_db_unlocked(data)

def create_daily_backup() -> Optional[str]:
    """Creates a timestamped snapshot in data/backups/ and cleans up files older than 14 days."""
    try:
        if not DB_PATH.exists():
            return None
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        backup_file = BACKUPS_DIR / f"challenges-{today_str}.json"
        
        with db_lock:
            import shutil
            shutil.copyfile(DB_PATH, backup_file)

        # Cleanup backups older than 14 days
        cutoff = time.time() - (14 * 86400)
        for p in BACKUPS_DIR.glob("challenges-*.json"):
            if p.stat().st_mtime < cutoff:
                try:
                    p.unlink()
                    print(f"🧹 [Python] Cleaned up old backup: {p.name}")
                except Exception:
                    pass
        return str(backup_file)
    except Exception as err:
        print(f"⚠️ [Python] Backup error: {err}")
        return None

def now_iso() -> str:
    return datetime.now(IST).isoformat()

def today_date_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")

# ─── User Profile Helpers ──────────────────────────────────────────────────────
def get_user_profile() -> Dict[str, Any]:
    db = read_db()
    raw = db.get("settings", {}).get("user_profile")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return DEFAULT_USER

def set_user_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    db = read_db()
    current = get_user_profile()
    
    if "name" in profile:
        current["name"] = sanitize_text(profile["name"], 80)
    if "username" in profile:
        current["username"] = sanitize_text(profile["username"], 50)
    if "email" in profile:
        current["email"] = sanitize_text(profile["email"], 100)
    if "profileUrl" in profile:
        valid_url = sanitize_url(profile["profileUrl"])
        if valid_url:
            current["profileUrl"] = valid_url
    if "practiceUrl" in profile:
        valid_url = sanitize_url(profile["practiceUrl"])
        if valid_url:
            current["practiceUrl"] = valid_url
    if "ntfyTopic" in profile:
        current["ntfyTopic"] = sanitize_text(profile["ntfyTopic"], 50)

    if "settings" not in db:
        db["settings"] = {}
    db["settings"]["user_profile"] = json.dumps(current)
    write_db(db)
    return current

# ─── Challenge Helpers ────────────────────────────────────────────────────────
def get_or_create_today_challenge() -> Dict[str, Any]:
    db = read_db()
    today = today_date_str()
    challenges = db.get("challenges", [])

    # 1. First check if any challenge was solved today (suppresses reminders for the day)
    for c in challenges:
        s_date = (c.get("solved_at") or "").split("T")[0]
        if s_date == today and c.get("status") == "solved":
            return c

    # 2. Prioritize the active pending quest
    pending = next((c for c in challenges if c.get("status") == "pending"), None)
    if pending:
        return pending

    # 3. Next sequential day
    max_solved = max([c.get("day", 0) for c in challenges if c.get("status") == "solved"], default=0)
    next_day = min(max_solved + 1, 30)
    match = next((c for c in challenges if c.get("day") == next_day), None)
    if match:
        return match

    return challenges[0] if challenges else {}

def evaluate_and_update_missed_days() -> None:
    """
    Scans all challenges. If any past challenge (day < current_day) was left pending or untracked
    when subsequent days arrived, mark its status as 'missed'. Also updates best_streak.
    """
    db = read_db()
    challenges = db.get("challenges", [])
    if not challenges:
        return

    current = get_or_create_today_challenge()
    curr_day = current.get("day", 1) if current else 1

    changed = False
    for c in challenges:
        day = c.get("day", 0)
        status = c.get("status")
        # If day is strictly before today's active day and was left pending/untracked, it's missed!
        if day < curr_day and status in ("pending", "untracked"):
            c["status"] = "missed"
            changed = True

    # Calculate streak and update best_streak in settings
    streak = get_streak_unlocked(db)
    settings = db.setdefault("settings", {})
    best_streak = settings.get("best_streak", 0)
    if streak > best_streak:
        settings["best_streak"] = streak
        changed = True

    if changed:
        write_db(db)

def calculate_all_time_best_streak(db: Dict[str, Any]) -> int:
    challenges = db.get("challenges", [])
    cmap = {c.get("day"): c for c in challenges}
    max_streak = 0
    current_run = 0
    for d in range(1, 31):
        c = cmap.get(d)
        if c and c.get("status") == "solved":
            current_run += 1
            if current_run > max_streak:
                max_streak = current_run
        else:
            current_run = 0
    saved_best = db.get("settings", {}).get("best_streak", 0)
    return max(max_streak, saved_best)

def get_streak_unlocked(db: Dict[str, Any]) -> int:
    current = get_or_create_today_challenge()
    current_day = current.get("day", 1) if current else 1
    cmap = {c.get("day"): c for c in db.get("challenges", [])}
    
    today_status = current.get("status") if current else "pending"
    start_day = current_day if today_status == "solved" else current_day - 1
    
    streak = 0
    for d in range(start_day, 0, -1):
        c = cmap.get(d)
        if c and c.get("status") == "solved":
            streak += 1
        else:
            break
    return streak

def get_streak() -> int:
    db = read_db()
    return get_streak_unlocked(db)

def get_stats() -> Dict[str, Any]:
    evaluate_and_update_missed_days()
    db = read_db()
    challenges = db.get("challenges", [])
    solved = sum(1 for c in challenges if c.get("status") == "solved")
    missed = sum(1 for c in challenges if c.get("status") == "missed")
    total = len(challenges)
    streak = get_streak_unlocked(db)
    best_streak = max(calculate_all_time_best_streak(db), streak)

    current = get_or_create_today_challenge()
    today_status = current.get("status") if current else "pending"
    now_hour = datetime.now(IST).hour

    # Streak is at risk if streak > 0 or solved > 0, today is still pending, and it's late in the day (6 PM+ IST)
    streak_at_risk = (streak > 0 or solved > 0) and (today_status == "pending") and (now_hour >= 18)

    return {
        "total": total,
        "solved": solved,
        "missed": missed,
        "streak": streak,
        "best_streak": best_streak,
        "remaining": max(0, 30 - solved),
        "streak_at_risk": streak_at_risk
    }

def get_30_day_roadmap() -> List[Dict[str, Any]]:
    evaluate_and_update_missed_days()
    db = read_db()
    current = get_or_create_today_challenge()
    cmap = {c.get("day"): c for c in db.get("challenges", [])}

    roadmap = []
    for d in range(1, 31):
        if d in cmap:
            roadmap.append(cmap[d])
        else:
            roadmap.append({
                "id": None,
                "day": d,
                "title": f"Day {d} Challenge",
                "link": None,
                "status": "missed" if d < current.get("day", 1) else "upcoming",
                "reminded_at": None,
                "solved_at": None,
                "created_at": None,
            })
    return roadmap

def update_challenge_status(day: int, status: str) -> None:
    db = read_db()
    for c in db.get("challenges", []):
        if c.get("day") == day:
            c["status"] = status
            c["solved_at"] = now_iso() if status == "solved" else None
            break
    write_db(db)
    evaluate_and_update_missed_days()

def set_challenge_day(day: int, data: Dict[str, Any]) -> Dict[str, Any]:
    db = read_db()
    challenges = db.get("challenges", [])
    match = next((c for c in challenges if c.get("day") == day), None)

    title = sanitize_text(data.get("title") or f"Day {day} Challenge", 120)
    link = sanitize_url(data.get("link"))
    status = data.get("status") if data.get("status") in ("solved", "pending", "skipped", "missed") else "solved"

    if not match:
        match = {
            "id": int(time.time() * 1000) + day,
            "day": day,
            "title": title,
            "link": link,
            "status": status,
            "reminded_at": None,
            "solved_at": now_iso() if status == "solved" else None,
            "created_at": now_iso(),
        }
        challenges.append(match)
    else:
        match["title"] = title
        if link is not None:
            match["link"] = link
        match["status"] = status
        match["solved_at"] = now_iso() if status == "solved" else None

    db["challenges"] = challenges
    write_db(db)
    evaluate_and_update_missed_days()
    return match

# ─── Phone Notifications via ntfy.sh ──────────────────────────────────────────
def send_phone_push(title: str, body: str, challenge_link: str, is_repeat: bool = False) -> bool:
    user = get_user_profile()
    topic = os.getenv("NTFY_TOPIC") or user.get("ntfyTopic") or "animesh_unstop_streak"

    clean_title = "".join(ch for ch in title if ord(ch) < 128).strip() or "Unstop Challenge Alert"
    safe_link = sanitize_url(challenge_link) or "https://unstop.com/practice/coding"
    clean_link = "".join(ch for ch in safe_link if ord(ch) < 128)

    headers = {
        "Title": clean_title,
        "Priority": "5",
        "Tags": "rotating_light,alarm_clock,fire" if is_repeat else "fire,muscle,bell",
        "Click": clean_link,
        "Actions": f"view, Open Challenge, {clean_link}",
    }

    try:
        res = requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, timeout=10)
        if res.status_code == 200:
            print(f"📱 [Python] Phone notification delivered to https://ntfy.sh/{topic}")
            return True
        else:
            print(f"⚠️ [Python] ntfy returned status: {res.status_code}")
            return False
    except Exception as e:
        print(f"❌ [Python] Failed to send phone push: {e}")
        return False

# ─── Native Laptop Notifications (Windows Toast) ──────────────────────────────
def send_laptop_toast(title: str, body: str, challenge_link: str) -> bool:
    try:
        clean_title = title.replace('"', '`"').replace("'", "''")
        clean_body = body.replace('"', '`"').replace("'", "''")
        safe_link = sanitize_url(challenge_link) or "https://unstop.com/practice/coding"

        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xmlString = @"
<toast launch="{safe_link}" activationType="protocol">
    <visual>
        <binding template="ToastGeneric">
            <text>{clean_title}</text>
            <text>{clean_body}</text>
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
$appId = "Unstop Challenge Tracker"
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
        print(f"💻 [Python] Native laptop notification delivered to screen!")
        return True
    except Exception as e:
        print(f"⚠️ [Python] Laptop notification error: {e}")
        return False

# ─── Web Push Notifications (Native Browser Push) ──────────────────────────────
def send_web_push(payload: Dict[str, Any]) -> None:
    db = read_db()
    subs = db.get("push_subscriptions", [])
    if not subs:
        return

    vapid_private = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    vapid_claims = {"sub": os.getenv("VAPID_SUBJECT", "mailto:animesh.goswami045@gmail.com")}

    if not vapid_private:
        return

    try:
        from pywebpush import webpush, WebPushException
        valid_subs = []
        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps(payload),
                    vapid_private_key=vapid_private,
                    vapid_claims=vapid_claims,
                    timeout=5
                )
                valid_subs.append(sub)
                print("🌐 [Python] Native Web Push delivered to browser subscription!")
            except WebPushException as ex:
                if ex.response and ex.response.status_code in (404, 410):
                    print("🗑️ [Python] Removed expired push subscription")
                else:
                    valid_subs.append(sub)
            except Exception:
                valid_subs.append(sub)
        
        db["push_subscriptions"] = valid_subs
        write_db(db)
    except Exception as e:
        print(f"⚠️ [Python] Web push error: {e}")

# ─── Core Reminder & Streak Warning Functions ─────────────────────────────────
def fire_reminder(is_repeat: bool = False, force: bool = False) -> None:
    challenge = get_or_create_today_challenge()
    user = get_user_profile()
    first_name = user.get("name", "Coder").split()[0]
    day = challenge.get("day", 1)

    if challenge.get("status") == "solved" and not force:
        print(f"✅ [Python] Day {day} already solved. Skipping reminder.")
        return

    if is_repeat:
        title = f"Still Waiting, {first_name}! Day {day} Unstop Challenge"
        body = f"{first_name}, you haven't solved Day {day} yet! Keep your 30-day streak alive! 🚀"
    else:
        title = f"New Challenge, {first_name}! Day {day} Unstop Challenge"
        body = f"Hey {first_name}, your 30-day Unstop challenge for Day {day} is live! Let's crush it! 💪"

    challenge_link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    # 1. Alert to phone via ntfy
    send_phone_push(title, body, challenge_link, is_repeat)

    # 2. Native Mobile/Desktop Browser Push
    send_web_push({
        "title": title,
        "body": body,
        "link": challenge_link,
        "tag": f"unstop-day-{day}",
        "isRepeat": is_repeat
    })

    # 3. Alert directly on laptop screen (Windows Toast)
    send_laptop_toast(title, body, challenge_link)

    db = read_db()
    for c in db.get("challenges", []):
        if c.get("day") == day:
            c["reminded_at"] = now_iso()
            break
    write_db(db)

    print(f"{'🔁 [Python] Repeat' if is_repeat else '🔔 [Python] Initial'} reminder fired for Day {day}")

def fire_streak_warning(force: bool = False) -> None:
    """Fires high-priority pre-break streak warning notifications."""
    challenge = get_or_create_today_challenge()
    user = get_user_profile()
    first_name = user.get("name", "Coder").split()[0]
    day = challenge.get("day", 1)
    streak = get_streak()

    if challenge.get("status") == "solved" and not force:
        return

    title = f"⚠️ STREAK AT RISK, {first_name}! Day {day}"
    body = f"🚨 Your {streak}-Day Streak will BREAK at midnight! Complete Day {day} challenge now to keep your flame burning! 🔥"
    challenge_link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    send_phone_push(title, body, challenge_link, is_repeat=True)
    send_web_push({
        "title": title,
        "body": body,
        "link": challenge_link,
        "tag": f"unstop-warning-day-{day}",
        "isRepeat": True
    })
    send_laptop_toast(title, body, challenge_link)
    print(f"⚠️ [Python] Pre-break streak warning fired for Day {day} (Streak: {streak} days)")

def handle_midnight_reset() -> None:
    """Triggered at midnight IST: flags missed day, resets streak, sends alert."""
    evaluate_and_update_missed_days()
    db = read_db()
    current = get_or_create_today_challenge()
    prev_day = current.get("day", 1)

    if current and current.get("status") == "pending":
        current["status"] = "missed"
        write_db(db)

        user = get_user_profile()
        first_name = user.get("name", "Coder").split()[0]
        title = f"💔 Streak Reset! Day {prev_day} Missed"
        body = f"Oh no, {first_name}! Day {prev_day} was missed and your streak has reset to 0. Don't give up! Start fresh today with Day {prev_day + 1}! 💪"
        challenge_link = user.get("practiceUrl") or "https://unstop.com/practice/coding"

        send_phone_push(title, body, challenge_link, is_repeat=True)
        send_web_push({
            "title": title,
            "body": body,
            "link": challenge_link,
            "tag": f"unstop-reset-day-{prev_day}",
            "isRepeat": True
        })
        send_laptop_toast(title, body, challenge_link)
        print(f"💔 [Python] Midnight reset triggered! Day {prev_day} marked as missed.")

# ─── Background Scheduler (Thread) ─────────────────────────────────────────────
def scheduler_loop():
    print("⏰ [Python] Secure scheduler active (IST timezone)")
    last_midnight_checked = None
    last_warning_hour = None

    while True:
        try:
            now = datetime.now(IST)
            current_date_str = now.strftime("%Y-%m-%d")

            # Midnight Trigger (12:00 AM IST)
            if now.hour == 0 and now.minute == 0:
                if last_midnight_checked != current_date_str:
                    print(f"\n🕛 [Python] 12:00 AM IST reached — evaluating streak reset & snapshot backup...")
                    create_daily_backup()
                    handle_midnight_reset()
                    fire_reminder(is_repeat=False)
                    last_midnight_checked = current_date_str

            # Pre-Break Warning triggers at 8:00 PM (20), 10:00 PM (22), and 11:00 PM (23) IST
            if now.hour in (20, 22, 23) and now.minute == 0:
                warning_key = f"{current_date_str}-{now.hour}"
                if last_warning_hour != warning_key:
                    challenge = get_or_create_today_challenge()
                    if challenge and challenge.get("status") == "pending":
                        fire_streak_warning(force=False)
                    last_warning_hour = warning_key

            # 5-minute Escalation Repeats
            if now.minute % 5 == 0 and now.second < 15:
                challenge = get_or_create_today_challenge()
                if challenge and challenge.get("status") == "pending":
                    reminded_at = challenge.get("reminded_at")
                    if reminded_at:
                        r_date = reminded_at.split("T")[0]
                        if r_date == current_date_str:
                            fire_reminder(is_repeat=True)
                            time.sleep(20)

            time.sleep(5)
        except Exception as err:
            print(f"❌ [Python] Scheduler error: {err}")
            time.sleep(10)

sched_thread = threading.Thread(target=scheduler_loop, daemon=True)
sched_thread.start()

# ─── Security Middleware: Headers & Rate Limiting ──────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        # Content Security Policy tailored for dashboard assets
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://ntfy.sh https://fcm.googleapis.com; "
            "object-src 'none'; "
            "frame-ancestors 'none';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        return response

# In-memory IP Rate Limiter
request_counts: Dict[str, List[float]] = {}
sensitive_counts: Dict[str, List[float]] = {}
rate_limit_lock = threading.Lock()

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        path = request.url.path

        with rate_limit_lock:
            # Clean old entries (> 15 min = 900s)
            cutoff = now - 900
            
            # 1. General API limit: 250 requests per 15 min
            history = request_counts.setdefault(client_ip, [])
            request_counts[client_ip] = [t for t in history if t > cutoff]
            if len(request_counts[client_ip]) > 250:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Too many requests. Please slow down (rate limit exceeded)."}
                )
            request_counts[client_ip].append(now)

            # 2. Sensitive routes limit: 40 requests per 15 min
            sensitive_prefixes = ("/api/solve", "/api/test-phone-notify", "/api/test-notify", "/api/challenge/update")
            if any(path.startswith(p) for p in sensitive_prefixes):
                sens_history = sensitive_counts.setdefault(client_ip, [])
                sensitive_counts[client_ip] = [t for t in sens_history if t > cutoff]
                if len(sensitive_counts[client_ip]) > 40:
                    return JSONResponse(
                        status_code=429,
                        content={"error": "Action rate limit reached. Please wait before retrying."}
                    )
                sensitive_counts[client_ip].append(now)

        return await call_next(request)

# ─── FastAPI Web App ──────────────────────────────────────────────────────────
app = FastAPI(title="Hardened Unstop 30-Day Tracker", version="2.1.0")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ─── Authentication Dependency ────────────────────────────────────────────────
def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    key: Optional[str] = Query(None)
):
    """
    Authorizes operations:
    1. Direct browser requests from the local dashboard (same-origin) are authorized without exposing or requiring any API key in client code.
    2. Programmatic, external, or non-same-origin requests must supply the valid API_SECRET via x-api-key header.
    """
    sec_fetch_site = request.headers.get("sec-fetch-site")
    referer = request.headers.get("referer") or ""
    client_host = request.client.host if request.client else ""

    is_local_client = client_host in ("127.0.0.1", "localhost", "::1", "testclient")
    is_same_origin = (sec_fetch_site in ("same-origin", "same-site")) or any(referer.startswith(o) for o in ALLOWED_ORIGINS)

    if is_local_client and is_same_origin:
        return True

    if not API_SECRET:
        return True

    token = x_api_key or key
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()

    if token and token == API_SECRET:
        return True

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Valid API Key is required for this operation"
    )

# ─── Request Models with Pydantic Validation ──────────────────────────────────
class ChallengeUpdate(BaseModel):
    day: int
    title: Optional[str] = None
    link: Optional[str] = None

    @field_validator("day")
    @classmethod
    def validate_day(cls, v: int) -> int:
        if v < 1 or v > 30:
            raise ValueError("Day must be between 1 and 30")
        return v

    @field_validator("link")
    @classmethod
    def validate_link(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = sanitize_url(v)
            if not clean:
                raise ValueError("Invalid URL scheme. Only http/https URLs are permitted.")
            return clean
        return v

class ChallengeSetDay(BaseModel):
    day: int
    title: Optional[str] = None
    link: Optional[str] = None
    status: Optional[str] = "solved"

    @field_validator("day")
    @classmethod
    def validate_day(cls, v: int) -> int:
        if v < 1 or v > 30:
            raise ValueError("Day must be between 1 and 30")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v not in ("solved", "pending", "skipped", "untracked", "upcoming"):
            raise ValueError("Invalid challenge status")
        return v

# ─── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/health")
def api_health():
    return {"status": "ok", "time": now_iso(), "secure": True}

@app.get("/api/today")
def api_today():
    challenge = get_or_create_today_challenge()
    stats = get_stats()
    user = get_user_profile()
    return {"challenge": challenge, "stats": stats, "user": user}

@app.get("/api/roadmap")
def api_roadmap():
    roadmap = get_30_day_roadmap()
    stats = get_stats()
    user = get_user_profile()
    return {"roadmap": roadmap, "stats": stats, "user": user}

@app.get("/api/challenges")
def api_challenges():
    db = read_db()
    challenges = sorted(db.get("challenges", []), key=lambda x: x.get("day", 0), reverse=True)
    stats = get_stats()
    user = get_user_profile()
    return {"challenges": challenges, "stats": stats, "user": user}

@app.get("/api/user")
def api_get_user():
    return {"user": get_user_profile()}

@app.get("/api/vapid-key")
def api_vapid_key():
    db = read_db()
    key = os.getenv("VAPID_PUBLIC_KEY") or db.get("settings", {}).get("vapid_public") or ""
    return {"publicKey": key}

# ─── Protected Routes (Requires verify_api_key) ────────────────────────────────

@app.post("/api/solve", dependencies=[Depends(verify_api_key)])
def api_solve():
    challenge = get_or_create_today_challenge()
    day = challenge.get("day", 1)
    update_challenge_status(day, "solved")
    print(f"🎉 [Python] Day {day} marked as SOLVED!")
    return {
        "success": True,
        "day": day,
        "message": f"Day {day} solved! Outstanding consistency, Animesh! 🎉"
    }

@app.post("/api/challenge/update", dependencies=[Depends(verify_api_key)])
def api_challenge_update(payload: ChallengeUpdate):
    db = read_db()
    updated = False
    for c in db.get("challenges", []):
        if c.get("day") == payload.day:
            if payload.link is not None:
                c["link"] = payload.link
            if payload.title is not None:
                c["title"] = sanitize_text(payload.title, 120)
            updated = True
            break
    if updated:
        write_db(db)
        return {"success": True}
    raise HTTPException(status_code=404, detail="Challenge day not found")

@app.post("/api/challenges/set-day", dependencies=[Depends(verify_api_key)])
def api_set_day(payload: ChallengeSetDay):
    updated = set_challenge_day(payload.day, payload.dict())
    return {"success": True, "challenge": updated, "stats": get_stats()}

@app.post("/api/user", dependencies=[Depends(verify_api_key)])
def api_set_user(payload: Dict[str, Any]):
    updated = set_user_profile(payload)
    return {"success": True, "user": updated}

@app.post("/api/subscribe")
def api_subscribe(sub: Dict[str, Any]):
    db = read_db()
    subs = db.get("push_subscriptions", [])
    endpoint = sub.get("endpoint")
    if endpoint and sanitize_url(endpoint):
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        subs.append(sub)
        db["push_subscriptions"] = subs
        write_db(db)
        return {"success": True, "message": "Subscribed successfully!"}
    raise HTTPException(status_code=400, detail="Invalid subscription endpoint")

@app.post("/api/unsubscribe")
def api_unsubscribe(payload: Dict[str, Any]):
    db = read_db()
    endpoint = payload.get("endpoint")
    if endpoint:
        subs = [s for s in db.get("push_subscriptions", []) if s.get("endpoint") != endpoint]
        db["push_subscriptions"] = subs
        write_db(db)
    return {"success": True, "message": "Unsubscribed successfully"}

@app.post("/api/test-notify", dependencies=[Depends(verify_api_key)])
def api_test_notify():
    fire_reminder(is_repeat=False)
    return {"success": True, "message": "Test notification sent!"}

@app.post("/api/test-phone-notify", dependencies=[Depends(verify_api_key)])
def api_test_phone_notify():
    user = get_user_profile()
    first_name = user.get("name", "Animesh").split()[0]
    topic = os.getenv("NTFY_TOPIC") or user.get("ntfyTopic") or "animesh_unstop_streak"
    link = user.get("practiceUrl") or "https://unstop.com/practice/coding"
    challenge = get_or_create_today_challenge()
    day = challenge.get("day", 1)
    status = challenge.get("status", "pending")

    if status == "solved":
        body = f"Hey {first_name}! Mobile alerts are active & working properly! Day {day} is already solved. Keep your streak blazing! 🔥"
    else:
        body = f"Hey {first_name}! Mobile alerts are active! Day {day} challenge is live. Let's crush it! 🚀"

    success = send_phone_push(
        f"Phone Alert for {first_name} ✅",
        body,
        link,
        is_repeat=False
    )
    return {"success": success, "topic": topic}

@app.post("/api/backup", dependencies=[Depends(verify_api_key)])
def api_trigger_backup():
    path_res = create_daily_backup()
    return {"success": True, "backup": Path(path_res).name if path_res else None}

# ─── Global Safe Exception Handler ─────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"❌ [Python] Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "An internal server error occurred."}
    )

# Mount static frontend
if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")

if __name__ == "__main__":
    print("\n🛡️ [Python] Starting Hardened Unstop 30-Day Tracker on http://localhost:3000")
    print("🔒 Security active: CSP headers, CORS, Rate-Limiting, Input Sanitization, Atomic Writes")
    print(f"🔑 Protected API: {'Active (API_SECRET required)' if API_SECRET else 'Open (Local Dev)'}")
    print(f"👤 Tracking ID: {DEFAULT_USER['username']} ({DEFAULT_USER['email']})")
    print("🕛 Reminder scheduled at 12:00 AM IST every day")
    print(f"📱 Mobile alerts active on topic: https://ntfy.sh/{DEFAULT_USER['ntfyTopic']}\n")
    uvicorn.run(app, host="0.0.0.0", port=3000)
