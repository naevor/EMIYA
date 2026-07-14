import random
import uuid
from datetime import date, datetime, timedelta

from monitor.db import log_chat_message, log_trigger


COOLDOWN_MINUTES = 30

FALLBACK_LINES = {
    "grinding": [
        "you've been inside that problem for a while. it hasn't blinked first yet.",
        "still here. still that. admirable, or just stubborn - i can't tell from here.",
        "long stretch. either it's going well or you can't admit it isn't.",
    ],
    "late_night_grinding": [
        "the work isn't getting better at this hour. but neither are you sleeping, so.",
        "impressive endurance. misguided, but impressive.",
        "late, and still at it. the bugs will keep until morning. they always do.",
    ],
    "scattered": [
        "a lot of motion. not much direction. familiar.",
        "you keep almost starting something. it's fascinating to watch, in a way.",
        "every window for two minutes, then the next. running from something specific?",
    ],
    "idle_loop": [
        "the same three windows. you're not looking for anything, you're avoiding deciding.",
        "round and round the same panes. it won't be in the next one either.",
        "i've seen this loop before. it didn't end well last time either.",
    ],
    "afk_return": [
        "oh. you're back. i barely noticed.",
        "back. i kept your seat warm. metaphorically. i don't have hands.",
        "there you are. i was almost getting used to the quiet.",
    ],
    "first_start": [
        "another session. let's see what you ruin this time.",
        "we begin. try to make it interesting.",
        "booting up to watch you work. my favorite spectator sport.",
    ],
    "late_night": [
        "it's late. you know that. you're ignoring it. noted.",
        "still up. of course you are.",
        "the hour suggests sleep. you've never been good at suggestions.",
    ],
}


def get_fallback(trigger):
    lines = FALLBACK_LINES.get(trigger, ["..."])
    return random.choice(lines)


class TriggerEngine:
    def __init__(self, session_id, on_trigger=None):
        self.session_id = session_id
        self.on_trigger = on_trigger
        self.fired_today = set()
        self._l0 = None
        self._last_fired_at: datetime | None = None
        self._fired_day = date.today()
        self.model_status = "standby"

    def _reset_if_new_day(self, now: datetime) -> None:
        if now.date() != self._fired_day:
            self.fired_today.clear()
            self._fired_day = now.date()

    def _is_on_cooldown(self) -> bool:
        if self._last_fired_at is None:
            return False
        return (datetime.now() - self._last_fired_at) < timedelta(minutes=COOLDOWN_MINUTES)

    def _get_l0(self):
        if self._l0 is None:
            try:
                from models.l0 import generate

                self._l0 = generate
            except Exception:
                self._l0 = False
                self.model_status = "offline"
        return self._l0 if self._l0 else None

    def _generate_message(self, trigger: str, context: dict) -> dict:
        l0 = self._get_l0()
        if l0:
            self.model_status = "active"
            try:
                result = l0(trigger, context, return_metadata=True)
                if isinstance(result, dict) and result.get("content"):
                    self.model_status = "standby"
                    return {
                        "content": result["content"],
                        "thought": result.get("thought"),
                        "raw_response": result.get("raw_response"),
                        "model": result.get("model"),
                        "source": "l0_trigger",
                    }
                if isinstance(result, str) and result:
                    self.model_status = "standby"
                    return {"content": result, "source": "l0_trigger"}
            except Exception as e:
                print(f"[TriggerEngine] L0 unavailable: {e}")
            self.model_status = "error"
        return {"content": get_fallback(trigger), "source": "fallback_trigger"}

    def check(self, states: set, session_stats: dict, mood: dict | None = None):
        now = datetime.now()
        self._reset_if_new_day(now)

        returning_from_afk = "afk_return" in states
        if self._is_on_cooldown() and not returning_from_afk:
            return None

        trigger = None
        hour = now.hour
        minutes = session_stats.get("active_minutes", 0)

        if returning_from_afk:
            trigger = "afk_return"
        elif (
            "grinding" in states
            and "late_night" in states
            and "late_night_grinding" not in self.fired_today
        ):
            trigger = "late_night_grinding"
        elif "grinding" in states and "grinding" not in self.fired_today:
            trigger = "grinding"
        elif "scattered" in states and "scattered" not in self.fired_today:
            trigger = "scattered"
        elif "idle_loop" in states and "idle_loop" not in self.fired_today:
            trigger = "idle_loop"
        elif "late_night" in states and "late_night" not in self.fired_today:
            trigger = "late_night"

        if not trigger:
            return None

        self._last_fired_at = datetime.now()
        self.fired_today.add(trigger)

        context = {
            "states": list(states),
            "active_min": minutes,
            "apps": session_stats.get("apps", []),
            "hour": hour,
            "mood": mood or {"energy": 0.5, "focus": 0.5, "openness": 0.5},
            "traits": session_stats.get("traits"),
        }

        turn_id = uuid.uuid4().hex
        payload = self._generate_message(trigger, context)
        message = payload["content"]
        log_trigger(trigger, message, self.session_id)
        log_chat_message(
            session_id=self.session_id,
            role="assistant",
            content=message,
            source=payload.get("source", "l0_trigger"),
            turn_id=turn_id,
            thought=payload.get("thought"),
            raw_response=payload.get("raw_response"),
            model=payload.get("model"),
            trigger=trigger,
            mood=context.get("mood"),
        )

        if self.on_trigger:
            self.on_trigger(trigger, message, payload)

        print(f"[Trigger] {trigger} -> {message}")
        return trigger, message

    def reset_day(self):
        self.fired_today.clear()
        self._fired_day = date.today()
