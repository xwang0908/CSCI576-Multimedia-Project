import argparse
import json
import subprocess
import wave
from pathlib import Path
from faster_whisper import WhisperModel
import numpy as np
import math
import ollama
import re


# ==========================================
# 路径常量
# ==========================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_VIDEOS_DIR = PROJECT_ROOT / "test" / "videos"
OUTPUT_DIR = PROJECT_ROOT / "output"


# ==========================================
# 全局配置参数
# ==========================================

DEFAULT_MODEL_NAME = "qwen2.5:7b"
MODEL_NAME = DEFAULT_MODEL_NAME  # overridden by --model CLI flag in main()

# LLM 上下文长度
LLM_NUM_CTX = 32768

# Whisper 模型
# Windows 推荐 faster-whisper:
# - CPU 稳定版: device="cpu", compute_type="int8"
# - 有 NVIDIA GPU 可以试: device="cuda", compute_type="float16"
# - 不确定就用 auto
WHISPER_MODEL_SIZE = "base"
WHISPER_DEVICE = "auto"
WHISPER_COMPUTE_TYPE = "auto"


# ==========================================
# Transition 切分参数
# ==========================================

# 普通情况下，gap >= 1.5 秒才切 block，避免输入太碎
GAP_THRESHOLD = 1.5

# 大停顿之后进入敏感模式，gap >= 1.0 秒也切，避免短插播被吞
SOFT_GAP_THRESHOLD = 1.0

# 如果 gap >= 8 秒，认为后面可能出现插播 / 场景切换
BIG_GAP_THRESHOLD = 8.0

# 大停顿后最多保留几个更细的 block
SENSITIVE_BLOCK_LIMIT = 4

# 如果当前 block 已经很长，认为回到正常正文合并模式
LONG_CONTENT_BLOCK = 35.0


# ==========================================
# 第一阶段：音视频物理特征提取
# ==========================================

def get_video_duration(video_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True
    )

    return float(result.stdout)


def extract_audio_sync(video_path, audio_path):
    print("🎬 [1/6] 正在使用 FFmpeg 提取同步音频...")

    command = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(audio_path)
    ]

    subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )


def load_audio_to_memory(wav_filename):
    with wave.open(str(wav_filename), "rb") as wf:
        framerate = wf.getframerate()
        raw_data = wf.readframes(wf.getnframes())
        audio_array = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0

    return audio_array, framerate


def classify_non_speech_fast(audio_array, framerate, start_time, end_time, silence_threshold=0.015):
    if end_time <= start_time:
        return "silence"

    start_idx = int(start_time * framerate)
    end_idx = int(end_time * framerate)

    segment = audio_array[start_idx:end_idx]

    if len(segment) == 0:
        return "silence"

    rms_energy = np.sqrt(np.mean(segment ** 2))

    return "silence" if rms_energy < silence_threshold else "music"


def transcribe_with_faster_whisper(audio_path):
    """
    Windows 版 Whisper：
    用 faster-whisper 替代 mlx-whisper。

    输出格式会被转换成和原来 mlx_whisper.transcribe 类似的 dict：
    {
        "segments": [
            {
                "start": ...,
                "end": ...,
                "text": ...,
                "words": [
                    {
                        "start": ...,
                        "end": ...,
                        "word": ...,
                        "probability": ...
                    }
                ],
                "avg_logprob": ...
            }
        ]
    }
    """

    model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE
    )

    whisper_segments, info = model.transcribe(
        str(audio_path),
        word_timestamps=True
    )

    result = {
        "segments": []
    }

    for seg in whisper_segments:
        words = []

        if seg.words:
            for word in seg.words:
                words.append({
                    "start": word.start,
                    "end": word.end,
                    "word": word.word,
                    "probability": word.probability if word.probability is not None else 0.9
                })

        result["segments"].append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "words": words,
            "avg_logprob": getattr(seg, "avg_logprob", -0.105)
        })

    return result


def run_whisper_extraction(video_path, audio_path):
    duration_seconds = round(get_video_duration(video_path), 2)

    extract_audio_sync(video_path, audio_path)

    print("🎙️ [2/6] 正在运行 Faster-Whisper 提取文本，并预加载音频...")

    audio_array, framerate = load_audio_to_memory(audio_path)

    result = transcribe_with_faster_whisper(audio_path)

    # 这是物理层 gap，用来生成 transition segment
    # 不等于给 LLM 的 transcript 合并阈值
    MIN_PHYSICAL_GAP_THRESHOLD = 1.0

    final_segments = []
    current_time = 0.0

    for seg in result["segments"]:
        if "words" in seg and len(seg["words"]) > 0:
            word_probs = [word.get("probability", 0.9) for word in seg["words"]]
            real_confidence = sum(word_probs) / len(word_probs)

            start = round(seg["words"][0]["start"], 2)
            end = round(seg["words"][-1]["end"], 2)
        else:
            real_confidence = math.exp(seg.get("avg_logprob", -0.105))
            start = round(seg["start"], 2)
            end = round(seg["end"], 2)

        real_confidence = round(max(0.0, min(1.0, real_confidence)), 2)

        text = seg.get("text", "").strip()

        if start > current_time:
            gap_duration = round(start - current_time, 2)

            if gap_duration >= MIN_PHYSICAL_GAP_THRESHOLD:
                audio_type = classify_non_speech_fast(
                    audio_array,
                    framerate,
                    current_time,
                    start
                )

                final_segments.append({
                    "start": round(current_time, 2),
                    "end": start,
                    "has_speech": False,
                    "audio_type": audio_type,
                    "transcript": None,
                    "asr_confidence": 1.0
                })

            else:
                if len(final_segments) > 0 and final_segments[-1]["has_speech"]:
                    final_segments[-1]["end"] = start
                else:
                    start = current_time

        if text:
            final_segments.append({
                "start": start,
                "end": end,
                "has_speech": True,
                "audio_type": "speech",
                "transcript": text,
                "asr_confidence": real_confidence
            })

        current_time = end

    if current_time < duration_seconds:
        gap_duration = round(duration_seconds - current_time, 2)

        if gap_duration >= MIN_PHYSICAL_GAP_THRESHOLD:
            final_segments.append({
                "start": round(current_time, 2),
                "end": duration_seconds,
                "has_speech": False,
                "audio_type": classify_non_speech_fast(
                    audio_array,
                    framerate,
                    current_time,
                    duration_seconds
                ),
                "transcript": None,
                "asr_confidence": 1.0
            })

        else:
            if len(final_segments) > 0:
                final_segments[-1]["end"] = duration_seconds

    return final_segments, duration_seconds


# ==========================================
# 第二阶段：生成全局主题画像
# ==========================================

def generate_video_profile(segments):
    print("🌍 [3/6] 正在快速扫描视频首尾，生成全局主题画像...")

    texts = [
        seg["transcript"]
        for seg in segments
        if seg.get("has_speech") and seg.get("transcript")
    ]

    full_text = " ".join(texts)
    words = full_text.split()

    if len(words) > 900:
        sample_text = " ".join(
            words[:450] + ["... [MIDDLE OMITTED] ..."] + words[-450:]
        )
    else:
        sample_text = full_text

    prompt = f"""
Read this sampled video transcript, including the beginning and end:

{sample_text}

Task:
Provide a 1 to 2 sentence global summary of what this video is mainly about.

Return ONLY valid JSON in this format:
{{
  "global_summary": "..."
}}
"""

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            format="json",
            options={
                "temperature": 0.2,
                "num_ctx": LLM_NUM_CTX
            }
        )

        data = json.loads(response["message"]["content"])

        return data.get("global_summary", "General video content.")

    except Exception as e:
        print(f"⚠️ 摘要生成失败，启用默认摘要: {e}")
        return "General video content."


# ==========================================
# 第三阶段：构造 block
# 每个 transition 之间的 speech 合并成一个 block，并分配 block_id
# ==========================================

def build_blocks_from_segments(segments):
    print("📝 [4/6] 正在根据 transition 构造 block id...")

    speech_segments = [
        {
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["transcript"].strip()
        }
        for seg in segments
        if seg.get("has_speech") and seg.get("transcript")
    ]

    if not speech_segments:
        return []

    raw_blocks = []

    current_start = speech_segments[0]["start"]
    current_end = speech_segments[0]["end"]
    current_text = speech_segments[0]["text"]

    sensitive_mode = False
    sensitive_blocks_left = 0

    for seg in speech_segments[1:]:
        gap = round(seg["start"] - current_end, 2)

        active_threshold = SOFT_GAP_THRESHOLD if sensitive_mode else GAP_THRESHOLD
        should_split = gap >= active_threshold

        if should_split:
            raw_blocks.append({
                "start": current_start,
                "end": current_end,
                "text": current_text.strip(),
                "transition_after": gap
            })

            if gap >= BIG_GAP_THRESHOLD:
                sensitive_mode = True
                sensitive_blocks_left = SENSITIVE_BLOCK_LIMIT

            elif sensitive_mode:
                sensitive_blocks_left -= 1

                if sensitive_blocks_left <= 0:
                    sensitive_mode = False

            current_start = seg["start"]
            current_end = seg["end"]
            current_text = seg["text"]

        else:
            current_end = seg["end"]
            current_text += " " + seg["text"]

        current_block_duration = current_end - current_start

        if sensitive_mode and current_block_duration >= LONG_CONTENT_BLOCK:
            sensitive_mode = False
            sensitive_blocks_left = 0

    raw_blocks.append({
        "start": current_start,
        "end": current_end,
        "text": current_text.strip(),
        "transition_after": None
    })

    blocks = []

    for i, block in enumerate(raw_blocks):
        blocks.append({
            "id": i,
            "start": round(block["start"], 2),
            "end": round(block["end"], 2),
            "text": block["text"],
            "transition_after": block["transition_after"]
        })

    return blocks


def build_block_classification_prompt(blocks, total_duration, global_summary, debug_block_input_path):
    block_texts = []

    for block in blocks:
        block_texts.append(
            f"""Block {block["id"]} | [{block["start"]:.2f}s -> {block["end"]:.2f}s]
{block["text"]}"""
        )

        if block["transition_after"] is not None:
            block_texts.append(f"(transition: {block['transition_after']:.2f}s)")

    blocks_section = "\n\n".join(block_texts)

    prompt = f"""
You are analyzing the audio transcript of a video.

Video duration:
{total_duration:.2f}s

Global topic:
{global_summary}

Task:
Classify EVERY numbered block into exactly one of these labels:
- intro
- ads
- outro
- content

Important:
Each block is a continuous speech section.
The line "(transition: X.XXs)" means there is a speech gap between two neighboring blocks.
Use the full video context, the global topic, and the neighboring blocks to judge continuity.

Label definitions:
- intro: opening countdown, opening greeting, title setup, or beginning introduction.
- ads: inserted external audio that breaks the main video's continuity, such as a commercial, promo, trailer, music ad, brand ad, sponsor message, or unrelated inserted clip.
- outro: final closing, goodbye, ending remarks, or end-screen style content.
- content: the main video content, including normal narration, examples, interviews, mission audio, commentary, jokes, or topic changes that still belong to the video's main story.

Decision rules:
- Classify a block as ads only when it is clearly unrelated to the global topic and feels inserted between surrounding content.
- A long transition alone is not enough to classify a block as ads.
- If a block continues the global topic or surrounding narrative, classify it as content.
- If a block is weird because ASR transcription is poor, compare it with its neighboring blocks before deciding.
- If uncertain, classify it as content.
- You must return one label for every block id.
- Do not invent block ids.
- Do not return timestamps.

Return ONLY valid JSON in this exact format:
{{
  "results": [
    {{"id": 0, "label": "intro"}},
    {{"id": 1, "label": "content"}}
  ]
}}

Blocks:

{blocks_section}
"""

    with open(debug_block_input_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    return prompt


# ==========================================
# 第四阶段：一次性 LLM 给每个 block 分类
# ==========================================

def normalize_label(label):
    if not isinstance(label, str):
        return "content"

    label = label.strip().lower()

    if label in {"intro", "ads", "outro", "content"}:
        return label

    return "content"


def classify_blocks_with_llm(blocks, total_duration, global_summary, debug_block_input_path, debug_block_output_path):
    print(f"🧠 [5/6] 大模型正在进行 block-level 一次性分类 ({MODEL_NAME})...")

    if not blocks:
        return {}

    system_prompt = """
You are an expert video audio-transcript continuity classifier.

You classify transcript blocks into:
intro, ads, outro, content.

Your main job is to identify inserted ads or unrelated inserted audio by comparing each block with the global topic and neighboring blocks.

Return only valid JSON.
"""

    user_prompt = build_block_classification_prompt(
        blocks=blocks,
        total_duration=total_duration,
        global_summary=global_summary,
        debug_block_input_path=debug_block_input_path
    )

    result_map = {}

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            format="json",
            options={
                "temperature": 0.0,
                "num_ctx": LLM_NUM_CTX
            }
        )

        raw_output = response["message"]["content"]

        cleaned_json_text = re.sub(
            r"```json\n?|```",
            "",
            raw_output
        ).strip()

        parsed = json.loads(cleaned_json_text)

        results = parsed.get("results", [])

        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue

                block_id = item.get("id")
                label = normalize_label(item.get("label", "content"))

                if isinstance(block_id, int):
                    result_map[block_id] = label

        # 没有返回的 block 默认 content
        for block in blocks:
            if block["id"] not in result_map:
                result_map[block["id"]] = "content"

        debug_data = {
            "LLM_Raw_Output": raw_output,
            "Parsed_Block_Labels": result_map
        }

        with open(debug_block_output_path, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)

        return result_map

    except Exception as e:
        print(f"❌ Block 分类失败，全部回退为 content: {e}")

        result_map = {
            block["id"]: "content"
            for block in blocks
        }

        debug_data = {
            "LLM_Raw_Output": f"ERROR: {str(e)}",
            "Parsed_Block_Labels": result_map
        }

        with open(debug_block_output_path, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)

        return result_map


def sanitize_intro_outro_block_labels(blocks, block_label_map, total_duration):
    """
    后处理 intro / outro：
    - intro 只能保留在视频开头附近
    - outro 只能保留在视频结尾附近
    - intro/outro 可能是连续多个 block，要保留连续的一组，不要只保留一个 block
    - 不符合位置要求的 intro/outro 改成 content
    - ads 不动
    """

    intro_window_end = min(90.0, total_duration * 0.08)
    outro_window_start = max(0.0, total_duration - min(90.0, total_duration * 0.08))

    # 先把明显不在合法区域的 intro/outro 改成 content
    for block in blocks:
        block_id = block["id"]
        label = block_label_map.get(block_id, "content")

        if label == "intro" and block["start"] > intro_window_end:
            block_label_map[block_id] = "content"

        elif label == "outro" and block["end"] < outro_window_start:
            block_label_map[block_id] = "content"

    # 找连续 label group
    def find_label_groups(target_label):
        groups = []
        current_group = []

        for block in blocks:
            block_id = block["id"]
            label = block_label_map.get(block_id, "content")

            if label == target_label:
                current_group.append(block)
            else:
                if current_group:
                    groups.append(current_group)
                    current_group = []

        if current_group:
            groups.append(current_group)

        return groups

    intro_groups = find_label_groups("intro")
    outro_groups = find_label_groups("outro")

    # intro 最多保留最靠前的一组连续 block
    if len(intro_groups) > 1:
        intro_groups = sorted(intro_groups, key=lambda group: group[0]["start"])
        groups_to_remove = intro_groups[1:]

        for group in groups_to_remove:
            for block in group:
                block_label_map[block["id"]] = "content"

    # outro 最多保留最靠后的一组连续 block
    if len(outro_groups) > 1:
        outro_groups = sorted(outro_groups, key=lambda group: group[-1]["end"], reverse=True)
        groups_to_remove = outro_groups[1:]

        for group in groups_to_remove:
            for block in group:
                block_label_map[block["id"]] = "content"

    return block_label_map


# ==========================================
# 第五阶段：block label 映射回原始 segments
# ==========================================

def find_block_label_for_segment(seg, blocks, block_label_map):
    mid_point = (seg["start"] + seg["end"]) / 2.0

    for block in blocks:
        if block["start"] <= mid_point <= block["end"]:
            return block_label_map.get(block["id"], "content")

    return "content"


def apply_block_labels_to_segments(segments, blocks, block_label_map):
    print("🎨 正在把 block label 映射回原始 segments...")

    for seg in segments:
        # 非语音段永远是 transition
        if not seg.get("has_speech"):
            seg["label"] = "transition"
            continue

        seg["label"] = find_block_label_for_segment(
            seg=seg,
            blocks=blocks,
            block_label_map=block_label_map
        )

    return segments


def restore_transition_labels(segments):
    """
    最终保险：
    无论前面怎么处理，只要 has_speech=False，
    最终 label 必须是 transition。
    """
    for seg in segments:
        if not seg.get("has_speech"):
            seg["label"] = "transition"

    return segments


def robust_smooth_labels(segments):
    """
    轻量平滑：
    - 填补 intro / ads / outro 内部的小 speech 缝隙
    - 不允许把 non-speech transition 改成 content
    """
    print("🧹 正在执行轻量平滑，保护 transition 标签...")

    labels = [seg.get("label", "content") for seg in segments]
    smoothed_labels = labels.copy()

    protected_labels = {
        "intro",
        "outro",
        "ads"
    }

    if len(labels) >= 3:
        for i in range(1, len(labels) - 1):
            prev_label = smoothed_labels[i - 1]
            curr_label = labels[i]
            next_label = labels[i + 1]

            # 非语音段永远保持 transition
            if not segments[i].get("has_speech"):
                smoothed_labels[i] = "transition"
                continue

            # 填补重点标签内部的 speech content 缝隙
            if (
                prev_label == next_label
                and prev_label in protected_labels
                and curr_label == "content"
                and segments[i].get("has_speech")
            ):
                smoothed_labels[i] = prev_label
                continue

            # 重点标签不被轻易抹掉
            if curr_label in protected_labels:
                continue

    for i, seg in enumerate(segments):
        seg["label"] = smoothed_labels[i]

    segments = restore_transition_labels(segments)

    return segments


def generate_macro_blocks(segments):
    print("📦 [6/6] 正在生成前端 UI 所需的宏观章节 video_chapters...")

    blocks = []
    current_block = None

    for seg in segments:
        label = seg.get("label", "content")

        if current_block is None:
            current_block = {
                "label": label,
                "start": seg["start"],
                "end": seg["end"]
            }

        elif current_block["label"] == label:
            current_block["end"] = seg["end"]

        else:
            blocks.append(current_block)

            current_block = {
                "label": label,
                "start": seg["start"],
                "end": seg["end"]
            }

    if current_block:
        blocks.append(current_block)

    return blocks


# ==========================================
# 路径解析
# ==========================================

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

    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / "temp_audio.wav"
    output_json = Path(args.output) if args.output else output_dir / "audio_signals.json"
    debug_block_input_path = output_dir / "debug-block-input.txt"
    debug_block_output_path = output_dir / "debug-block-output.json"

    return input_path, audio_path, output_json, debug_block_input_path, debug_block_output_path


# ==========================================
# 主流程
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Audio analysis: Faster-Whisper transcription + Ollama block-level semantic classification"
    )
    parser.add_argument("--name", help="test id (e.g. test_001) — auto-resolves test/videos/<name>.mp4")
    parser.add_argument("--input", help="path to input video (overrides --name)")
    parser.add_argument("--output_dir", help="custom output directory (default: output/<name>/)")
    parser.add_argument("--output", help="custom output JSON path (overrides --output_dir)")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_NAME,
        help=f"Ollama model name (default: {DEFAULT_MODEL_NAME}). e.g. qwen2.5:3b, qwen2.5:7b"
    )

    args = parser.parse_args()

    global MODEL_NAME
    MODEL_NAME = args.model
    print(f"[INFO] model: {MODEL_NAME}")

    input_path, audio_path, output_json, debug_block_input_path, debug_block_output_path = resolve_paths(args)
    print(f"[INFO] input:  {input_path}")
    print(f"[INFO] output: {output_json}")

    segments, duration = run_whisper_extraction(input_path, audio_path)

    if not segments:
        print("❌ 未提取到任何分段。")
        return

    global_summary = generate_video_profile(segments)

    print(f"✅ 全局主题: {global_summary}")
    print(f"✅ 总时长: {duration} 秒")

    blocks = build_blocks_from_segments(segments)

    block_label_map = classify_blocks_with_llm(
        blocks=blocks,
        total_duration=duration,
        global_summary=global_summary,
        debug_block_input_path=debug_block_input_path,
        debug_block_output_path=debug_block_output_path
    )

    block_label_map = sanitize_intro_outro_block_labels(
        blocks=blocks,
        block_label_map=block_label_map,
        total_duration=duration
    )

    segments = apply_block_labels_to_segments(
        segments=segments,
        blocks=blocks,
        block_label_map=block_label_map
    )

    segments = robust_smooth_labels(segments)

    segments = restore_transition_labels(segments)

    macro_blocks = generate_macro_blocks(segments)

    final_output = {
        "video_filename": input_path.name,
        "duration_seconds": duration,
        "global_summary": global_summary,
        "video_chapters": macro_blocks,
        "segments": segments
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=2, ensure_ascii=False)

    if audio_path.exists():
        audio_path.unlink()

    print(f"🎉 任务完成！完整数据已保存至 {output_json}")
    print(f"🧾 Debug block 输入保存至: {debug_block_input_path}")
    print(f"🧾 Debug block 输出保存至: {debug_block_output_path}")


if __name__ == "__main__":
    main()