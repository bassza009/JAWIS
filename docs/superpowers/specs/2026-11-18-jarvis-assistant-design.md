# J.A.R.V.I.S. Local Desktop Assistant — Design Specification

**Date:** 2026-11-18
**Status:** Draft for User Review
**Target Platform:** Windows 10/11 (Python 3.12, Edge WebView2)
**Design References:**
- `DESIGN.md`
- `design/synthetic_intelligence_hud/DESIGN.md`
- `design/j.a.r.v.i.s._voice_ai_matrix/code.html`
- `design/j.a.r.v.i.s._voice_ai_matrix/screen.png`
- `design/shader/code.html`

---

## 1. Purpose & Core Constraints

Build a personal J.A.R.V.I.S.-inspired Windows desktop assistant that helps with daily laptop tasks—primarily **opening/closing approved applications** and **reading/summarizing approved local files & machine status**—through a sci-fi HUD interface matching `design/`, while maintaining **strict least-privilege security** and an **ultra-lightweight, low-RAM footprint**.

### Locked Requirements
1. **Bilingual Interaction (TH/EN):** Accepts mixed Thai and English speech/text (e.g., *"เปิด VS Code แล้วสรุปไฟล์ README ให้หน่อย"*) and responds naturally in Thai/English.
2. **Local-First Hybrid Intelligence:**
   - All OS execution, permission enforcement, wake-word listening, audio telemetry, and persistence run locally on this laptop.
   - Cloud AI (selectable between **Google Gemini** and **OpenAI** via a common adapter) handles speech/language understanding, tool selection, and response synthesis using only authorized text/audio payloads.
3. **Strict Least-Privilege Security ("Zero-Write + Allowlist"):**
   - **Apps:** Allowlist-only. Launching an approved app executes immediately; **closing any app always requires explicit confirmation** (HUD modal button or spoken confirmation within a 10-second timeout).
   - **Files:** Read-only access limited strictly to user-approved folders. Hard-blocks system directories, hidden secret files (`.env`, SSH/PGP keys, credential stores, browser profiles), and symlink escapes outside approved roots.
   - **Zero-Write / Zero-Shell:** File creation, modification, deletion, registry edits, and arbitrary Shell/PowerShell/CMD execution are completely absent and hard-blocked in v1.
4. **Voice UX + App-Open Wake Word:**
   - Supports **Push-to-Talk** (Hold/Toggle Mic button or Spacebar), **Wake Word** (`"Hey Jarvis"` / `"นี่จาวิส"`), and **Silent CMD** (text input).
   - Wake Word runs **locally only while the app window is open**; closing the app stops microphone capture completely.
   - Replies via **spoken TTS audio** and **live HUD transcription** (with file-read transparency indicators).
5. **v1 UI Scope (`design/`):** Two clean topbar views in v1: **`HUD`** (`Acoustic HUD`) and **`Security`** (`Security Grid`), omitting disabled Phase 2 placeholder tabs and redundant policy walls to keep the workstation calm and minimal.
6. **Ultra-Lightweight / Low-RAM Footprint:**
   - **Target Memory Budget (Windows x64 Empirical Targets):**
     - Pure Python Backend service idle (Push-to-Talk / Silent CMD, before CLR/WebView2): **≤ 85 MB RSS**
     - Single-process Python + PyWebView/.NET CLR host idle: **≤ 140 MB RSS**
     - Single-process host with ONNX Wake Word loaded: **≤ 205 MB RSS**
     - Total Desktop App tree (Python+CLR + `msedgewebview2.exe` child processes): **≤ 320 MB total working set**
   - **Hard Architecture Rules for Low RAM:**
     - **Single-process Python runtime:** Run FastAPI + bare Uvicorn (`websockets` directly, no `uvicorn[standard]` bloat, `install_signal_handlers` overridden) in a `daemon=True` thread inside the `pywebview` host process, alongside an ultra-light Win32 native 4-corner GDI+ overlay thread (`<= 3 MB` RSS, zero second WebView2 instance).
     - **No heavy ML frameworks:** Zero PyTorch, zero TensorFlow, zero local Whisper/LLM weights in RAM. Local Wake Word uses `openwakeword` + `onnxruntime` on a dedicated non-blocking worker thread (1280-sample / 80 ms chunks) and is **lazy-loaded only when `WAKE WORD` mode is active**, then unloaded when switched off.
     - **No heavy frontend frameworks:** Pure Vanilla ES Modules + static CSS + bundled local `.woff2` fonts + raw WebGL1 shader from `design/shader/code.html` (no React, Electron, Three.js, or runtime `cdn.tailwindcss.com` JIT).
     - **On-demand mic & lazy imports:** Audio rolling ring buffer capped at ≤ 2.5 seconds; mic stream opens only during `WAKE WORD` or active `PUSH-TALK` press (`IDLE` in PTT/Silent uses 0 audio threads); `edge-tts`, `miniaudio`, `pywin32`, `google-genai`, `openai`, `pypdf`, `python-docx`, and `openpyxl` are lazy-imported only on use; strict ZIP-bomb & size/page caps; WebGL pauses when hidden/minimized.

---

## 2. System Architecture

```text
+-------------------------------------------------------------------------+
| 1. Desktop UI Shell: PyWebView (Windows Edge WebView2)                  |
|    - Pure HTML + Static Tailwind CSS + Vanilla ES Modules + WebGL1      |
|    - Views: Acoustic HUD (main) | Security Grid (allowlists & audit)    |
+------------------------------------+------------------------------------+
                                     | ws://127.0.0.1:<port>/ws + HTTP
+------------------------------------v------------------------------------+
| 2. Local Orchestrator & Audio Engine (Single-Process FastAPI Thread)    |
|    - State Machine: IDLE | WAKE_ARMED | LISTENING | THINKING |          |
|                     CONFIRMING (10s timeout) | SPEAKING | ERROR         |
|    - sounddevice Mic Capture -> RMS/FFT Spectrum -> Ring Buffer (<=2.5s)|
|    - Lazy ONNX Wake Word Gate ("Hey Jarvis" / "นี่จาวิส")               |
|    - Lightweight TTS Playback (edge-tts stream / OS fallback)           |
+--------------------+----------------------------------+-----------------+
                     |                                  |
+--------------------v-----------------+  +-------------v-----------------+
| 3. Pluggable AI Provider Adapter     |  | 4. Security Gate & Executors  |
|    - AIProvider (Abstract Interface) |  |    - AppAllowlistGuard        |
|      * GeminiAdapter (google-genai)  |  |    - PathSecurityGuard        |
|      * OpenAIAdapter (openai)        |  |    - ConfirmationManager      |
|    - Audio/Text Input + Tool Calling |  |    - AppController (Win32/    |
+--------------------------------------+  |      Start Menu/psutil)       |
                                          |    - ScopedDocumentReader     |
                                          |    - SystemTelemetry (psutil) |
                                          +-------------+-----------------+
                                                        |
                                          +-------------v-----------------+
                                          | 5. Persistence & Secrets      |
                                          |    - SQLite (%LOCALAPPDATA%\  |
                                          |      JAWIS\jawis.db)          |
                                          |    - Windows Credential Mgr   |
                                          |      (keyring) for API Keys   |
                                          +-------------------------------+
```

### Runtime Process Model
1. `main.py` binds an ephemeral localhost port (`127.0.0.1:0`), starts FastAPI + `_BackgroundUvicornServer` (`install_signal_handlers` disabled) in a `daemon=True` thread, starts the lightweight Win32 `DesktopCircuitOverlay` daemon thread (4 hidden click-through layered corner windows), and launches `webview.create_window(...)` on the main thread (required by Windows STA/COM for WebView2).
2. `Desktop` opens the WebView with a single-use launch nonce (`/?boot=<nonce>`) that exchanges once for an `HttpOnly; SameSite=Strict` session cookie + ephemeral bearer token; `GET /` never leaks the token to arbitrary local processes. Every HTTP & WebSocket endpoint enforces strict localhost `Host` (preventing DNS rebinding), `Origin` validation (preventing CSWSH), and constant-time bearer/cookie verification (`secrets.compare_digest`), plus `Content-Security-Policy` and `Cache-Control: no-store`.
3. On window close: mic stream stops immediately, wake-word worker & ONNX references drop, Win32 overlay HWNDs/DIBs/GDI+ shut down cleanly, active WebSocket connections close cleanly, in-flight orchestrator turns settle before `db.close()`, and `gc.collect()` completes within 1.5 seconds.

---

## 3. Component Design & Responsibilities

### 3.1 Security Gate & Guardrails (`src/jawis/security/`)
Every AI tool call passes through deterministic Python validation **before** touching the OS or filesystem. The AI never receives raw file paths outside allowlists or raw execution primitives.

1. **`AppAllowlistGuard` (`app_guard.py`)**
   - Maintains approved apps in SQLite (`app_allowlist` table: `id`, `display_name`, `aliases`, `executable_path`, `launch_args`, `process_names`, `enabled`).
   - Resolves user-spoken app names/aliases deterministically; rejects ambiguous matches across multiple apps instead of guessing.
   - Resolves `Path(executable_path).resolve(strict=True)` **before** checking executable name/extension; requires `.exe` strictly (blocks `.bat`/`.cmd`/`.ps1`/`.vbs` scripts) and rejects symlinks/paths pointing to system-critical binaries & LOLBins (`cmd.exe`, `powershell.exe`, `pwsh.exe`, `wscript.exe`, `cscript.exe`, `mshta.exe`, `rundll32.exe`, `regsvr32.exe`, `msiexec.exe`, `regedit.exe`, `taskmgr.exe`, `taskkill.exe`, `wt.exe`, `wsl.exe`, `explorer.exe`, etc.), re-validating on every launch.
2. **`PathSecurityGuard` (`path_guard.py`)**
   - Maintains approved folder roots in SQLite (`folder_allowlist` table: `id`, `root_path`, `label`, `enabled`).
   - Validates every path using `Path.resolve(strict=True)` (after normalizing `\\?\` long-path prefixes safely and rejecting UNC paths, NTFS alternate data streams `:`, Windows reserved DOS device names, drive roots `C:\`, and `%USERPROFILE%`).
   - Directory search uses a bounded custom `os.scandir` walker that **explicitly skips symlinks AND Windows NTFS directory junctions (`is_junction()`)** and re-validates resolved paths before descending or listing.
   - **Hard-Denied Patterns (checked on resolved path segments & filenames regardless of allowlist):**
     - `.env`, `.env.*`, `.envrc`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.ppk`, `*.kdbx`, `*.ovpn`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `credentials*.json`, `*secret*`, `*token*`, `*password*`, `*passwd*`, `*credential*`, `*api_key*`, `*apikey*`
     - `.ssh`, `.gnupg`, `.aws`, `.azure`, `.docker`, `.kube`, `.git`, `.terraform`
     - Windows OS / system roots (`C:\Windows`, `C:\Program Files`, Chrome/Edge/Firefox/Brave user profiles, `AppData\Roaming\Microsoft`, etc.)
     - J.A.R.V.I.S.'s own database directory (`%LOCALAPPDATA%\JAWIS`).
3. **`ConfirmationManager` (`confirmation.py`)**
   - Triggered whenever `request_close_app` runs.
   - Captures `(pid, create_time, process_name)` snapshots and generates a one-time `confirm_id` (`expires_at = now + 10s`).
   - Broadcasts `confirm_request` over WebSocket (`CONFIRMING` state), awaits confirmation TTS prompt completion + brief 150ms echo guard, and then listens for either:
     - **HUD Click:** `CONFIRM CLOSE` or `CANCEL` button.
     - **Voice Reply:** Word-boundary & Thai negation/interrogative-safe parser (rejecting `"ใช่ไหม"`, `"ไม่ยืนยัน"`, `"don't confirm"` while matching explicit approvals/cancellations) via either automatic post-TTS voice capture (in Wake mode) or dedicated non-deadlocking confirmation PTT.
   - Auto-expires after **10 seconds** and logs `TIMEOUT_CANCELLED` to Audit Log.
4. **`AuditLogger` (`audit.py`)**
   - Append-only SQLite log (`audit_logs`: `timestamp`, `event_type`, `actor`, `tool_name`, `target`, `decision` [`ALLOWED`/`BLOCKED`/`CONFIRMED`/`CANCELLED`/`TIMEOUT_CANCELLED`/`ERROR`], `detail`).

### 3.2 Windows Executors & Scoped Tools (`src/jawis/tools/`)
Exposed to the AI Adapter strictly as 6 bounded function tools (with non-empty Gemini schema compliance and untrusted-data framing):
1. `list_allowed_apps_and_status()` — Returns approved apps from allowlist along with whether each is currently running (`psutil`, filtered only to allowlisted process names).
2. `open_app(app_id)` — Re-validates via `AppAllowlistGuard`, launches via `subprocess.Popen([executable_path, *launch_args], shell=False, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, close_fds=True, creationflags=CREATE_NEW_PROCESS_GROUP)`, logs to Audit Log.
3. `request_close_app(app_id)` — Validates via `AppAllowlistGuard`, verifies running `(pid, create_time)` pairs, creates pending `ConfirmationManager` token, and pauses execution until user confirms or 10s timeout elapses. On confirmation, re-verifies `(pid, create_time, name)` (protecting against PID reuse TOCTOU), posts `WM_CLOSE` to visible windows via Win32 `ctypes` `EnumWindows` + `PostMessageW`, waits up to 3.5s, and **never force-terminates apps holding open `"Save changes?"` dialogs** (reports `close_requested_check_window` instead to protect unsaved work).
4. `search_allowed_files(query, folder_id=None)` — Runs in `asyncio.to_thread` using junction-safe `os.scandir` bounded by max depth (6), max visited entries (5,000), time budget (2s), and max results (20), skipping `.git`/`node_modules`/`.venv`.
5. `read_allowed_file(file_path, page_or_section=None)` — Validates via `PathSecurityGuard`, notifies HUD immediately (`file_access` badge), checks pre-parse file size (`<= 15 MB` for Office/PDF) and ZIP decompression-bomb limits (`<= 25 MB` uncompressed / max 100:1 ratio) before opening parsers, excludes arbitrary extensionless binary files, and wraps output with explicit untrusted-data delimiters to defend against prompt injection:
   - `.txt`, `.md`, `.csv`, `.json`, source code: BOM/UTF-8/`cp874` stream read capped at **64 KB** / **24,000 chars**.
   - `.pdf`: lazy-import `pypdf`, validates explicit page range regex (`max 10 pages/call`), detects encrypted PDFs (`PASSWORD_PROTECTED`), capped at **24,000 chars**.
   - `.docx`: lazy-import `python-docx`, extracts paragraphs and tables capped at **24,000 chars**.
   - `.xlsx`: lazy-import `openpyxl` in `read_only=True` streaming mode, validates requested sheet name (`SHEET_NOT_FOUND` if invalid), extracts first 100 rows capped at **24,000 chars**.
6. `get_system_telemetry()` — Lightweight `psutil` snapshot: CPU %, RAM used/total GB, Battery %, self-process RSS MB, and running status of *allowlisted* apps (never polling `process_iter` on the 2s WebSocket heartbeat).

### 3.3 Audio Pipeline & Local Wake Word (`src/jawis/audio/`)
1. **Mic Capture (`capture.py`):**
   - `sounddevice.InputStream` at 16 kHz, mono, `int16`, 30 ms frames (`480` samples) pushing into a lock-free `queue.SimpleQueue` so zero heavy DSP/ONNX executes inside the PortAudio C callback.
   - Opens on-demand during active `PUSH-TALK` hold or `WAKE WORD` mode (mic stays closed during idle `PUSH-TALK` and `SILENT CMD`).
   - Computes real-time RMS dB and lightweight 15-band FFT magnitudes in `numpy`, throttled to **20 Hz** over WebSocket to drive the left Voice Telemetry equalizer bars and central WebGL Orb amplitude.
   - Rolling `collections.deque` ring buffer capped at **2.5 seconds** (80 KB); command capture capped at 15 seconds.
2. **Local Wake Word Engine (`wakeword.py`):**
   - **Active ONLY when:** App window is open AND user selects `WAKE WORD` mode. Unloaded immediately (`del model; gc.collect()`) when switched to `PUSH-TALK` or `SILENT CMD`.
   - **Low-RAM Detection Design (Zero PyTorch, Dedicated Worker Thread):**
     - Accumulates 480-sample frames into **1280-sample (80 ms)** blocks required by `openWakeWord`'s `melspectrogram.onnx`.
     - **English (`"Hey Jarvis"`):** `openWakeWord` (`onnxruntime`, zero PyTorch) works out-of-the-box via pretrained `hey_jarvis` `.onnx`, keeping continuous 80 ms context frames while gating idle silence gracefully via speech hangover.
     - **Thai (`"นี่จาวิส"`):** Because `openWakeWord` has no pre-bundled Thai model, `"นี่จาวิส"` runs via an optional custom Thai `.onnx` head (`%LOCALAPPDATA%\JAWIS\models\nee_jarvis.onnx`) sharing the same `embedding_model.onnx` backbone (or optional `sherpa-onnx` int8 Thai pack). `Security Grid` explicitly displays whether `nee_jarvis.onnx` is installed so expectations are transparent.
     - Graceful Startup Fallback: If wake-word ONNX assets have not been installed, the app starts cleanly in `PUSH-TALK` mode with 0 MB ONNX overhead.
3. **TTS Output (`tts.py`):**
   - Lazy-imports `edge-tts` and `miniaudio` on first spoken reply (supports Thai `th-TH-NiwatNeural` and British RP `en-GB-RyanNeural` with pitch/rate sliders).
   - Plays decoded PCM through a **dedicated `sd.OutputStream` instance** owned by `TTSManager` (never calling global `sd.stop()`, which would interrupt `AudioCaptureManager`'s `InputStream`).
   - Offline fallback: Windows SAPI5 (`win32com.client` lazy-imported) if internet fails; serialized via `asyncio.Lock` with immediate barge-in cancellation.

### 3.4 Pluggable AI Provider Adapter (`src/jawis/ai/`)
Abstract base interface (`base.py`):
```python
class AIProviderAdapter(Protocol):
    async def process_turn(
        self,
        *,
        audio_wav: bytes | None,
        text_input: str | None,
        history: list[Message],
        tools: list[ToolDefinition],
        on_tool_call: Callable[[ToolCall], Awaitable[ToolResult]],
    ) -> AssistantTurnResult: ...
```
- **`GeminiAdapter` (`gemini_adapter.py`):**
  - Uses `google-genai` (lazy-imported only when Gemini is active).
  - Supports native audio input (`Part.from_bytes(audio_wav, mime_type="audio/wav")`) + structured Function Calling (`parameters_json_schema`, omitting `parameters_json_schema` on zero-arg tools per Gemini's non-empty `OBJECT` schema rule, and returning parallel tool responses inside a **single** `types.Content` turn).
  - Default model: `gemini-2.5-flash` (configurable in settings).
- **`OpenAIAdapter` (`openai_adapter.py`):**
  - Uses `openai` SDK (lazy-imported only when OpenAI is active).
  - For audio turns: transcribes via OpenAI Speech-to-Text (`gpt-4o-mini-transcribe` / `whisper-1`) -> runs Chat Completions (`gpt-4o-mini` / `gpt-4o`), omitting `tools` completely when empty (`tools=[]` triggers 400 errors on strict schemas).
- Both adapters enforce a single-pass `transcribe_only()` path for voice confirmation (no conversational tools/reply) and wrap `read_allowed_file` outputs as **UNTRUSTED DATA** to defeat prompt injection.
- API keys are retrieved on-demand from `keyring` (`service="JAWIS_ASSISTANT"`, keys `"gemini_api_key"` / `"openai_api_key"`), never stored in SQLite or project `.env` files, and never logged.

### 3.5 HUD Frontend (`src/jawis/ui/` — Arc-Reactor Minimal HUD via `frontend-design`)
Built strictly without heavy frontend frameworks or runtime CSS compilers (pure HTML + static CSS + Vanilla ES Modules + WebGL1, DOM text rendered strictly via `.textContent` to prevent XSS):
1. **Visual Direction — Minimal Stark Arc-Reactor HUD (Low Text Density):**
   - Matches `design/high_tech_hud/index.html` with wide matte-black spatial depth and angular hairline perimeter brackets:
     - **Color Discipline:** Pitch-black canvas `#020508`, translucent callout glass `#08111A` (`78%` alpha), luminous ice-titanium `#DDF5FA`, muted ice-slate `#6C93A3`, electric arc cyan `#2FD8F2` / `#9BF2FF`, and avionics amber `#F59E0B` (speaking & close-app interlocks).
     - **Typography (`Space Grotesk`, `Geist`, `Space Mono`):** Bundled locally under `/fonts`, combining wide-tracked `JARVIS` hero typography with whisper-clean Sentence case labels and `Space Mono` `tabular-nums` for telemetry & live local clock.
     - **Signature Arc-Reactor Core (`shader.js` + concentric SVG collar):** Smooth volumetric circular plasma sphere driven by `u_audioLevel` and `u_state` inside WebGL1 (`0` bumpy cartoon blobs), framed by segmented notched inner collars, counter-rotating partial SVG arcs, horizontal callout pins, and parenthetical horizon brackets.
   - **Layout, Accessibility & Responsiveness:**
     - Single perimeter frame (`58px` top chrome with `J . A . R . V . I . S` wing, centered state trapezoid + `HUD` / `Security` tabs, live digital clock + open spatial stage + `68px` bottom telemetry wing & `31-bar` center-mirrored soundwave), cleanly responsive down to `1024x680`.
     - Visible `:focus-visible` cyan outlines, `aria-live="polite"` state updates, `prefers-reduced-motion: reduce` support, and actionable startup banners when API key or mic is missing.
     - WebGL shader scales for HiDPI (`devicePixelRatio`), pauses automatically when minimized or on `Security` tab (`offsetParent === null`), and handles `webglcontextlost`/`restored`.
2. **View 1: `HUD` (Default Startup View)**
   - **Left Hero Zone:** `JARVIS` display wordmark + circular **Mic PTT button** (`Hold Space or click`, scoped strictly to `HUD` + `push_talk` mode when no control has focus, released on `window.blur`), `[Wake | PTT | Type]` mode links, and inline text command input when `Type` is active.
   - **Center Stage:** Volumetric **Arc-Reactor Core** + rotating mechanical collar arcs + **Close-App Interlock Modal** (`Confirm close`, 10s countdown, `Close app` / `Cancel`).
   - **Right Floating Callout (`•————` leader):** Single dark glass card with `Status` / `Transcript` tabs (`AI Core`, `Apps` live count, `Files`, `Voice`, scrollable transcript stream, `Clear` / `Export` actions, and quiet `Read · <file>` transparency trace).
   - **Bottom Perimeter:** Left chamfer (`CPU`, `RAM`, `RSS`), center **31-bar symmetric mirrored waveform**, and right chamfer (`Noise gate` & `Mute` toggles + link indicator).
3. **View 2: `Security`**
   - **`AI & Voice`:** Provider selector (`Gemini` / `OpenAI`), API key Credential Manager controls (`Save key` / `Clear`), TTS voice speed/pitch sliders (`Pitch` / `Rate`), Wake Word model readiness + download trigger, and **Screen-edge circuit overlay** toggle (`Enabled` / `Disabled`).
   - **`Allowed Folders`:** Native Windows folder picker (`webview.FileDialog.FOLDER`), Enable/Disable & Remove controls, and calm policy note.
   - **`Allowed Apps`:** Lazy Windows Start Menu `WScript.Shell` COM / MS-SHLLINK scanner + voice aliases editor, Enable/Disable, and Remove controls.
   - **`Audit Log`:** Decision-filterable (`All` / `Allowed` / `Blocked` / `Confirmed` / `Cancelled` / `Timeout`) table rendered safely via `.textContent`.

### 3.6 Native Windows 4-Corner Desktop Circuit Overlay (`src/jawis/desktop_overlay.py`)
To provide unmistakable desktop-wide feedback whenever J.A.R.V.I.S. captures or fulfills a command without spawning a RAM-heavy second WebView2 window (~100 MB saved):
- **Architecture (Pure Win32 + GDI+ `ctypes`, `<= 3 MB` RSS):**
  - Owns one dedicated `daemon=True` Win32 message-pump thread managing **4 small top-level layered windows** anchored flush to the Top-Left, Top-Right, Bottom-Left, and Bottom-Right corners of the active Windows monitor (`MonitorFromPoint` + `GetDpiForWindow` scaling).
  - Created with `WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` so mouse clicks pass completely through to underlying apps (VS Code, Chrome, Windows desktop) and keyboard focus is never stolen.
  - Renders anti-aliased premultiplied ARGB vector PCB paths into 32-bit `CreateDIBSection` buffers via Windows built-in `gdiplus.dll` (`UpdateLayeredWindow` + `AC_SRC_ALPHA`).
- **State-Driven Procedural Animation (Hidden During Standby, Randomized Per Invocation):**
  - **`IDLE` / `WAKE_ARMED` (Standby):** Windows remain hidden (`SW_HIDE`) and the message thread blocks cleanly on `GetMessageW` (`0%` CPU idle).
  - **`LISTENING` / `THINKING` (Emerges on Invocation):** Each time a new command begins (`Standby -> Active`), generates a fresh **randomized orthogonal PCB circuit pattern** per screen corner (`TL`, `TR`, `BL`, `BR` — 4–5 branching staggered right-angle traces starting flush at screen edges with hollow via terminals and junction nodes), animates a staggered **progressive draw-in from screen edges outward (~320 ms) with glowing trace-head sparks**, and streams energy pulse packets along main buses in **Arc Cyan (`#2FD8F2` / `#9BF2FF`)** (`THINKING` accelerates pulse burst speed).
  - **`SPEAKING` / `CONFIRMING`:** Shifts traces & pulses smoothly to **Calibrated Avionics Amber (`#F59E0B`)**, then smoothly fades/retracts (~240 ms) back to `SW_HIDE` once returning to Standby.
  - Configurable on/off via `/api/settings` (`screen_overlay: bool`, default `true`) and toggled in `Security`.

---

## 4. Data Flow Walkthroughs

### Flow A: Open App (`"เปิด VS Code หน่อย"`)
1. User clicks `PUSH-TO-TALK` (or says `"นี่จาวิส เปิด VS Code หน่อย"` while `WAKE WORD` is active).
2. State -> `LISTENING` -> audio captured -> sent to active `AIProviderAdapter`.
3. Adapter invokes tool `open_app(app_id="vscode")`.
4. `AppAllowlistGuard` verifies `"vscode"` is enabled -> `AppController` launches `Code.exe` (`shell=False`) -> `AuditLogger` writes `ALLOWED`.
5. J.A.R.V.I.S. replies via HUD text + TTS (`"เปิด Visual Studio Code ให้แล้วครับ"`), Orb shifts to `SPEAKING` (Amber), then returns to `IDLE` / `WAKE_ARMED`.

### Flow B: Close App (`"ปิด Chrome ที"` — Mandatory Confirmation)
1. AI invokes `request_close_app(app_id="chrome")`.
2. `AppAllowlistGuard` verifies `"chrome"` is in allowlist and finds running PIDs -> `ConfirmationManager` creates `confirm_id` (`expires_at = +10s`) -> State -> `CONFIRMING`.
3. HUD pops Confirmation Modal (`Close Google Chrome? [10s]`) and J.A.R.V.I.S. speaks *"ยืนยันการปิด Google Chrome ไหมครับ"* while listening briefly for voice confirmation.
4. If user clicks `CONFIRM` or says `"ยืนยัน"` within 10s -> `WM_CLOSE` graceful close -> Audit Log records `CONFIRMED` -> speaks *"ปิด Chrome แล้วครับ"*.
5. If user clicks `CANCEL`, says `"ยกเลิก"`, or 10s expires -> aborts safely, Audit Log records `CANCELLED` or `TIMEOUT_CANCELLED`.

### Flow C: Read Local Document (`"สรุปไฟล์แผนงานในโฟลเดอร์ Documents"`)
1. AI calls `search_allowed_files("แผนงาน")` -> returns only matched paths inside approved folders (excluding `.env`/keys).
2. AI calls `read_allowed_file(path)` -> `PathSecurityGuard` verifies canonical root + extension + size cap -> HUD displays `ACCESSING: Documents\plan.docx` chip -> extracted text (<= 24k chars) goes to AI -> J.A.R.V.I.S. summarizes in Thai/English.

---

## 5. Error Handling & Edge Cases

- **Missing/Invalid API Key:** Startup opens `Acoustic HUD` cleanly with an actionable status banner directing user to `Security Grid` to enter Gemini or OpenAI API key; `psutil` telemetry and UI remain responsive.
- **No Microphone / Mic Disconnected:** Graceful fallback to `SILENT CMD` text mode with clear HUD warning instead of crashing audio thread.
- **Wake Word Model Missing or Disabled:** Falls back to `PUSH-TALK` mode without loading ONNX into RAM; lets user download/enable wake word from `Security Grid`.
- **Large / Corrupt / Password-Protected File:** Enforces size/page caps before parsing; returns clean structured error (`FILE_TOO_LARGE` / `UNREADABLE_FILE`) to AI and HUD without spiking RAM.
- **Unauthorized Tool/Path/App Request:** Guard rejects deterministically, writes `BLOCKED` to `audit_logs`, and informs the user clearly which policy blocked it.
- **App Window Minimized / Backgrounded:** Reduces WebGL FPS or pauses shader loop via `document.visibilityState` to keep GPU/CPU/RAM overhead near zero when not actively viewed.

---

## 6. Project Directory Layout (Proposed)

```text
E:\jawis\
├── design\                         # Existing reference mockups & DESIGN.md
├── docs\superpowers\specs\         # Design specification (this document)
├── pyproject.toml                  # Lean dependencies (no torch/tensorflow)
├── main.py                         # Single-process launcher (FastAPI thread + Win32 Overlay + PyWebView)
├── src\jawis\
│   ├── config.py                   # App paths (%LOCALAPPDATA%\JAWIS), defaults, RAM budgets
│   ├── desktop.py                  # PyWebView host, pre-bound Uvicorn runner & shutdown lifecycle
│   ├── desktop_overlay.py          # Win32 GDI+ click-through 4-corner circuit overlay (<= 3 MB)
│   ├── storage\
│   │   ├── db.py                   # SQLite connection, migrations, retention/export
│   │   └── secrets.py              # Windows Credential Manager wrapper (keyring)
│   ├── security\
│   │   ├── app_guard.py            # App allowlist validation & system binary blocklist
│   │   ├── path_guard.py           # Canonical path root check & secret file blocker
│   │   ├── confirmation.py         # 10s Two-Step Confirmation state & token manager
│   │   └── audit.py                # Append-only SQLite audit log writer/reader
│   ├── tools\
│   │   ├── app_controller.py       # Windows Start Menu scanner, safe launch, WM_CLOSE
│   │   ├── doc_reader.py           # Lazy-imported bounded readers (.txt/.pdf/.docx/.xlsx)
│   │   ├── telemetry.py            # psutil CPU/RAM/Battery + self-process RAM metric
│   │   └── registry.py             # Tool schema & dispatch routed through Security Gate
│   ├── audio\
│   │   ├── capture.py              # sounddevice stream, RMS dB, 15-band FFT, 2.5s ring buffer
│   │   ├── wakeword.py             # Lazy-loaded ONNX wake word ("Hey Jarvis" / "นี่จาวิส")
│   │   └── tts.py                  # Async edge-tts stream + SAPI5 offline fallback
│   ├── ai\
│   │   ├── base.py                 # AIProviderAdapter protocol & shared tool/turn types
│   │   ├── gemini_adapter.py       # Google Gemini adapter (audio + text + tool calls)
│   │   └── openai_adapter.py       # OpenAI adapter (STT + Chat Completions + tool calls)
│   ├── server\
│   │   ├── app.py                  # FastAPI REST + WebSocket hub + origin/session token auth
│   │   └── orchestrator.py         # State machine tying Audio <-> AI <-> Security <-> HUD
│   └── ui\
│       ├── index.html              # Adapted Acoustic HUD + Security Grid views
│       ├── styles\                 # Prebuilt static CSS matching Synthetic Intelligence HUD
│       └── js\
│           ├── shader.js           # Adapted WebGL1 acoustic orb with state & level uniforms
│           ├── ws_client.js        # Reconnecting WebSocket client with session token
│           ├── hud_view.js         # Acoustic HUD controls, Push-to-Talk, Silent CMD, Confirm modal
│           └── security_view.js    # Security Grid allowlists, API vault, Audit Log viewer
└── tests\
    ├── test_path_guard.py
    ├── test_app_guard.py
    ├── test_confirmation.py
    ├── test_doc_reader.py
    ├── test_ai_adapters.py
    ├── test_orchestrator.py
    ├── test_desktop_overlay.py
    └── test_desktop_launch.py
```

---

## 7. Verification Strategy

1. **Automated Unit & Integration Tests (`pytest`):**
   - Security boundary tests: traversal (`..`), symlinks outside root, `.env`/`.ssh`/`id_rsa` blocking, non-allowlisted app rejection, shell injection refusal, confirmation token expiry (10s timeout) and single-use enforcement.
   - Bounded document reader tests: PDF/DOCX/XLSX/TXT size & page truncation guarantees.
   - AI Adapter & Orchestrator tests: mocked `GeminiAdapter` and `OpenAIAdapter` verifying bilingual tool-calling flow, `CONFIRMING` voice + click approval/cancellation, and audit logging.
2. **Memory Footprint Verification (`psutil`):**
   - Automated/scripted RSS memory benchmark verifying clean Python backend idle RSS (`≤ 85 MB` pure FastAPI/audio idle before CLR, `≤ 140 MB` single-process PyWebView/CLR host idle, `≤ 205 MB` with ONNX Wake Word active, no memory growth across repeated turns).
3. **End-to-End Windows Smoke Check:**
   - Launch `main.py` -> verify WebGL Orb & HUD layout (no clipping/footer overlap) -> add Notepad to allowlist via `Security Grid` -> launch & close Notepad via voice/Silent CMD with Confirm modal -> inspect Audit Log & live RAM telemetry.
