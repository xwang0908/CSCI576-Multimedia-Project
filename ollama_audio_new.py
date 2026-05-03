import json
import subprocess
import wave
import mlx_whisper
import numpy as np
import math
import ollama
import re


# ==========================================
# 全局配置参数
# ==========================================

MODEL_NAME = "qwen2.5:7b"

VIDEO_FILENAME = "test_001.mp4"
AUDIO_FILENAME = "temp_audio.wav"

FINAL_OUTPUT_JSON = "final_video_analysis-2.json"

DEBUG_INPUT_TXT = "debug-input.txt"
DEBUG_OUTPUT_JSON = "debug-output.json"


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
        video_path
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
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path
    ]

    subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )


def load_audio_to_memory(wav_filename):
    with wave.open(wav_filename, "rb") as wf:
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


def run_whisper_extraction():
    duration_seconds = round(get_video_duration(VIDEO_FILENAME), 2)

    extract_audio_sync(VIDEO_FILENAME, AUDIO_FILENAME)

    print("🎙️ [2/6] 正在运行 MLX-Whisper 提取文本，并预加载音频...")

    audio_array, framerate = load_audio_to_memory(AUDIO_FILENAME)

    result = mlx_whisper.transcribe(
        AUDIO_FILENAME,
        path_or_hf_repo="mlx-community/whisper-base-mlx",
        word_timestamps=True
    )

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

    if len(words) > 800:
        sample_text = " ".join(
            words[:400] + ["... [MIDDLE OMITTED] ..."] + words[-400:]
        )
    else:
        sample_text = full_text

    prompt = f"""
Read this sampled video transcript, including the beginning and end:
"{sample_text}"

Task:
Provide a 1 to 2 sentence global summary of what this video is about.

Return STRICTLY in JSON format:
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
                "temperature": 0.2
            }
        )

        data = json.loads(response["message"]["content"])

        return data.get("global_summary", "General video content.")

    except Exception as e:
        print(f"⚠️ 摘要生成失败，启用默认摘要: {e}")
        return "General video content."


# ==========================================
# 第三阶段：构造给 LLM 的导航版 transcript
# ==========================================

def build_nav_transcript_from_segments(segments, total_duration):
    print("📝 [4/6] 正在构建 adaptive navigation transcript...")

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
        nav_transcript = f"Video duration: {total_duration:.2f}s\n"

        with open(DEBUG_INPUT_TXT, "w", encoding="utf-8") as f:
            f.write(nav_transcript)

        return nav_transcript

    blocks = []

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
            blocks.append({
                "start": current_start,
                "end": current_end,
                "text": current_text.strip(),
                "next_gap": gap
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

    blocks.append({
        "start": current_start,
        "end": current_end,
        "text": current_text.strip(),
        "next_gap": None
    })

    lines = [
        f"Video duration: {total_duration:.2f}s",
        ""
    ]

    for block in blocks:
        lines.append(f"[{block['start']:.2f}s -> {block['end']:.2f}s]")
        lines.append(block["text"])
        lines.append("")

        if block["next_gap"] is not None:
            lines.append(f"(transition: {block['next_gap']:.2f}s)")
            lines.append("")

    nav_transcript = "\n".join(lines)

    with open(DEBUG_INPUT_TXT, "w", encoding="utf-8") as f:
        f.write(nav_transcript)

    return nav_transcript


# ==========================================
# 第四阶段：一次性 LLM 提取 intro / ads / outro spans
# ==========================================

def is_valid_span(span):
    return (
        isinstance(span, dict)
        and "start" in span
        and "end" in span
        and isinstance(span["start"], (int, float))
        and isinstance(span["end"], (int, float))
        and span["start"] <= span["end"]
    )


def normalize_spans(spans, total_duration):
    cleaned = {
        "intro": [],
        "ads": [],
        "outro": []
    }

    for key in ["intro", "ads", "outro"]:
        raw_items = spans.get(key, [])

        if not isinstance(raw_items, list):
            continue

        for span in raw_items:
            if not is_valid_span(span):
                continue

            start = max(0.0, float(span["start"]))
            end = min(float(span["end"]), float(total_duration))

            if end <= start:
                continue

            cleaned[key].append({
                "start": round(start, 2),
                "end": round(end, 2)
            })

    return cleaned


def extract_spans_from_llm(transcript, total_duration, global_summary):
    print(f"🧠 [5/6] 大模型正在进行一次性全局结构分析 ({MODEL_NAME})...")

    system_prompt = f"""You are an expert Audio Transcript Structure Analyzer.

Your task is to identify timestamp spans for intro, ads, and outro based only on the audio transcript.

Global video topic:
{global_summary}

Transcript Format Guide:
The transcript is grouped into continuous speech blocks.
Each block has this format:

[start_time_s -> end_time_s]
spoken content

Blocks are separated by:

(transition: X.XXs)

The transition means there was a speech gap between two speech blocks.
Short pauses under the threshold have already been merged and are not shown.
Very long transitions may indicate scene changes or inserted audio, but transition length alone is NOT enough to classify something as an ad.

Categories to find:
1. "intro": opening greetings, channel intro, or opening setup.
2. "ads": clear external advertisements, sponsorships, brand promotions, product/service promotions, promo codes, discount offers, or commercial breaks.
3. "outro": final goodbye, closing remarks, like/subscribe reminders, or ending section.

How to decide ads:
- Judge whether the speech is semantically abrupt compared with the global topic and the surrounding blocks.
- A segment may be an ad if it suddenly switches away from the main topic into commercial or promotional language.
- A segment may be an ad if it sounds like an inserted commercial break rather than part of the main explanation.
- A long transition before or after a semantically unrelated segment can be supporting evidence.
- The spoken content still matters most.

Important negative rules:
- Do NOT classify normal lecture content as ads.
- Do NOT classify examples, jokes, stories, quotes, experiments, academic explanations, cartoons, or case studies as ads.
- Do NOT classify mentions of companies, products, courses, money, business, famous people, or brands as ads unless the segment is actually promotional or commercial.
- If a segment is part of the main topic or is used as an example inside the talk, classify it as content, not ads.
- If uncertain, classify as content, not ads.

Rules:
- Use ONLY timestamps shown in the transcript.
- Do not invent timestamps.
- If a category is missing, return [].
- If a section starts or ends inside a block, use the closest block boundary.
- Output ONLY a valid JSON object.
"""

    user_prompt = f"""Transcript:
{transcript}

Output EXACTLY a JSON object with this structure:
{{
  "intro": [
    {{"start": 0.0, "end": 15.0}}
  ],
  "ads": [
    {{"start": 120.5, "end": 180.0}},
    {{"start": 400.0, "end": 450.5}}
  ],
  "outro": []
}}
"""

    raw_response_text = ""

    result_json = {
        "intro": [],
        "ads": [],
        "outro": []
    }

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
                "num_ctx": 8192
            }
        )

        raw_response_text = response["message"]["content"]

        cleaned_json_text = re.sub(
            r"```json\n?|```",
            "",
            raw_response_text
        ).strip()

        parsed_data = json.loads(cleaned_json_text)

        if isinstance(parsed_data, dict):
            result_json["intro"] = parsed_data.get("intro", [])
            result_json["ads"] = parsed_data.get("ads", [])
            result_json["outro"] = parsed_data.get("outro", [])

        result_json = normalize_spans(result_json, total_duration)

    except Exception as e:
        print(f"❌ LLM 提取或解析失败: {e}")
        raw_response_text = f"ERROR: {str(e)}\nRaw Output: {raw_response_text}"

    debug_data = {
        "LLM_Raw_Output": raw_response_text,
        "Parsed_JSON": result_json
    }

    with open(DEBUG_OUTPUT_JSON, "w", encoding="utf-8") as df:
        json.dump(debug_data, df, indent=2, ensure_ascii=False)

    return result_json


# ==========================================
# 第五阶段：span 映射回原始 segments
# ==========================================

def span_contains_midpoint(span, start, end):
    mid_point = (start + end) / 2.0
    return span["start"] <= mid_point <= span["end"]


def apply_labels_from_spans(segments, spans):
    print("🎨 正在把 intro / ads / outro spans 映射回原始 segments...")

    for seg in segments:
        # 非语音段不交给 LLM 判，也不允许被 content 覆盖
        if not seg.get("has_speech"):
            seg["label"] = "transition"
            continue

        s = seg["start"]
        e = seg["end"]

        label = "content"

        # 优先级：ads > intro > outro > content
        for span in spans.get("ads", []):
            if is_valid_span(span) and span_contains_midpoint(span, s, e):
                label = "ads"
                break

        if label == "content":
            for span in spans.get("intro", []):
                if is_valid_span(span) and span_contains_midpoint(span, s, e):
                    label = "intro"
                    break

        if label == "content":
            for span in spans.get("outro", []):
                if is_valid_span(span) and span_contains_midpoint(span, s, e):
                    label = "outro"
                    break

        seg["label"] = label

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
    只做非常轻量的平滑：
    - 可以填补 intro / ads / outro 内部的小 speech 缝隙
    - 绝对不把 non-speech transition 改成 content
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

            # 非语音段永远不参与平滑改写
            if not segments[i].get("has_speech"):
                smoothed_labels[i] = "transition"
                continue

            # 只填补 speech segment 中间的重点标签缝隙
            if (
                prev_label == next_label
                and prev_label in protected_labels
                and curr_label == "content"
                and segments[i].get("has_speech")
            ):
                smoothed_labels[i] = prev_label
                continue

            # 当前本来就是重点标签，不抹掉
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
# 主流程
# ==========================================

def main():
    segments, duration = run_whisper_extraction()

    if not segments:
        print("❌ 未提取到任何分段。")
        return

    global_summary = generate_video_profile(segments)

    print(f"✅ 全局主题: {global_summary}")
    print(f"✅ 总时长: {duration} 秒")

    nav_transcript = build_nav_transcript_from_segments(segments, duration)

    spans = extract_spans_from_llm(
        transcript=nav_transcript,
        total_duration=duration,
        global_summary=global_summary
    )

    # LLM 只负责 intro / ads / outro
    # content 和 transition 在这里规则化生成
    segments = apply_labels_from_spans(segments, spans)

    # 轻量平滑，但不允许破坏 transition
    segments = robust_smooth_labels(segments)

    # 最后一层保险：所有 non-speech 必须是 transition
    segments = restore_transition_labels(segments)

    macro_blocks = generate_macro_blocks(segments)

    final_output = {
        "video_filename": VIDEO_FILENAME,
        "duration_seconds": duration,
        "global_summary": global_summary,
        "video_chapters": macro_blocks,
        "segments": segments
    }

    with open(FINAL_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=2, ensure_ascii=False)

    print(f"🎉 任务完成！完整数据已保存至 {FINAL_OUTPUT_JSON}")
    print(f"🧾 Debug LLM 输入保存至: {DEBUG_INPUT_TXT}")
    print(f"🧾 Debug LLM 输出保存至: {DEBUG_OUTPUT_JSON}")


if __name__ == "__main__":
    main()