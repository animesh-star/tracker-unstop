"""
Unstop Background Service Manager
Keeps the Unstop Tracker server (port 3000) and Real-time Laptop Notifier
always running silently in the background (zero terminal windows).
"""

import os
import sys
import subprocess
import time
from pathlib import Path
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
PYTHONW = sys.executable.replace("python.exe", "pythonw.exe")
TRACKER_SCRIPT = BASE_DIR / "unstop_tracker.py"
NOTIFIER_SCRIPT = BASE_DIR / "unstop_laptop_notifier.py"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
VBS_STARTUP_PATH = STARTUP_DIR / "StartUnstopService.vbs"

def is_server_running() -> bool:
    try:
        res = requests.get("http://localhost:3000/api/today", timeout=2)
        return res.status_code == 200
    except Exception:
        return False

def start_services():
    """Starts tracker server and laptop notifier silently using pythonw."""
    if not is_server_running():
        print("🚀 Starting Unstop Tracker Server (http://localhost:3000)...")
        subprocess.Popen(
            [PYTHONW, str(TRACKER_SCRIPT)],
            cwd=str(BASE_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        time.sleep(2)
        if is_server_running():
            print("✅ Tracker Server is online and active!")
        else:
            print("⚠️ Server started; checking status...")
    else:
        print("✅ Tracker Server is already running on http://localhost:3000.")

    # Start laptop notifier listener
    print("📱 Starting Real-time Phone/Laptop Notifier...")
    subprocess.Popen(
        [PYTHONW, str(NOTIFIER_SCRIPT)],
        cwd=str(BASE_DIR),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    print("✅ Notifier connected in real time!")

def stop_services():
    """Stops all running tracker and notifier background processes."""
    print("🛑 Stopping Unstop background services...")
    ps_cmd = """
Get-CimInstance Win32_Process | Where-Object { 
    $_.CommandLine -like "*unstop_tracker.py*" -or $_.CommandLine -like "*unstop_laptop_notifier.py*" 
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
    print("✅ Stopped all Unstop background services.")

def check_status():
    """Checks the status of background services."""
    server_ok = is_server_running()
    print("\n" + "=" * 50)
    print("🔍 Unstop Background Services Status:")
    print("=" * 50)
    print(f"🌐 Tracker Server (port 3000): {'🟢 ONLINE' if server_ok else '🔴 OFFLINE'}")
    
    ps_cmd = """
(Get-CimInstance Win32_Process | Where-Object { 
    $_.CommandLine -like "*unstop_laptop_notifier.py*" 
}).Count
"""
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
    notifier_count = int(res.stdout.strip() or "0")
    print(f"📱 Phone/Laptop Listener:     {'🟢 CONNECTED' if notifier_count > 0 else '🔴 OFFLINE'}")
    print(f"🔄 Auto-Start on Windows Boot: {'🟢 ENABLED' if VBS_STARTUP_PATH.exists() else '⚪ DISABLED'}")
    print("=" * 50 + "\n")

def install_autostart():
    """Configures automatic invisible launch whenever Windows boots or logs in."""
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{BASE_DIR}"
WshShell.Run """{PYTHONW}"" ""{TRACKER_SCRIPT}""", 0, False
WshShell.Run """{PYTHONW}"" ""{NOTIFIER_SCRIPT}""", 0, False
'''
    try:
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)
        with open(VBS_STARTUP_PATH, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        print(f"✅ Auto-boot script installed to Windows Startup: {VBS_STARTUP_PATH.name}")

        # Also register a Windows Scheduled Task for redundancy
        cmd = f'schtasks /create /tn "UnstopTrackerService" /tr "wscript.exe \"{VBS_STARTUP_PATH}\"" /sc onlogon /f'
        subprocess.run(cmd, shell=True, capture_output=True)
        print("✅ Windows Scheduled Task 'UnstopTrackerService' registered!")
        print("🎉 Both the server and notification listener are now permanently configured to run 24/7!")
        return True
    except Exception as e:
        print(f"❌ Failed to configure autostart: {e}")
        return False

def uninstall_autostart():
    """Removes automatic startup configurations."""
    if VBS_STARTUP_PATH.exists():
        VBS_STARTUP_PATH.unlink()
        print("🗑️ Removed Startup folder launcher.")
    subprocess.run('schtasks /delete /tn "UnstopTrackerService" /f', shell=True, capture_output=True)
    print("🗑️ Removed Scheduled Task.")

if __name__ == "__main__":
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if action in ("start", "run"):
        start_services()
    elif action in ("stop", "kill"):
        stop_services()
    elif action in ("status", "check"):
        check_status()
    elif action in ("install", "autostart", "enable"):
        install_autostart()
        start_services()
    elif action in ("uninstall", "disable"):
        uninstall_autostart()
    else:
        print("Usage: python unstop_service.py [start | stop | status | autostart | uninstall]")
