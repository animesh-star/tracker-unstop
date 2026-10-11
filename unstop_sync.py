"""
Unstop Automatic Challenge Detection & Sync Engine
Automatically monitors browser history (Chrome & Edge) for Unstop coding practice activity.
When a user completes or practices a challenge on Unstop without clicking the notification:
1. Automatically fetches the exact problem title and practice URL.
2. Automatically marks today's challenge as solved in challenges.json.
3. Updates notifier_state.json to completely suppress further popups and notifications.
"""

import os
import sys
import json
import sqlite3
import shutil
import tempfile
import subprocess
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "challenges.json"
STATE_FILE = DATA_DIR / "notifier_state.json"
IST = timezone(timedelta(hours=5, minutes=30))

def today_date_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")

def clean_problem_title(raw_title: Optional[str], url: Optional[str] = None) -> str:
    """Cleans browser page title into clean challenge problem title."""
    if not raw_title:
        title = ""
    else:
        title = raw_title.strip()

    # Patterns: 'Dev\'s Momentum Ledger | Practice | Unstop' -> 'Dev\'s Momentum Ledger'
    for delimiter in (" | Practice | Unstop", " | Unstop", " - Unstop", " | Practice"):
        if delimiter.lower() in title.lower():
            idx = title.lower().find(delimiter.lower())
            title = title[:idx].strip()

    # If title mentions Coding Practice Platform
    if "coding practice platform" in (raw_title or "").lower() or (url and "practice/coding" in url):
        return "Coding Practice Platform for Placements & Interviews"

    # If title is empty or generic site marketing title
    is_generic = (
        not title 
        or "Competitions, Quizzes" in title 
        or "Internships for Students" in title
        or title == "Unstop"
    )

    if is_generic:
        if url and "/code/practice/" in url:
            parts = [p for p in url.split("/") if p]
            prob_id = parts[-1] if parts else ""
            if prob_id.isdigit():
                return f"Practice Problem #{prob_id}"
        return "Coding Practice Platform for Placements & Interviews"

    return title or "Coding Practice Platform for Placements & Interviews"

def get_browser_history_candidates() -> List[Path]:
    """Returns potential Chrome and Edge history paths on Windows."""
    candidates = []
    # Chrome
    chrome_base = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
    if chrome_base.exists():
        candidates.append(chrome_base / "Default" / "History")
        for p in chrome_base.glob("Profile */History"):
            candidates.append(p)
    # Edge
    edge_base = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"))
    if edge_base.exists():
        candidates.append(edge_base / "Default" / "History")
        for p in edge_base.glob("Profile */History"):
            candidates.append(p)
    return [p for p in candidates if p.exists()]

def fetch_unstop_activity_for_date(target_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Scans browser history for Unstop coding practice on the specified date (YYYY-MM-DD in IST).
    Defaults to today's date in IST.
    """
    if not target_date:
        target_date = today_date_str()

    history_paths = get_browser_history_candidates()
    best_match = None

    for hp in history_paths:
        tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".sqlite_hist")
        os.close(tmp_fd)
        tmp_file = Path(tmp_path_str)

        try:
            shutil.copy2(hp, tmp_file)
            conn = sqlite3.connect(tmp_file)
            c = conn.cursor()

            # Query URLs matching unstop practice
            query = """
            SELECT u.url, u.title, u.visit_count, u.last_visit_time, v.visit_duration
            FROM urls u
            LEFT JOIN visits v ON v.url = u.id AND v.visit_time = u.last_visit_time
            WHERE (u.url LIKE '%unstop.com/code/practice/%' OR u.url LIKE '%unstop.com/practice/coding%')
            ORDER BY u.last_visit_time DESC
            """
            c.execute(query)
            rows = c.fetchall()
            conn.close()

            for url, title, count, ts, duration in rows:
                if not ts:
                    continue
                # Chrome epoch: microseconds since 1601-01-01 UTC
                dt_utc = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ts)
                dt_ist = dt_utc.astimezone(IST)
                date_str = dt_ist.strftime("%Y-%m-%d")

                if date_str == target_date:
                    is_specific_code = "/code/practice/" in url and any(char.isdigit() for char in url.split("/")[-1])
                    is_practice_portal = "unstop.com/practice/coding" in url or "practice/coding" in url
                    cleaned = clean_problem_title(title, url=url)
                    duration_sec = (duration or 0) / 1_000_000

                    if is_specific_code or is_practice_portal:
                        match_data = {
                            "url": url,
                            "raw_title": title,
                            "title": cleaned,
                            "time_iso": dt_ist.isoformat(),
                            "timestamp": dt_ist.timestamp(),
                            "duration_sec": duration_sec,
                            "visit_count": count,
                            "is_specific": is_specific_code,
                            "browser": "Chrome" if "Google" in str(hp) else "Edge"
                        }

                        if not best_match:
                            best_match = match_data
                        elif is_specific_code and not best_match.get("is_specific"):
                            best_match = match_data
                        elif (is_specific_code == best_match.get("is_specific", False)) and match_data["timestamp"] > best_match["timestamp"]:
                            best_match = match_data

        except Exception as e:
            # History file might be locked briefly, skip
            pass
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass

    return best_match

def send_completion_notification(day: int, title: str, streak: int, challenge_link: Optional[str] = None) -> None:
    """Delivers celebration notification upon confirmed completion."""
    print(f"🎉 [Sync Engine] Day {day} marked SOLVED! Streak: {streak} Days.")
    user = {"name": "Animesh"}
    try:
        from unstop_laptop_notifier import send_laptop_toast
        clean_title = title.split(":", 1)[1].strip() if ":" in title else title
        send_laptop_toast(
            "🎉 Challenge Solved & Streak Secured!",
            f"Awesome work Animesh! Day {day} ('{clean_title}') is SOLVED! Streak: {streak} Days! 🔥",
            challenge_link or "https://unstop.com/practice/coding",
            subtext=f"🔥 {streak}-Day Streak Active • Keep up the momentum!",
            force=True
        )
    except Exception:
        pass

def auto_sync_and_mark_completed(target_date: Optional[str] = None, mark_solved: bool = False) -> Dict[str, Any]:
    """
    Checks if the user accessed an Unstop challenge in their browser on target_date (default today).
    - If found: Enriches challenge title and practice URL in challenges.json.
    - Only marks 'solved' if mark_solved=True or if already solved, preventing premature
      suppression of notifications while the task is still in progress.
    Returns result dictionary.
    """
    if not target_date:
        target_date = today_date_str()

    activity = fetch_unstop_activity_for_date(target_date)
    if not activity:
        return {
            "synced": False,
            "reason": f"No specific Unstop problem activity detected in browser history for {target_date}"
        }

    if not DB_PATH.exists():
        return {
            "synced": False,
            "reason": f"Database file not found at {DB_PATH}"
        }

    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            db = json.load(f)

        challenges = db.get("challenges", [])
        if not challenges:
            return {"synced": False, "reason": "No challenges in database"}

        # Find today's challenge:
        today_solved = next((c for c in challenges if (c.get("solved_at") or "").split("T")[0] == target_date and c.get("status") == "solved"), None)
        pending = next((c for c in challenges if c.get("status") in ("pending", "upcoming") and (not c.get("assigned_date") or c.get("assigned_date") <= target_date)), None)
        if not pending:
            pending = next((c for c in challenges if c.get("status") in ("pending", "upcoming")), None)

        target_challenge = today_solved or pending
        if not target_challenge:
            target_challenge = challenges[-1]

        day = target_challenge.get("day", 1)
        was_already_solved = (target_challenge.get("status") == "solved")

        # Update problem details with actual fetched specific challenge title & url from Unstop
        if activity.get('title') and activity['title'] not in ("Unstop", "Unstop Coding Challenge"):
            curr_title = target_challenge.get("title", "")
            has_specific_title = bool(curr_title and ":" in curr_title and "Coding Challenge" not in curr_title and "Coding Practice Platform" not in curr_title)
            if activity.get("is_specific") or not has_specific_title:
                new_title = f"Day {day}: {activity['title']}" if not activity['title'].startswith(f"Day {day}") else activity['title']
                target_challenge["title"] = new_title
        elif not target_challenge.get("title") or "Coding Challenge" in target_challenge.get("title"):
            prob_id = activity.get('url', '').split('/')[-1]
            if prob_id.isdigit():
                target_challenge["title"] = f"Day {day}: Practice Problem #{prob_id}"

        new_title = target_challenge.get("title", f"Day {day} Challenge")
        if activity.get("is_specific") or not target_challenge.get("link"):
            target_challenge["link"] = activity["url"]

        # Only change status to solved if explicitly requested
        if mark_solved and not was_already_solved:
            target_challenge["status"] = "solved"
            target_challenge["solved_at"] = activity.get("time_iso") or datetime.now(IST).isoformat()

        # Recalculate streak
        solved_days = {c.get("day") for c in challenges if c.get("status") == "solved"}
        cur_day = target_challenge.get("day", 1)
        start_day = cur_day if target_challenge.get("status") == "solved" else cur_day - 1
        streak = 0
        for d in range(start_day, 0, -1):
            if d in solved_days:
                streak += 1
            else:
                break

        settings = db.setdefault("settings", {})
        settings["best_streak"] = max(settings.get("best_streak", 16), streak, 16)

        # Save DB atomically
        tmp_db = DATA_DIR / "challenges.tmp"
        with open(tmp_db, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
        tmp_db.replace(DB_PATH)

        # Mirror to root challenges.json
        try:
            root_db = BASE_DIR / "challenges.json"
            if root_db != DB_PATH:
                with open(root_db, "w", encoding="utf-8") as rf:
                    json.dump(db, rf, indent=2)
        except Exception:
            pass

        # Update notifier_state.json ONLY if challenge is truly solved
        if mark_solved and target_challenge.get("status") == "solved" and STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as sf:
                    state = json.load(sf)
                state["last_first_open_date"] = target_date
                state["first_open_resolved_at"] = target_challenge.get("solved_at") or activity["time_iso"]
                state["snooze_until"] = 0
                state_tmp = DATA_DIR / "notifier_state.tmp"
                with open(state_tmp, "w", encoding="utf-8") as stf:
                    json.dump(state, stf, indent=2)
                state_tmp.replace(STATE_FILE)
            except Exception as e:
                print(f"Error updating notifier state: {e}")

        newly_solved = mark_solved and not was_already_solved
        if newly_solved:
            send_completion_notification(day, new_title, streak, activity.get("url"))

        return {
            "synced": True,
            "day": day,
            "title": new_title,
            "link": activity["url"],
            "status": target_challenge.get("status"),
            "solved_at": target_challenge.get("solved_at"),
            "newly_solved": newly_solved,
            "was_already_solved": was_already_solved,
            "streak": streak,
            "activity": activity,
            "message": f"Successfully synced Day {day} ('{new_title}') from Unstop."
        }

    except Exception as ex:
        return {"synced": False, "error": str(ex)}

if __name__ == "__main__":
    print("🔍 Testing Unstop Browser Activity Fetcher...")
    act = fetch_unstop_activity_for_date()
    if act:
        print(f"✅ Found today's activity: {act['title']}")
        print(f"   URL: {act['url']}")
        print(f"   Time (IST): {act['time_iso']}")
        print(f"   Duration: {act['duration_sec']:.1f}s, Visits: {act['visit_count']}")
    else:
        print("❌ No activity found for today.")

    print("\n⚡ Running Auto-Sync & Mark Completed...")
    res = auto_sync_and_mark_completed()
    print("Result:", json.dumps(res, indent=2))
