import time
import threading
from datetime import datetime
from monitor.db import init_db, start_session, end_session, log_state
from window_tracker import WindowTracker, get_app_time, get_switch_count
from session_tracker import SessionTracker
from system_tracker import SystemTracker
from trigger_engine import TriggerEngine

GRINDING_MINUTES   = 120
SCATTERED_SWITCHES = 5
LATE_NIGHT_START   = 23
LATE_NIGHT_END     = 5

class Aggregator:
    def __init__(self):
        init_db()
        self.session_id      = start_session()
        self.session_tracker = SessionTracker(self.session_id)
        self.window_tracker  = WindowTracker(self.session_id, interval=5)
        self.system_tracker  = SystemTracker(interval=30)
        self.trigger_engine  = TriggerEngine(
            session_id=self.session_id,
            on_trigger=self.on_emiya_speak
        )
        self.last_sys        = {}
        self.running         = False

    def on_emiya_speak(self, trigger, message, payload=None):
        """Handle an autonomous Emiya line."""
        print(f"\n{'═'*50}")
        print(f"  EMIYA  →  {message}")
        print(f"{'═'*50}\n")
        # Later this can also be sent to the UI through WebSocket.

    def analyze_state(self):
        states   = set()
        stats    = self.session_tracker.get_stats()

        if stats["is_afk"]:
            states.add("afk")
            return states

        switches = get_switch_count(self.session_id, minutes=10)
        apps     = get_app_time(self.session_id, minutes=30)

        if switches >= SCATTERED_SWITCHES:
            states.add("scattered")

        if apps and apps[0]["minutes"] >= 20 and switches < 3:
            states.add("deep_work")

        if stats["active_minutes"] >= GRINDING_MINUTES:
            states.add("grinding")

        if switches >= 3 and len(apps) <= 2:
            states.add("idle_loop")

        if apps and apps[0]["category"] == "gaming":
            states.add("gaming")

        hour = datetime.now().hour
        if hour >= LATE_NIGHT_START or hour < LATE_NIGHT_END:
            states.add("late_night")

        if not states:
            states.add("normal")

        return states

    def on_system_update(self, snapshot):
        self.last_sys = snapshot

    def print_status(self, states, stats):
        apps = get_app_time(self.session_id, minutes=30)
        print("\n" + "─" * 50)
        print(f"  time of day  : {stats['time_of_day']}")
        print(f"  active       : {stats['active_minutes']} min")
        print(f"  state        : {', '.join(states)}")
        if self.last_sys:
            print(f"  CPU          : {self.last_sys['cpu_percent']}%")
            print(f"  RAM          : {self.last_sys['ram_percent']}%")
        print("  top apps:")
        for a in apps[:3]:
            print(f"    {a['app']:25} {a['category']:10} {a['minutes']}m")
        print("─" * 50)

    def start(self):
        self.running = True

        t_window = threading.Thread(
            target=self.window_tracker.start, daemon=True)
        t_system = threading.Thread(
            target=lambda: self.system_tracker.start(
                callback=self.on_system_update), daemon=True)

        t_window.start()
        t_system.start()

        print("[Aggregator] started. Emiya is watching.")
        print("─" * 50)

        try:
            while self.running:
                self.session_tracker.ping()
                states = self.analyze_state()
                stats  = self.session_tracker.get_stats()

                # Log detected state.
                for s in states:
                    log_state(s, self.session_id)

                # Check triggers.
                self.trigger_engine.check(states, stats)

                self.print_status(states, stats)
                time.sleep(15)

        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.running = False
        self.window_tracker.stop()
        self.system_tracker.stop()
        end_session(self.session_id)
        print("\n[Aggregator] stopped")

if __name__ == "__main__":
    agg = Aggregator()
    agg.start()
