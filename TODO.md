# CSCI 576 Project - Task Tracking

> **Demo Date**: May 6-8, 2026
> **Your Role**: Person C - Integration / Fusion / Final Segmentation Logic
> **Current Phase**: Week 1 - Foundation

---

## Schema Design: Core vs Optional

```
CORE (Week 1)     → Must have, stable, won't change
OPTIONAL (Week 2+) → Add when ready, improves accuracy
```

| Week | Person A adds | Person B adds | Person C outputs |
|------|---------------|---------------|------------------|
| 1 | `visual_type` | `has_speech` | `type` only |
| 2 | `motion_level` | `audio_type`, `detected_keywords` | + `subtype`, `confidence` |
| 3 | `confidence` | `transcript`, `confidence` | + `summary`, tuning |

---

## Week 1 Goal: Binary Classification Pipeline

**Success Criteria:**
- [ ] Pipeline runs with CORE fields only
- [ ] Output `segments.json` loads in frontend player
- [ ] Timeline shows 2 colors (content / non_content)
- [ ] A and B approved their Week 1 schema
- [ ] Tested against `test/test_001.json`

---

## Week 1 Tasks

### Day 1-2: Schema & Algorithm Design
- [x] Design Core vs Optional schema structure
- [x] Define Week 1 classification rules (visual_type + has_speech)
- [x] Document in `docs/schemas.md`
- [x] Update `.claude/claude.md`

### Day 2-3: Team Coordination
- [ ] Share `docs/schemas.md` with Person A
- [ ] Share `docs/schemas.md` with Person B
- [ ] Share `docs/schemas.md` with Person D
- [ ] Get confirmation: A can deliver `visual_type`
- [ ] Get confirmation: B can deliver `has_speech`
- [ ] Get confirmation: D can render Week 1 output

### Day 3-4: Backend Skeleton
- [ ] Create `backend/` directory
- [ ] Create `requirements.txt`
- [ ] Create `integrator.py` with functions:
  - [ ] `load_video_signals(path)` → parse A's JSON
  - [ ] `load_audio_signals(path)` → parse B's JSON
  - [ ] `align_segments(video_segs, audio_segs)` → match by time
  - [ ] `classify_segment(video_seg, audio_seg, position)` → Week 1 rules
  - [ ] `generate_output(segments, metadata)` → write segments.json
- [ ] Create `mock_generator.py` → generate test data

### Day 4-5: Week 1 Classification
- [ ] Implement `classify_week1()`:
  ```python
  if static + no_speech → non_content
  if has_speech → content
  if dynamic → content
  default → content
  ```
- [ ] Test with mock data
- [ ] Generate `segments.json`

### Day 5-7: Integration & Validation
- [ ] Test output with frontend player
- [ ] Verify 2-color timeline works
- [ ] Compare against `test/test_001.json`
- [ ] Document accuracy / issues

---

## Week 2 Goal: Subtype Classification

### Prerequisites (from A and B)
- [ ] A delivers `motion_level` (optional)
- [ ] B delivers `audio_type` (optional)
- [ ] B delivers `detected_keywords` (optional)

### Week 2 Tasks
- [ ] Extend `classify_week2()` with optional field handling:
  ```python
  keywords = seg.get("detected_keywords", [])  # graceful default
  ```
- [ ] Add subtype classification rules:
  - [ ] Sponsor keywords → `ad`
  - [ ] Start + static/music → `intro`
  - [ ] End + outro keywords → `outro`
  - [ ] Silence → `dead_air`
- [ ] Add `confidence` scoring
- [ ] Add `skip_recommended` field
- [ ] Test with real data from A and B
- [ ] Compute accuracy metrics

---

## Week 3 Goal: Polish & Demo

### Tasks
- [ ] Tune classification thresholds
- [ ] Handle edge cases
- [ ] Add `summary` statistics
- [ ] Final integration test with D
- [ ] Prepare demo videos
- [ ] Demo dry-run

---

## Key Files

| File | Purpose | Status |
|------|---------|--------|
| `docs/schemas.md` | Core/Optional schema spec | v2.0 Done |
| `backend/integrator.py` | Main integration logic | Not started |
| `backend/mock_generator.py` | Generate test data | Not started |
| `frontend/segments.json` | Output for player | Sample exists |
| `test/*.json` | Ground truth | Available |

---

## Commands

```bash
# Run frontend
cd frontend && python -m http.server 8000

# Run integration (Week 1)
python backend/integrator.py \
  --video video_signals.json \
  --audio audio_signals.json \
  --output frontend/segments.json

# Generate mock data
python backend/mock_generator.py \
  --duration 120 \
  --output-video mock_video.json \
  --output-audio mock_audio.json
```

---

## Classification Rules Reference

### Week 1 (Core only)
```
static + no_speech → non_content
has_speech → content
dynamic → content
default → content
```

### Week 2 (+ Optional)
```
sponsor_keywords → non_content (ad)
position < 15% + static/music → non_content (intro)
position > 85% + outro_keywords → non_content (outro)
silence → non_content (dead_air)
has_speech → content (main)
```

---

*Last updated: 2026-04-20*
