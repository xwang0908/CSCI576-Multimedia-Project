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


# Intro/outro can only appear in the outer 90 s of the video. Absolute
# seconds (not a ratio) so the window is meaningful on both 5-min and
# 60-min videos.
INTRO_WINDOW_SECONDS = 90.0
OUTRO_WINDOW_SECONDS = 90.0

# Multi-modality consensus required to assert intro/outro. Applied at
# classify_segment exit: anything below this conf gets demoted to content
# before any downstream merging. Set to 0.80 (matches Path B's bare
# per-genre confidence): single-genre Path B intros barely survive, the
# A+B agreement bonus pushes consenting intros well above the gate, and
# downstream sliding-window collapse handles any over-production.
INTRO_OUTRO_MIN_CONF = 0.80

# Aligned intervals shorter than this come from float-precision drift
# between A's frame-level and B's ms-level timestamps (e.g. one says
# 134.998s, the other says 135.000s, producing a 2ms phantom interval
# between two real boundaries). 100ms is below human perception and
# well above timestamp noise — drop these at align time so phantoms
# never reach classification.
MIN_INTERVAL_DURATION = 0.1

# Sliding-window collapser parameters. A run of >= MIN_FRAGMENTS consecutive
# segments shorter than SHORT_DURATION is a fragmented region; we score each
# (type,subtype) with duration * conf^2 and rewrite the whole window to the
# winner.
WINDOW_SHORT_DURATION = 30.0
WINDOW_MIN_FRAGMENTS = 3

# Strong ad run detection (P3 — pre-classification override).
# A's ads label is noisy (low precision per-segment), but A's CONSECUTIVE
# ads runs with high cut_density + high confidence are reliable. When this
# pattern appears, override the weighted vote regardless of B's verdict —
# B has been observed to misclassify ad dialogue as content (test_004) and
# ad music as transition (test_005). Per-segment filter first kills weak
# ads (low cd or low conf) so a long-tail content segment that A mislabeled
# can't extend the run boundary into actual content.
STRONG_AD_MIN_CUT_DENSITY = 0.5
STRONG_AD_MIN_CONFIDENCE = 0.65
STRONG_AD_MIN_RUN = 3
STRONG_AD_MAX_GAP = 5.0
STRONG_AD_OVERRIDE_CONF = 0.85


def is_in_intro_zone(seg_start: float) -> bool:
    return seg_start < INTRO_WINDOW_SECONDS


def is_in_outro_zone(seg_end: float, total_duration: float) -> bool:
    return seg_end > total_duration - OUTRO_WINDOW_SECONDS


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
    cut_density: Optional[float] = None  # used by strong-ad-run detection


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
            cut_density=seg.get('cut_density'),
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

        # Drop phantom intervals from float-precision boundary drift
        # between A and B (see MIN_INTERVAL_DURATION docstring).
        if end - start < MIN_INTERVAL_DURATION:
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


def detect_strong_ad_runs(
    video_segs: list[VideoSegment],
) -> list[tuple[float, float]]:
    """Find time ranges where A's CLIP is strongly signalling an ad block.

    Two-stage filter (per-segment first, then run aggregation):
      1. Keep only A's ads-labeled segments with cut_density >= 0.5 AND
         confidence >= 0.65. This drops two failure modes: low-cd long
         segments (A mislabels a slow content shot as ads) and low-conf
         scattered segments (CLIP's per-frame noise).
      2. Group surviving segments into runs, allowing gaps up to 5s.
         Only runs of >= 3 segments survive — single strong ads can be
         legitimate content frames, but 3+ in a row means a real ad block.

    Returns the [start, end] span of each surviving run. Aligned segments
    falling inside any span get force-classified as ad in classify_segment.
    """
    strong = [
        s for s in video_segs
        if s.label == "ads"
        and (s.cut_density or 0.0) >= STRONG_AD_MIN_CUT_DENSITY
        and (s.confidence or 0.0) >= STRONG_AD_MIN_CONFIDENCE
    ]
    if not strong:
        return []

    runs: list[list[VideoSegment]] = [[strong[0]]]
    for s in strong[1:]:
        if s.start - runs[-1][-1].end <= STRONG_AD_MAX_GAP:
            runs[-1].append(s)
        else:
            runs.append([s])

    return [
        (run[0].start, run[-1].end)
        for run in runs
        if len(run) >= STRONG_AD_MIN_RUN
    ]


def is_in_strong_ad_run(
    seg_start: float, seg_end: float, runs: list[tuple[float, float]]
) -> bool:
    """True if the aligned segment overlaps any strong ad run."""
    for run_start, run_end in runs:
        if seg_start < run_end and seg_end > run_start:
            return True
    return False


def classify_segment(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    seg_start: float,
    seg_end: float,
    total_duration: float,
    genre: str = "mixed",
    strong_ad_runs: Optional[list[tuple[float, float]]] = None,
) -> tuple[str, str, Optional[float]]:
    """
    Router: fork on has_speech, delegate to Path A or Path B.

    Computes intro/outro zone flags once and threads them down so each
    helper just consumes booleans instead of re-deriving from a ratio.
    """
    # Strong-ad-run override (P3): pre-classification short-circuit.
    # When A's CLIP shows a sustained, visually-coherent ad block, trust
    # it over B regardless of speech state. B has been observed to label
    # ad dialogue as content and ad music as transition, so neither path
    # below would catch these reliably without help from A.
    if strong_ad_runs and is_in_strong_ad_run(seg_start, seg_end, strong_ad_runs):
        return ("non_content", "ad", STRONG_AD_OVERRIDE_CONF)

    has_speech = audio_seg.has_speech if audio_seg else True  # default: assume content

    segment_duration = seg_end - seg_start
    in_intro = is_in_intro_zone(seg_start)
    in_outro = is_in_outro_zone(seg_end, total_duration)

    if has_speech:
        type_, subtype, conf = _classify_with_speech(
            video_seg, audio_seg, in_intro, in_outro, segment_duration, genre
        )
    else:
        type_, subtype, conf = _classify_no_speech(
            video_seg, audio_seg, in_intro, in_outro, segment_duration, genre
        )

    # Strict gate: intro/outro requires multi-modality consensus.
    # Path A's noisy-OR (when A and B agree) and Path B's agreement bonus
    # (when A's label backs the audio-derived verdict) push real intros
    # above the threshold; single-signal speculation drops below it.
    if subtype in ("intro", "outro") and (conf or 0.0) < INTRO_OUTRO_MIN_CONF:
        return ("content", "main", conf)
    return (type_, subtype, conf)


def _classify_with_speech(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    in_intro_zone: bool,
    in_outro_zone: bool,
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

    # When the speaker is talking, B's intro/outro labels are unreliable.
    # The LLM systematically mislabels speech-heavy openings as intros
    # ("Welcome to my lecture..." → intro) and closings as outros
    # ("Thank you, in conclusion..." → outro). Observed over-extends:
    # test_001 80s, test_003 44s, test_004 74s on intro side; test_001
    # outro sandwich also driven by this. Force to content — real
    # intros/outros are visual title/credit cards which have no speech
    # and are caught by Path B's (no_face + zone) rule.
    if label_B in ("intro", "outro"):
        label_B = "content"

    # Step 2: keyword extraction with position vetoes.
    # intro/outro keywords only count inside their respective zones;
    # ads keywords count anywhere (sponsorship reads can appear mid-video).
    transcript = audio_seg.transcript if audio_seg else None
    kw_label = extract_keyword_label(transcript)
    if kw_label == "intro" and not in_intro_zone:
        kw_label = None
    elif kw_label == "outro" and not in_outro_zone:
        kw_label = None

    # Step 2.5: A high-confidence ads override (before keyword correction).
    # When A is very sure a segment is an ad (raw conf>=0.75), trust it
    # over B's verdict. CLIP picks up visual discontinuity at ad boundaries
    # that B's LLM misses when ad copy is conversational (e.g. a snack ad
    # that reads like a story has no sponsorship keywords). Placed BEFORE
    # keyword correction so the lone-visual penalty (*0.7) doesn't drag
    # conf below the 0.75 gate — that penalty is meant for the weighted
    # vote, not for cases where A alone is highly certain.
    if label_A == "ads" and conf_A >= 0.75:
        label, conf = _sanity_check_path_a(
            "ads", conf_A, in_intro_zone, in_outro_zone, segment_duration
        )
        type_, subtype = LABEL_TO_TYPE.get(label, ("content", "main"))
        return (type_, subtype, conf)

    # Step 3: keyword-based correction of A's label
    label_A, conf_A = _apply_keyword_correction(label_A, conf_A, kw_label)

    # Step 4: weighted vote between A and B
    label, conf = _weighted_vote(label_A, conf_A, label_B, conf_B)

    # Step 5: post-vote sanity checks
    label, conf = _sanity_check_path_a(
        label, conf, in_intro_zone, in_outro_zone, segment_duration
    )

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
    label: str,
    conf: float,
    in_intro_zone: bool,
    in_outro_zone: bool,
    segment_duration: float,
) -> tuple[str, float]:
    """Post-vote demotions. If the chosen label looks implausible given
    position or duration, fall back to content. Zone flags are shared
    with Step 2 keyword vetoes and the Path B helpers — together they
    enforce that intro/outro can only appear in the outer 90s of the
    video."""
    if label == "ads" and segment_duration < 2.0:
        return "content", conf * 0.7
    # Position-based demotes lower conf so the sandwich collapser later
    # can recognize these as low-confidence content that should be
    # absorbed back into a neighboring intro/outro run.
    if label == "intro" and not in_intro_zone:
        return "content", conf * 0.5
    if label == "outro" and not in_outro_zone:
        return "content", conf * 0.5
    if label == "transition" and segment_duration > 30.0:
        return "content", conf
    return label, conf


def _classify_no_speech(
    video_seg: Optional[VideoSegment],
    audio_seg: Optional[AudioSegment],
    in_intro_zone: bool,
    in_outro_zone: bool,
    segment_duration: float,
    genre: str,
) -> tuple[str, str, Optional[float]]:
    """Path B: rule-based classification when there is no speech.

    Branches on detected video genre. Two genre-agnostic guards run first:
      - very short segments (<2s) → transition (the only path to transition)
      - long music/mixed blocks (≥10s) outside the intro/outro zones → ads
        (B's LLM under-labels these as `transition` because no transcript)
    Intro / outro detection lives inside each genre helper, after which
    we apply an A+B agreement bonus: if A's CLIP label also says
    intro/outro, noisy-OR boost the conf to reflect multi-modality
    consensus (mirrors what Path A's _weighted_vote does in the speech
    path). Single-modality intros stay at 0.80-0.85 and get demoted by
    the threshold gate in classify_segment.
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
    has_face = video_seg.has_face if video_seg else None

    # Long music/mixed block in the body = ad — exclude cinematic only.
    # Rationale: in lecture/talking-head videos, a 10s+ music block in the
    # body is almost always sponsorship/ad bumper. In cinematic genres
    # (films, documentaries), long music blocks are score-over-scene and
    # firing this rule generated huge FPs (test_005 had a 126.8s FP from
    # one 76.5s music block). For mixed (uncertain genre) we keep the
    # rule because mixed videos still tend to follow the talking_head ad
    # pattern; dropping it caused test_002 to lose Ad 2 entirely.
    # Cinematic ads now rely on Path A's strong-ad-run override.
    if (
        genre != "cinematic"
        and audio_is_music
        and audio_duration >= 10.0
        and not in_intro_zone
        and not in_outro_zone
    ):
        return ("non_content", "ad", 0.80)

    if genre == "talking_head":
        type_, subtype, conf = _path_b_talking_head(in_intro_zone, in_outro_zone, has_face)
    elif genre == "cinematic":
        type_, subtype, conf = _path_b_cinematic(in_intro_zone, in_outro_zone, has_face)
    else:
        type_, subtype, conf = _path_b_mixed(in_intro_zone, in_outro_zone, has_face)

    # A+B agreement bonus on intro/outro. Without A's backing, conf stays
    # at the bare per-genre value (0.80-0.85) and gets killed by the
    # threshold gate downstream.
    if subtype in ("intro", "outro") and video_seg and video_seg.label == subtype:
        conf_A = video_seg.confidence or 0.7
        conf = 1.0 - (1.0 - conf) * (1.0 - conf_A)

    return (type_, subtype, conf)


def _path_b_talking_head(
    in_intro_zone: bool,
    in_outro_zone: bool,
    has_face: Optional[bool],
) -> tuple[str, str, float]:
    """Talking-head Path B (no speech).

    Intro/outro require: in zone + no face on screen (has_face != True).
    Audio type is irrelevant — silence (pre-intro buffer) and music
    (title music) are both legitimate. A presenter on screen during a
    silent pause is content, not intro/outro. Middle silences (lecture
    pauses) fall through to content because they are not in any zone.
    """
    no_face = has_face is not True
    if no_face:
        if in_intro_zone:
            return ("non_content", "intro", 0.80)
        if in_outro_zone:
            return ("non_content", "outro", 0.80)
    return ("content", "main", 0.50)


def _path_b_cinematic(
    in_intro_zone: bool,
    in_outro_zone: bool,
    has_face: Optional[bool],
    conf_offset: float = 0.0,
) -> tuple[str, str, float]:
    """Cinematic Path B (no speech).

    Same intro/outro rule as talking_head: in zone + no face → intro/
    outro. A character close-up over score (has_face=True) is content,
    not opening credits. Middle music/silence default to content —
    score-over-scene or deliberate beat.
    """
    no_face = has_face is not True

    def c(v: float) -> float:
        return max(0.0, min(1.0, v + conf_offset))

    if no_face:
        if in_intro_zone:
            return ("non_content", "intro", c(0.85))
        if in_outro_zone:
            return ("non_content", "outro", c(0.80))
    return ("content", "main", c(0.55))


def _path_b_mixed(
    in_intro_zone: bool,
    in_outro_zone: bool,
    has_face: Optional[bool],
) -> tuple[str, str, float]:
    """Fallback when genre is uncertain. Same shape as cinematic with
    confidences scaled down ~7% (single tuneable offset).
    """
    return _path_b_cinematic(
        in_intro_zone, in_outro_zone, has_face, conf_offset=-0.07
    )


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


def collapse_fragmented_window(
    segments: list[OutputSegment],
    short_duration: float = WINDOW_SHORT_DURATION,
    min_fragments: int = WINDOW_MIN_FRAGMENTS,
) -> list[OutputSegment]:
    """Find runs of >= min_fragments consecutive short segments and
    collapse each run to its dominant label.

    "Dominant" = highest score by Σ duration * conf² across the window.
    Squaring confidence rewards strong signals over weak ones with the
    same total duration; long high-conf segments dominate, low-conf
    noise contributes little.

    Long segments (>= short_duration) act as natural boundaries — they
    are never inside a window and never modified.

    Output conf for the collapsed run = winner's share of total score
    (winner_score / total_score). Uniform-label runs land near 1.0;
    contested runs land lower, signalling weak consensus.
    """
    if not segments:
        return []

    n = len(segments)
    result = list(segments)
    i = 0
    while i < n:
        if (result[i].end - result[i].start) >= short_duration:
            i += 1
            continue
        # Extend forward through consecutive short segments.
        j = i
        while j < n and (result[j].end - result[j].start) < short_duration:
            j += 1
        if j - i < min_fragments:
            i = j
            continue

        # Score each (type, subtype) over the window.
        scores: dict[tuple[str, str], float] = {}
        for k in range(i, j):
            seg = result[k]
            duration = seg.end - seg.start
            conf = seg.confidence or 0.5
            key = (seg.type, seg.subtype)
            scores[key] = scores.get(key, 0.0) + duration * conf * conf

        total = sum(scores.values())
        winner_key = max(scores, key=scores.get)
        winner_conf = scores[winner_key] / total if total > 0 else 0.5
        winner_type, winner_subtype = winner_key
        winner_label = generate_label(winner_type, winner_subtype, 0)

        for k in range(i, j):
            result[k] = OutputSegment(
                label=winner_label,
                type=winner_type,
                start=result[k].start,
                end=result[k].end,
                subtype=winner_subtype,
                confidence=winner_conf,
                skip_recommended=(winner_type == "non_content"),
            )
        i = j
    return result


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
    strong_ad_runs: Optional[list[tuple[float, float]]] = None,
) -> dict:
    """
    Generate final segments.json for the video player.

    Args:
        aligned_segments: Output from align_segments()
        metadata: Video metadata (filename, duration)
        output_path: Where to write the JSON file
        genre: Per-video genre tag from detect_genre() — drives Path B rules
        strong_ad_runs: Pre-computed strong ad spans from detect_strong_ad_runs()

    Returns:
        The output dict (also written to file)
    """
    duration = metadata.get('duration_seconds', 0.0)
    output_segments = []

    for i, (start, end, video_seg, audio_seg) in enumerate(aligned_segments):
        seg_type, subtype, confidence = classify_segment(
            video_seg, audio_seg, start, end, duration, genre, strong_ad_runs
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
    # Collapse fragmented runs to their dominant label. The intro/outro
    # threshold gate inside classify_segment has already filtered weak
    # speculation, so the windower scores against a clean signal pool.
    merged_segments = collapse_fragmented_window(merged_segments)
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

    # Pre-compute strong ad runs (P3) — A's high-confidence consecutive ad
    # blocks that override the weighted vote.
    strong_ad_runs = detect_strong_ad_runs(video_segments)
    print(f"Strong ad runs: {len(strong_ad_runs)}")
    for s, e in strong_ad_runs:
        print(f"  {s:.1f}-{e:.1f}  ({e-s:.1f}s)")

    # Generate output
    print(f"Generating output: {output_path}")
    output = generate_output(aligned, metadata, str(output_path), genre, strong_ad_runs)

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
