import asyncio
import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from datetime import datetime

import websockets

from agent.gate import GatePolicy
from agent.loop import AgentLoop
from memory.action_log import ActionLogStore
from memory.retriever import MemoryRetriever
from memory.store import MemoryStore
from memory.writer import MemoryWriter
from models.agent_provider import OllamaAgentProvider
from monitor import db as monitor_db
from monitor.db import (
    close_stale_sessions,
    end_session,
    get_chat_log,
    init_db,
    log_chat_message,
    log_state,
    start_session,
)
from monitor.session_tracker import SessionTracker
from monitor.state_modifiers import states_to_activity_hints
from monitor.system_tracker import SystemTracker
from monitor.trigger_engine import TriggerEngine
from monitor.window_tracker import WindowTracker, get_app_time, get_switch_count
from mood.engine import MoodEngine
from personality.traits import TRAIT_KEYS, apply_preset, load_presets, load_traits, save_traits
from routing.agent_config import load_agent_runtime_config, log_agent_runtime_config
from routing.agent_service import AgentService, LIVE_OBSERVATION_CAP_CHARS
from routing.pre_router import PreRouter, Route
from skills import build_core_registry
from telemetry.pipeline_log import pipeline_logger


HOST = "localhost"
PORT = 7474
BROADCAST_INTERVAL = 0.1
TELEMETRY_INTERVAL = 0.5
MONITOR_INTERVAL = 5.0

GRINDING_MINUTES = 120
SCATTERED_SWITCHES = 5
LATE_NIGHT_START = 23
LATE_NIGHT_END = 5
STATE_NUDGES = {
    "grinding": ("x", +1.5),
    "scattered": ("y", -2.0),
    "deep_work": ("y", +1.5),
    "idle_loop": ("y", -1.0),
    "late_night": ("x", -1.0),
    "afk": ("z", +1.5),
    "gaming": ("x", +1.0),
}

FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in FALSE_ENV_VALUES


def configure_output_encoding():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


class EmiyaServer:
    def __init__(self):
        init_db()
        stale_sessions = close_stale_sessions()
        if stale_sessions:
            print(f"[DB] closed {stale_sessions} stale session(s)")
        self.session_id = start_session()
        self.session_tracker = SessionTracker(self.session_id)
        self.window_tracker = WindowTracker(self.session_id, interval=5)
        self.system_tracker = SystemTracker(interval=30)
        self.trigger_engine = TriggerEngine(
            session_id=self.session_id,
            on_trigger=self.on_emiya_speak,
        )
        self.mood_engine = MoodEngine()
        self.memory_store = MemoryStore()
        self.memory_store.init_schema()
        self.memory_writes_enabled = env_flag("EMIYA_MEMORY_WRITES", default=True)
        self.memory_writer = MemoryWriter(self.memory_store, enabled=self.memory_writes_enabled)
        self.memory_retriever = MemoryRetriever(self.memory_store)
        self.pipeline_dump_enabled = env_flag("EMIYA_PIPELINE_DUMP", default=False)
        self.traits = load_traits()
        self.personality_presets = load_presets()
        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._started_monotonic = time.monotonic()
        self.last_sys = {}
        self.clients = set()
        self.telemetry_clients = set()
        self.pending_message = None
        self.chat_history = []
        self._l1 = None
        self._l1_status = "standby"
        self._last_reply_metadata = {}
        self._last_states = set()
        self._current_states = {"normal"}
        self._current_apps = []
        self._current_stats = self.session_tracker.get_stats()
        self._chat_lock = None
        self._chat_log_dirty = threading.Event()
        self.mood_influence = deque(maxlen=50)
        self._shutdown_complete = False
        self.pre_router = PreRouter(lambda: self.last_sys or None)
        self.agent_service = self._build_agent_service()

    def get_l1(self):
        if self._l1 is None:
            try:
                from models.l1 import chat

                self._l1 = chat
            except Exception:
                self._l1 = False
                self._l1_status = "offline"
        return self._l1 if self._l1 else None

    def _build_agent_service(self) -> AgentService:
        from models import l1

        config = load_agent_runtime_config()
        log_agent_runtime_config(config)

        self._agent_provider_calls = deque(maxlen=16)
        provider = OllamaAgentProvider(
            host=config.ollama_host,
            model=config.model,
            observer=self._agent_provider_calls.append,
        )
        registry = build_core_registry(lambda: self.last_sys)
        loop = AgentLoop(
            provider,
            registry,
            GatePolicy(),
            action_log=ActionLogStore(monitor_db.DB_PATH),
            observation_cap_chars=LIVE_OBSERVATION_CAP_CHARS,
        )

        def consume_provider_call():
            if not self._agent_provider_calls:
                return None
            return self._agent_provider_calls.popleft()

        return AgentService(
            loop,
            allowed_roots=list(config.allowed_roots),
            voice_fn=l1.voice_finalize,
            conversation_store=self.memory_writer.write_conversation,
            pipeline_logger=pipeline_logger,
            mood_provider=self._mood_context,
            traits_provider=lambda: self.traits.to_dict(),
            voice_model=l1.MODEL,
            provider_call_getter=consume_provider_call,
            pipeline_dump_enabled=self.pipeline_dump_enabled,
        )

    def analyze_state(self):
        states = set()
        stats = self.session_tracker.get_stats()

        if stats["is_afk"]:
            states.add("afk")
            return states

        switches = get_switch_count(self.session_id, minutes=10)
        apps = get_app_time(self.session_id, minutes=30)

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

    def apply_mood_nudges(self, states: set):
        new_states = states - self._last_states
        for state in new_states:
            if state in STATE_NUDGES:
                axis, delta = STATE_NUDGES[state]
                self.mood_engine.nudge(axis, delta)
                self.mood_influence.append(
                    {
                        "source": state,
                        "axis": axis,
                        "delta": delta,
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                    }
                )
                print(f"[Mood] nudge from '{state}': {axis} {delta:+.1f}")
                try:
                    self.memory_writer.write_observation(
                        f"state detected: {state}",
                        mood_snapshot=self._mood_context(),
                        tags=[state],
                    )
                except Exception as e:
                    print(f"[Memory] observation write error: {e}")
        self._last_states = states

    def on_emiya_speak(self, trigger, message, payload=None):
        payload = payload or {}
        self.pending_message = {
            "trigger": trigger,
            "message": message,
            "source": payload.get("source", "fallback_trigger"),
            "model": payload.get("model"),
            "thought": payload.get("thought"),
        }
        self._chat_log_dirty.set()
        self.chat_history.append({"role": "assistant", "content": message})
        try:
            self.memory_writer.write_trigger_event(
                trigger,
                message,
                mood_snapshot=self._mood_context(),
            )
        except Exception as e:
            print(f"[Memory] trigger write error: {e}")
        print(f"\n[EMIYA] {message}\n")

    def on_system_update(self, snapshot):
        self.last_sys = snapshot

    def handle_user_message(self, text):
        route = self.pre_router.classify(text)
        if route.route is Route.AGENT:
            turn_id = uuid.uuid4().hex
            result = asyncio.run(
                self.agent_service.run(
                    original_user_message=text,
                    agent_task=route.task,
                    run_id=turn_id,
                )
            )
            self._record_routed_exchange(
                text,
                result.reply,
                turn_id=turn_id,
                source="agent",
                route="agent",
                model=self.agent_service.loop.provider.name,
            )
            return result.reply

        if route.route is Route.CACHED or route.response is not None:
            return self._handle_deterministic_route(text, route)

        response = self._handle_chat_message(text)
        self._last_reply_metadata["route"] = "chat"
        return response

    def _record_routed_exchange(
        self,
        user_text: str,
        reply: str,
        *,
        turn_id: str,
        source: str,
        route: str,
        model: str | None = None,
    ) -> None:
        mood = self._mood_context()
        self.chat_history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply},
            ]
        )
        log_chat_message(
            session_id=self.session_id,
            role="user",
            content=user_text,
            source="user",
            turn_id=turn_id,
            mood=mood,
        )
        log_chat_message(
            session_id=self.session_id,
            role="assistant",
            content=reply,
            source=source,
            turn_id=turn_id,
            model=model,
            mood=mood,
        )
        self._chat_log_dirty.set()
        self._last_reply_metadata = {
            "source": source,
            "model": model,
            "thought": None,
            "route": route,
        }

    def _handle_deterministic_route(self, text, route):
        turn_id = uuid.uuid4().hex
        reply = route.response or "telemetry is unavailable."
        pipeline_logger.start_request(turn_id, text, {"route": route.route.value})
        pipeline_logger.add_step(turn_id, "INPUT", details={"chars": len(text)})
        pipeline_logger.add_step(
            turn_id,
            "ROUTE",
            details={"route": route.route.value, "intent": route.intent},
        )
        if route.route is Route.CACHED:
            pipeline_logger.add_step(
                turn_id,
                "CACHED",
                details={"intent": route.intent, "response": reply[:500]},
            )
        source = "cached" if route.route is Route.CACHED else "router"
        self._record_routed_exchange(
            text,
            reply,
            turn_id=turn_id,
            source=source,
            route=route.route.value,
        )
        pipeline_logger.add_step(
            turn_id,
            "OUT",
            details={"chars": len(reply), "source": source},
        )
        pipeline_logger.finish_request(
            turn_id,
            "ok",
            dump=self.pipeline_dump_enabled,
        )
        return reply

    def _handle_chat_message(self, text):
        turn_id = uuid.uuid4().hex
        mood = self._mood_context()
        context = self._build_context(user_text=text)
        pipeline_logger.start_request(turn_id, text, context)
        pipeline_logger.add_step(
            turn_id,
            "INPUT",
            details={"chars": len(text), "history": len(self.chat_history)},
        )

        self.chat_history.append({"role": "user", "content": text})
        log_chat_message(
            session_id=self.session_id,
            role="user",
            content=text,
            source="user",
            turn_id=turn_id,
            mood=mood,
        )
        self._chat_log_dirty.set()

        l1_fn = self.get_l1()
        if l1_fn:
            self._l1_status = "active"
            try:
                started = time.perf_counter()
                result = l1_fn(self.chat_history[-10:], context, return_metadata=True)
                latency_ms = (time.perf_counter() - started) * 1000
                if isinstance(result, dict):
                    response = result.get("content")
                    thought = result.get("thought")
                    raw_response = result.get("raw_response")
                    model = result.get("model")
                    system_prompt = result.get("system_prompt")
                    mood_seed = result.get("mood_seed")
                    metrics = result.get("metrics")
                else:
                    response = result
                    thought = None
                    raw_response = None
                    model = None
                    system_prompt = None
                    mood_seed = None
                    metrics = None

                pipeline_logger.add_step(
                    turn_id,
                    "L1",
                    latency_ms=latency_ms,
                    details={
                        "model": model,
                        "mood_seed": mood_seed,
                        "metrics": metrics,
                        "thought": thought,
                        "raw_response": raw_response,
                        "system_prompt": system_prompt,
                    },
                )

                if response:
                    self._l1_status = "standby"
                    self._last_reply_metadata = {
                        "source": "l1",
                        "model": model,
                        "thought": thought,
                    }
                    self.chat_history.append({"role": "assistant", "content": response})
                    log_chat_message(
                        session_id=self.session_id,
                        role="assistant",
                        content=response,
                        source="l1",
                        turn_id=turn_id,
                        thought=thought,
                        raw_response=raw_response,
                        model=model,
                        mood=context.get("mood"),
                    )
                    self._chat_log_dirty.set()
                    self.memory_writer.write_conversation(
                        text,
                        response,
                        mood_snapshot=context.get("mood"),
                        tags=["l1"],
                        turn_id=turn_id,
                    )
                    pipeline_logger.add_step(
                        turn_id,
                        "OUT",
                        details={"chars": len(response), "source": "l1"},
                    )
                    pipeline_logger.finish_request(turn_id, "ok", dump=self.pipeline_dump_enabled)
                    return response
            except Exception as e:
                print(f"[L1] error: {e}")
                pipeline_logger.add_step(turn_id, "L1", status="error", details={"error": str(e)})
            self._l1_status = "error"
        else:
            self._l1_status = "offline"

        log_chat_message(
            session_id=self.session_id,
            role="assistant",
            content="...",
            source="fallback",
            turn_id=turn_id,
            mood=mood,
        )
        self._chat_log_dirty.set()
        self._last_reply_metadata = {"source": "fallback", "model": None, "thought": None}
        self.memory_writer.write_conversation(
            text,
            "...",
            mood_snapshot=mood,
            importance=0.2,
            tags=["fallback"],
            turn_id=turn_id,
        )
        pipeline_logger.add_step(turn_id, "OUT", details={"chars": 3, "source": "fallback"})
        pipeline_logger.finish_request(turn_id, "fallback", dump=self.pipeline_dump_enabled)
        return "..."

    def _build_context(self, user_text: str | None = None) -> dict:
        stats = self.session_tracker.get_stats()
        states = self.analyze_state()
        apps = get_app_time(self.session_id, minutes=30)
        mood = self._mood_context()

        context = {
            "time_of_day": stats["time_of_day"],
            "active_min": stats["active_minutes"],
            "is_afk": stats["is_afk"],
            "states": list(states),
            "activity_hints": states_to_activity_hints(states),
            "apps": apps[:5],
            "cpu": self.last_sys.get("cpu_percent", 0),
            "ram": self.last_sys.get("ram_percent", 0),
            "mood": mood,
            "traits": self.traits.to_dict(),
        }

        try:
            recent = self.memory_retriever.get_recent(8)
            relevant = self.memory_retriever.search(user_text or "", limit=4)
            mood_matches = self.memory_retriever.by_mood(mood, limit=3)
            anchors = self.memory_retriever.get_anchors(limit=4)
            seen = {memory["id"] for memory in relevant}
            relevant.extend(memory for memory in mood_matches if memory["id"] not in seen)
            context["recent_memory"] = recent
            context["relevant_memory"] = relevant[:6]
            context["voice_anchors"] = anchors
        except Exception as e:
            print(f"[Memory] retrieval error: {e}")
            context["recent_memory"] = []
            context["relevant_memory"] = []
            context["voice_anchors"] = []

        return context

    def _mood_context(self) -> dict:
        mood = self.mood_engine.get_current()
        return {
            "energy": round(mood.energy, 3),
            "focus": round(mood.focus, 3),
            "openness": round(mood.openness, 3),
        }

    def build_state_packet(self):
        stats = self.session_tracker.get_stats()
        states = self._current_states
        apps = self._current_apps
        mood_state = self.mood_engine.get_state()
        sys_state = {
            "cpu_pct": self.last_sys.get("cpu_percent", 0),
            "ram_pct": self.last_sys.get("ram_percent", 0),
            "ram_used_gb": self.last_sys.get("ram_used_gb"),
            "ram_total_gb": self.last_sys.get("ram_total_gb"),
            "top_processes": self.last_sys.get("top_processes", []),
            "uptime": self._uptime(),
            "started_at": self._started_at,
        }
        params = {
            "sigma": mood_state.sigma,
            "rho": mood_state.rho,
            "beta": round(mood_state.beta, 4),
        }

        packet = {
            "type": "state_update",
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "time_of_day": stats["time_of_day"],
            "active_min": stats["active_minutes"],
            "active_minutes": stats["active_minutes"],
            "is_afk": stats["is_afk"],
            "states": list(states),
            "apps": apps[:5],
            "cpu": self.last_sys.get("cpu_percent", 0),
            "ram": self.last_sys.get("ram_percent", 0),
            "sys": sys_state,
            "params": params,
            "models": {
                "L-meta": "inactive",
                "L0": self.trigger_engine.model_status,
                "L1": self._l1_status,
                "L2": "inactive",
            },
            "memory": {"writes_enabled": self.memory_writes_enabled},
            "emiya": self.pending_message,
            "traits": self.traits.to_dict(),
            "personality_presets": list(self.personality_presets.keys()),
            "pipeline": pipeline_logger.recent(20, compact=True),
            "influence": list(self.mood_influence),
            "mood": {
                "x": round(mood_state.x, 4),
                "y": round(mood_state.y, 4),
                "z": round(mood_state.z, 4),
                "energy": round(mood_state.energy, 3),
                "focus": round(mood_state.focus, 3),
                "openness": round(mood_state.openness, 3),
                "raw_x": round(mood_state.raw_x, 2),
                "raw_y": round(mood_state.raw_y, 2),
                "raw_z": round(mood_state.raw_z, 2),
                "sigma": params["sigma"],
                "rho": params["rho"],
                "beta": params["beta"],
                "timestamp": mood_state.timestamp,
            },
            "trail": mood_state.trail[-200:],
        }
        self.pending_message = None
        return packet

    def _uptime(self) -> str:
        elapsed = max(0, int(time.monotonic() - self._started_monotonic))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def build_chat_log_packet():
        return {
            "type": "chat_log_update",
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "entries": get_chat_log(100),
        }

    async def broadcast(self, packet):
        if not self.clients:
            return
        msg = json.dumps(packet, ensure_ascii=False)
        await asyncio.gather(
            *[client.send(msg) for client in self.clients],
            return_exceptions=True,
        )

    def build_telemetry_packet(self):
        return {
            "type": "telemetry_update",
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "pipeline": pipeline_logger.recent(100, compact=False),
        }

    async def broadcast_telemetry(self):
        if not self.telemetry_clients:
            return
        msg = json.dumps(self.build_telemetry_packet(), ensure_ascii=False)
        await asyncio.gather(
            *[client.send(msg) for client in self.telemetry_clients],
            return_exceptions=True,
        )

    def _update_traits(self, patch: dict) -> None:
        clean_patch = {key: patch[key] for key in TRAIT_KEYS if key in patch}
        self.traits = save_traits(self.traits.updated(clean_patch))

    @staticmethod
    def _websocket_path(websocket) -> str:
        request = getattr(websocket, "request", None)
        if request is not None:
            path = getattr(request, "path", None)
            if path:
                return path
        return getattr(websocket, "path", "/") or "/"

    async def telemetry_handler(self, websocket):
        self.telemetry_clients.add(websocket)
        print("[WS] telemetry client connected")
        try:
            await websocket.send(json.dumps(self.build_telemetry_packet(), ensure_ascii=False))
            async for msg in websocket:
                data = json.loads(msg)
                if data.get("type") == "telemetry_request":
                    await websocket.send(json.dumps(self.build_telemetry_packet(), ensure_ascii=False))
        except Exception as e:
            print(f"[WS] telemetry handler error: {e}")
        finally:
            self.telemetry_clients.discard(websocket)
            print("[WS] telemetry client disconnected")

    async def handler(self, websocket):
        if self._websocket_path(websocket) == "/ws/telemetry":
            await self.telemetry_handler(websocket)
            return

        self.clients.add(websocket)
        print("[WS] client connected")
        try:
            await websocket.send(json.dumps(self.build_chat_log_packet(), ensure_ascii=False))
            async for msg in websocket:
                data = json.loads(msg)

                if data.get("type") == "user_message":
                    text = data.get("text", "")
                    print(f"[USER] {text}")
                    if self._chat_lock is None:
                        self._chat_lock = asyncio.Lock()
                    async with self._chat_lock:
                        response = await asyncio.to_thread(self.handle_user_message, text)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "emiya_reply",
                                "message": response,
                                **self._last_reply_metadata,
                            },
                            ensure_ascii=False,
                        )
                    )

                elif data.get("type") == "mood_params":
                    sigma = data.get("sigma")
                    rho = data.get("rho")
                    beta = data.get("beta")
                    self.mood_engine.set_params(sigma=sigma, rho=rho, beta=beta)
                    print(f"[Mood] params updated: sigma={sigma} rho={rho} beta={beta}")

                elif data.get("type") == "mood_preset":
                    name = data.get("name", "standard")
                    ok = self.mood_engine.set_preset(name)
                    print(f"[Mood] preset '{name}': {'ok' if ok else 'not found'}")

                elif data.get("type") == "personality_update":
                    self._update_traits(data.get("traits") or data)
                    print(f"[Traits] updated: {self.traits.to_dict()}")

                elif data.get("type") == "personality_preset":
                    name = data.get("name", "default")
                    try:
                        self.traits = apply_preset(name)
                        print(f"[Traits] preset '{name}': ok")
                    except ValueError:
                        print(f"[Traits] preset '{name}': not found")

        except Exception as e:
            print(f"[WS] handler error: {e}")
        finally:
            self.clients.discard(websocket)
            print("[WS] client disconnected")

    def run_trackers(self):
        threading.Thread(target=self.window_tracker.start, daemon=True).start()
        threading.Thread(
            target=lambda: self.system_tracker.start(callback=self.on_system_update),
            daemon=True,
        ).start()

    def monitor_tick(self):
        activity_transition = self.session_tracker.poll_activity()
        states = self.analyze_state()
        if activity_transition == "afk_return":
            states.add("afk_return")
        stats = self.session_tracker.get_stats()
        apps = get_app_time(self.session_id, minutes=30)

        self._current_states = states
        self._current_stats = stats
        self._current_apps = apps[:5]

        for state in states:
            log_state(state, self.session_id)

        self.apply_mood_nudges(states)

        trigger_context = {**stats, "apps": apps, "traits": self.traits.to_dict()}
        self.trigger_engine.check(
            states,
            trigger_context,
            mood=self._mood_context(),
        )

    async def monitor_loop(self):
        while True:
            await asyncio.to_thread(self.monitor_tick)
            await asyncio.sleep(MONITOR_INTERVAL)

    async def broadcast_loop(self):
        while True:
            if self._chat_log_dirty.is_set():
                self._chat_log_dirty.clear()
                await self.broadcast(self.build_chat_log_packet())
            await self.broadcast(self.build_state_packet())
            await asyncio.sleep(BROADCAST_INTERVAL)

    async def telemetry_loop(self):
        while True:
            await self.broadcast_telemetry()
            await asyncio.sleep(TELEMETRY_INTERVAL)

    async def loop(self):
        await asyncio.gather(
            self.monitor_loop(),
            self.broadcast_loop(),
            self.telemetry_loop(),
        )

    async def main(self):
        self.run_trackers()
        mood_task = asyncio.create_task(self.mood_engine.run())
        print("[Mood] engine started")
        if not self.memory_writes_enabled:
            print("[Memory] writes disabled by EMIYA_MEMORY_WRITES=0")
        if self.pipeline_dump_enabled:
            print("[Telemetry] pipeline JSONL dump enabled")

        print(f"[EMIYA] server -> ws://{HOST}:{PORT}")
        try:
            async with websockets.serve(self.handler, HOST, PORT):
                await self.loop()
        finally:
            self.shutdown()
            mood_task.cancel()
            with suppress(asyncio.CancelledError):
                await mood_task

    def shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.window_tracker.stop()
        self.system_tracker.stop()
        self.mood_engine.stop()
        end_session(self.session_id)


if __name__ == "__main__":
    configure_output_encoding()
    server = EmiyaServer()
    asyncio.run(server.main())
