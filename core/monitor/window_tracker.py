import time
import win32gui
import win32process
import psutil
from datetime import datetime
from monitor.db import log_window, get_connection

# Application categories.
APP_CATEGORIES = {
    # code
    "code.exe": "code", "cursor.exe": "code", "pycharm64.exe": "code",
    "idea64.exe": "code", "sublime_text.exe": "code", "vim.exe": "code",
    "nvim.exe": "code", "zed.exe": "code",
    # browser
    "chrome.exe": "browser", "firefox.exe": "browser", "msedge.exe": "browser",
    "arc.exe": "browser", "brave.exe": "browser",
    # terminal
    "windowsterminal.exe": "terminal", "powershell.exe": "terminal",
    "cmd.exe": "terminal", "git-bash.exe": "terminal",
    # design
    "figma.exe": "design", "xd.exe": "design", "photoshop.exe": "design",
    "illustrator.exe": "design",
    # docs
    "winword.exe": "docs", "notion.exe": "docs", "obsidian.exe": "docs",
    # comms
    "discord.exe": "comms", "telegram.exe": "comms", "slack.exe": "comms",
    # media
    "vlc.exe": "media", "spotify.exe": "media",
}

def get_active_window():
    """Return the active window title and process executable."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        exe = proc.name().lower()
        return title, exe
    except Exception:
        return None, None

def categorize(exe):
    """Resolve an app category from an executable name."""
    if exe in APP_CATEGORIES:
        return APP_CATEGORIES[exe]
    # GPU-based detection can be added later for unknown apps.
    return "other"

def get_app_time(session_id, minutes=30):
    """Return minutes spent in each app during the last N minutes."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT app_name, category, COUNT(*) as ticks
        FROM window_log
        WHERE session_id = ?
          AND julianday(timestamp) >= julianday('now', 'localtime', ?)
        GROUP BY app_name
        ORDER BY ticks DESC
    ''', (session_id, f"-{max(0, float(minutes)):g} minutes"))
    rows = c.fetchall()
    conn.close()
    # Each tick is 5 seconds.
    return [{"app": r["app_name"], "category": r["category"],
             "minutes": round(r["ticks"] * 5 / 60, 1)} for r in rows]

def get_switch_count(session_id, minutes=10):
    """Count window switches during the last N minutes."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT COUNT(*) as cnt FROM (
            SELECT timestamp, app_name,
                   LAG(app_name) OVER (ORDER BY timestamp) as prev_app
            FROM window_log
            WHERE session_id = ?
              AND julianday(timestamp) >= julianday('now', 'localtime', ?)
        ) WHERE app_name != prev_app AND prev_app IS NOT NULL
    ''', (session_id, f"-{max(0, float(minutes)):g} minutes"))
    row = c.fetchone()
    conn.close()
    return row["cnt"] if row else 0

class WindowTracker:
    def __init__(self, session_id, interval=5):
        self.session_id = session_id
        self.interval = interval  # seconds
        self.running = False
        self.last_exe = None

    def start(self):
        self.running = True
        print("[WindowTracker] started")
        while self.running:
            title, exe = get_active_window()
            if exe:
                category = categorize(exe)
                log_window(exe, category, self.session_id)
                if exe != self.last_exe:
                    print(f"[WindowTracker] {exe} ({category})")
                    self.last_exe = exe
            time.sleep(self.interval)

    def stop(self):
        self.running = False
        print("[WindowTracker] stopped")

if __name__ == "__main__":
    from db import init_db, start_session
    init_db()
    sid = start_session()
    
    tracker = WindowTracker(session_id=sid, interval=5)
    print("tracking active window (Ctrl+C to stop)...")
    try:
        tracker.start()
    except KeyboardInterrupt:
        tracker.stop()
        
        apps = get_app_time(sid)
        print("\n-- stats --")
        for a in apps:
            print(f"  {a['app']:30} {a['category']:10} {a['minutes']}m")
        
        switches = get_switch_count(sid)
        print(f"\nwindow switches in 10 min: {switches}")
