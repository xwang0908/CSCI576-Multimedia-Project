# Video Algorithmic Flow

High-level Mermaid diagrams for `video.py`.
Six views, from the full pipeline down to ad-detection refinement.

---

## 1. Top-level pipeline

```mermaid
flowchart TD
    IN[Input video] --> SC[TransNetV2<br/>Scene detection]
    SC --> AN[CLIP + visual cues<br/>Scene analysis]
    AN --> POST[Post-processing]
    POST --> OUT[video_signals.json]
```

---

## 2. Per-scene analysis

```mermaid
flowchart TD
    IN([One detected scene]) --> F{Valid scene?}
    F -- no --> SKIP[Skip scene]
    F -- yes --> FR[Frame sampling]

    FR --> E[Feature extraction]
    E --> MOT[Motion]
    E --> FACE[Faces]
    E --> CUT[Cut density]
    E --> CLIP[CLIP scores]

    MOT --> C[Classification]
    FACE --> C
    CUT --> C
    CLIP --> C

    C --> SEM[Semantic label]
    C --> VIS[Visual type]
    C --> CONF[Confidence]

    SEM --> SEG[Segment record]
    VIS --> SEG
    CONF --> SEG
```

---

## 3. Semantic classification router

```mermaid
flowchart TD
    IN([CLIP scores + position]) --> TR{Transition?}
    TR -- yes --> T[(Transition)]
    TR -- no --> I{Intro zone?}
    I -- yes --> INT[(Intro)]
    I -- no --> O{Outro zone?}
    O -- yes --> OUTR[(Outro)]
    O -- no --> C[(Content)]
```

---

## 4. Visual type decision

```mermaid
flowchart TD
    IN([Visual features]) --> TH{Face-heavy?}
    TH -- yes --> A[(Talking head)]
    TH -- no --> ST{Low motion?}
    ST -- yes --> B[(Static)]
    ST -- no --> C[(Dynamic)]
```

---

## 5. Ad detection and temporal merge

```mermaid
flowchart TD
    IN([Segments]) --> MG[Merge neighbors]
    MG --> SC[Ad score]
    SC --> CAD{Ad-like?}
    CAD -- no --> KEEP[Keep label]
    CAD -- yes --> RUN[Group ad run]
    RUN --> LEN{Long enough?}
    LEN -- no --> KEEP
    LEN -- yes --> RELABEL[Relabel as ads]
    RELABEL --> OUT[Final segments]
```

---

## 6. Output structure

```mermaid
flowchart LR
    A[Filename] --> O[video_signals.json]
    B[Duration] --> O
    C[Segments] --> O
```

---

## Constants quick reference

| Constant / threshold | Value | Where |
|---|---:|---|
| Minimum scene duration | `1.0s` | Drop very short scenes before analysis |
| Sampled frames per scene | `6` | Frame sampling |
| Motion thresholds | `<5 low`, `<20 medium`, otherwise high | `compute_motion` |
| Intro gate | `clip intro > 0.4` and `position < 0.2` | Semantic router |
| Outro gate | `clip outro > 0.4` and `position > 0.8` | Semantic router |
| Talking-head gate | `face_ratio > 0.6` and `cut_density < 0.5` | Visual type |
| Static gate | `motion low` and `face_ratio < 0.3` | Visual type |
| Merge gap | `< 1.0s` | `merge_segments` |
| Ad-candidate gate | `ad_score > 0.6` | Ad scoring |
| Ad-run minimum | `> 5s` | `merge_ad_segments` |

---

## VLM attempt

In `video_vLLM.py`, we also tried adding a VLM-based verification step after the CLIP + heuristic ad-detection pass.

```mermaid
flowchart TD
    IN([Merged segments]) --> GATE{Uncertain ad-like segment?}
    GATE -- no --> KEEP[Keep label]
    GATE -- yes --> MID[Extract middle frame]
    MID --> VLM[Qwen2-VL check]
    VLM --> DEC{Ads or content?}
    DEC -- ads --> REFINE[Refine boundaries]
    DEC -- content --> KEEP
```

This VLM branch can improve ad detection on ambiguous segments, but it is much slower than the CLIP-based pipeline.
