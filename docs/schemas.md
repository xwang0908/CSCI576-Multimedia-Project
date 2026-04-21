# JSON Schema Specifications

> **Version**: 2.0
> **Last Updated**: 2026-04-20

---

## Data Flow

```
Person A (Video) ──→ video_signals.json ──┐
                                          ├──→ Person C (Integration) ──→ segments.json ──→ Person D (Player)
Person B (Audio) ──→ audio_signals.json ──┘
```

---

## 1. Person A Output: `video_signals.json`

### Core

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "start": 0.0,
      "end": 30.0,
      "visual_type": "static"
    },
    {
      "start": 30.0,
      "end": 120.0,
      "visual_type": "talking_head"
    }
  ]
}
```

### Full (with Optional)

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
      "confidence": 0.85
    }
  ]
}
```

### Field Reference

| Field | Required | Week | Type | Values |
|-------|----------|------|------|--------|
| `video_filename` | **CORE** | 1 | string | filename |
| `duration_seconds` | **CORE** | 1 | float | total seconds |
| `segments[].start` | **CORE** | 1 | float | seconds |
| `segments[].end` | **CORE** | 1 | float | seconds |
| `segments[].visual_type` | **CORE** | 1 | string | see below |
| `segments[].motion_level` | optional | 2 | string | `low` / `medium` / `high` |
| `segments[].confidence` | optional | 3 | float | 0.0 - 1.0 |

### visual_type Values

| Value | Description |
|-------|-------------|
| `static` | Still image, title card, logo, minimal movement |
| `talking_head` | Person speaking to camera |
| `dynamic` | High motion, b-roll, action scenes |

---

## 2. Person B Output: `audio_signals.json`

### Core

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "start": 0.0,
      "end": 30.0,
      "has_speech": false
    },
    {
      "start": 30.0,
      "end": 120.0,
      "has_speech": true
    }
  ]
}
```

### Full (with Optional)

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "start": 0.0,
      "end": 30.0,
      "has_speech": false,
      "audio_type": "music",
      "detected_keywords": [],
      "transcript": null,
      "confidence": 0.90
    },
    {
      "start": 30.0,
      "end": 90.0,
      "has_speech": true,
      "audio_type": "speech",
      "detected_keywords": ["welcome", "today"],
      "transcript": "Welcome back everyone...",
      "confidence": 0.95
    }
  ]
}
```

### Field Reference

| Field | Required | Week | Type | Values |
|-------|----------|------|------|--------|
| `video_filename` | **CORE** | 1 | string | filename |
| `duration_seconds` | **CORE** | 1 | float | total seconds |
| `segments[].start` | **CORE** | 1 | float | seconds |
| `segments[].end` | **CORE** | 1 | float | seconds |
| `segments[].has_speech` | **CORE** | 1 | boolean | true / false |
| `segments[].audio_type` | optional | 2 | string | see below |
| `segments[].detected_keywords` | optional | 2 | array | strings |
| `segments[].transcript` | optional | 3 | string | text or null |
| `segments[].confidence` | optional | 3 | float | 0.0 - 1.0 |

### audio_type Values

| Value | Description |
|-------|-------------|
| `silence` | No significant audio |
| `music` | Music only, no speech |
| `speech` | Primarily talking |
| `mixed` | Speech over music |

### detected_keywords Categories

| Category | Keywords |
|----------|----------|
| Sponsor | "sponsored", "sponsor", "brought to you", "use code", "check out", "link in description" |
| Intro | "welcome", "hello", "hey everyone", "what's up", "today we", "in this video" |
| Outro | "thanks for watching", "subscribe", "like and subscribe", "see you next", "bye" |

---

## 3. Person C Output: `segments.json`

### Core

```json
{
  "videoTitle": "Test Video",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 120.0,
  "segments": [
    {
      "label": "Intro",
      "type": "non_content",
      "start": 0.0,
      "end": 30.0
    },
    {
      "label": "Main Content",
      "type": "content",
      "start": 30.0,
      "end": 120.0
    }
  ]
}
```

### Full (with Optional)

```json
{
  "videoTitle": "Test Video",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 120.0,
  "generated_at": "2026-04-20T15:30:00Z",
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

### Field Reference

| Field | Required | Week | Type | Values |
|-------|----------|------|------|--------|
| `videoTitle` | **CORE** | 1 | string | display name |
| `videoFilename` | **CORE** | 1 | string | filename |
| `duration_seconds` | **CORE** | 1 | float | total seconds |
| `segments[].label` | **CORE** | 1 | string | human-readable |
| `segments[].type` | **CORE** | 1 | string | `content` / `non_content` |
| `segments[].start` | **CORE** | 1 | float | seconds |
| `segments[].end` | **CORE** | 1 | float | seconds |
| `segments[].subtype` | optional | 2 | string | see below |
| `segments[].confidence` | optional | 2 | float | 0.0 - 1.0 |
| `segments[].skip_recommended` | optional | 2 | boolean | true / false |
| `generated_at` | optional | 2 | string | ISO 8601 |
| `summary` | optional | 3 | object | see below |

### type Values

| Value | Description |
|-------|-------------|
| `content` | Main material viewer wants to watch |
| `non_content` | Skippable material |

### subtype Values

| Type | Subtypes |
|------|----------|
| content | `main`, `highlight` |
| non_content | `intro`, `outro`, `ad`, `promo`, `transition`, `dead_air` |

### summary Object

| Field | Type | Description |
|-------|------|-------------|
| `content_duration` | float | Total content seconds |
| `non_content_duration` | float | Total non-content seconds |
| `content_percentage` | float | Percentage that is content |

---