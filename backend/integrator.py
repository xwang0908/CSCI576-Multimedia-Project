#!/usr/bin/env python3

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class VideoSegment:
    start: float
    end: float
    visual_type: str  # "static" | "talking_head" | "dynamic"
    motion_level: Optional[str] = None  # TODO: "low" | "medium" | "high"
    confidence: Optional[float] = None  # TODO


@dataclass
class AudioSegment:
    start: float
    end: float
    has_speech: bool
    audio_type: Optional[str] = None  # TODO "silence" | "music" | "speech" | "mixed"
    detected_keywords: Optional[list] = None  # TODO: list of detected keywords/phrases (e.g. "sponsored", "thanks for watching")
    transcript: Optional[str] = None  # TODO: full transcript text for this segment (if available)
    confidence: Optional[float] = None  # TODO: confidence score for speech detection (0.0-1.0)
    content_category: Optional[str] = None  # B's LLM category: Content / Sponsorship/Advertisement / Intro / Outro / Recap / Transition/Intermission / Dead Air/Filler / Self-Promotion


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
            confidence=seg.get('confidence')
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
            confidence=seg.get('confidence'),
            content_category=seg.get('content_category'),
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

def classify_segment(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    position_ratio: float,
    duration: float
) -> tuple[str, str, Optional[float]]:
    """
    Apply classification rules to determine segment type.

    Returns:
        tuple: (type, subtype, confidence)

    """
    # Extract values with defaults for missing data
    visual_type = video_seg.visual_type if video_seg else "unknown"
    has_speech = audio_seg.has_speech if audio_seg else True  # Default to content

    # TODO: optional fields (with graceful defaults)
    audio_type = getattr(audio_seg, 'audio_type', None) or "unknown"
    keywords = getattr(audio_seg, 'detected_keywords', None) or []
    motion_level = getattr(video_seg, 'motion_level', None) or "unknown"
    content_category = getattr(audio_seg, 'content_category', None)

    # Confidence tracking: start at base, increase with signal agreement
    confidence = 0.7

    # -------------------------------------------------------------------------
    # Highest-priority rule: trust B's LLM category when present.
    # -------------------------------------------------------------------------
    CATEGORY_MAP = {
        "Sponsorship/Advertisement": ("non_content", "ad",         0.90),
        "Self-Promotion":            ("non_content", "promo",      0.85),
        "Intro":                     ("non_content", "intro",      0.85),
        "Outro":                     ("non_content", "outro",      0.85),
        "Recap":                     ("non_content", "recap",      0.75),
        "Transition/Intermission":   ("non_content", "transition", 0.75),
        "Dead Air/Filler":           ("non_content", "dead_air",   0.85),
    }
    if content_category in CATEGORY_MAP:
        return CATEGORY_MAP[content_category]

    # -------------------------------------------------------------------------
    # Week 2 Rules (check first - more specific)
    # -------------------------------------------------------------------------

    # Rule: Sponsor keywords → ad
    sponsor_keywords = ["sponsored", "sponsor", "brought to you", "use code",
                        "check out", "link in description"]
    if any(kw.lower() in ' '.join(keywords).lower() for kw in sponsor_keywords):
        return ("non_content", "ad", 0.9)

    # Rule: Start position + static/music → intro
    if position_ratio < 0.15 and not has_speech:
        if visual_type == "static" or audio_type == "music":
            return ("non_content", "intro", 0.85)

    # Rule: End position + outro keywords → outro
    outro_keywords = ["thanks for watching", "subscribe", "like and subscribe",
                      "see you next", "bye", "peace out"]
    if position_ratio > 0.85:
        if any(kw.lower() in ' '.join(keywords).lower() for kw in outro_keywords):
            return ("non_content", "outro", 0.85)

    # Rule: Silence → dead_air
    if audio_type == "silence":
        return ("non_content", "dead_air", 0.9)

    # -------------------------------------------------------------------------
    # Week 1 Rules (core classification)
    # -------------------------------------------------------------------------

    # Rule 1: Static + no speech → non_content
    if visual_type == "static" and not has_speech:
        # Determine subtype based on position
        if position_ratio < 0.15:
            return ("non_content", "intro", 0.8)
        elif position_ratio > 0.85:
            return ("non_content", "outro", 0.8)
        else:
            return ("non_content", "transition", 0.75)

    # Rule 2: Has speech → content
    if has_speech:
        confidence = 0.85
        # Higher confidence if video also shows talking head
        if visual_type == "talking_head":
            confidence = 0.95
        return ("content", "main", confidence)

    # Rule 3: Dynamic video → content
    if visual_type == "dynamic":
        return ("content", "main", 0.8)

    # Default: content (conservative - don't skip what might be valuable)
    return ("content", "main", 0.6)


# =============================================================================
# Merging Adjacent Segments
# =============================================================================

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
    output_path: str
) -> dict:
    """
    Generate final segments.json for the video player.

    Args:
        aligned_segments: Output from align_segments()
        metadata: Video metadata (filename, duration)
        output_path: Where to write the JSON file

    Returns:
        The output dict (also written to file)
    """
    duration = metadata.get('duration_seconds', 0.0)
    output_segments = []

    for i, (start, end, video_seg, audio_seg) in enumerate(aligned_segments):
        # Calculate position in video (0.0 to 1.0)
        position_ratio = (start + end) / 2 / duration if duration > 0 else 0.5

        # Classify this segment
        seg_type, subtype, confidence = classify_segment(
            video_seg, audio_seg, position_ratio, duration
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

    # Merge adjacent segments with same classification
    merged_segments = merge_adjacent_segments(output_segments)

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

    # Generate output
    print(f"Generating output: {output_path}")
    output = generate_output(aligned, metadata, str(output_path))

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
