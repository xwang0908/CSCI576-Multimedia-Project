# JSON Schema Specifications

> **Version**: 3.0 (Week 2 v2 — unified 5-class taxonomy)
> **Last Updated**: 2026-04-30

---

## Data Flow

```
Person A (Video) ──→ video_signals.json ──┐
                                          ├──→ Person C (Integration) ──→ segments.json ──→ Person D (Player)
Person B (Audio) ──→ audio_signals.json ──┘
```

---

## Shared Label Taxonomy

Both A and B emit `label` from this exact 5-value enum. Integrator works in the same label space.

| Value | Description |
|-------|-------------|
| `intro` | Opening segment: logo, theme music, greeting, channel intro |
| `outro` | Closing segment: sign-off, end credits, "thanks for watching" |
| `ads` | Sponsorship, advertisement, self-promotion |
| `content` | Main subject matter (including recaps and topic transitions within content) |
| `transition` | Dead air, filler, between-section music, intermission |

Integrator output mapping:
- `content` → `type=content`
- `intro` / `outro` / `ads` / `transition` → `type=non_content` with `subtype=label`

---

## 1. Person A: `video_signals.json`

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "start": 0.0,
      "end": 30.0,
      "visual_type": "static",
      "motion_level": "low",
      "label": "intro",
      "has_face": false,
      "confidence": 0.85
    }
  ]
}
```

| Field | Type | Values |
|-------|------|--------|
| `video_filename` | string | filename |
| `duration_seconds` | float | total seconds |
| `segments[].start` | float | seconds |
| `segments[].end` | float | seconds |
| `segments[].visual_type` | string | `static` / `talking_head` / `dynamic` |
| `segments[].motion_level` | string | `low` / `medium` / `high` |
| `segments[].label` | string | shared 5-class enum (see above) |
| `segments[].has_face` | boolean | true if any face detected in sampled frames |
| `segments[].confidence` | float | 0.0 – 1.0 |

`has_face` notes: use `mediapipe` BlazeFace (`blaze_face_short_range.tflite` already in repo) on the same 6 frames sampled for motion analysis. `true` if at least one frame has a face.

---

## 2. Person B: `audio_signals.json`

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "start": 30.0,
      "end": 90.0,
      "has_speech": true,
      "label": "content",
      "audio_type": "speech",
      "transcript": "Welcome back everyone...",
      "asr_confidence": 0.92
    }
  ]
}
```

| Field | Type | Values |
|-------|------|--------|
| `video_filename` | string | filename |
| `duration_seconds` | float | total seconds |
| `segments[].start` | float | seconds |
| `segments[].end` | float | seconds |
| `segments[].has_speech` | boolean | true / false |
| `segments[].label` | string | shared 5-class enum (renamed from `content_category`) |
| `segments[].audio_type` | string | `silence` / `music` / `speech` / `mixed` |
| `segments[].transcript` | string \| null | text or null |
| `segments[].asr_confidence` | float | 0.0 – 1.0 (Whisper word-level avg) |

Deprecated in v3.0: `content_category` (renamed to `label`), `llm_confidence` (LLM self-rating unreliable; integrator uses `asr_confidence` instead).

---

## 3. Person C: `segments.json`

```json
{
  "videoTitle": "Test Video",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 120.0,
  "generated_at": "2026-04-30T15:30:00Z",
  "segments": [
    {
      "label": "Intro",
      "type": "non_content",
      "subtype": "intro",
      "start": 0.0,
      "end": 30.0,
      "confidence": 0.85,
      "skip_recommended": true
    }
  ],
  "summary": {
    "content_duration": 90.0,
    "non_content_duration": 30.0,
    "content_percentage": 75.0
  }
}
```

| Field | Type | Values |
|-------|------|--------|
| `videoTitle` | string | display name |
| `videoFilename` | string | filename |
| `duration_seconds` | float | total seconds |
| `generated_at` | string | ISO 8601 |
| `segments[].label` | string | human-readable |
| `segments[].type` | string | `content` / `non_content` |
| `segments[].subtype` | string | see below |
| `segments[].start` | float | seconds |
| `segments[].end` | float | seconds |
| `segments[].confidence` | float | 0.0 – 1.0 |
| `segments[].skip_recommended` | boolean | true / false |
| `summary.content_duration` | float | total content seconds |
| `summary.non_content_duration` | float | total non-content seconds |
| `summary.content_percentage` | float | percentage that is content |

### subtype Values (aligned with shared 5-class enum)

| Type | Subtype | Source label |
|------|---------|--------------|
| `content` | `main` | `content` |
| `non_content` | `intro` | `intro` |
| `non_content` | `outro` | `outro` |
| `non_content` | `ads` | `ads` |
| `non_content` | `transition` | `transition` |
