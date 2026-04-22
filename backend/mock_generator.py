#!/usr/bin/env python3
"""
CSCI 576 - Mock Data Generator

Generates realistic test data for video_signals.json and audio_signals.json
to test the integration pipeline before real data arrives from Person A and B.

Patterns generated:
    intro → content → ad → content → ad → content → outro

Usage:
    python mock_generator.py --duration 300 --output-video mock_video.json --output-audio mock_audio.json
    python mock_generator.py --from-test ../test/test_001.json --output-video v.json --output-audio a.json
"""

import argparse
import json
import random
from pathlib import Path
from typing import Optional


# =============================================================================
# Pattern Templates
# =============================================================================

# Realistic video patterns for different segment types
VIDEO_PATTERNS = {
    "intro": {"visual_type": "static", "motion_level": "low"},
    "content": {"visual_type": "talking_head", "motion_level": "medium"},
    "dynamic_content": {"visual_type": "dynamic", "motion_level": "high"},
    "ad": {"visual_type": "dynamic", "motion_level": "high"},
    "promo": {"visual_type": "static", "motion_level": "low"},
    "outro": {"visual_type": "static", "motion_level": "low"},
    "transition": {"visual_type": "static", "motion_level": "low"},
}

# Realistic audio patterns for different segment types
# NOTE: Keywords removed from ads to avoid "cheating" - classifier must detect ads
# based on other signals (visual_type, has_speech patterns, position, etc.)
AUDIO_PATTERNS = {
    "intro": {"has_speech": False, "audio_type": "music", "keywords": []},
    "content": {"has_speech": True, "audio_type": "speech", "keywords": []},
    "ad": {"has_speech": True, "audio_type": "speech", "keywords": []},  # No cheating!
    "promo": {"has_speech": True, "audio_type": "speech", "keywords": []},
    "outro": {"has_speech": True, "audio_type": "mixed", "keywords": []},
    "transition": {"has_speech": False, "audio_type": "silence", "keywords": []},
}


# =============================================================================
# Helper Functions for Realistic Mock Generation
# =============================================================================

def add_jitter(value: float, min_jitter: float = 1.0, max_jitter: float = 3.0) -> float:
    """Add random jitter (±1-3 seconds) to a boundary value."""
    jitter = random.uniform(min_jitter, max_jitter)
    if random.random() < 0.5:
        jitter = -jitter
    return max(0.0, value + jitter)  # Don't go negative


def subdivide_segment(start: float, end: float, min_subseg: float = 10.0) -> list[tuple[float, float]]:
    """
    Subdivide a segment into multiple sub-segments (simulating shot detection).

    Video analysis typically detects more fine-grained shots than the ground truth
    content/ad boundaries.
    """
    duration = end - start

    # Don't subdivide short segments
    if duration < min_subseg * 2:
        return [(start, end)]

    # Randomly decide how many sub-segments (1-4)
    num_subseg = random.randint(1, min(4, int(duration / min_subseg)))

    if num_subseg == 1:
        return [(start, end)]

    # Generate random cut points
    cuts = sorted([random.uniform(start + min_subseg, end - min_subseg)
                   for _ in range(num_subseg - 1)])

    # Build sub-segments
    subsegments = []
    prev = start
    for cut in cuts:
        if cut - prev >= min_subseg / 2:  # Avoid tiny segments
            subsegments.append((prev, cut))
            prev = cut
    subsegments.append((prev, end))

    return subsegments


def generate_audio_segments_independent(
    ground_truth_segments: list[dict],
    duration: float
) -> list[dict]:
    """
    Generate audio segments independently from video.

    Audio analysis detects speech/silence boundaries, which don't align
    perfectly with video shot boundaries.
    """
    audio_segments = []

    for seg in ground_truth_segments:
        gt_start = seg['final_video_start_seconds']
        gt_end = seg['final_video_end_seconds']
        seg_type = seg['type']

        # Map to pattern
        pattern_key = "content" if seg_type == "video_content" else "ad"
        audio_pattern = AUDIO_PATTERNS.get(pattern_key, AUDIO_PATTERNS["content"])

        # Add jitter to boundaries (audio detection is independent)
        audio_start = add_jitter(gt_start, 0.5, 2.0)
        audio_end = add_jitter(gt_end, 0.5, 2.0)

        # Clamp to valid range
        audio_start = max(0.0, audio_start)
        audio_end = min(duration, audio_end)

        # Ensure start < end
        if audio_start >= audio_end:
            audio_start = gt_start
            audio_end = gt_end

        audio_segments.append({
            "start": round(audio_start, 3),
            "end": round(audio_end, 3),
            "has_speech": audio_pattern["has_speech"],
            "audio_type": audio_pattern["audio_type"],
            "detected_keywords": audio_pattern["keywords"],
            "confidence": round(random.uniform(0.75, 0.95), 2)
        })

    # Sort by start time and fix any overlaps
    audio_segments.sort(key=lambda x: x["start"])

    return audio_segments


# =============================================================================
# Generate from Test File (with realistic misalignment)
# =============================================================================

def generate_from_test_file(test_path: str) -> tuple[dict, dict]:
    """
    Generate mock video and audio signals from a test/*.json ground truth file.

    Key differences from simple generation:
    1. Video boundaries have jitter (±1-3s) from ground truth
    2. Video segments are subdivided into multiple shots
    3. Audio segments are generated independently with different jitter
    4. No "cheating" keywords that directly reveal segment type

    Args:
        test_path: Path to test file (e.g., test/test_001.json)

    Returns:
        tuple: (video_signals dict, audio_signals dict)
    """
    with open(test_path, 'r') as f:
        test_data = json.load(f)

    video_filename = test_data.get('output_filename', 'test.mp4')
    duration = test_data.get('output_duration_seconds', 0)
    ground_truth = test_data.get('timeline_segments', [])

    # -------------------------------------------------------------------------
    # Generate VIDEO segments (with sub-segmentation and jitter)
    # -------------------------------------------------------------------------
    video_segments = []

    for i, seg in enumerate(ground_truth):
        gt_start = seg['final_video_start_seconds']
        gt_end = seg['final_video_end_seconds']
        seg_type = seg['type']

        # Map to pattern
        pattern_key = "content" if seg_type == "video_content" else "ad"

        # Add jitter to main boundaries (except first start and last end)
        video_start = gt_start if i == 0 else add_jitter(gt_start, 1.0, 3.0)
        video_end = gt_end if i == len(ground_truth) - 1 else add_jitter(gt_end, 1.0, 3.0)

        # Clamp
        video_start = max(0.0, video_start)
        video_end = min(duration, video_end)

        if video_start >= video_end:
            video_start, video_end = gt_start, gt_end

        # Subdivide into multiple shots (video analysis is more granular)
        subsegments = subdivide_segment(video_start, video_end, min_subseg=15.0)

        for sub_start, sub_end in subsegments:
            # Vary visual type within content segments
            if pattern_key == "content":
                visual_type = random.choice(["talking_head", "talking_head", "dynamic"])
            else:
                # Ads often have dynamic visuals
                visual_type = random.choice(["dynamic", "dynamic", "talking_head"])

            video_segments.append({
                "start": round(sub_start, 3),
                "end": round(sub_end, 3),
                "visual_type": visual_type,
                "motion_level": "high" if visual_type == "dynamic" else "medium",
                "confidence": round(random.uniform(0.80, 0.95), 2)
            })

    # Sort and ensure no gaps/overlaps
    video_segments.sort(key=lambda x: x["start"])

    # -------------------------------------------------------------------------
    # Generate AUDIO segments (independent boundaries)
    # -------------------------------------------------------------------------
    audio_segments = generate_audio_segments_independent(ground_truth, duration)

    # -------------------------------------------------------------------------
    # Build output
    # -------------------------------------------------------------------------
    video_signals = {
        "video_filename": video_filename,
        "duration_seconds": duration,
        "segments": video_segments
    }

    audio_signals = {
        "video_filename": video_filename,
        "duration_seconds": duration,
        "segments": audio_segments
    }

    return video_signals, audio_signals


# =============================================================================
# Generate Realistic Pattern (intro → content → ad → content → outro)
# =============================================================================

def generate_realistic_pattern(
    duration: float,
    num_ads: int = 2,
    video_filename: str = "generated.mp4"
) -> tuple[dict, dict]:
    """
    Generate a realistic video structure with intro, content, ads, and outro.

    Structure:
        [intro] [content] [ad] [content] [ad] [content] [outro]

    Args:
        duration: Total video duration in seconds
        num_ads: Number of ad breaks to insert
        video_filename: Name for the generated video

    Returns:
        tuple: (video_signals dict, audio_signals dict)
    """
    # Calculate segment durations
    intro_duration = random.uniform(10, 30)  # 10-30 seconds
    outro_duration = random.uniform(15, 45)  # 15-45 seconds
    ad_duration = random.uniform(30, 120)  # 30-120 seconds per ad

    total_ad_time = ad_duration * num_ads
    total_bookend_time = intro_duration + outro_duration + total_ad_time
    content_time = duration - total_bookend_time

    if content_time < 60:
        # Not enough time for content, adjust
        content_time = 60
        duration = content_time + total_bookend_time

    # Split content between ad breaks
    num_content_segments = num_ads + 1
    content_segment_duration = content_time / num_content_segments

    # Build timeline
    segments = []
    current_time = 0.0

    # Intro
    segments.append({
        "type": "intro",
        "start": current_time,
        "end": current_time + intro_duration
    })
    current_time += intro_duration

    # Interleave content and ads
    for i in range(num_content_segments):
        # Content segment
        end_time = current_time + content_segment_duration
        segments.append({
            "type": "content",
            "start": current_time,
            "end": end_time
        })
        current_time = end_time

        # Ad (except after last content)
        if i < num_ads:
            end_time = current_time + ad_duration
            segments.append({
                "type": "ad",
                "start": current_time,
                "end": end_time
            })
            current_time = end_time

    # Outro
    segments.append({
        "type": "outro",
        "start": current_time,
        "end": current_time + outro_duration
    })
    current_time += outro_duration

    # Convert to video and audio signals
    video_segments = []
    audio_segments = []

    for seg in segments:
        seg_type = seg["type"]
        start = seg["start"]
        end = seg["end"]

        # Vary content between talking_head and dynamic
        if seg_type == "content":
            pattern_key = random.choice(["content", "dynamic_content"])
        else:
            pattern_key = seg_type

        # Get patterns (default to content if not found)
        video_pattern = VIDEO_PATTERNS.get(pattern_key, VIDEO_PATTERNS["content"])
        audio_pattern = AUDIO_PATTERNS.get(pattern_key, AUDIO_PATTERNS["content"])

        video_segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "visual_type": video_pattern["visual_type"],
            "motion_level": video_pattern["motion_level"],
            "confidence": round(random.uniform(0.8, 0.98), 2)
        })

        audio_segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "has_speech": audio_pattern["has_speech"],
            "audio_type": audio_pattern["audio_type"],
            "detected_keywords": audio_pattern["keywords"],
            "confidence": round(random.uniform(0.8, 0.98), 2)
        })

    video_signals = {
        "video_filename": video_filename,
        "duration_seconds": round(current_time, 3),
        "segments": video_segments
    }

    audio_signals = {
        "video_filename": video_filename,
        "duration_seconds": round(current_time, 3),
        "segments": audio_segments
    }

    return video_signals, audio_signals


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate mock video and audio signals for testing"
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--duration", "-d",
        type=float,
        help="Generate realistic pattern with this duration (seconds)"
    )
    input_group.add_argument(
        "--from-test", "-t",
        help="Generate from a test/*.json ground truth file"
    )

    # Output options
    parser.add_argument(
        "--output-video", "-ov",
        default="mock_video_signals.json",
        help="Output path for video signals (default: mock_video_signals.json)"
    )
    parser.add_argument(
        "--output-audio", "-oa",
        default="mock_audio_signals.json",
        help="Output path for audio signals (default: mock_audio_signals.json)"
    )

    # Pattern options
    parser.add_argument(
        "--num-ads",
        type=int,
        default=2,
        help="Number of ad breaks to insert (default: 2)"
    )
    parser.add_argument(
        "--filename",
        default="generated.mp4",
        help="Video filename to use in output (default: generated.mp4)"
    )

    args = parser.parse_args()

    # Generate mock data
    if args.from_test:
        print(f"Generating from test file: {args.from_test}")
        video_signals, audio_signals = generate_from_test_file(args.from_test)
    else:
        print(f"Generating realistic pattern ({args.duration}s, {args.num_ads} ads)")
        video_signals, audio_signals = generate_realistic_pattern(
            duration=args.duration,
            num_ads=args.num_ads,
            video_filename=args.filename
        )

    # Write outputs
    with open(args.output_video, 'w') as f:
        json.dump(video_signals, f, indent=2)
    print(f"Wrote video signals: {args.output_video}")
    print(f"  {len(video_signals['segments'])} segments, {video_signals['duration_seconds']}s")

    with open(args.output_audio, 'w') as f:
        json.dump(audio_signals, f, indent=2)
    print(f"Wrote audio signals: {args.output_audio}")
    print(f"  {len(audio_signals['segments'])} segments")

    print("\nDone! You can now run:")
    print(f"  python integrator.py --video {args.output_video} --audio {args.output_audio} --output segments.json")


if __name__ == "__main__":
    main()
