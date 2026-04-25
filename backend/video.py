# INPUT VIDEO
#     ↓
# [1] TransNet
#     → detects scene boundaries
#     ↓
# SCENES (start, end)
#     ↓
# [2] Frame Sampling
#     → sample N frames per segment
#     ↓
# FRAMES
#     ↓
# [3] Motion Analysis
#     → frame difference
#     → compute motion_level:
#         low / medium / high
#     ↓
# MOTION FEATURES
#     ↓
# [4] CLIP Semantic Classification
#     → image-text similarity
#     → outputs:
#         label ∈ {
#             core_content, intro, outro,
#             advertisement, self_promotion,
#             recap, transition, dead_air,
#             waiting, filler
#         }
#     ↓
# SEMANTIC LABEL
#     ↓
# [5] Fusion Logic
#     → combine motion + semantic
#     → decide visual_type:
#         static / talking_head / dynamic
#     ↓
# VISUAL SIGNALS
#     ↓
# [6] Confidence Estimation
#     → combine:
#         motion_score + clip_conf
#     ↓
# CONFIDENCE SCORE
#     ↓
# [7] Temporal Merge
#     → merge adjacent segments if:
#         same visual_type OR same label
#         AND time gap small
#     ↓
# FINAL SEGMENTS
#     ↓
# [8] Output JSON
#     → video_signals.json
import argparse
import json
import os
import sys
from pathlib import Path
import cv2
import numpy as np
import torch
from PIL import Image
import clip


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_VIDEOS_DIR = PROJECT_ROOT / "test" / "videos"
OUTPUT_DIR = PROJECT_ROOT / "output"
TRANSNET_SCRIPT = PROJECT_ROOT / "third_party" / "TransNetV2" / "inference" / "transnetv2.py"


# =========================
# 加载 CLIP
# =========================
def load_model(device):
    model, preprocess = clip.load("ViT-B/32", device=device)
    model.eval()
    return model, preprocess


# =========================
# motion 计算
# =========================
def compute_motion(frames):
    diffs = []

    for i in range(len(frames) - 1):
        diff = np.mean(np.abs(frames[i].astype(float) - frames[i+1].astype(float)))
        diffs.append(diff)

    if len(diffs) == 0:
        return "low", 0

    avg = np.mean(diffs)

    if avg < 5:
        return "low", avg
    elif avg < 20:
        return "medium", avg
    else:
        return "high", avg


# =========================
# CLIP 分类（10类）
# =========================
def classify_clip(frames, model, preprocess, device):

    image = preprocess(
        Image.fromarray(cv2.cvtColor(frames[0], cv2.COLOR_BGR2RGB))
    ).unsqueeze(0).to(device)

    texts = [
        "a person talking to camera explaining something",
        "a video intro with title or opening screen",
        "a video outro with subscribe or ending screen",
        "a product advertisement showing brand or product",
        "a youtube subscribe or like animation",
        "a repeated or recap scene",
        "a transition screen like black frame or fade",
        "a blank or empty screen with no activity",
        "a waiting screen with countdown",
        "random unrelated footage or filler content"
    ]

    labels = [
        "core_content",
        "intro",
        "outro",
        "advertisement",
        "self_promotion",
        "recap",
        "transition",
        "dead_air",
        "waiting",
        "filler"
    ]

    text_tokens = clip.tokenize(texts).to(device)

    with torch.no_grad():
        image_features = model.encode_image(image)
        text_features = model.encode_text(text_tokens)

        logits = (image_features @ text_features.T).softmax(dim=-1)

    idx = logits.argmax().item()
    confidence = float(logits[0][idx].item())

    return labels[idx], confidence


# =========================
# visual_type 决策（修正版本）
# =========================
def decide_visual_type(motion_level, clip_label):

    # 静态（标题页 / 黑屏 / waiting）
    if motion_level == "low":
        return "static"

    # talking head（主内容）
    if clip_label in ["core_content", "self_promotion"]:
        return "talking_head"

    # 其余
    return "dynamic"


# =========================
# 工具函数
# =========================
def sample_frame_times(start, end, n=6):
    if end <= start:
        return [start]
    return np.linspace(start, end, n).tolist()


def read_frame(video_path, t):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)

    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, frame = cap.read()
    cap.release()

    return frame if ok else None


# =========================
# TransNet 分段
# =========================
def run_transnet(video_path):
    os.system(f'"{sys.executable}" "{TRANSNET_SCRIPT}" "{video_path}"')

    scene_file = str(video_path) + ".scenes.txt"

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    duration = total / fps

    scenes = []
    with open(scene_file) as f:
        for line in f:
            s, e = map(float, line.strip().split())
            scenes.append((s / fps, e / fps))

    return scenes, duration


# =========================
# merge（优化版本）
# =========================
def merge_segments(segments):
    merged = []

    for seg in segments:
        if not merged:
            merged.append(seg)
            continue

        prev = merged[-1]

        same_type = seg["visual_type"] == prev["visual_type"]
        same_label = seg["label"] == prev["label"]

        close = seg["start"] - prev["end"] < 1.0

        # ⭐ 语义 + 视觉双重合并
        if (same_type or same_label) and close:
            prev["end"] = seg["end"]
            prev["confidence"] = round(
                (prev["confidence"] + seg["confidence"]) / 2, 3
            )
        else:
            merged.append(seg)

    return merged


# =========================
# 主函数
# =========================
def resolve_paths(args):
    if args.name:
        input_path = TEST_VIDEOS_DIR / f"{args.name}.mp4"
        output_dir = OUTPUT_DIR / args.name
    else:
        if not args.input:
            raise SystemExit("error: provide --name <test_id> or --input <video_path>")
        input_path = Path(args.input)
        output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR / input_path.stem

    if not input_path.exists():
        raise SystemExit(f"error: video not found: {input_path}")

    return input_path, output_dir


def main():
    parser = argparse.ArgumentParser(
        description="Video segmentation: scene cuts + motion + CLIP visual_type"
    )
    parser.add_argument("--name", help="test id (e.g. test_001) — auto-resolves paths")
    parser.add_argument("--input", help="path to input video (overrides --name)")
    parser.add_argument("--output_dir", help="custom output directory (default: output/<name>/)")
    args = parser.parse_args()

    input_path, output_dir = resolve_paths(args)
    output_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"[INFO] input:  {input_path}")
    print(f"[INFO] output: {output_dir}")
    print("[INFO] loading CLIP...")
    model, preprocess = load_model(device)

    print("[INFO] running TransNet...")
    scenes, duration = run_transnet(str(input_path))

    segments = []

    print("[INFO] analyzing segments...")

    for i, (s, e) in enumerate(scenes):

        # ⭐ 去掉极短片段（降噪）
        if (e - s) < 1.0:
            continue

        times = sample_frame_times(s, e, 6)

        frames = []
        for t in times:
            f = read_frame(str(input_path), t)
            if f is not None:
                frames.append(f)

        if len(frames) < 2:
            continue

        # motion
        motion_level, motion_score = compute_motion(frames)

        # CLIP 分类
        clip_label, clip_conf = classify_clip(frames, model, preprocess, device)

        # visual_type
        visual_type = decide_visual_type(motion_level, clip_label)

        # confidence（融合）
        confidence = round(
            0.6 * clip_conf + 0.4 * min(1.0, motion_score / 20),
            3
        )

        segments.append({
            "start": round(s, 3),
            "end": round(e, 3),
            "visual_type": visual_type,
            "motion_level": motion_level,
            "confidence": confidence,
            "label": clip_label  # ⭐附加字段
        })

        print(f"[SEG {i}] {s:.2f}-{e:.2f} | {visual_type} | {clip_label}")

    # merge
    segments = merge_segments(segments)

    # 输出 JSON
    output = {
        "video_filename": input_path.name,
        "duration_seconds": round(duration, 3),
        "segments": segments
    }

    out_path = output_dir / "video_signals.json"

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print("\n[DONE] saved:", out_path)


if __name__ == "__main__":
    main()



