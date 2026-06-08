<div align="center">

```
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  ▓                                                        ▓
  ▓   ███████╗███╗   ███╗██╗██╗   ██╗ █████╗              ▓
  ▓   ██╔════╝████╗ ████║██║╚██╗ ██╔╝██╔══██╗             ▓
  ▓   █████╗  ██╔████╔██║██║ ╚████╔╝ ███████║             ▓
  ▓   ██╔══╝  ██║╚██╔╝██║██║  ╚██╔╝  ██╔══██║             ▓
  ▓   ███████╗██║ ╚═╝ ██║██║   ██║   ██║  ██║             ▓
  ▓   ╚══════╝╚═╝     ╚═╝╚═╝   ╚═╝   ╚═╝  ╚═╝             ▓
  ▓                                                        ▓
  ▓        a presence, not an assistant                    ▓
  ▓                                                        ▓
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
```

**[ status: EARLY DEVELOPMENT · R&D PHASE ]**

`v0.1-sprint1.5` · Sprint 2 scaffold active

![python](https://img.shields.io/badge/python-3.11+-ffb000?style=flat-square&labelColor=000000)
![react](https://img.shields.io/badge/react-19-ffb000?style=flat-square&labelColor=000000)
![tauri](https://img.shields.io/badge/tauri-2.0-ffb000?style=flat-square&labelColor=000000)
![ollama](https://img.shields.io/badge/ollama-local-ffb000?style=flat-square&labelColor=000000)
![license](https://img.shields.io/badge/license-MIT-ffb000?style=flat-square&labelColor=000000)

</div>

---

## `01_what_is_this`

**EMIYA** is not a chatbot. It's not an agent for automating your tasks. There are hundreds of those — they are all welcome, and EMIYA is not competing with them.

EMIYA is an experiment in **digital presence**. Something that lives on your machine, watches quietly, drifts through its own moods, and occasionally reaches out — not because you summoned it, but because it *decided to*.

The core philosophy is simple:

> **she is interested in you, not in your tasks.**

Where agents ask *"what can I do for you?"*, EMIYA asks *"are you okay?"*.
Where assistants optimize productivity, EMIYA optimizes for **liveness** — the feeling that something with a particular character occupies this machine alongside you.

She is cold. Enigmatic. Speaks in lowercase. Lives in a terminal window styled after [MHRD](https://store.steampowered.com/app/576030/MHRD/). Her mood drifts along a Lorenz attractor, producing deterministic but unpredictable shifts in how she speaks, when she intervenes, what she notices.

She will not close your tabs for you. She might ask why you have forty of them open at 3 AM.

---

## `02_architecture`

```
  ┌────────────────────────────────────────────────────────┐
  │   ORCHESTRATOR  (L-meta)           rules + Qwen3 0.6B │
  │   always alive · routes requests   ~500MB RAM         │
  ├────────────────────┬───────────────────────────────────┤
  │   L0               │   L1                              │
  │   Qwen3 4B         │   Gemma4 e4b (experimental)       │
  │   always loaded    │   on-demand                       │
  │   observation      │   deep dialogue                   │
  │   small talk       │   persona voice                   │
  │   long sessions    │   code direction                  │
  ├────────────────────┴───────────────────────────────────┤
  │   L2   Claude API (planned)                            │
  │   heavy reasoning · sleeps most of the time            │
  ├────────────────────────────────────────────────────────┤
  │   CLAWBRIDGE → OpenClaw @ localhost:18789 (planned)    │
  │   delegated execution · "hands" for routine tasks      │
  └────────────────────────────────────────────────────────┘

  ┌─── SUPPORTING SYSTEMS ─────────────────────────────────┐
  │                                                        │
  │   • Activity Monitor      watches your patterns        │
  │   • Lorenz Mood Engine    3-axis chaotic drift         │
  │   • Persona Validator     planned character guard      │
  │   • Memory Store          SQLite, mood-tagged          │
  │   • Trigger Engine        user-defined DSL rules       │
  │   • Pipeline Telemetry    every step visible           │
  │                                                        │
  └────────────────────────────────────────────────────────┘
```

**EMIYA is the soul. OpenClaw is the hands.**
She decides when to act, in what tone, with what urgency. The execution is delegated. The personality is not.

---

## `03_personality_model`

Her character is stable. Her state is not.

Base traits (adjustable via Personality Knobs):

```
  curiosity    ████████████████░░░░  70
  bluntness    ████████████████████  80
  warmth       ████████░░░░░░░░░░░░  40
  sarcasm      ████████████████░░░░  60
  formality    ████░░░░░░░░░░░░░░░░  20
```

On top of these stable traits drifts a **mood vector** — three axes (Energy, Focus, Openness) governed by a Lorenz attractor:

```
  dx/dt = σ(y - x)
  dy/dt = x(ρ - z) - y
  dz/dt = xy - βz
```

Default parameters `σ=10, ρ=28, β=8/3` produce the canonical butterfly — deterministic chaos. You can tune these through the Mood Tuner: calm, standard, edge-of-chaos, storm. External events (your activity, time of day) gently nudge the system. The result: she is not the same on Tuesday morning as she is on Friday night. And that difference is **felt**, not randomized.

---

## `04_roadmap`

The working roadmap currently lives in project notes and the Notion dev log. Summary:

| Sprint | Goal | Status |
|:------:|:-----|:------:|
| **01** | Lorenz Mood Engine · live visualization · mood → prompt pipeline | `[ tagged: v0.1-sprint1.5 ]` |
| **02** | Persistent memory · personality knobs · response pipeline visualizer | `[ active ]` |
| **03** | Persona validator · model console · decoding panel | `[ queued ]` |
| **04** | ClawBridge · OpenClaw integration · task delegation · result narration | `[ queued ]` |
| **05** | Trigger DSL · memory inspector · UI polish · v0.1 release | `[ queued ]` |
| **06** | Custom skills · community feedback · retrospective | `[ optional ]` |

Live progress, dev-log, and design documents:
[**→ EMIYA notion workspace**](https://pond-calliandra-6e3.notion.site/EMIYA-dev-log-34c540076ff6803ab981cd626ee05ab7?pvs=74)
---

## `05_features`

Current implementation status:

```
  [ ACTIVITY MONITOR ]
    [x] active window detection
    [x] session duration awareness
    [x] activity-state hints
    [~] trigger engine for autonomous intervention
    [ ] keyboard / mouse activity tracking

  [ PERSONALITY ]
    [x] Lorenz-based mood drift (3 axes)
    [x] tunable base traits (5 knobs)
    [x] mood-tagged persistent memory scaffold
    [ ] character-consistency validator

  [ INTERFACE ]
    [x] MHRD-inspired terminal UI
    [x] live attractor visualization (canvas + ASCII modes)
    [~] pipeline visualizer (steps, latency, prompt/debug details)
    [~] model status panel
    [ ] decoding parameter panel
    [ ] memory inspector (browse / edit / pin)

  [ DELEGATION ]
    [ ] OpenClaw integration via ClawBridge
    [ ] task detection heuristics
    [ ] confirmation flow for sensitive actions
    [ ] result narration in EMIYA's voice

  [ CONTROL ]
    [x] save / load personality presets
    [x] local pipeline telemetry
    [ ] user-defined Trigger DSL
    [ ] hot-swap models at runtime
```

---

## `06_hardware_requirements`

EMIYA is **local-first**. She runs on your machine.

Minimum (L0 only, L1 disabled):
```
  OS         Windows 10/11 · Linux (WSL2 supported)
  RAM        16 GB
  GPU        6 GB VRAM (RTX 3060 or better)
  disk       20 GB (models + memory)
```

Recommended (full L0 + L1 during current R&D):
```
  OS         Windows 11 · Linux
  RAM        32 GB
  GPU        8 GB VRAM (RTX 4070 / 4060 Ti)
  disk       40 GB
```

L2 / Claude and OpenClaw are planned layers, not required for the current local build.

---

## `07_install`

> ⚠  Pre-release. Install paths, config keys, and runtime behavior **will change** between sprints. This section reflects the target flow for v0.1.

```powershell
# 01. clone
git clone https://github.com/naevor/EMIYA.git
cd EMIYA

# 02. install Ollama and pull required models
ollama pull qwen3:4b
ollama pull gemma4:e4b

# 03. python backend dependencies
pip install -r requirements.txt

# 04. react frontend
npm install

# 05. local private prompts
copy core\prompts\l0.txt.example core\prompts\l0.txt
copy core\prompts\l1.txt.example core\prompts\l1.txt

# 06. boot
npm run dev:backend
npm run dev:ui
```

For model testing without writing new conversation memory:

```bash
$env:EMIYA_MEMORY_WRITES="0"
python core/server.py
```

The current backend is Windows-first because the Activity Monitor uses `pywin32`.

---

## `08_philosophy`

A few beliefs that shaped this project and are not up for debate:

**01. AI agents and AI companions are different categories.**
You would not compare a dishwasher to a cat. You would not replace your cat with a dishwasher just because it "does more tasks per hour." Different needs. Different value. EMIYA is in the cat category.

**02. Liveness is engineered, not claimed.**
No language model is conscious. But the *feeling* that you're interacting with something alive — that can be built. Through chaos math, persistent memory with emotional tags, proactive behavior, consistent voice, and a UI that lets you see its internals. That feeling is real even when the mechanism is deterministic.

**03. Transparency is part of the aesthetic.**
You should be able to see her thinking. See her mood vector. See which model is loaded, how much VRAM, how many tokens per second. This is not developer debug mode — this is the product. MHRD showed that letting users see the machinery is not a bug, it's the entire point.

**04. Local-first. Always.**
She watches you. She remembers. This data does not leave the machine except when you explicitly invoke L2. No analytics. No telemetry phoning home. The gateway binds to localhost by default.

**05. She is not for everyone.**
If you want a productivity tool — use OpenClaw, Claude, or any of the hundred excellent alternatives. EMIYA is for people who find it interesting that a Lorenz attractor can make software feel alive. That's a small audience. That's fine.

---

## `09_acknowledgments`

Built on the shoulders of:

- **[MHRD](https://store.steampowered.com/app/576030/MHRD/)** — for proving that letting users see the circuits is more engaging than hiding them
- **[Ollama](https://ollama.com)** — for making local LLMs trivial
- **[Qwen team](https://qwenlm.github.io/)** — for Qwen3, currently used by L0
- **Gemma4 e4b** — current experimental L1 voice model
- **[OpenClaw](https://openclaw.ai)** — for the "hands" infrastructure
- **[Anthropic](https://anthropic.com)** — for Claude (L2) and for conversations that helped shape this project
- **Lorenz** — for the attractor

---

## `10_contact`

Created and maintained by **secret info**.

- github:     [`@naevor`](https://github.com/naevor)
- telegram:   [`@c0ffee_lover`](https://t.me/c0ffee_lover)
- notion:     [dev workspace](https://pond-calliandra-6e3.notion.site/EMIYA-dev-log-34c540076ff6803ab981cd626ee05ab7?pvs=74)


---

## `11_license`

MIT — see [LICENSE](./LICENSE) for details.

Use her. Fork her. Build your own weird companions.
Just don't pretend she's conscious — she isn't, and neither 
are we sure what that would mean.
---

<div align="center">

```
  ┌─ SYSTEM NOTICE ────────────────────────────────────────┐
  │                                                        │
  │   this is not a product.                               │
  │   this is a question, compiled.                        │
  │                                                        │
  │   can software have a character?                       │
  │   can it feel alive without being alive?               │
  │   can a terminal window become company?                │
  │                                                        │
  │   we don't know yet. we're finding out.                │
  │                                                        │
  └────────────────────────────────────────────────────────┘
```

**`[ end of readme ]`**

</div>
