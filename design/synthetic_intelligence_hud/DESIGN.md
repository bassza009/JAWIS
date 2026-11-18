---
name: Synthetic Intelligence HUD
colors:
  surface: '#101418'
  surface-dim: '#101418'
  surface-bright: '#36393e'
  surface-container-lowest: '#0b0f13'
  surface-container-low: '#181c20'
  surface-container: '#1c2024'
  surface-container-high: '#272a2f'
  surface-container-highest: '#31353a'
  on-surface: '#e0e2e9'
  on-surface-variant: '#b9cacb'
  inverse-surface: '#e0e2e9'
  inverse-on-surface: '#2d3136'
  outline: '#849495'
  outline-variant: '#3b494b'
  surface-tint: '#00dbe9'
  primary: '#dbfcff'
  on-primary: '#00363a'
  primary-container: '#00f0ff'
  on-primary-container: '#006970'
  inverse-primary: '#006970'
  secondary: '#78d5dd'
  on-secondary: '#00363a'
  secondary-container: '#007c84'
  on-secondary-container: '#d5fbff'
  tertiary: '#fff4ec'
  on-tertiary: '#482900'
  tertiary-container: '#ffd1a3'
  on-tertiary-container: '#895200'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#7df4ff'
  primary-fixed-dim: '#00dbe9'
  on-primary-fixed: '#002022'
  on-primary-fixed-variant: '#004f54'
  secondary-fixed: '#94f1fa'
  secondary-fixed-dim: '#78d5dd'
  on-secondary-fixed: '#002022'
  on-secondary-fixed-variant: '#004f54'
  tertiary-fixed: '#ffdcbb'
  tertiary-fixed-dim: '#ffb869'
  on-tertiary-fixed: '#2c1700'
  on-tertiary-fixed-variant: '#673d00'
  background: '#101418'
  on-background: '#e0e2e9'
  surface-variant: '#31353a'
typography:
  display-hero:
    fontFamily: Space Grotesk
    fontSize: 56px
    fontWeight: '700'
    lineHeight: 64px
    letterSpacing: -0.02em
  display-hero-mobile:
    fontFamily: Space Grotesk
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.01em
  headline-lg:
    fontFamily: Space Grotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: 0.02em
  headline-lg-mobile:
    fontFamily: Space Grotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: 0.01em
  headline-md:
    fontFamily: Space Grotesk
    fontSize: 22px
    fontWeight: '500'
    lineHeight: 28px
    letterSpacing: 0.04em
  headline-sm:
    fontFamily: Space Grotesk
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: 0.08em
  body-lg:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0.01em
  body-sm:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0.01em
  telemetry-data:
    fontFamily: Space Mono
    fontSize: 13px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.06em
  label-caps:
    fontFamily: Space Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.14em
  micro-hud:
    fontFamily: Space Mono
    fontSize: 9px
    fontWeight: '400'
    lineHeight: 12px
    letterSpacing: 0.2em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1.5rem
  margin: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system channels an advanced, cinematic sci-fi intelligence interface inspired by tactical aerospace head-up displays (HUDs) and conversational synthetic intelligences. The interface behaves not merely as a passive viewport, but as an active, omniscient cockpit instrument running real-time diagnostics, continuous audio telemetry, and dynamic AI-agent state projection.

### Aesthetic Foundation
- **Tactical Cyber-Minimalism & Holographic Projection:** Crisp vector geometry, semi-translucent substrate panels, and optical neon luminescence mimic polarized holographic glass. Visual structures remain skeletal, lightweight, and engineered—never heavy or opaque.
- **Dynamic Energy States:** Visual tension transitions between an ultra-controlled, silent surveillance state (deep void obsidian, calibrated cyan, holographic teal) and an active, vocal generation state (energetic ionized amber/gold resonance).
- **Telemetry-Driven Density:** Micro-labels, precise geographic coordinates, frequency visualizers, hexadecimal telemetry registers, and concentric circular trackers establish technical authority and spatial calibration.

### Target Mood
Autonomous, omniscient, hyper-precise, immediate, and cinematic. The user should feel sovereign authority over a hyper-capable computational entity.

## Colors

The palette establishes an optical hierarchy modeled on night-vision HUD instruments and luminous plasma displays.

### Primary Spectrum: Cyan Luminescence (`#00F0FF`)
The primary frequency represents standard system vitality, active telemetry vectors, core AI voice responsiveness, and primary interaction elements. When energized, this hue carries an optical bloom effect against dark backgrounds.

### Secondary Spectrum: Holographic Teal (`#0D828A`)
Serves as structural scaffolding: passive grid lines, inactive gauge arcs, structural panel borders, and muted diagnostic readouts. It maintains technical complexity without producing visual noise or competing with primary attention vectors.

### Tertiary Spectrum: Kinetic Amber / Plasma Gold (`#FF9D00`)
Reserved strictly for elevated states: the active vocal synthesizer speaking channel, real-time auditory output bursts, critical system alerts, and acoustic overload thresholds.

### Neutral / Void Substrate (`#05080C`)
The substrate is a pitch-black void with a blue-shifted carbon tone, engineered to maximize luminescence contrast and eliminate viewport boundaries. Surface panels elevate via faint alpha layers (`rgba(0, 240, 255, 0.04)` to `rgba(13, 130, 138, 0.12)`) rather than opaque grayscale fills.

## Typography

The typographic hierarchy combines brutalist mechanical precision with legible technical prose:

- **Headlines & System Identifiers (Space Grotesk):** Engineered geometric grotesque displaying forward-leaning, sharp modern terminal angles. Used in all high-level system indicators, status alerts, and primary dialog readouts.
- **Narrative & AI Transcriptions (Geist):** Clean, ultra-neutral grotesque tuned for dense machine-to-human verbal communication, query parsing, and conversational log streams.
- **HUD Micro-Metrics & Telemetry (Space Mono):** Fixed-pitch monospaced font dedicated to live sensory counters, decibel frequencies, packet latency readouts, memory allocation addresses, and spatial orientation coordinates.

All label and micro-HUD tiers mandate full uppercase casing (`text-transform: uppercase`) paired with elevated tracking to emphasize military-grade aerospace instrumentation.

## Layout & Spacing

The spatial architecture balances central focal telemetry with peripheral modular arrays:

### Layout Philosophy
- **Radial-Concentric Core Anchor:** The central stage is anchored by a persistent circular AI acoustic orb and orbital ring visualizers.
- **Peripheral Telemetry Grid:** 12-column dynamic fluid matrix framing the central orb. Diagnostic readouts, audio spectral analyzers, and agent subsystem modules latch to perimeter glass brackets.
- **Precision Alignment Lines:** Visual margins, reticles, and framing brackets line up strictly to a rigid 4px/8px coordinate base, giving structural authenticity to the heads-up display.

### Breakpoint Adaptations
- **Desktop / Display Cockpit (≥ 1200px):** Full 12-column layout. Center stage occupies 6 columns for the responsive orbital core; flanking quadrants (3 columns left, 3 columns right) house audio spectral analysis, neural engine metrics, memory telemetry, and ongoing dialogue transcripts.
- **Tablet (768px – 1199px):** 8-column layout. The central orb moves to the upper visual hemisphere; secondary telemetry stacks into dual collapsible dashboard wings below.
- **Mobile (< 768px):** Single-column stacked viewport. Margins contract to `1rem`. Central orb scales proportionally to viewport width; tactical telemetry reduces to essential status pills, single-line audio frequency bands, and full-width conversational transcription overlays.

## Elevation & Depth

Visual depth does not use standard dropped shadows. Instead, it relies on optical photon projection, progressive luminance stacking, and holographic glass layers.

### Atmospheric Substrates
- **Base Level (Z0):** Deep void obsidian (`#05080C`) paired with a faint vector crosshair matrix pattern rendered at 4% cyan opacity.
- **HUD Tactical Panels (Z1):** Translucent composite dark backing (`rgba(5, 12, 20, 0.7)`) layered with backdrop blur (`blur(16px)`). Framed by razor-thin, low-contrast cyan border strokes (`rgba(0, 240, 255, 0.18)`).
- **Focused Visualizer Rings (Z2):** Concentric rings positioned around the central voice core featuring dynamic neon photon blooms (`box-shadow: 0 0 24px rgba(0, 240, 255, 0.35), inset 0 0 12px rgba(0, 240, 255, 0.2)`).
- **Active Transmission State (Z3):** Amber/Gold vocalization surge (`box-shadow: 0 0 35px rgba(255, 157, 0, 0.5), 0 0 70px rgba(255, 157, 0, 0.2)`), signaling immediate synthesized acoustic emission.

### Boundary Treatment
Panels use clipped technical corners, hairline divider tick marks, and localized corner reticle brackets (`L`-shaped markers) rather than drop shadows to delimit interaction boundaries.

## Shapes

The interface embraces a calibrated architectural language:

- **Soft Chamfer & Micro-Radii:** Structural buttons, panel wrappers, and telemetry chips leverage minimal `0.25rem` corners to suggest milled glass or hardened polycarbonate flight displays.
- **Geometric Orthogonality:** Cut-corner diagonals (chamfers at 45 degrees, 6px to 10px offsets) are applied to primary frame corners and tactical containers to evoke an authentic aerospace/cybernetic HUD aesthetic.
- **Absolute Concentric Circles:** The core audio entity, frequency orbit paths, target reticles, and circular dial gauges maintain mathematically pure 1:1 circular proportions (`border-radius: 9999px`).

## Components

### Central Acoustic Orb & Waveform Rings
- **Orb Structure:** Concentric multi-layered SVG/Canvas sphere with an animated multi-frequency particle cloud.
- **Idle State:** Deep holographic teal pulsing slowly at 0.5 Hz with micro-rotational perimeter degree ticks.
- **Listening State:** Cyan neon contraction and rapid particle vibration keyed to external decibel input.
- **Speaking State:** Ionized plasma amber bloom with concentric acoustic shockwave rings expanding outwards and frequency equalizers flexing along the equatorial axis.

### Interactive Buttons & Triggers
- **Primary Tactical Trigger:** Transparent fill, `1px` solid border (`#00F0FF`), uppercase `Space Mono` typography, cyan glow on hover (`box-shadow: 0 0 15px rgba(0, 240, 255, 0.4)`). Accented with small angled corner ticks.
- **Destructive / Abort Trigger:** Pitch carbon background, amber/red outline (`#FF9D00`), high-frequency stutter animation on press.

### Telemetry Cards & Glass HUD Consoles
- Engineered with razor-thin structural outlines (`rgba(0, 240, 255, 0.15)`), chamfered corners, and diagnostic header rows with micro-metrics (`SYS.REF // 0984.X`).
- Background: Glassmorphic dark tint (`rgba(6, 14, 24, 0.6)`) with `12px` background blur.

### Frequency Visualizers & Spectral Bars
- Real-time segmented vertical LED-style bars. Segments feature 1px gaps. Unlit segments idle at 8% teal opacity; energized segments illuminate to full primary cyan or speaking amber.

### Telemetry Data Chips & Status Badges
- Pill-compact or chamfered micro-containers housing monospace indicators (`STATUS: ARMED`, `FREQ: 142.08 MHz`). Flanked by a pulsing 4px luminescent dot.

### Input Fields & Command Terminal
- Minimalist underline or boxed outline with a blinking cyan vertical block cursor (`|`).
- Placeholder text displays at 30% teal opacity. Input text renders in crisp monospace with real-time audio chirp feedback on keystroke.

### Audio Diagnostic Toggle / Radios & Checkboxes
- Replaced with technical binary state selectors: angular crosshairs for checkboxes that populate with a glowing cyan `+` or solid square reticle when active; circular targeted rings for radio choices.