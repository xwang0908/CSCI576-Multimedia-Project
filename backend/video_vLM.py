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

# This version adds vLM, it reuqires installing transformers and Qwen2-VL-2B-Instruct model. 
# It will be used to refine the classification results, especially for ads detection. 
# The vLM will be triggered for segments that are likely to be ads but not very certain, and it will analyze multiple frames in a larger context window to make a more informed decision. 
# The final segment labels may be updated based on the vLM results, and the output JSON will include the refined labels.
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
import importlib.util
from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

device = "cuda" if torch.cuda.is_available() else "cpu"

vlm_processor = None
vlm_model = None


def load_vlm():
    global vlm_processor, vlm_model

    if vlm_processor is not None and vlm_model is not None:
        return vlm_processor, vlm_model

    model_id = "Qwen/Qwen2-VL-2B-Instruct"

    print("[INFO] loading Qwen2-VL...")
    vlm_processor = AutoProcessor.from_pretrained(model_id)
    vlm_model = Qwen2VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto",
        trust_remote_code=True
    )

    return vlm_processor, vlm_model

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_VIDEOS_DIR = PROJECT_ROOT / "test" / "videos"
OUTPUT_DIR = PROJECT_ROOT / "output"
TRANSNET_SCRIPT = PROJECT_ROOT / "TransNetV2" / "inference" / "transnetv2.py"


spec = importlib.util.spec_from_file_location("transnetv2", str(TRANSNET_SCRIPT))
transnet_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transnet_module)

TransNetV2 = transnet_module.TransNetV2

# =========================
# load CLIP
# =========================
def load_model(device):
    model, preprocess = clip.load("ViT-B/32", device=device)
    model.eval()
    return model, preprocess
def compute_face_ratio(frames, face_model):
    count = 0

    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = face_model.detectMultiScale(gray, 1.1, 4)

        if len(faces) > 0:
            count += 1

    return count / len(frames)

def extract_segment_middle_frame(seg, cap, fps):
    mid = (seg["start"] + seg["end"]) / 2
    frame = read_frame(cap, mid, fps)
    return [frame] if frame is not None else []

# =========================
# motion computation
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
# CLIP classes
# =========================
def classify_clip(frames, model, preprocess, device):

    texts = [
        "a video intro with title or opening animation",
        "a person talking or main content explaining something",
        "a video outro with subscribe or ending screen",
        "a transition scene like fade, black screen or cut",
        "a product advertisement or commercial showing brand or product"
    ]

    labels = ["intro", "content", "outro", "transition", "ads"]

    text_tokens = clip.tokenize(texts).to(device)

    image_tensors = []
    for f in frames:
        img = preprocess(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
        image_tensors.append(img)

    image_batch = torch.stack(image_tensors).to(device)

    with torch.no_grad():
        image_features = model.encode_image(image_batch)
        text_features = model.encode_text(text_tokens)

        logits = (image_features @ text_features.T).softmax(dim=-1)

    avg_logits = logits.mean(dim=0)
    probs = avg_logits.cpu().numpy()

    result = {
        "intro": float(probs[0]),
        "content": float(probs[1]),
        "outro": float(probs[2]),
        "transition": float(probs[3]),
        "ads": float(probs[4]),
    }

    label = max(result, key=result.get)
    confidence = result[label]

    return label, confidence, result

def load_face_detector():
    return cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

def detect_face(frames, face_model):
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = face_model.detectMultiScale(gray, 1.1, 4)

        if len(faces) > 0:
            return True

    return False

def sample_frame_times(start, end, n=3):
    if end <= start:
        return [start]
    return np.linspace(start, end, n).tolist()


def read_frame(cap, t, fps):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, frame = cap.read()
    return frame if ok else None

def run_transnet(video_path):

    print("[INFO] loading TransNet model...")
    model = TransNetV2()

    print("[INFO] running TransNet inference...")
    video_frames, single_frame_predictions, _ = model.predict_video(video_path)

    scenes_idx = model.predictions_to_scenes(single_frame_predictions)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    scenes = [(s / fps, e / fps) for s, e in scenes_idx]

    duration = len(video_frames) / fps

    return scenes, duration

def compute_cut_density(segment, scenes):
    s, e = segment
    duration = max(1e-6, e - s)

    cuts = sum(1 for (cs, _) in scenes if s <= cs <= e)
    return cuts / duration

WINDOW = 2

def get_context_window(segments, i):
    start = max(0, i - WINDOW)
    end = min(len(segments), i + WINDOW + 1)
    return segments[start:end]

def extract_window_frames(window, cap, fps):
    frames = []

    for seg in window:
        start = seg["start"]
        end = seg["end"]

        
        times = np.linspace(start, end, 3)

        for t in times:
            f = read_frame(cap, t, fps)
            if f is not None:
                frames.append(f)

    return frames

def run_vlm(frames, max_frames=1):
    if len(frames) == 0:
        return "content"

    processor, model = load_vlm()

    frames = frames[:max_frames]
    votes = []

    for frame in frames:
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {
                    "type": "text",
                    "text": (
                        "Classify this video frame as advertisement or normal content. "
                        "Advertisement includes commercial, product promotion, brand ad, trailer, or inserted ad clip. "
                        "Normal content includes main story, lecture, interview, movie scene, or gameplay. "
                        "Answer only one word: ads or content."
                    )
                }
            ]
        }]

        text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        inputs = processor(
            text=[text],
            images=[image],
            return_tensors="pt"
        ).to(device)

        with torch.no_grad():
            outputs = model.generate(**inputs, max_new_tokens=6)

        result = processor.batch_decode(outputs, skip_special_tokens=True)[0].lower()
        votes.append("ads" if "ads" in result else "content")

    return "ads" if votes.count("ads") > len(votes) // 2 else "content"

def should_run_vlm(segments, i):
    if i == 0 or i == len(segments) - 1:
        return False

    seg = segments[i]
    ads_p = seg["clip_probs"]["ads"]

    if is_ad_seed(seg):
        return True

    if ads_p >= 0.25 and seg["cut_density"] >= 0.35:
        return True

    return False

def is_strong_content(seg):
    ads_p = seg["clip_probs"]["ads"]
    transition_p = seg["clip_probs"]["transition"]

    if seg["face_ratio"] >= 0.67 and ads_p < 0.25:
        return True

    if transition_p > 0.75 and ads_p < 0.15:
        return True

    return False

def decide_semantic_label(clip_probs, motion_level, cut_density, position, duration):

    clip_label = max(clip_probs, key=clip_probs.get)

    if clip_label == "transition" and duration < 3:
        return "transition"

    if clip_label == "ads" and clip_probs["ads"] >= 0.5:
        return "ads"

    if clip_label == "intro" and position < 0.2:
        return "intro"

    if clip_label == "outro" and position > 0.8:
        return "outro"

    return "content"

MIN_AD_DURATION = 20
MAX_AD_DURATION = 65
MAX_AD_GAP = 4.0


def is_clear_content(seg):
    ads_p = seg["clip_probs"]["ads"]
    transition_p = seg["clip_probs"]["transition"]

    if seg["face_ratio"] >= 0.67 and ads_p < 0.25:
        return True

    if transition_p > 0.75 and ads_p < 0.15:
        return True

    return False


def is_ad_seed(seg):
    ads_p = seg["clip_probs"]["ads"]

    if ads_p >= 0.50:
        return True

    if ads_p >= 0.30 and seg["cut_density"] >= 0.35:
        return True

    return False




def is_ad_support(seg):
    ads_p = seg["clip_probs"]["ads"]

    if is_clear_content(seg):
        return False

    if ads_p >= 0.18:
        return True

    if ads_p >= 0.08 and seg["cut_density"] >= 0.55:
        return True

    if ads_p >= 0.04 and seg["cut_density"] >= 0.80 and seg["face_ratio"] <= 0.33:
        return True

    return False


def merge_ad_segments(segments):
    merged = []
    used = set()

    for i, seg in enumerate(segments):
        if i in used:
            continue

        if not is_ad_seed(seg):
            continue

        left = i
        right = i

        # expand left
        j = i - 1
        while j >= 0:
            gap = segments[left]["start"] - segments[j]["end"]
            if gap > MAX_AD_GAP:
                break
            if not is_ad_support(segments[j]):
                break
            left = j
            j -= 1

        # expand right
        j = i + 1
        while j < len(segments):
            gap = segments[j]["start"] - segments[right]["end"]
            if gap > MAX_AD_GAP:
                break
            if not is_ad_support(segments[j]):
                break
            right = j
            j += 1

        start = segments[left]["start"]
        end = segments[right]["end"]
        duration = end - start


        if MIN_AD_DURATION <= duration <= MAX_AD_DURATION:
            merged.append((start, end))
            for k in range(left, right + 1):
                used.add(k)

    return merged


# =========================
# merge function
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

        # semantic and visual
        if same_type and same_label and close:
            prev["end"] = seg["end"]
            prev["confidence"] = round(
                (prev["confidence"] + seg["confidence"]) / 2, 3
            )
        else:
            merged.append(seg)

    return merged

def decide_visual_type(motion_level, semantic_label, face_ratio, cut_density):

    # talking head
    if face_ratio > 0.6 and cut_density < 0.5:
        return "talking_head"

    # static
    if motion_level == "low" and face_ratio < 0.3:
        return "static"

    # others
    return "dynamic"

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

def extract_segment_frames(seg, cap, fps, n=3):
    start = seg["start"]
    end = seg["end"]

    if end <= start:
        times = [start]
    else:
        times = np.linspace(start, end, n).tolist()

    frames = []
    for t in times:
        frame = read_frame(cap, t, fps)
        if frame is not None:
            frames.append(frame)

    return frames
# def merge_ad_segments(segments):
#     merged = []
#     used = set()

#     for i, seg in enumerate(segments):
#         if i in used:
#             continue

#         if not is_ad_seed(seg):
#             continue

#         left, right = grow_ad_region(segments, i)

#         start = segments[left]["start"]
#         end = segments[right]["end"]
#         duration = end - start

#         if MIN_AD_DURATION <= duration <= MAX_AD_DURATION:
#             merged.append((start, end))
#             for k in range(left, right + 1):
#                 used.add(k)

#     return merged

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
    face_model = load_face_detector()

    print("[INFO] running TransNet...")
    scenes, duration = run_transnet(str(input_path))

    segments = []

    print("[INFO] analyzing segments...")

    cap = cv2.VideoCapture(str(input_path))
    fps = cap.get(cv2.CAP_PROP_FPS)

    for i, (s, e) in enumerate(scenes):

        # cut the quite short episodes
        if (e - s) < 1.0:
            continue

        times = sample_frame_times(s, e, 2)

        frames = []
        for t in times:
            f = read_frame(cap, t, fps)
            if f is not None:
                frames.append(f)
        

        if len(frames) < 2:
            continue
    

        # motion
        motion_level, motion_score = compute_motion(frames)
        face_ratio = compute_face_ratio(frames, face_model)
        has_face = face_ratio > 0

        cut_density = compute_cut_density((s, e), scenes)

        # CLIP classification
        clip_label, clip_conf, clip_probs = classify_clip(frames, model, preprocess, device)

        mid_time = (s + e) / 2
        position = mid_time / duration

        semantic_label = decide_semantic_label(
            clip_probs,
            motion_level,
            cut_density,
            position,
            duration=e-s
        )

        # visual_type
        visual_type = decide_visual_type(
            motion_level,
            semantic_label,
            face_ratio,
            cut_density
        )

        # confidence
        confidence = round(
            0.5 * clip_conf +
            0.3 * min(1.0, motion_score / 20) +
            0.2 * min(1.0, cut_density),
            3
        )

        segments.append({
            "start": round(s, 3),
            "end": round(e, 3),
            "label": semantic_label,
            "visual_type": visual_type,
            "motion_level": motion_level,
            "has_face": has_face,
            "face_ratio": round(face_ratio, 2), 
            "cut_density": round(cut_density, 3),
            "confidence": confidence,
            "clip_probs": clip_probs
        })
    

        

    # merge
    segments = merge_segments(segments)

    ad_segments = merge_ad_segments(segments)

    for seg in segments:
        if is_strong_content(seg):
            continue

        for s, e in ad_segments:
            if not (seg["end"] < s or seg["start"] > e):
                seg["label"] = "ads"
    # =========================
    # Step 5 + 6: VLM boundary refine
    # =========================

    BACKTRACK = 1
    FORWARD = 1

    for i in range(len(segments)):
        if should_run_vlm(segments, i):
            frames = extract_segment_frames(segments[i], cap, fps, n=3)

            if len(frames) == 0:
                continue

            label = run_vlm(frames, max_frames=3)
            if label == "ads" and is_ad_seed(segments[i]):
                for j in range(max(0, i - BACKTRACK), i):
                    if not is_ad_support(segments[j]):
                        break
                    segments[j]["label"] = "ads"

                for j in range(i + 1, min(len(segments), i + FORWARD + 1)):
                    if not is_ad_support(segments[j]):
                        break
                    segments[j]["label"] = "ads"
    
    
    for i, seg in enumerate(segments):
        print(f"[SEG {i}] {seg['start']:.2f}-{seg['end']:.2f} | {seg['label']} | {seg['visual_type']}")
    cap.release()
    # Json output
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



