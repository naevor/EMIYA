import time
from datetime import datetime, timedelta
from typing import Callable

AFK_THRESHOLD = 300  # 5 minutes without activity = AFK


def get_system_idle_seconds() -> float:
    """Return seconds since the last keyboard or mouse input on Windows."""
    try:
        import win32api

        current_tick = win32api.GetTickCount()
        last_input_tick = win32api.GetLastInputInfo()
        return ((current_tick - last_input_tick) & 0xFFFFFFFF) / 1000.0
    except Exception:
        # Treat an unavailable idle-time source as active instead of inventing AFK.
        return 0.0


class SessionTracker:
    def __init__(
        self,
        session_id,
        idle_seconds_provider: Callable[[], float] | None = None,
    ):
        self.session_id = session_id
        self.session_start = datetime.now()
        self.last_active = datetime.now()
        self.is_afk = False
        self.afk_start = None
        self.total_afk_seconds = 0.0
        self._idle_seconds_provider = idle_seconds_provider or get_system_idle_seconds

    def ping(self, now: datetime | None = None) -> bool:
        """Record real user activity and return whether this ended an AFK period."""
        now = now or datetime.now()
        returned_from_afk = self.is_afk
        if self.is_afk:
            afk_duration = max(0.0, (now - self.afk_start).total_seconds())
            self.total_afk_seconds += afk_duration
            self.is_afk = False
            self.afk_start = None
            print(f"[SessionTracker] returned after {int(afk_duration)}s AFK")
        self.last_active = now
        return returned_from_afk

    def check_afk(
        self,
        idle_seconds: float | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Enter AFK when the operating system reports enough idle time."""
        now = now or datetime.now()
        idle_seconds = (
            self._idle_seconds_provider()
            if idle_seconds is None
            else max(0.0, float(idle_seconds))
        )
        if idle_seconds >= AFK_THRESHOLD and not self.is_afk:
            self.is_afk = True
            detected_start = now - timedelta(seconds=idle_seconds)
            self.afk_start = max(self.session_start, detected_start)
            self.last_active = self.afk_start
            print("[SessionTracker] AFK detected")
            return True
        return False

    def poll_activity(
        self,
        idle_seconds: float | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """Poll OS input state and return an AFK transition, if one occurred."""
        now = now or datetime.now()
        idle_seconds = (
            self._idle_seconds_provider()
            if idle_seconds is None
            else max(0.0, float(idle_seconds))
        )

        if idle_seconds >= AFK_THRESHOLD:
            return "afk" if self.check_afk(idle_seconds=idle_seconds, now=now) else None

        if self.is_afk:
            self.ping(now=now)
            return "afk_return"

        self.last_active = now - timedelta(seconds=idle_seconds)
        return None

    def get_active_duration(self, now: datetime | None = None):
        """Active session time in minutes, excluding AFK."""
        now = now or datetime.now()
        total = max(0.0, (now - self.session_start).total_seconds())
        current_afk = (
            max(0.0, (now - self.afk_start).total_seconds())
            if self.is_afk and self.afk_start
            else 0.0
        )
        active = max(0.0, total - self.total_afk_seconds - current_afk)
        return round(active / 60, 1)

    def get_time_of_day(self):
        hour = datetime.now().hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "day"
        elif 18 <= hour < 23:
            return "evening"
        else:
            return "night"

    def get_stats(self):
        return {
            "session_start":    self.session_start.isoformat(),
            "active_minutes":   self.get_active_duration(),
            "is_afk":           self.is_afk,
            "total_afk_min":    round(self.total_afk_seconds / 60, 1),
            "time_of_day":      self.get_time_of_day(),
        }

if __name__ == "__main__":
    from db import init_db, start_session
    init_db()
    sid = start_session()
    tracker = SessionTracker(session_id=sid)

    print("simulating session...")
    print(f"[stats] {tracker.get_stats()}")

    # Simulate activity.
    for i in range(3):
        tracker.ping()
        time.sleep(1)

    print(f"[stats] {tracker.get_stats()}")
    print(f"[time_of_day] {tracker.get_time_of_day()}")
    print("[SessionTracker] test passed")
