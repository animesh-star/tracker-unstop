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
from typing import Optional, Dict, Any, List, Tuple
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

try:
    from unstop_sync import auto_sync_and_mark_completed, fetch_unstop_activity_for_date
except ImportError:
    auto_sync_and_mark_completed = None
    fetch_unstop_activity_for_date = None

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
    """Atomic write with retry and fallback for Windows/OneDrive file locking."""
    written = False
    for attempt in range(5):
        try:
            with open(TMP_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(TMP_PATH, DB_PATH)
            written = True
            break
        except Exception:
            if attempt < 4:
                time.sleep(0.05 * (attempt + 1))
    if not written:
        try:
            with open(DB_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ [Python] Atomic write error: {e}")

    if LEGACY_DB_PATH.exists() and LEGACY_DB_PATH != DB_PATH:
        try:
            with open(LEGACY_DB_PATH, "w", encoding="utf-8") as rf:
                json.dump(data, rf, indent=2)
        except Exception:
            pass

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
# ─── Challenge Helpers ────────────────────────────────────────────────────────
def get_or_create_today_challenge() -> Dict[str, Any]:
    db = read_db()
    today = today_date_str()
    challenges = db.get("challenges", [])
    if not challenges:
        return {}

    # 1. First check if any challenge was solved today (suppresses reminders for the day)
    for c in challenges:
        s_date = (c.get("solved_at") or "").split("T")[0]
        a_date = c.get("assigned_date") or ""
        if (s_date == today or a_date == today) and c.get("status") == "solved":
            return c

    # 2. Check if there is an active challenge assigned for today
    for c in challenges:
        if c.get("assigned_date") == today and c.get("status") in ("pending", "upcoming"):
            if c.get("status") != "pending":
                c["status"] = "pending"
                write_db(db)
            return c

    # 3. Check for any existing pending challenge
    pending = next((c for c in challenges if c.get("status") == "pending"), None)
    if pending:
        if not pending.get("assigned_date"):
            pending["assigned_date"] = today
            write_db(db)
        return pending

    # 4. Next sequential day after highest solved or missed challenge
    max_day = 0
    for c in challenges:
        if c.get("status") in ("solved", "missed"):
            if c.get("day", 0) > max_day:
                max_day = c.get("day", 0)

    next_day = min(max_day + 1, 30)
    match = next((c for c in challenges if c.get("day") == next_day), None)
    if match:
        if match.get("status") != "solved":
            match["status"] = "pending"
            match["assigned_date"] = today
            write_db(db)
        return match

    return challenges[0] if challenges else {}

def evaluate_and_update_missed_days() -> None:
    """
    Carefully tracks deadlines, missed days, and streak breaks:
    - Identifies challenges whose deadline has expired (assigned_date < today and not solved).
    - If streak breaks, captures the lost streak, logs a Streak Break event in history,
      updates streak break stats, preserves all-time best streak, and alerts the user.
    """
    db = read_db()
    challenges = db.get("challenges", [])
    if not challenges:
        return

    today = today_date_str()
    settings = db.setdefault("settings", {})
    changed = False

    # Check active streak right now before processing any missed days
    active_streak = get_streak_unlocked(db)

    # Scan for any challenge whose assigned_date is before today and is still pending/untracked
    for c in challenges:
        status = c.get("status")
        assigned_date = c.get("assigned_date")
        day = c.get("day", 0)

        is_expired = False
        if status in ("pending", "untracked") and assigned_date and assigned_date < today:
            is_expired = True

        if is_expired:
            c["status"] = "missed"
            c["missed_at"] = datetime.now(IST).isoformat()
            changed = True

            # If an active streak was alive, record the break event!
            if active_streak > 0:
                breaks = settings.setdefault("streak_breaks", [])
                break_event = {
                    "id": len(breaks) + 1,
                    "day": day,
                    "streak_lost": active_streak,
                    "broken_at": datetime.now(IST).isoformat(),
                    "missed_date": assigned_date,
                    "reason": f"Day {day} was not completed before midnight on {assigned_date}"
                }
                breaks.append(break_event)
                settings["last_broken_streak"] = active_streak
                settings["last_broken_at"] = break_event["broken_at"]
                settings["last_broken_day"] = day
                settings["total_streak_breaks"] = settings.get("total_streak_breaks", 0) + 1
                settings["streak_status"] = "broken"

                # Keep best streak frozen at its all-time peak (never loses record)
                if active_streak > settings.get("best_streak", 0):
                    settings["best_streak"] = active_streak

                print(f"💔 [Streak Tracker] Streak of {active_streak} Days BROKEN on Day {day}! Saved to break history.")

                # Deliver quiet, single notification to laptop and phone
                user = get_user_profile()
                first_name = user.get("name", "Coder").split()[0]
                next_day = day + 1 if day < 30 else 30
                remaining_after = max(1, 30 - next_day + 1)
                best_rec = max(settings.get("best_streak", 16), 16)
                break_title = f"💔 Streak Broken! Reset to 0 (30 Tasks Remaining)"
                break_body = f"Oh no, {first_name}! Day {day} was missed and your {active_streak}-day streak broke. Current streak is now 0 Days. Tasks remaining reset back to 30. Don't give up! Complete Day {next_day} today! 💪"
                break_subtext = f"📍 Streak: 0 Days (RESET TO 30 Tasks Remaining) • Best: {best_rec}d"
                challenge_link = user.get("practiceUrl") or "https://unstop.com/practice/coding"

                try:
                    send_phone_push(break_title, break_body, challenge_link, is_repeat=True)
                except Exception:
                    pass
                try:
                    send_laptop_toast(break_title, break_body, challenge_link, subtext=break_subtext)
                except Exception:
                    pass

                # Reset active streak for subsequent checks
                active_streak = 0

    # Ensure best_streak is always up to date and never decreases
    all_time_best = calculate_all_time_best_streak(db)
    if all_time_best > settings.get("best_streak", 0):
        settings["best_streak"] = all_time_best
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
    saved_best = db.get("settings", {}).get("best_streak", 16)
    return max(max_streak, saved_best, 16)

def get_streak_chain_details(db: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes detailed information about the currently active streak run:
    - streak: current consecutive days (0 if broken or not solved)
    - streak_started_day: day number where this current streak chain started
    - streak_started_date: ISO date (YYYY-MM-DD) when this streak chain started
    - streak_days_running: how many days this streak has been started and active
    - remaining: tasks remaining to reach the 30-day streak target (resets to 30 if streak breaks!)
    """
    challenges = db.get("challenges", [])
    if not challenges:
        return {
            "streak": 0,
            "streak_started_day": None,
            "streak_started_date": None,
            "streak_days_running": 0,
            "remaining": 30
        }

    cmap = {c.get("day"): c for c in challenges}
    today = today_date_str()
    # Determine today's challenge directly from passed-in db
    today_c = None
    for c in challenges:
        s_date = (c.get("solved_at") or "").split("T")[0]
        a_date = c.get("assigned_date") or ""
        if (s_date == today or a_date == today) and c.get("status") == "solved":
            today_c = c
            break

    if not today_c:
        for c in challenges:
            if c.get("assigned_date") == today and c.get("status") in ("pending", "upcoming"):
                today_c = c
                break

    if not today_c:
        today_c = next((c for c in challenges if c.get("status") == "pending"), None)

    if not today_c:
        max_solved = max([c.get("day", 0) for c in challenges if c.get("status") == "solved"], default=0)
        today_c = cmap.get(min(max_solved + 1, 30))

    if not today_c and challenges:
        today_c = challenges[0]

    today_day = today_c.get("day", 1) if today_c else 1
    today_status = today_c.get("status", "pending") if today_c else "pending"

    # Start checking from today if solved, or yesterday if today is pending
    start_day = today_day if today_status == "solved" else today_day - 1

    streak = 0
    chain = []
    for d in range(start_day, 0, -1):
        c = cmap.get(d)
        if not c or c.get("status") != "solved":
            break
        streak += 1
        chain.append(c)

    if streak > 0 and chain:
        oldest = chain[-1]
        streak_started_day = oldest.get("day")
        raw_date = oldest.get("solved_at") or oldest.get("created_at") or ""
        streak_started_date = raw_date.split("T")[0] if "T" in raw_date else (raw_date or now_iso().split("T")[0])
        streak_days_running = streak
        remaining = max(0, 30 - streak)
    else:
        streak = 0
        streak_started_day = None
        streak_started_date = None
        streak_days_running = 0
        remaining = 30  # Resets back to 30 when streak is broken!

    return {
        "streak": streak,
        "streak_started_day": streak_started_day,
        "streak_started_date": streak_started_date,
        "streak_days_running": streak_days_running,
        "remaining": remaining
    }

def get_streak_unlocked(db: Dict[str, Any]) -> int:
    return get_streak_chain_details(db)["streak"]

def get_streak() -> int:
    db = read_db()
    return get_streak_unlocked(db)

def get_stats() -> Dict[str, Any]:
    evaluate_and_update_missed_days()
    db = read_db()
    challenges = db.get("challenges", [])
    settings = db.get("settings", {})
    solved = sum(1 for c in challenges if c.get("status") == "solved")
    missed = sum(1 for c in challenges if c.get("status") == "missed")
    total = len(challenges)

    streak_info = get_streak_chain_details(db)
    streak = streak_info["streak"]
    remaining = streak_info["remaining"] # Resets to 30 when streak breaks, else 30 - streak
    best_streak = max(calculate_all_time_best_streak(db), settings.get("best_streak", 16), streak, 16)

    current = get_or_create_today_challenge()
    today_status = current.get("status") if current else "pending"
    now_hour = datetime.now(IST).hour

    # Streak is at risk if streak > 0, today is still pending, and it's late in the day (6 PM+ IST)
    streak_at_risk = (streak > 0) and (today_status == "pending") and (now_hour >= 18)

    # Determine streak status: if streak is 0 and any missed day or previously broken, it is broken
    is_broken = (streak == 0 and (best_streak > 0 or missed > 0 or len(settings.get("streak_breaks", [])) > 0))
    if is_broken:
        streak_status = "broken"
    elif streak_at_risk:
        streak_status = "at_risk"
    else:
        streak_status = "active"

    # Find last solved date
    last_solved_date = None
    for c in sorted(challenges, key=lambda x: x.get("solved_at") or "", reverse=True):
        if c.get("status") == "solved" and c.get("solved_at"):
            last_solved_date = c.get("solved_at").split("T")[0]
            break

    missed_days = [c.get("day") for c in sorted(challenges, key=lambda x: x.get("day", 0)) if c.get("status") == "missed"]
    streak_breaks = settings.get("streak_breaks", [])

    return {
        "total": total,
        "solved": solved,
        "missed": missed,
        "streak": streak,
        "best_streak": best_streak,
        "remaining": remaining,
        "streak_started_day": streak_info["streak_started_day"],
        "streak_started_date": streak_info["streak_started_date"],
        "streak_days_running": streak_info["streak_days_running"],
        "quest_remaining": max(0, 30 - solved),
        "streak_at_risk": streak_at_risk,
        "streak_status": streak_status,
        "is_broken": is_broken,
        "last_broken_streak": settings.get("last_broken_streak", 0),
        "last_broken_at": settings.get("last_broken_at"),
        "last_broken_day": settings.get("last_broken_day"),
        "total_streak_breaks": settings.get("total_streak_breaks", len(streak_breaks)),
        "streak_breaks": streak_breaks,
        "missed_days": missed_days,
        "last_solved_date": last_solved_date,
        "recovery_day": current.get("day", 1) if current else 1
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
def send_phone_push(title: str, body: str, challenge_link: str, is_repeat: bool = False, custom_tags: Optional[str] = None, priority: str = "5") -> bool:
    user = get_user_profile()
    topic = os.getenv("NTFY_TOPIC") or user.get("ntfyTopic") or "animesh_unstop_streak"

    clean_title = "".join(ch for ch in title if 32 <= ord(ch) < 127).strip() or "Unstop Challenge Alert"
    safe_link = sanitize_url(challenge_link) or "https://unstop.com/practice/coding"
    clean_link = "".join(ch for ch in safe_link if 32 <= ord(ch) < 127).strip()

    tags = custom_tags if custom_tags else ("rotating_light,alarm_clock,fire" if is_repeat else "fire,muscle,bell")

    headers = {
        "Title": clean_title,
        "Priority": priority,
        "Tags": tags,
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
_last_tracker_toast_time = 0
_tracker_toast_lock = threading.Lock()

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

def is_today_completed() -> bool:
    """Returns True if today's challenge has already been completed / solved."""
    today = today_date_str()
    db = read_db()
    challenges = db.get("challenges", [])

    # 1. First check if any challenge was solved today
    for c in challenges:
        if c.get("status") == "solved":
            solved_date = (c.get("solved_at") or "").split("T")[0]
            assigned_date = c.get("assigned_date") or ""
            if solved_date == today or assigned_date == today:
                return True

    # 2. Check if there is an active pending challenge for today
    today_pending = next((c for c in challenges if c.get("status") == "pending" or (c.get("assigned_date") == today and c.get("status") != "solved")), None)
    if today_pending:
        return False

    return False

def is_notification_allowed(force: bool = False) -> tuple[bool, str]:
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
    challenge = get_or_create_today_challenge()
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

def send_laptop_toast(title: str, body: str, challenge_link: str, subtext: str = "", force: bool = False) -> bool:
    """Sends native Windows Toast notification asynchronously and 100% silently."""
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed:
            print(f"🔕 [Laptop Toast Suppressed] {reason}")
            return False

    global _last_tracker_toast_time
    now = time.time()
    with _tracker_toast_lock:
        if not force and (now - _last_tracker_toast_time) < 10:
            return False
        _last_tracker_toast_time = now

    def _run():
        try:
            ps1_path = BASE_DIR / "send_laptop_notification.ps1"
            safe_link = sanitize_url(challenge_link) or "https://unstop.com/practice/coding"

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
                print(f"💻 [Python] Native laptop notification delivered to screen!")
        except Exception as e:
            print(f"⚠️ [Python] Laptop notification notice: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return True

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
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed:
            print(f"🔕 [Python Reminder Suppressed] {reason}")
            return

    challenge = get_or_create_today_challenge()
    user = get_user_profile()
    first_name = user.get("name", "Coder").split()[0]
    day = challenge.get("day", 1)

    if challenge.get("status") == "solved" and not force:
        print(f"✅ [Python] Day {day} already solved. Skipping reminder.")
        return

    stats = get_stats()
    streak = stats.get("streak", 0)
    is_broken = stats.get("is_broken", False)
    last_broken_day = stats.get("last_broken_day")
    remaining = stats.get("remaining", max(1, 30 - day + 1))
    total = stats.get("total", 30)

    challenge_link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    if is_broken:
        broken_day_str = f"Day {last_broken_day}" if last_broken_day else "earlier"
        title = f"💔 Streak Broken! Day {day} Remaining ({remaining} Days Left)"
        body = f"Hey {first_name}! Your streak broke after {broken_day_str}. Remaining: Day {day} to {total} ({remaining} days left in challenge). Complete Day {day} now to restart your streak!"
        subtext = f"📍 From Day {day} to {total} Remaining • {remaining}/30 Days Remaining • Click to Open"
    elif is_repeat:
        title = f"Still Waiting, {first_name}! Day {day} Challenge ({remaining} Days Left)"
        body = f"{first_name}, you haven't solved Day {day} yet! Keep your {streak}-day streak alive! {remaining} days remaining (Day {day} to {total}). 🚀"
        subtext = f"🔥 {streak}-Day Streak Active • Day {day} Pending"
    else:
        title = f"New Challenge, {first_name}! Day {day} Challenge ({remaining} Days Left)"
        body = f"Hey {first_name}, your challenge for Day {day} is live! Keep your {streak}-day streak alive! {remaining} days remaining (Day {day} to {total}). 💪"
        subtext = f"🔥 {streak}-Day Streak Active • Day {day} Live"

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
    send_laptop_toast(title, body, challenge_link, subtext=subtext)

    db = read_db()
    for c in db.get("challenges", []):
        if c.get("day") == day:
            c["reminded_at"] = now_iso()
            break
    write_db(db)

    print(f"{'🔁 [Python] Repeat' if is_repeat else '🔔 [Python] Initial'} reminder fired for Day {day}")

def fire_completion_notification(day: int, title: str, streak: int, challenge_link: Optional[str] = None) -> None:
    """Per policy: after completing it doesn't show any notification."""
    print(f"🔕 [Python] Day {day} marked SOLVED! All notifications suppressed after completion.")
    return

def fire_streak_warning(force: bool = False) -> None:
    """Fires high-priority pre-break streak warning notifications."""
    if not force:
        allowed, reason = is_notification_allowed(force=False)
        if not allowed:
            print(f"🔕 [Python Streak Warning Suppressed] {reason}")
            return

    challenge = get_or_create_today_challenge()
    user = get_user_profile()
    first_name = user.get("name", "Coder").split()[0]
    day = challenge.get("day", 1)
    streak_info = get_streak_chain_details(read_db())
    streak = streak_info["streak"]
    streak_remaining = streak_info["remaining"]

    if challenge.get("status") == "solved" and not force:
        return

    title = f"⚠️ STREAK AT RISK, {first_name}! Day {day} ({streak_remaining} Tasks Remaining)"
    body = f"🚨 Your {streak}-Day Streak will BREAK at midnight and reset to 0 with 30 tasks remaining! Complete Day {day} challenge now! 🔥"
    subtext = f"⚠️ Midnight Deadline • {streak_remaining} Tasks Remaining for 30-Day Streak • Click to Open"
    challenge_link = challenge.get("link") or user.get("practiceUrl") or "https://unstop.com/practice/coding"

    send_phone_push(title, body, challenge_link, is_repeat=True)
    send_web_push({
        "title": title,
        "body": body,
        "link": challenge_link,
        "tag": f"unstop-warning-day-{day}",
        "isRepeat": True
    })
    send_laptop_toast(title, body, challenge_link, subtext=subtext)
    print(f"⚠️ [Python] Pre-break streak warning fired for Day {day} (Streak: {streak} days)")

def handle_midnight_reset() -> None:
    """Triggered at midnight IST: flags missed day from yesterday if not solved, records streak break, sends alert."""
    db = read_db()
    today = today_date_str()
    challenges = db.get("challenges", [])
    
    # Only look for challenges whose assigned_date is before today and was left pending
    expired_pending = [c for c in challenges if c.get("assigned_date") and c.get("assigned_date") < today and c.get("status") in ("pending", "untracked")]
    
    for current in expired_pending:
        prev_day = current.get("day", 1)
        active_streak = get_streak_unlocked(db)
        current["status"] = "missed"
        current["missed_at"] = datetime.now(IST).isoformat()

        settings = db.setdefault("settings", {})
        if active_streak > 0:
            breaks = settings.setdefault("streak_breaks", [])
            break_event = {
                "id": len(breaks) + 1,
                "day": prev_day,
                "streak_lost": active_streak,
                "broken_at": datetime.now(IST).isoformat(),
                "reason": f"Day {prev_day} missed at midnight deadline ({current.get('assigned_date')})"
            }
            breaks.append(break_event)
            settings["last_broken_streak"] = active_streak
            settings["last_broken_at"] = break_event["broken_at"]
            settings["last_broken_day"] = prev_day
            settings["total_streak_breaks"] = settings.get("total_streak_breaks", 0) + 1
            settings["streak_status"] = "broken"
            if active_streak > settings.get("best_streak", 0):
                settings["best_streak"] = active_streak

        write_db(db)

        user = get_user_profile()
        first_name = user.get("name", "Coder").split()[0]
        next_day = prev_day + 1 if prev_day < 30 else 30
        best_rec = max(settings.get("best_streak", 16), 16)
        title = f"💔 Streak Broken! Reset to 0 (30 Tasks Remaining)"
        body = f"Oh no, {first_name}! Day {prev_day} was missed and your {active_streak}-day streak broke. Current streak is now 0 Days. Tasks remaining reset back to 30. Start fresh with Day {next_day}! 💪"
        subtext = f"📍 Streak: 0 Days (RESET TO 30 Tasks Remaining) • Best: {best_rec}d"
        challenge_link = user.get("practiceUrl") or "https://unstop.com/practice/coding"

        try:
            send_phone_push(title, body, challenge_link, is_repeat=True)
        except Exception:
            pass
        send_web_push({
            "title": title,
            "body": body,
            "link": challenge_link,
            "tag": f"unstop-reset-day-{prev_day}",
            "isRepeat": True
        })
        send_laptop_toast(title, body, challenge_link, subtext=subtext)
        print(f"💔 [Python] Midnight reset triggered! Day {prev_day} marked as missed (lost streak: {active_streak}).")

    evaluate_and_update_missed_days()

# ─── Background Scheduler (Thread) ─────────────────────────────────────────────
def scheduler_loop():
    print("⏰ [Python] Secure scheduler active (IST timezone)")
    last_midnight_checked = None
    last_warning_hour = None
    last_sync_time = 0

    while True:
        try:
            now = datetime.now(IST)
            current_date_str = now.strftime("%Y-%m-%d")
            now_ts = time.time()

            # Background Auto-Sync: Check every 25 seconds for Unstop completion in browser
            if now_ts - last_sync_time >= 25:
                last_sync_time = now_ts
                if auto_sync_and_mark_completed:
                    try:
                        sync_res = auto_sync_and_mark_completed()
                        if sync_res.get("synced") and sync_res.get("newly_solved"):
                            print(f"🎉 [Python Scheduler] Detected completed challenge on Unstop! '{sync_res.get('title')}' auto-marked as SOLVED.")
                    except Exception as e:
                        pass

            # Midnight Trigger (12:00 AM IST) - on and after 00:00
            if now.hour == 0 and now.minute == 0:
                if last_midnight_checked != current_date_str:
                    print(f"\n🕛 [Python] 12:00 AM IST reached (00:00) — evaluating streak reset & snapshot backup...")
                    create_daily_backup()
                    handle_midnight_reset()
                    if not is_today_completed():
                        fire_reminder(is_repeat=False)
                    last_midnight_checked = current_date_str

            # Evening Streak Warning at 10:00 PM (22:00) IST before midnight deadline
            if now.hour == 22 and now.minute == 0:
                warning_key = f"{current_date_str}-{now.hour}"
                if last_warning_hour != warning_key:
                    if not is_today_completed():
                        fire_streak_warning(force=False)
                    last_warning_hour = warning_key

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

        if client_ip in ("127.0.0.1", "localhost", "::1", "testclient"):
            return await call_next(request)

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
            sensitive_prefixes = ("/api/solve", "/api/test-phone-notify", "/api/test-notify", "/api/test-complete", "/api/challenge/update")
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
    host = request.headers.get("host") or ""
    is_same_origin = (sec_fetch_site in ("same-origin", "same-site")) or any(referer.startswith(o) for o in ALLOWED_ORIGINS) or (host and host in referer)

    if is_local_client or is_same_origin:
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
        if v not in ("solved", "pending", "missed", "skipped", "untracked", "upcoming"):
            raise ValueError("Invalid challenge status")
        return v

# ─── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/streak-details")
def api_streak_details():
    evaluate_and_update_missed_days()
    db = read_db()
    stats = get_stats()
    challenges = db.get("challenges", [])
    settings = db.get("settings", {})
    user = get_user_profile()
    today_c = get_or_create_today_challenge()
    return {
        "stats": stats,
        "challenge": today_c,
        "user": user,
        "streak_breaks": settings.get("streak_breaks", []),
        "last_broken_streak": settings.get("last_broken_streak", 0),
        "last_broken_at": settings.get("last_broken_at"),
        "total_breaks": settings.get("total_streak_breaks", 0),
        "missed_challenges": [c for c in challenges if c.get("status") == "missed"]
    }

@app.post("/api/streak/test-break", dependencies=[Depends(verify_api_key)])
def api_test_streak_break(day: Optional[int] = None):
    """Safely test streak break behavior: marks a challenge as missed and records the break."""
    db = read_db()
    challenges = db.get("challenges", [])
    settings = db.setdefault("settings", {})

    target_day = day
    if not target_day:
        current = get_or_create_today_challenge()
        target_day = current.get("day", 1)

    previous_streak = get_streak_unlocked(db)
    for c in challenges:
        if c.get("day") == target_day:
            c["status"] = "missed"
            c["missed_at"] = datetime.now(IST).isoformat()
            break

    if previous_streak > 0:
        breaks = settings.setdefault("streak_breaks", [])
        break_event = {
            "id": len(breaks) + 1,
            "day": target_day,
            "streak_lost": previous_streak,
            "broken_at": datetime.now(IST).isoformat(),
            "reason": f"Manual test streak break for Day {target_day}"
        }
        breaks.append(break_event)
        settings["last_broken_streak"] = previous_streak
        settings["last_broken_at"] = break_event["broken_at"]
        settings["last_broken_day"] = target_day
        settings["total_streak_breaks"] = settings.get("total_streak_breaks", 0) + 1
        settings["streak_status"] = "broken"

    write_db(db)
    return {"success": True, "message": f"Streak break tested on Day {target_day}!", "stats": get_stats()}

@app.post("/api/streak/restore", dependencies=[Depends(verify_api_key)])
def api_restore_streak(day: Optional[int] = None):
    """Restores a missed challenge back to solved/pending after testing."""
    db = read_db()
    challenges = db.get("challenges", [])
    target_day = day or 11
    for c in challenges:
        if c.get("day") == target_day:
            c["status"] = "solved"
            c["solved_at"] = datetime.now(IST).isoformat()
            c["missed_at"] = None
            break
    write_db(db)
    return {"success": True, "message": f"Day {target_day} restored to solved!", "stats": get_stats()}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "time": now_iso(), "secure": True}

@app.get("/api/today")
def api_today():
    if auto_sync_and_mark_completed:
        try:
            auto_sync_and_mark_completed()
        except Exception:
            pass
    challenge = get_or_create_today_challenge()
    stats = get_stats()
    user = get_user_profile()
    today = today_date_str()
    state_file = DATA_DIR / "notifier_state.json"
    first_open_done = False
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
                first_open_done = (st.get("last_first_open_date") == today)
        except Exception:
            pass
    return {"challenge": challenge, "stats": stats, "user": user, "first_open_done": first_open_done}

@app.get("/api/sync")
@app.post("/api/sync")
def api_sync():
    sync_res = {}
    if auto_sync_and_mark_completed:
        try:
            sync_res = auto_sync_and_mark_completed()
        except Exception as e:
            sync_res = {"synced": False, "error": str(e)}
    challenge = get_or_create_today_challenge()
    stats = get_stats()
    user = get_user_profile()
    return {"sync": sync_res, "challenge": challenge, "stats": stats, "user": user}



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
    try:
        challenge = get_or_create_today_challenge()
        day = challenge.get("day", 1)
        title = challenge.get("title", f"Day {day} Challenge")
        link = challenge.get("link", "https://unstop.com/practice/coding")
        update_challenge_status(day, "solved")
        stats = get_stats()
        streak = stats.get("streak", 1)
        print(f"🎉 [Python] Day {day} marked as SOLVED!")

        # Update notifier_state.json to completely suppress notifications
        state_file = DATA_DIR / "notifier_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as sf:
                    state = json.load(sf)
                state["last_first_open_date"] = today_date_str()
                state["first_open_resolved_at"] = now_iso()
                state["potd_ended"] = True
                state["notifications_suppressed"] = True
                state_tmp = DATA_DIR / "notifier_state.tmp"
                with open(state_tmp, "w", encoding="utf-8") as stf:
                    json.dump(state, stf, indent=2)
                state_tmp.replace(state_file)
            except Exception:
                pass

        try:
            fire_completion_notification(day, title, streak, link)
        except Exception:
            pass

        return {
            "success": True,
            "day": day,
            "streak": streak,
            "message": f"Day {day} solved! Outstanding consistency, Animesh! 🎉"
        }
    except Exception as e:
        print(f"❌ Error in api_solve: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to mark solved: {str(e)}"})

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

@app.post("/api/test-complete", dependencies=[Depends(verify_api_key)])
def api_test_complete():
    challenge = get_or_create_today_challenge()
    day = challenge.get("day", 1)
    title = challenge.get("title", f"Day {day} Challenge")
    link = challenge.get("link", "https://unstop.com/practice/coding")
    stats = get_stats()
    streak = stats.get("streak", 1)
    fire_completion_notification(day, title, streak, link)
    return {"success": True, "message": "Completion notification sent!"}

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

@app.post("/api/test-first-open", dependencies=[Depends(verify_api_key)])
def api_test_first_open():
    notifier_script = BASE_DIR / "unstop_laptop_notifier.py"
    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
    cmd = [pythonw_exe, str(notifier_script), "--first-open", "--force"]
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    subprocess.Popen(cmd, creationflags=creationflags, startupinfo=startupinfo)
    return {"success": True, "message": "Notification triggered silently!"}


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
