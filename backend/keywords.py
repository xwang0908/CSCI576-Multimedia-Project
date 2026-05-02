"""Regex-based keyword classifier for transcripts.

Pure text in, label out. Position vetoes and confidence weighting live in
the integrator's classify_segment(); this module never sees timing.

Public API:
    extract_keyword_label(transcript) -> "intro" | "outro" | "ads" | None

A return value of None means "no keyword evidence" — NOT "vote for content".
The caller must distinguish silence from a positive vote.
"""

from __future__ import annotations

import re
from typing import Optional


# Order of evaluation: ads -> outro -> intro.
# Ads phrases are the most specific and least likely to false-positive,
# so they win when multiple categories could match (e.g. an ad read at
# the end of a video that also contains "thanks for watching").

ADS_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bsponsor(ed)?\s+by\b",
        r"\bbrought\s+to\s+you\s+by\b",
        r"\b(today'?s|this)\s+(video|episode)('?s)?\s+sponsor\b",
        r"\bthanks?\s+to\s+.{0,40}\bfor\s+sponsoring\b",
        r"\buse\s+(code|promo|coupon)\b",
        r"\bpromo\s+code\b",
        r"\b(check|head)\s+out\s+.{0,60}\.(com|net|io|co)\b",
        r"\blink\s+in\s+(the\s+)?(description|bio)\b",
        r"\bgo\s+to\s+\S+\.(com|net|io|co)\b",
        r"\bget\s+\d+%?\s+off\b",
        r"\bfree\s+trial\b",
        r"\bsign\s+up\s+at\b",
    ]
]

OUTRO_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bthanks?\s+(for\s+)?(watching|listening)\b",
        r"\bsee\s+you\s+(next|in\s+the\s+next)\b",
        r"\b(don'?t|do\s+not)\s+forget\s+to\s+(subscribe|like|comment|share)\b",
        r"\b(please\s+)?(like|subscribe)\s+and\s+(subscribe|like)\b",
        r"\bhit\s+the\s+(bell|subscribe)\b",
        r"\bthat'?s\s+(it|all)\s+for\s+(today|this\s+(video|episode))\b",
        r"\buntil\s+next\s+time\b",
        r"\bcatch\s+you\s+(later|next\s+time)\b",
        r"\bsignin[g']?\s+off\b",
        r"\bgoodbye\b",
    ]
]

INTRO_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bwelcome\s+(back\s+)?to\b",
        r"\bhello\s+(everyone|everybody|guys|folks|friends)\b",
        r"\bhi\s+(everyone|everybody|guys|folks|friends)\b",
        r"\bhey\s+(everyone|everybody|guys|folks|friends)\b",
        r"\bin\s+this\s+(video|episode|tutorial|lesson)\b",
        r"\btoday\s+(we'?re|i'?m|we\s+are|i\s+am)\s+(going\s+to|gonna)\b",
        r"\bin\s+today'?s\s+(video|episode|tutorial)\b",
        r"\bmy\s+name\s+is\b.{0,40}\b(and|welcome)\b",
        r"\blet'?s\s+(get\s+)?(started|begin|dive\s+in)\b",
    ]
]


def extract_keyword_label(transcript: Optional[str]) -> Optional[str]:
    """Return 'ads' / 'outro' / 'intro' if any pattern matches, else None.

    None means "no keyword signal" — the caller decides what that implies.
    """
    if not transcript or not transcript.strip():
        return None

    for pat in ADS_PATTERNS:
        if pat.search(transcript):
            return "ads"
    for pat in OUTRO_PATTERNS:
        if pat.search(transcript):
            return "outro"
    for pat in INTRO_PATTERNS:
        if pat.search(transcript):
            return "intro"
    return None


if __name__ == "__main__":
    cases = [
        ("", None),
        ("   ", None),
        ("Welcome back to the channel, today we're going to talk about", "intro"),
        ("Hello everyone, in this video we'll cover", "intro"),
        ("Thanks for watching, see you next time!", "outro"),
        ("Don't forget to like and subscribe", "outro"),
        ("This video is sponsored by NordVPN, use code SAVE20", "ads"),
        ("Brought to you by our friends at Squarespace", "ads"),
        ("Check out example.com for more info", "ads"),
        ("So the algorithm works by iterating through the array", None),
        ("And then we add the result to the list", None),
    ]
    failures = 0
    for transcript, expected in cases:
        got = extract_keyword_label(transcript)
        ok = "OK" if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"[{ok}] expected={expected!s:>8}  got={got!s:>8}  | {transcript[:60]}")
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    raise SystemExit(0 if failures == 0 else 1)
