/**
 * EMIYA root — BIOS-style frontend
 *
 * Architecture:
 *   - WebSocket to ws://localhost:7474
 *   - tab routing: MONITOR / CHAT / PATTERNS / LOG
 *   - the right side-zone is shared by MONITOR and CHAT so telemetry stays visible.
 *
 * Components in src/components/ stay isolated and easy to change.
 *
 * Backend contract (state_update packet):
 *   {
 *     mood:    { energy, focus, openness, raw_x, raw_y, raw_z },
 *     trail:   [...],
 *     params:  { sigma, rho, beta },
 *     models:  { 'L-meta', L0, L1, L2 },
 *     sys:     { cpu_pct, ram_pct, vram_pct, gpu_temp_c, uptime },
 *     apps:    [{ app, type, minutes }],
 *     states:  [string],
 *     active_minutes: number,
 *     influence: [{ source, axis, delta, timestamp }]   // optional
 *   }
 *
 *   chat_log packet:
 *   { type: 'chat_log_update', entries: [...] }
 */

import { useEffect, useRef, useState } from 'react';

import BiosHeader     from './components/BiosHeader';
import LorenzPanel    from './components/LorenzPanel';
import ParamsReadout  from './components/ParamsReadout';
import MoodInfluence  from './components/MoodInfluence';
import ModelsPanel    from './components/ModelsPanel';
import PersonalityPanel from './components/PersonalityPanel';
import PipelineView    from './components/PipelineView';
import SystemPanel    from './components/SystemPanel';
import WindowsPanel   from './components/WindowsPanel';
import AsciiArtZone   from './components/AsciiArtZone';
import BankBlock      from './components/BankBlock';
import ChatPanel      from './components/ChatPanel';
import LogPanel       from './components/LogPanel';
import PatternsPanel  from './components/PatternsPanel';

import './styles/bios.css';
import './styles/crt.css';

const WS_URL = 'ws://localhost:7474';
const TELEMETRY_WS_URL = `${WS_URL}/ws/telemetry`;

const TABS = [
  { id: 'monitor',  label: 'MONITOR'  },
  { id: 'chat',     label: 'CHAT'     },
  { id: 'patterns', label: 'PATTERNS' },
  { id: 'log',      label: 'LOG'      },
];

const DEFAULT_TRAITS = {
  curiosity: 70,
  bluntness: 80,
  warmth: 40,
  sarcasm: 60,
  formality: 20,
};

const DEFAULT_PERSONALITY_PRESETS = ['default', 'unhinged', 'professional', 'tired friend'];
const MODEL_RECENT_MS = 60_000;
const DEFAULT_MODELS = {
  'L-meta': 'inactive',
  L0: 'standby',
  L1: 'standby',
  L2: 'inactive',
};

const hasNumber = (v) => typeof v === 'number' && Number.isFinite(v);

const normalizeMood = (mood) => {
  if (!mood) return null;
  return {
    ...mood,
    raw_x: mood.raw_x ?? mood.x,
    raw_y: mood.raw_y ?? mood.y,
    raw_z: mood.raw_z ?? mood.z,
  };
};

const normalizeParams = (payload) => {
  if (payload.params) return payload.params;
  const mood = payload.mood;
  if (!mood || !hasNumber(mood.sigma) || !hasNumber(mood.rho) || !hasNumber(mood.beta)) {
    return null;
  }
  return {
    sigma: mood.sigma,
    rho: mood.rho,
    beta: mood.beta,
  };
};

const normalizeSys = (payload) => {
  if (payload.sys) return payload.sys;
  const sys = {};
  if (hasNumber(payload.cpu)) sys.cpu_pct = payload.cpu;
  if (hasNumber(payload.ram)) sys.ram_pct = payload.ram;
  if (hasNumber(payload.cpu_percent)) sys.cpu_pct = payload.cpu_percent;
  if (hasNumber(payload.ram_percent)) sys.ram_pct = payload.ram_percent;
  return Object.keys(sys).length ? sys : null;
};

const normalizeApps = (apps) =>
  (apps ?? []).map((app) => ({
    ...app,
    type: app.type ?? app.category ?? 'other',
  }));

const toTriggerEvent = (emiya, timestamp) => {
  if (!emiya?.message) return null;
  return {
    timestamp: timestamp ?? new Date().toISOString(),
    trigger: emiya.trigger ?? 'l0',
    message: emiya.message,
    source: emiya.source ?? 'fallback_trigger',
    model: emiya.model ?? null,
    thought: emiya.thought ?? null,
  };
};

const autonomousModelLabel = (event) => {
  if (event?.model) return event.model;
  return event?.source === 'l0_trigger' ? 'L0' : 'FALLBACK';
};

const applyModelStatuses = (incoming, backendStatusesRef, timersRef, setModels) => {
  const displayPatch = {};

  Object.entries(incoming ?? {}).forEach(([id, status]) => {
    const previousBackendStatus = backendStatusesRef.current[id];
    const existingTimer = timersRef.current[id];

    if (status === 'active') {
      clearTimeout(existingTimer);
      delete timersRef.current[id];
      displayPatch[id] = 'active';
    } else if (status === 'standby' && previousBackendStatus === 'active') {
      clearTimeout(existingTimer);
      displayPatch[id] = 'recent';
      timersRef.current[id] = setTimeout(() => {
        delete timersRef.current[id];
        setModels((current) => (
          current[id] === 'recent' ? { ...current, [id]: 'standby' } : current
        ));
      }, MODEL_RECENT_MS);
    } else if (status === 'standby' && existingTimer) {
      displayPatch[id] = 'recent';
    } else {
      clearTimeout(existingTimer);
      delete timersRef.current[id];
      displayPatch[id] = status;
    }

    backendStatusesRef.current[id] = status;
  });

  setModels((current) => ({ ...current, ...displayPatch }));
};

export default function App() {
  /* ─── tab state ─── */
  const [activeTab, setActiveTab] = useState('chat');

  /* ─── connection ─── */
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const telemetryConnectedRef = useRef(false);
  const backendModelStatusesRef = useRef(DEFAULT_MODELS);
  const modelStatusTimersRef = useRef({});

  /* ─── live state ─── */
  const [trail,         setTrail]         = useState([]);
  const [currentMood,   setCurrentMood]   = useState(null);
  const [params,        setParams]        = useState({ sigma: 10, rho: 28, beta: 2.667 });
  const [models,        setModels]        = useState(DEFAULT_MODELS);
  const [sys,           setSys]           = useState({});
  const [apps,          setApps]          = useState([]);
  const [states,        setStates]        = useState([]);
  const [activeMinutes, setActiveMinutes] = useState(0);
  const [influence,     setInfluence]     = useState([]);
  const [moodHistory,   setMoodHistory]   = useState([]);
  const [traits,        setTraits]        = useState(DEFAULT_TRAITS);
  const [personalityPresets, setPersonalityPresets] = useState(DEFAULT_PERSONALITY_PRESETS);
  const [pipeline,      setPipeline]      = useState([]);

  /* ─── chat ─── */
  const [messages,      setMessages]      = useState([]);
  const [chatLog,       setChatLog]       = useState([]);
  const [triggerEvents, setTriggerEvents] = useState([]);
  const [isWaiting,     setIsWaiting]     = useState(false);

  /* ─── ASCII canvas toggle ─── */
  const [asciiMode, setAsciiMode] = useState(false);

  /* ─── session timer ─── */
  const [sessionTime, setSessionTime] = useState('00:00:00');
  useEffect(() => {
    const tick = () => {
      const d = new Date();
      setSessionTime(d.toTimeString().slice(0, 8));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  /* ─── WebSocket ─── */
  useEffect(() => {
    let ws;
    let reconnectTimer;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        console.log('[ws] connected');
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          if (data.type === 'state_update' || data.mood) {
            const payload = data.payload ?? data;

            if (payload.mood) {
              const mood = normalizeMood(payload.mood);
              setCurrentMood(mood);
              setMoodHistory((h) => [
                ...h.slice(-200),
                { ...mood, timestamp: payload.mood.timestamp ?? payload.timestamp ?? new Date().toISOString() },
              ]);
            }
            if (payload.trail)        setTrail(payload.trail);
            const nextParams = normalizeParams(payload);
            if (nextParams)           setParams(nextParams);
            if (payload.models) {
              applyModelStatuses(
                payload.models,
                backendModelStatusesRef,
                modelStatusTimersRef,
                setModels,
              );
            }
            const nextSys = normalizeSys(payload);
            if (nextSys)              setSys((s) => ({ ...s, ...nextSys }));
            if (payload.apps)         setApps(normalizeApps(payload.apps));
            if (payload.states)       setStates(payload.states);
            const active = payload.active_minutes ?? payload.active_min;
            if (typeof active === 'number') setActiveMinutes(active);
            if (payload.influence)    setInfluence(payload.influence);
            if (payload.traits)       setTraits((current) => ({ ...current, ...payload.traits }));
            if (Array.isArray(payload.personality_presets)) setPersonalityPresets(payload.personality_presets);
            if (payload.pipeline && !telemetryConnectedRef.current) setPipeline(payload.pipeline);

            const autonomous = toTriggerEvent(payload.emiya, payload.timestamp);
            if (autonomous) {
              setTriggerEvents((t) => [...t.slice(-50), autonomous]);
              setMessages((m) => [
                ...m,
                {
                  role: 'emiya',
                  content:   autonomous.message,
                  timestamp: autonomous.timestamp,
                  model:     autonomousModelLabel(autonomous),
                  thought:   autonomous.thought,
                  trigger:   autonomous.trigger,
                  source:    autonomous.source,
                },
              ]);
            }
          }

          if (data.type === 'emiya_reply') {
            setIsWaiting(false);
            setMessages((m) => [
              ...m,
              {
                role: 'emiya',
                content:   data.message,
                timestamp: new Date().toISOString(),
                model:     data.model ?? (data.source === 'fallback' ? 'FALLBACK' : 'L1'),
                thought:   data.thought   ?? null,
                source:    data.source    ?? 'l1',
              },
            ]);
          }

          if (data.type === 'trigger_event') {
            const ev = {
              timestamp: new Date().toISOString(),
              trigger:   data.trigger,
              message:   data.message,
            };
            setTriggerEvents((t) => [...t.slice(-50), ev]);
            /* Autonomous L0 lines also appear in chat. */
            setMessages((m) => [
              ...m,
              {
                role: 'emiya',
                content:   data.message,
                timestamp: new Date().toISOString(),
                model:     data.model ?? (data.source === 'l0_trigger' ? 'L0' : 'FALLBACK'),
                thought:   data.thought ?? null,
                trigger:   data.trigger,
                source:    data.source ?? 'fallback_trigger',
              },
            ]);
          }

          if (data.type === 'chat_log_update' && Array.isArray(data.entries)) {
            setChatLog(data.entries);
          }
        } catch (err) {
          console.error('[ws] parse error', err);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log('[ws] closed, reconnect in 3s');
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };

      ws.onerror = (err) => {
        console.error('[ws] error', err);
      };
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  useEffect(() => () => {
    Object.values(modelStatusTimersRef.current).forEach(clearTimeout);
  }, []);

  /* pipeline telemetry channel */
  useEffect(() => {
    let ws;
    let reconnectTimer;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(TELEMETRY_WS_URL);

      ws.onopen = () => {
        telemetryConnectedRef.current = true;
        ws.send(JSON.stringify({ type: 'telemetry_request' }));
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === 'telemetry_update' && Array.isArray(data.pipeline)) {
            setPipeline(data.pipeline);
          }
        } catch (err) {
          console.error('[telemetry] parse error', err);
        }
      };

      ws.onclose = () => {
        telemetryConnectedRef.current = false;
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };

      ws.onerror = (err) => {
        console.error('[telemetry] error', err);
      };
    };

    connect();

    return () => {
      cancelled = true;
      telemetryConnectedRef.current = false;
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  /* ─── send chat message ─── */
  const handleSend = (text) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    setMessages((m) => [
      ...m,
      { role: 'user', content: text, timestamp: new Date().toISOString() },
    ]);
    setIsWaiting(true);
    wsRef.current.send(JSON.stringify({ type: 'user_message', text }));
  };

  const handleTraitsChange = (nextTraits) => {
    setTraits(nextTraits);
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'personality_update', traits: nextTraits }));
  };

  const handleTraitsPreset = (name) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'personality_preset', name }));
  };

  /* ─── BANK content ─── */
  /* BANK_1: Emiya internal state: mood zones plus last trigger. */
  const lastTrigger = triggerEvents.length > 0 ? triggerEvents[triggerEvents.length - 1] : null;
  const moodComboLabel = (() => {
    if (!currentMood) return '—';
    const z = (v) => (v < 0.4 ? 'low' : v < 0.6 ? 'mid' : 'high');
    return `${z(currentMood.energy)} · ${z(currentMood.focus)} · ${z(currentMood.openness)}`;
  })();

  const bank1Lines = [
    { text: `STATE  ${moodComboLabel}` },
    { text: `RAW    x${currentMood?.raw_x?.toFixed(2) ?? '—'} y${currentMood?.raw_y?.toFixed(2) ?? '—'}`, muted: true },
    lastTrigger
      ? { text: `LAST   ${lastTrigger.trigger}` }
      : { text: 'LAST   —', muted: true },
  ];

  /* BANK_2: user external context: top app plus detected state. */
  const topApp    = apps[0]?.app?.replace(/\.exe$/i, '') ?? '—';
  const topMin    = apps[0]?.minutes?.toFixed(1) ?? '0.0';
  const stateText = states && states.length > 0 ? states[0] : 'normal';
  const bank2Lines = [
    { text: `FOCUS  ${topApp}` },
    { text: `TIME   ${topMin}m`, muted: true },
    { text: `STATE  ${stateText}` },
  ];

  /* ─── render tab content ─── */
  const renderMain = () => {
    if (activeTab === 'chat') {
      return <ChatPanel messages={messages} onSend={handleSend} isWaiting={isWaiting} />;
    }
    if (activeTab === 'log') {
      return <LogPanel moodHistory={moodHistory} chatLog={chatLog} triggerEvents={triggerEvents} />;
    }
    if (activeTab === 'patterns') {
      return <PatternsPanel />;
    }
    /* monitor */
    return (
      <div style={{ padding: 0 }}>
        <div className="banks">
          <BankBlock title="BANK 1 · INTERNAL" lines={bank1Lines} />
          <BankBlock title="BANK 2 · EXTERNAL" lines={bank2Lines} />
        </div>

        <WindowsPanel
          apps={apps}
          states={states}
          activeMinutes={activeMinutes}
        />

        <SystemPanel sys={sys} />

        <AsciiArtZone current={currentMood} />
      </div>
    );
  };

  return (
    <div className="app-shell crt-flicker">
      {/* background logo */}
      <div className="bg-logo">EMIYA</div>

      <BiosHeader
        tabs={TABS}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        connected={connected}
        sessionTime={sessionTime}
        uptime={sys?.uptime}
      />

      <div className="app-body">
        <div className="main-zone">
          {renderMain()}
        </div>

        <aside className="side-zone" style={{ padding: 12, overflowY: 'auto' }}>
          <LorenzPanel
            trail={trail}
            current={currentMood}
            asciiMode={asciiMode}
            onToggleAscii={() => setAsciiMode(!asciiMode)}
          />
          <ParamsReadout params={params} />
          <MoodInfluence events={influence} />
          <PersonalityPanel
            traits={traits}
            presets={personalityPresets}
            onChange={handleTraitsChange}
            onPreset={handleTraitsPreset}
          />
          <ModelsPanel models={models} />
          <PipelineView runs={pipeline} />
        </aside>
      </div>

      {/* CRT scanlines + flicker overlay */}
      <div className="crt-overlay" />
    </div>
  );
}
