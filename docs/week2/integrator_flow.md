# Integrator Algorithmic Flow

High-level Mermaid diagrams for [backend/integrator.py](../../backend/integrator.py).
Five views, in increasing zoom level.

---

## 1. Top-level pipeline

```mermaid
flowchart TD
    A[video_signals.json<br/>Person A] --> L[load_video_signals]
    B[audio_signals.json<br/>Person B] --> M[load_audio_signals]
    L --> AL[align_segments<br/>merge boundaries, drop &lt;100ms phantoms]
    M --> AL
    L --> G[detect_genre<br/>talking_head / cinematic / mixed]
    M --> G
    L --> SAR[detect_strong_ad_runs<br/>cd&ge;0.5, conf&ge;0.65, run&ge;3, gap&le;5s]
    AL --> CL[per-interval classify_segment]
    G --> CL
    SAR --> CL
    CL --> PP[post-processing chain]
    PP --> OUT[segments.json]
```

---

## 2. classify_segment — router

```mermaid
flowchart TD
    IN([aligned interval<br/>start, end, video_seg, audio_seg]) --> S0{in any<br/>strong_ad_run?}
    S0 -- yes --> AD[(non_content / ad / 0.85)]
    S0 -- no --> HS{has_speech?}
    HS -- True --> PA[Path A<br/>weighted voting]
    HS -- False --> PB[Path B<br/>genre rules]
    PA --> GATE
    PB --> GATE
    GATE{subtype &isin; intro,outro<br/>AND conf &lt; 0.80?}
    GATE -- yes --> DEMO[(content / main)]
    GATE -- no --> KEEP[(type, subtype, conf)]
```

---

## 3. Path A — speech present

```mermaid
flowchart TD
    IN([has_speech = True]) --> VOTE[weighted vote between A and B]
    VOTE --> SC[sanity check]
    SC --> OUT[(verdict)]
```

---

## 4. Path B — no speech

```mermaid
flowchart TD
    IN([has_speech = False]) --> G[detect genre]
    G --> RULE[apply rules to classify]
    RULE --> OUT[(verdict)]
```

---

## 5. Post-processing chain — `generate_output`

```mermaid
flowchart LR
    R[raw classified segments] --> CN[cleanup short segments]
    CN --> CW[collapse sandwich]
    CW --> M[merge]
    M --> OUT[segments.json]
```

---

## Constants quick reference

| Constant | Value | Where |
|---|---|---|
| `BASE_W_A` / `BASE_W_B` | 0.4 / 0.6 | Path A weighted vote |
| `INTRO_WINDOW_SECONDS` / `OUTRO_WINDOW_SECONDS` | 90.0 | zone gates |
| `INTRO_OUTRO_MIN_CONF` | 0.80 | router exit gate |
| `MIN_INTERVAL_DURATION` | 0.1 | alignment phantom filter |
| `STRONG_AD_*` | cd&ge;0.5, conf&ge;0.65, run&ge;3, gap&le;5s, override=0.85 | Step 0 override |
| `WINDOW_SHORT_DURATION` / `WINDOW_MIN_FRAGMENTS` | 30.0 / 3 | post-processing collapser |
