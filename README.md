# 🐍 Tracker Unstop - 30-Day Challenge Tracker

Personalized for **Animesh Goswami** (`animegos31002` / `animesh.goswami045@gmail.com`).

A full-stack Python application that:
- Fires a reminder **every night at 12:00 AM IST**
- Keeps reminding (with escalating alerts) **every 5 minutes until today's challenge is solved**
- Sends ringing alerts straight to your **Mobile Phone lockscreen** via `ntfy.sh`
- Tracks your **30-day streak, past records, and upcoming tasks**
- Provides both a **Web Dashboard** and a **Python CLI**

---

## 🚀 How to Run the Server

```bash
cd "C:\Users\ANIMESH\OneDrive\Attachments\unstop"
python unstop_tracker.py
```
Open in browser: **http://localhost:3000**

---

## 📱 Mobile Phone Notifications (Instant Setup)

1. Open **[https://ntfy.sh/animesh_unstop_streak](https://ntfy.sh/animesh_unstop_streak)** on your phone (Chrome/Safari) or install the free **ntfy** app.
2. Tap **"Subscribe"** to topic `animesh_unstop_streak` and allow notifications.
3. Test your phone alert anytime:
   ```bash
   python unstop_cli.py alert
   ```

---

## 💻 Python CLI Companion

Control your challenge tracker directly from the terminal:

```bash
# Check current day, status, streak, tasks done & remaining
python unstop_cli.py status

# Mark today's challenge as SOLVED (stops 5-minute repeat alarms)
python unstop_cli.py solve

# View full 30-day task list (Past, Today, Upcoming)
python unstop_cli.py roadmap

# Send a test alert to your phone
python unstop_cli.py alert
```

---

## 📊 How Tasks Are Tracked

All progress is stored in `challenges.json`:
- **Tasks Done**: Number of challenges marked as `solved`.
- **Tasks To Do**: Today's active challenge (`pending`) + future days.
- **Past Records**: Complete history of every day, timestamp, problem link, and status.

---

## 🛠️ Architecture

- **Backend**: Python 3.14 + FastAPI + Uvicorn + Schedule + Requests
- **Mobile Push**: ntfy.sh (open source, zero-config push protocol)
- **Laptop Home Screen Notifier**: Native Windows Toast + Tkinter Interactive Home Screen Popup
- **Database**: Local JSON storage (`challenges.json` + `notifier_state.json`)
- **Frontend**: HTML5 + CSS3 + Vanilla JS (dark mode, glassmorphism, responsive)

---

## ☀️ First Laptop Open of the Day (Home Screen Popup)

Whenever you open your laptop for the first time in the day:
1. **Automatic Detection**: Detects both laptop boot / Windows login and opening the laptop lid / waking from sleep.
2. **Interactive Home Screen Popup**: If today's challenge is not completed, pops up an interactive desktop card directly on your screen with:
   - Today's day number & challenge title
   - Current active streak flame 🔥
   - `🚀 Open Task in Chrome` button (redirects directly to Google Chrome)
   - `✅ Mark Solved` button (marks challenge complete directly)
   - `⏰ Remind in 30m` snooze button
3. **Suppressed if Solved**: If you already solved today's challenge, it will never interrupt you.
4. **Test Anytime**:
   ```bash
   # Test the home screen popup & Chrome redirection
   python unstop_cli.py first-open --force

   # Open today's challenge directly in Chrome
   python unstop_cli.py open
   ```
   Or click **"☀️ Test Home Screen Popup"** on the Web Dashboard!


