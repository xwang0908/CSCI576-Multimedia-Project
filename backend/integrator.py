#!/usr/bin/env python3

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from keywords import extract_keyword_label


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


# =============================================================================
# Path A weighted-voting constants — calibrate via evaluate.py
# =============================================================================

BASE_W_A = 0.4   # visual modality (CLIP) — known to be noisy
BASE_W_B = 0.6   # audio/semantic modality (LLM on transcript) — more trusted

# Per-label tilts. CLIP is weak on ads (no visual signature); LLM is strong on
# ads/transition (sponsorship phrasing, filler chatter).
CTX_W_A = {"intro": 1.3, "outro": 1.3, "ads": 0.5, "content": 1.0, "transition": 0.8}
CTX_W_B = {"intro": 1.0, "outro": 1.0, "ads": 1.4, "content": 0.9, "transition": 1.1}


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class VideoSegment:
    start: float
    end: float
    visual_type: str  # "static" | "talking_head" | "dynamic"
    motion_level: Optional[str] = None  # "low" | "medium" | "high"
    confidence: Optional[float] = None
    label: Optional[str] = None       # Week 2 v2: A's 5-class label
    has_face: Optional[bool] = None   # Week 2 v2: face presence flag


@dataclass
class AudioSegment:
    start: float
    end: float
    has_speech: bool
    audio_type: Optional[str] = None  # "silence" | "music" | "speech" | "mixed"
    detected_keywords: Optional[list] = None
    transcript: Optional[str] = None
    confidence: Optional[float] = None
    label: Optional[str] = None  # Week 2 v2: B's 5-class label (intro|outro|ads|content|transition)


@dataclass
class OutputSegment:
    label: str
    type: str  # "content" | "non_content"
    start: float
    end: float
    subtype: Optional[str] = None  # TODO: "intro" | "outro" | "ad" | "main" | etc.
    confidence: Optional[float] = None  # TODO: 0.0-1.0 confidence score
    skip_recommended: Optional[bool] = None  # TODO: True if this segment is recommended to skip (for non_content)

def load_video_signals(path: str) -> tuple[list[VideoSegment], dict]:
    with open(path, 'r') as f:
        data = json.load(f)

    metadata = {
        'video_filename': data.get('video_filename', 'unknown.mp4'),
        'duration_seconds': data.get('duration_seconds', 0.0)
    }

    segments = []
    for seg in data.get('segments', []):
        segments.append(VideoSegment(
            start=seg['start'],
            end=seg['end'],
            visual_type=seg['visual_type'],
            motion_level=seg.get('motion_level'),
            confidence=seg.get('confidence'),
            label=seg.get('label'),
            has_face=seg.get('has_face'),
        ))

    return segments, metadata


def load_audio_signals(path: str) -> list[AudioSegment]:
    with open(path, 'r') as f:
        data = json.load(f)

    segments = []
    for seg in data.get('segments', []):
        segments.append(AudioSegment(
            start=seg['start'],
            end=seg['end'],
            has_speech=seg['has_speech'],
            audio_type=seg.get('audio_type'),
            detected_keywords=seg.get('detected_keywords'),
            transcript=seg.get('transcript'),
            confidence=seg.get('asr_confidence') or seg.get('confidence'),
            label=seg.get('label'),
        ))

    return segments


# =============================================================================
# Alignment: Merge Boundaries from Video and Audio
# =============================================================================

def get_all_boundaries(video_segs: list[VideoSegment], audio_segs: list[AudioSegment]) -> list[float]:
    """
    Collect all boundary points from both modalities.
    """
    boundaries = set()

    for seg in video_segs:
        boundaries.add(seg.start)
        boundaries.add(seg.end)

    for seg in audio_segs:
        boundaries.add(seg.start)
        boundaries.add(seg.end)

    return sorted(boundaries)


def find_segment_at_time(segments: list, time: float):    
    for seg in segments:
        if seg.start <= time < seg.end:
            return seg
        # Handle final segment end boundary
        if time == seg.end and seg == segments[-1]:
            return seg
    return None


def align_segments(
    video_segs: list[VideoSegment],
    audio_segs: list[AudioSegment]
) -> list[tuple[float, float, Optional[VideoSegment], Optional[AudioSegment]]]:
    """
    Merge video and audio segment boundaries into unified intervals.
    """
    boundaries = get_all_boundaries(video_segs, audio_segs)

    if len(boundaries) < 2:
        return []

    aligned = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]

        # Skip zero-length intervals
        if end <= start:
            continue

        # Find which segments contain the midpoint of this interval
        midpoint = (start + end) / 2
        video_seg = find_segment_at_time(video_segs, midpoint)
        audio_seg = find_segment_at_time(audio_segs, midpoint)

        aligned.append((start, end, video_seg, audio_seg))

    return aligned


# =============================================================================
# Classification Logic
# =============================================================================

# Week 2 v2 unified taxonomy — both A and B emit one of these 5 labels.
# Used by Path A / Path B helpers (Steps 4-5) to translate their 5-class
# verdict into the (type, subtype) shape the rest of the pipeline expects.
LABEL_TO_TYPE = {
    "content":    ("content",     "main"),
    "intro":      ("non_content", "intro"),
    "outro":      ("non_content", "outro"),
    "ads":        ("non_content", "ad"),
    "transition": ("non_content", "transition"),
}


def detect_genre(
    video_segs: list[VideoSegment],
    audio_segs: list[AudioSegment],
    total_duration: float,
) -> str:
    """Classify a whole video as talking_head | cinematic | mixed.

    Computed once per video; Path B uses it to pick which rule set to apply.
    Path A is genre-agnostic for now.

    dynamic_ratio is intentionally NOT used: A's CLIP marks the camera-
    follows-speaker shots in TED talks as `dynamic` too, which is
    physically reasonable but makes dynamic_ratio useless for separating
    talking_head from cinematic. speech_ratio + music_ratio are the
    discriminating signals.
    """
    if total_duration <= 0:
        return "mixed"

    speech_dur = sum(s.end - s.start for s in audio_segs if s.has_speech)
    music_dur = sum(s.end - s.start for s in audio_segs if s.audio_type == "music")

    speech_ratio = speech_dur / total_duration
    music_ratio = music_dur / total_duration

    if speech_ratio > 0.80 and music_ratio < 0.20:
        return "talking_head"
    if music_ratio > 0.25 or speech_ratio < 0.50:
        return "cinematic"
    return "mixed"


def classify_segment(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    position_ratio: float,
    segment_duration: float,
    genre: str = "mixed",
) -> tuple[str, str, Optional[float]]:
    """
    Router: fork on has_speech, delegate to Path A or Path B.

    Returns (type, subtype, confidence). segment_duration is the length
    of THIS aligned segment (end - start), not the video total — used by
    Path B's <2s and ≥30s gates.
    """
    has_speech = audio_seg.has_speech if audio_seg else True  # default: assume content

    if has_speech:
        return _classify_with_speech(video_seg, audio_seg, position_ratio, segment_duration, genre)
    else:
        return _classify_no_speech(audio_seg, position_ratio, segment_duration, genre)


def _classify_with_speech(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    position_ratio: float,
    segment_duration: float,
    genre: str,
) -> tuple[str, str, Optional[float]]:
    """Path A: keyword correction → A/B weighted voting → sanity checks.

    genre is accepted but unused; Path A is genre-agnostic by design.
    """
    # Step 1: extract A and B signals (with safe fallbacks)
    label_A = video_seg.label if video_seg and video_seg.label else "content"
    conf_A = video_seg.confidence if video_seg and video_seg.confidence else 0.7
    label_B = audio_seg.label if audio_seg and audio_seg.label else "content"
    asr_conf = audio_seg.confidence if audio_seg and audio_seg.confidence else 0.7
    conf_B = max(0.5, asr_conf)  # floor protects B's vote against bad ASR

    # Step 2: keyword extraction with position vetoes.
    # intro/outro keywords only count in the outer 10% of the video;
    # ads keywords count anywhere (sponsorship reads can appear mid-video).
    # Threshold aligned with Path B intro/outro rule and Path A sanity check.
    transcript = audio_seg.transcript if audio_seg else None
    kw_label = extract_keyword_label(transcript)
    if kw_label == "intro" and position_ratio >= 0.10:
        kw_label = None
    elif kw_label == "outro" and position_ratio <= 0.90:
        kw_label = None

    # Step 3: keyword-based correction of A's label
    label_A, conf_A = _apply_keyword_correction(label_A, conf_A, kw_label)

    # Step 4: weighted vote between A and B
    label, conf = _weighted_vote(label_A, conf_A, label_B, conf_B)

    # Step 5: post-vote sanity checks
    label, conf = _sanity_check_path_a(label, conf, position_ratio, segment_duration)

    type_, subtype = LABEL_TO_TYPE.get(label, ("content", "main"))
    return (type_, subtype, conf)


def _apply_keyword_correction(
    label_A: str, conf_A: float, kw_label: Optional[str]
) -> tuple[str, float]:
    """Use the keyword extractor as a calibration check on A's verdict.

    Keywords are a deterministic regex over the transcript — when they
    fire, they're highly reliable for intro/outro/ads. A's CLIP is noisy.
    So:
      - keyword agrees with A → A is corroborated, boost conf
      - keyword disagrees with A → A misread the visuals, keyword wins
      - keyword silent → A has no transcript-side support; dampen conf
        (lone-visual penalty: A is acting alone in a noisy modality)
    """
    if kw_label is None:
        return label_A, conf_A * 0.7
    if kw_label == label_A:
        return label_A, min(1.0, conf_A + 0.3)
    return kw_label, 0.75


def _weighted_vote(
    label_A: str, conf_A: float, label_B: str, conf_B: float
) -> tuple[str, float]:
    """Two-modality fusion.

    Agreement → noisy-OR over BARE confidences (not scores). Scores
    already carry the context_multiplier; using scores in noisy-OR would
    double-count those multipliers in the resulting confidence.
    Disagreement → argmax of base × context × conf.
    """
    if label_A == label_B:
        return label_A, 1.0 - (1.0 - conf_A) * (1.0 - conf_B)

    score_A = BASE_W_A * CTX_W_A.get(label_A, 1.0) * conf_A
    score_B = BASE_W_B * CTX_W_B.get(label_B, 1.0) * conf_B
    total = score_A + score_B
    if total <= 0:
        return label_B, conf_B  # degenerate fallback

    if score_A > score_B:
        return label_A, score_A / total
    return label_B, score_B / total


def _sanity_check_path_a(
    label: str, conf: float, position: float, segment_duration: float
) -> tuple[str, float]:
    """Post-vote demotions. If the chosen label looks implausible given
    position or duration, fall back to content. Position thresholds are
    aligned with Step 2 keyword vetoes and Path B intro/outro rule
    (0.10 / 0.90) — together they enforce that intro/outro can only
    appear in the outer 10% of the video."""
    if label == "ads" and segment_duration < 2.0:
        return "content", conf * 0.7
    if label == "intro" and position > 0.10:
        return "content", conf
    if label == "outro" and position < 0.90:
        return "content", conf
    if label == "transition" and segment_duration > 30.0:
        return "content", conf
    return label, conf


def _classify_no_speech(
    audio_seg: Optional[AudioSegment],
    position_ratio: float,
    segment_duration: float,
    genre: str,
) -> tuple[str, str, Optional[float]]:
    """Path B: rule-based classification when there is no speech.

    Branches on detected video genre. Two genre-agnostic guards run first:
      - very short segments (<2s) → transition (the only path to transition)
      - long music/mixed blocks (≥10s) in the body → ads
        (B's LLM under-labels these as `transition` because no transcript)
    Intro / outro detection lives inside each genre helper so the rules
    can carry genre-appropriate confidences.
    """
    if segment_duration < 2.0:
        return ("non_content", "transition", 0.80)

    audio_type = audio_seg.audio_type if audio_seg else None
    audio_is_music = audio_type in ("music", "mixed")
    # B's audio segment length, NOT the aligned-interval length. A long
    # music block in B can be chopped into many short aligned intervals
    # by video scene cuts inside it; using audio_duration keeps the
    # ad-detection rule stable against that fragmentation.
    audio_duration = (audio_seg.end - audio_seg.start) if audio_seg else 0.0

    # Long music/mixed block in the body = ad. Position bounds keep
    # intros/outros (handled in genre helpers) out of this rule's reach.
    if (
        audio_is_music
        and audio_duration >= 10.0
        and 0.10 < position_ratio < 0.90
    ):
        return ("non_content", "ad", 0.80)

    if genre == "talking_head":
        return _path_b_talking_head(audio_type, position_ratio)
    if genre == "cinematic":
        return _path_b_cinematic(audio_type, position_ratio)
    return _path_b_mixed(audio_type, position_ratio)


def _path_b_talking_head(
    audio_type: Optional[str], position: float
) -> tuple[str, str, float]:
    """Talking-head Path B (no speech).

    Music at the very edges = intro/outro. Everything else (including
    silence) defaults to content — silences in lectures are mostly real
    pauses between sentences, not transitions. Transition only happens
    via the <2s guard above the genre dispatch.
    """
    if audio_type in ("music", "mixed"):
        if position <= 0.10:
            return ("non_content", "intro", 0.80)
        if position >= 0.90:
            return ("non_content", "outro", 0.80)
    return ("content", "main", 0.50)


def _path_b_cinematic(
    audio_type: Optional[str], position: float, conf_offset: float = 0.0
) -> tuple[str, str, float]:
    """Cinematic Path B (no speech).

    Music at the very edges = intro/outro (drops the static + has_face
    requirement; intros and credits in films can be dynamic). Everything
    else defaults to content — middle music is score-over-scene; silence
    is a deliberate beat or quiet shot. Transition only via the <2s guard.
    """
    audio_is_music = audio_type in ("music", "mixed")

    def c(v: float) -> float:
        return max(0.0, min(1.0, v + conf_offset))

    if audio_is_music:
        if position <= 0.10:
            return ("non_content", "intro", c(0.85))
        if position >= 0.90:
            return ("non_content", "outro", c(0.80))
    return ("content", "main", c(0.55))


def _path_b_mixed(
    audio_type: Optional[str], position: float
) -> tuple[str, str, float]:
    """Fallback when genre is uncertain. Same shape as cinematic with
    confidences scaled down ~7% (single tuneable offset).
    """
    return _path_b_cinematic(audio_type, position, conf_offset=-0.07)


# =============================================================================
# Merging Adjacent Segments
# =============================================================================

def cleanup_short_non_content(
    segments: list[OutputSegment], min_duration: float = 3.0
) -> list[OutputSegment]:
    """Convert sub-`min_duration` non_content segments to content/main.

    Short non_content predictions (<3s) are typically classification noise
    sandwiched between content runs (e.g. a 0.5s 'transition' between two
    speech blocks). They clutter the timeline without offering useful skip
    targets, so we promote them to content so adjacent content merges
    naturally on the next pass.

    Ad-subtype segments are preserved regardless of length: the Path A
    sanity check already demotes ads shorter than 2s, so anything still
    labelled `ad` here is a signal we trust.
    """
    cleaned = []
    for seg in segments:
        is_short_non_ad = (
            seg.type == "non_content"
            and seg.subtype != "ad"
            and (seg.end - seg.start) < min_duration
        )
        if is_short_non_ad:
            cleaned.append(OutputSegment(
                label="Main Content",
                type="content",
                start=seg.start,
                end=seg.end,
                subtype="main",
                confidence=seg.confidence,
                skip_recommended=False,
            ))
        else:
            cleaned.append(seg)
    return cleaned


def merge_adjacent_segments(segments: list[OutputSegment]) -> list[OutputSegment]:
    """
    Merge consecutive segments with the same type and subtype.

    This cleans up the output by combining fragmented segments.
    """
    if not segments:
        return []

    merged = [segments[0]]

    for seg in segments[1:]:
        prev = merged[-1]

        # Merge if same type and subtype
        if prev.type == seg.type and prev.subtype == seg.subtype:
            # Extend previous segment
            merged[-1] = OutputSegment(
                label=prev.label,
                type=prev.type,
                start=prev.start,
                end=seg.end,
                subtype=prev.subtype,
                confidence=min(prev.confidence or 1.0, seg.confidence or 1.0),
                skip_recommended=prev.skip_recommended
            )
        else:
            merged.append(seg)

    return merged


def generate_label(seg_type: str, subtype: str, index: int) -> str:
    """Generate a human-readable label for a segment."""
    labels = {
        ("content", "main"): "Main Content",
        ("content", "highlight"): "Highlight",
        ("non_content", "intro"): "Intro",
        ("non_content", "outro"): "Outro",
        ("non_content", "ad"): "Ad Break",
        ("non_content", "promo"): "Channel Promo",
        ("non_content", "recap"): "Recap",
        ("non_content", "transition"): "Transition",
        ("non_content", "dead_air"): "Dead Air",
    }
    return labels.get((seg_type, subtype), f"Segment {index + 1}")


# =============================================================================
# Output Generation
# =============================================================================

def generate_output(
    aligned_segments: list[tuple],
    metadata: dict,
    output_path: str,
    genre: str = "mixed",
) -> dict:
    """
    Generate final segments.json for the video player.

    Args:
        aligned_segments: Output from align_segments()
        metadata: Video metadata (filename, duration)
        output_path: Where to write the JSON file
        genre: Per-video genre tag from detect_genre() — drives Path B rules

    Returns:
        The output dict (also written to file)
    """
    duration = metadata.get('duration_seconds', 0.0)
    output_segments = []

    for i, (start, end, video_seg, audio_seg) in enumerate(aligned_segments):
        # position_ratio uses video total duration; classify_segment receives
        # this segment's own length so its <2s / ≥30s gates fire correctly.
        position_ratio = (start + end) / 2 / duration if duration > 0 else 0.5
        segment_duration = end - start

        seg_type, subtype, confidence = classify_segment(
            video_seg, audio_seg, position_ratio, segment_duration, genre
        )

        output_segments.append(OutputSegment(
            label=generate_label(seg_type, subtype, i),
            type=seg_type,
            start=start,
            end=end,
            subtype=subtype,
            confidence=confidence,
            skip_recommended=(seg_type == "non_content")
        ))

    # First merge fuses adjacent same-classification runs.
    merged_segments = merge_adjacent_segments(output_segments)
    # Promote tiny non_content noise to content; second merge fuses the
    # newly-adjacent content blocks together.
    merged_segments = cleanup_short_non_content(merged_segments)
    merged_segments = merge_adjacent_segments(merged_segments)

    # Re-generate labels after merging
    for i, seg in enumerate(merged_segments):
        seg.label = generate_label(seg.type, seg.subtype, i)

    # Calculate summary statistics
    content_duration = sum(
        seg.end - seg.start for seg in merged_segments if seg.type == "content"
    )
    non_content_duration = sum(
        seg.end - seg.start for seg in merged_segments if seg.type == "non_content"
    )
    total = content_duration + non_content_duration

    output = {
        "videoTitle": metadata.get('video_filename', 'Video').replace('.mp4', ''),
        "videoFilename": metadata.get('video_filename', 'unknown.mp4'),
        "duration_seconds": duration,
        "generated_at": datetime.now().isoformat(),
        "segments": [asdict(seg) for seg in merged_segments],
        "summary": {
            "content_duration": round(content_duration, 2),
            "non_content_duration": round(non_content_duration, 2),
            "content_percentage": round(content_duration / total * 100, 1) if total > 0 else 0
        }
    }

    # Write to file
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    return output


# =============================================================================
# CLI Entry Point
# =============================================================================

def resolve_paths(args):
    if args.name:
        base = OUTPUT_DIR / args.name
        video = Path(args.video) if args.video else base / "video_signals.json"
        audio = Path(args.audio) if args.audio else base / "audio_signals.json"
        output = Path(args.output) if args.output else base / "segments.json"
    else:
        if not (args.video and args.audio):
            raise SystemExit(
                "error: provide --name <test_id> or both --video and --audio paths"
            )
        video = Path(args.video)
        audio = Path(args.audio)
        output = Path(args.output) if args.output else video.parent / "segments.json"

    for p, label in [(video, "video signals"), (audio, "audio signals")]:
        if not p.exists():
            raise SystemExit(f"error: {label} not found: {p}")

    return video, audio, output


def main():
    parser = argparse.ArgumentParser(
        description="CSCI 576 Integration Module - Merge video and audio signals"
    )
    parser.add_argument(
        "--name",
        help="test id (e.g. test_001) — auto-resolves all paths under output/<name>/"
    )
    parser.add_argument(
        "--video", "-v",
        help="Path to video_signals.json from Person A (overrides --name)"
    )
    parser.add_argument(
        "--audio", "-a",
        help="Path to audio_signals.json from Person B (overrides --name)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output path for segments.json (default: output/<name>/segments.json)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed processing information"
    )

    args = parser.parse_args()
    video_path, audio_path, output_path = resolve_paths(args)

    # Load inputs
    print(f"Loading video signals from: {video_path}")
    video_segments, metadata = load_video_signals(str(video_path))
    print(f"  Found {len(video_segments)} video segments")

    print(f"Loading audio signals from: {audio_path}")
    audio_segments = load_audio_signals(str(audio_path))
    print(f"  Found {len(audio_segments)} audio segments")

    # Align segments
    print("Aligning segments...")
    aligned = align_segments(video_segments, audio_segments)
    print(f"  Created {len(aligned)} aligned intervals")

    # Detect genre once per video — Path B rules fork on this.
    genre = detect_genre(video_segments, audio_segments, metadata.get('duration_seconds', 0.0))
    print(f"Detected genre: {genre}")

    # Generate output
    print(f"Generating output: {output_path}")
    output = generate_output(aligned, metadata, str(output_path), genre)

    # Print summary
    print("\n--- Summary ---")
    print(f"Total segments: {len(output['segments'])}")
    print(f"Content: {output['summary']['content_percentage']}% ({output['summary']['content_duration']}s)")
    print(f"Non-content: {output['summary']['non_content_duration']}s")

    if args.verbose:
        print("\n--- Segments ---")
        for seg in output['segments']:
            print(f"  [{seg['start']:.1f}-{seg['end']:.1f}] {seg['type']}: {seg['label']}")

    print(f"\nDone! Output written to: {output_path}")


if __name__ == "__main__":
    main()
