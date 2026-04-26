# Week 1 Pipeline Evaluation Report

> Generated 2026-04-25. Two test videos evaluated end-to-end (test_001, test_002).

---

## 1. Current Results

### test_001 (24 min, 3 ads totalling 178.7 s)

| Metric | Value |
|---|---|
| Final segments | 46 (23 content, 23 non_content) |
| Total non_content predicted | 236.6 s |
| **Accuracy** | **94.2 %** |
| **Precision** | **70.1 %** |
| **Recall** | **92.1 %** |
| **F1** | **79.6 %** |

| Ad | Range | Recall |
|---|---|---|
| Ad 1 (`ads_009.mp4`, 118 s) | 106.2 – 224.4 s | 99.1 % |
| Ad 2 (`ads_014.mp4`, 32 s) | 628.2 – 660.3 s | 58.3 % |
| Ad 3 (`ads_008.mp4`, 28 s) | 1088.7 – 1117.1 s | 100 % |

### test_002 (23 min, 3 ads totalling 150.3 s)

| Metric | Value |
|---|---|
| Final segments | 168 (84 content, 84 non_content) |
| Total non_content predicted | 577.7 s |
| **Accuracy** | **64.8 %** |
| **Precision** | **21.6 %** |
| **Recall** | **82.7 %** |
| **F1** | **34.3 %** |

| Ad | Range | Recall |
|---|---|---|
| Ad 1 (60 s) | 270.7 – 330.7 s | 68.3 % |
| Ad 2 (30 s) | 678.6 – 708.7 s | 80.7 % |
| Ad 3 (60 s) | 1115.2 – 1175.4 s | 100 % |

### Average across the two tests

| Accuracy | Precision | Recall | F1 |
|---|---|---|---|
| 79.5 % | 45.9 % | 87.4 % | 57.0 % |

**Headline finding:** Recall is now strong (87 % avg), but precision is weak — especially on test_002 (21.6 %). The pipeline catches almost all ad seconds but also produces many false-positive non_content segments (168 segments on test_002, half of which are <2 s noise from over-tagged LLM transitions and silences).

---

## 2. Per-Person Analysis

### Person A — `video.py` (TransNetV2 + CLIP)

**What works**
- TransNet scene-cut detection looks reasonable: ~110 raw cuts on test_001 reduced to 10 merged segments, ~150 raw → 17 merged on test_002.
- `motion_level` and `confidence` populated for every segment.

**What's wrong**
- `visual_type` distribution is heavily skewed to `dynamic` (8/10 on test_001, 12/17 on test_002). With ads almost always `dynamic`, this signal alone cannot separate ads from main content.
- **Zero segments tagged `talking_head`** in either test, even though both videos are mostly lectures. Cause: [video.py:148-159](../backend/video.py#L148) uses `motion_level == low → static` first, which captures all stationary lecture content; `talking_head` only fires when CLIP returns `core_content` / `self_promotion`, which it rarely does.
- The CLIP `label` field (one of intro/outro/advertisement/recap/filler/…) is **noisy and unreliable**. During real ads, CLIP returns "intro", "filler", "recap" far more often than "advertisement". On test_002 only 0/3 ads contain a segment that CLIP labelled `advertisement`.
- **CLIP only sees one frame per segment** ([video.py:101](../backend/video.py#L101) uses `frames[0]`) even though 6 frames are sampled for motion. Wasted signal.
- After temporal merging, segments are too coarse — single segments can span >3 minutes, hiding ad boundaries.

**Week 2 modifications for A**
1. **Re-balance the visual_type rules.** Don't let `motion_level == low` short-circuit to `static` for talking-head footage. Require both static motion AND a CLIP signal that is *not* a person.
2. **Average CLIP across all sampled frames** instead of just the first.
3. **Add face/person detection** ([blaze_face_short_range.tflite](../backend/blaze_face_short_range.tflite) is in the repo and unused). Output a `has_face` boolean per segment.
4. **Output cut density** (TransNet cuts per second within each merged segment). Ads typically have higher cut frequency.
5. **Stop merging across CLIP label changes.** `merge_segments()` currently merges if either visual_type OR label match. Switch to merge only when *both* match, so ad boundaries are preserved.
6. **Calibrate CLIP prompts.** 10 classes dilute the "advertisement" probability. Try a 3-class scheme (`is_ad`, `is_main_content`, `is_transition`) and emit raw probabilities.

### Person B — `ollama_audio.py` (Whisper + Ollama qwen2.5:3b)

**What works**
- Fine-grained segmentation: 135 segments on test_001, 293 on test_002 — high enough to catch short ad boundaries.
- `transcript` populated for every speech segment.
- `content_category` (7-way LLM classification) is the integrator's primary signal — the single most valuable field in the whole pipeline.

**What's wrong**
- **Schema mismatch with [docs/schemas.md](schemas.md).** B emits `asr_confidence`, `llm_confidence`, `content_category` instead of the spec's `detected_keywords`, `confidence`. C consumes `content_category` directly, but C's sponsor/outro keyword rules still cannot fire because `detected_keywords` is missing.
- **LLM under-detects ads.** On test_001, in the 3 GT ads the LLM only tagged 1 segment as `Sponsorship/Advertisement` — most got `Transition/Intermission` or `Content`. Likely cause: LLM has no scene-cut hint, no audio shift signal; with `temperature=0` and no boundary cue it stays anchored to the prior topic.
- **Silence over-segmentation.** test_002 has 29 silence + 54 music segments totalling >250 s. Many silences are short pauses inside normal speech, not real "dead air". They become false-positive `dead_air` segments downstream.
- `silence_threshold = 0.015 RMS` is too sensitive; pauses between sentences trigger it.
- `audio_type` only emits `silence`/`music`/`speech` — the spec also defines `mixed` (speech over music) which is common in ads.

**Week 2 modifications for B**
1. **Align output schema with [docs/schemas.md](schemas.md):**
   - Rename `asr_confidence` → `confidence` (or add `confidence` as the consensus value).
   - Add `detected_keywords` field — extract sponsor/intro/outro keywords from `transcript` using the keyword list in `schemas.md` § 2.
   - Keep `content_category` as an extra (non-spec) field.
2. **Tighten silence detection.** Raise `silence_threshold` to ~0.03 RMS and require `min_duration ≥ 1.5 s` before emitting a silence segment. Goal: drop from 29 → ~5 silences on test_002.
3. **Boundary-aware LLM prompting.** Whisper produces hard timestamp gaps. Pass these to the LLM as "the speaker abruptly changes here, decide if this is an ad boundary."
4. **Boost ad detection.** Inject a system rule: any segment whose transcript contains commercial vocabulary (price, brand, "buy", "use code", "sponsored by") → force category `Sponsorship/Advertisement`, bypass the LLM softmax.
5. **Add `audio_type=mixed`** for speech-over-music segments.

### Person C — `integrator.py` (you)

**What works**
- Boundary alignment is correct: collects all A and B boundaries, splits the timeline at each, finds the segment that contains each midpoint. test_001's 154 aligned intervals from 10 + 135 inputs verify this works.
- Reads B's `content_category` (CATEGORY_MAP) as the highest-priority classification signal. This is the dominant driver of recall.

**What's wrong**
- **Too many short non_content segments survive merging.** test_002 has 84 non_content segments after merging, ~40 of them <2 s. Most are LLM micro-tagged transitions inside speech, not real non_content. The merge step only collapses adjacent segments with the *same* (type, subtype) — so a 1-second `transition` between two `content/main` segments stays.
- **Position-based intro rule is over-eager.** [integrator.py:191-194](../backend/integrator.py#L191) `position < 0.15 + (static OR music) + no_speech → intro` fires for any music in the first 15 % of the timeline. Now that CATEGORY_MAP handles intros, this rule mostly causes false positives.
- **`silence → dead_air` fires unconditionally.** [integrator.py:204-205](../backend/integrator.py#L204) Any `audio_type == silence` becomes non_content regardless of duration. With B over-segmenting silences (test_002), this produces ~25 false-positive `dead_air` segments.
- **No transcript fallback for keywords.** Sponsor / outro keyword rules cannot fire because B doesn't emit `detected_keywords`. The integrator could regex `transcript` itself instead of waiting for B.
- **`confidence` is not used to break conflicts.** When A says `talking_head` (high conf) and B says `Sponsorship/Advertisement` (low conf), the integrator always lets B's CATEGORY_MAP win regardless of the confidence values both modules report.

**Week 2 modifications for C**
1. **Filter sub-3 s non_content segments** (post-merge). Demote any `non_content` segment <3 s back to `content/main`, then re-merge.
2. **Drop or narrow the position-based intro rule.** Either remove (CATEGORY_MAP handles it) or tighten to `position < 0.05 AND static AND duration ≥ 5 s`.
3. **Require `silence ≥ 5 s`** before emitting `dead_air`.
4. **Transcript-based keyword fallback.** Run a regex against `transcript` for sponsor / outro vocabulary when `detected_keywords` is empty.
5. **Use `confidence` to break A↔B conflicts.** Pick the higher-confidence side when CATEGORY_MAP and visual_type disagree.

---
