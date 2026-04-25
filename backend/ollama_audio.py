import argparse
import json
import subprocess
import wave
from pathlib import Path
import whisper
import numpy as np
import math
import ollama
from collections import Counter

# ==========================================
# 路径常量
# ==========================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_VIDEOS_DIR = PROJECT_ROOT / "test" / "videos"
OUTPUT_DIR = PROJECT_ROOT / "output"

# ==========================================
# 全局配置参数（默认值，可被 CLI 参数覆盖）
# ==========================================
MODEL_NAME = "qwen2.5:3b"  # 本地大模型名称，可换成 3b 提速
BATCH_SIZE = 12  # LLM 批量处理的段落数

# ==========================================
# 第一阶段：音视频物理特征提取 (FFmpeg + Whisper)
# ==========================================
def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return float(result.stdout)

def extract_audio_sync(video_path, audio_path):
    print("🎬 [1/6] 正在使用 FFmpeg 提取同步音频...")
    command = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def classify_non_speech(wav_filename, start_time, end_time, silence_threshold=0.015):
    if end_time <= start_time: return "silence"
    try:
        with wave.open(wav_filename, 'rb') as wf:
            framerate = wf.getframerate()
            wf.setpos(int(start_time * framerate))
            raw_data = wf.readframes(int((end_time - start_time) * framerate))
            audio_array = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
            if len(audio_array) == 0: return "silence"
            rms_energy = np.sqrt(np.mean(audio_array**2))
            return "silence" if rms_energy < silence_threshold else "music"
    except:
        return "silence"

def run_whisper_extraction(video_path, audio_path):
    video_path = str(video_path)
    audio_path = str(audio_path)
    duration_seconds = round(get_video_duration(video_path), 2)
    extract_audio_sync(video_path, audio_path)

    print("🎙️ [2/6] 正在运行 Whisper 提取文本与物理缝隙...")
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, word_timestamps=True)

    MIN_GAP_THRESHOLD = 1.0  # 小于1秒的空白自动抹平
    final_segments = []
    current_time = 0.0

    for seg in result["segments"]:
        # 计算 Whisper 真实的语音识别置信度
        real_confidence = 0.0
        if "words" in seg and len(seg["words"]) > 0:
            word_probs = [word["probability"] for word in seg["words"]]
            real_confidence = sum(word_probs) / len(word_probs)
            start = round(seg["words"][0]["start"], 2)
            end = round(seg["words"][-1]["end"], 2)
        else:
            real_confidence = math.exp(seg.get("avg_logprob", -1.0))
            start = round(seg["start"], 2)
            end = round(seg["end"], 2)
            
        real_confidence = round(max(0.0, min(1.0, real_confidence)), 2)
        text = seg.get("text", "").strip()

        # 缝隙处理逻辑
        if start > current_time:
            gap_duration = round(start - current_time, 2)
            if gap_duration >= MIN_GAP_THRESHOLD:
                final_segments.append({
                    "start": current_time,
                    "end": start,
                    "has_speech": False,
                    "audio_type": classify_non_speech(audio_path, current_time, start),
                    "transcript": None,
                    "asr_confidence": 1.0 # 物理静音/音乐的识别置信度设定为1
                })
            else:
                if len(final_segments) > 0 and final_segments[-1]["has_speech"]:
                    final_segments[-1]["end"] = start
                else:
                    start = current_time
        
        # 插入当前语音段
        final_segments.append({
            "start": start,
            "end": end,
            "has_speech": True,
            "audio_type": "speech",
            "transcript": text if text else None,
            "asr_confidence": real_confidence 
        })
        current_time = end

    # 处理末尾
    if current_time < duration_seconds:
        gap_duration = round(duration_seconds - current_time, 2)
        if gap_duration >= MIN_GAP_THRESHOLD:
            final_segments.append({
                "start": current_time,
                "end": duration_seconds,
                "has_speech": False,
                "audio_type": classify_non_speech(audio_path, current_time, duration_seconds),
                "transcript": None,
                "asr_confidence": 1.0
            })
        else:
            if len(final_segments) > 0:
                final_segments[-1]["end"] = duration_seconds

    return final_segments, duration_seconds


# ==========================================
# 第二阶段：大模型语义分析与置信度推理
# ==========================================
def generate_video_profile(segments):
    print("🌍 [3/6] 正在快速扫描视频首尾，生成全局主题画像...")
    texts = [seg["transcript"] for seg in segments if seg.get("has_speech") and seg.get("transcript")]
    full_text = " ".join(texts)
    
    # 取前400词和后400词
    words = full_text.split()
    if len(words) > 800:
        sample_text = " ".join(words[:400] + ["... [MIDDLE OMITTED] ..."] + words[-400:])
    else:
        sample_text = full_text

    prompt = f"""
    Read this sampled video transcript (beginning and end):
    "{sample_text}"
    
    Task: Provide a 1 to 2 sentence global summary of what this video is about.
    Return STRICTLY in JSON format:
    {{
      "global_summary": "..."
    }}
    """
    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[{'role': 'user', 'content': prompt}],
            format='json',
            options={'temperature': 0.2}
        )
        data = json.loads(response['message']['content'])
        return data.get("global_summary", "General video content.")
    except Exception as e:
        print(f"⚠️ 摘要生成失败，启用默认摘要: {e}")
        return "General video content."

def analyze_batch_with_llm(segment_batch, global_summary, batch_index):
    """
    修改了 Prompt，强制输出严格的枚举标签，并增加了“广告/打断会突兀结束”的规则，防止标签惯性蔓延。
    """
    system_prompt = """You are a precise video text semantic analyzer.
You will receive a chronological batch of video transcripts as a JSON array.

Task: 
Classify EACH text segment into ONE of the following STRICT ENUM CATEGORIES ONLY:
["Content", "Intro", "Outro", "Sponsorship/Advertisement", "Self-Promotion", "Recap"]

RULES:
1. "Content": The actual subject matter, main presentation, or core topic of the video.
2. "Sponsorship/Advertisement": Brand endorsements, commercial breaks, or selling products (e.g., Lay's chips, software).
3. BE ALERT TO ABRUPT TRANSITIONS: Commercials or interludes often end abruptly. The exact moment the speaker resumes discussing the main topic (e.g., returning to the academic lecture, story, or core presentation), you MUST immediately switch the category back to "Content". Do NOT let the advertisement label bleed into the main content.
4. DO NOT output generic words like "Non-Content". You must choose exactly one string from the list above.

Output STRICTLY a JSON object with a "results" array matching the input length. 
Do NOT include extra text or explanations.

FORMAT:
{
  "results": [
    {
      "id": <input_id>,
      "category": "<Exact Category Name from the list>",
      "llm_confidence": 0.95 // Float between 0.0 and 1.0 indicating your confidence
    }
  ]
}
"""

    user_prompt = f"""
Global Topic: {global_summary}
Sequential Transcripts (Batch #{batch_index}):
{json.dumps(segment_batch, indent=2)}
"""

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ],
            format='json',
            options={'temperature': 0.0} # 设为 0，追求极度稳定性
        )
        result_json = json.loads(response['message']['content'])
        return result_json.get("results", [])
    except Exception as e:
        print(f"⚠️ 批处理 #{batch_index} 失败: {e}")
        return [{"id": seg["id"], "category": "Content", "llm_confidence": 0.5} for seg in segment_batch]

# ==========================================
# 第三阶段：平滑去噪与进度条区块生成
# ==========================================
def robust_smooth_labels(segments, window_size=5):
    print(f"🧹 [5/6] 正在使用滑动窗口消除零碎的分类噪点...")
    # 注意：只平滑有语言内容的部分，不要把物理静音给平滑掉了
    labels = [seg.get("content_category", "Content") for seg in segments]
    smoothed_labels = labels.copy()
    half_window = window_size // 2
    
    for i in range(len(labels)):
        start_idx = max(0, i - half_window)
        end_idx = min(len(labels), i + half_window + 1)
        window_labels = labels[start_idx:end_idx]
        most_common = Counter(window_labels).most_common(1)[0][0]
        smoothed_labels[i] = most_common
        
    for i, seg in enumerate(segments):
        # 只有原本就是语音/字幕的段落才接受平滑，维持音乐/死寂等硬规则的独立性
        if seg.get("transcript"):
            seg["content_category"] = smoothed_labels[i]
            
    return segments

def generate_macro_blocks(segments):
    print("📦 [6/6] 正在生成前端 UI 所需的宏观章节(Chapters)...")
    blocks = []
    current_block = None

    for seg in segments:
        label = seg.get("content_category", "Content")
        if current_block is None:
            current_block = {"label": label, "start": seg["start"], "end": seg["end"]}
        elif current_block["label"] == label:
            # 标签连续，顺延结束时间
            current_block["end"] = seg["end"]
        else:
            blocks.append(current_block)
            current_block = {"label": label, "start": seg["start"], "end": seg["end"]}
            
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

    return input_path, audio_path, output_json


# ==========================================
# 主流程编排
# ==========================================
def main():
    parser = argparse.ArgumentParser(
        description="Audio analysis: Whisper transcription + Ollama semantic classification"
    )
    parser.add_argument("--name", help="test id (e.g. test_001) — auto-resolves paths")
    parser.add_argument("--input", help="path to input video (overrides --name)")
    parser.add_argument("--output_dir", help="custom output directory (default: output/<name>/)")
    parser.add_argument("--output", help="custom output JSON path (overrides --output_dir)")
    args = parser.parse_args()

    input_path, audio_path, output_json = resolve_paths(args)
    print(f"[INFO] input:  {input_path}")
    print(f"[INFO] output: {output_json}")

    # 1. 音视频处理
    segments, duration = run_whisper_extraction(input_path, audio_path)
    if not segments:
        print("❌ 未提取到任何分段。")
        return

    # 2. 生成全局摘要
    global_summary = generate_video_profile(segments)
    print(f"✅ 全局主题: {global_summary}")

    # 3. 准备数据：硬逻辑分流 vs 大模型推理
    print(f"🧠 [4/6] 开始大模型语义推理 (每批 {BATCH_SIZE} 句话)...")
    
    llm_inputs = []
    for i, seg in enumerate(segments):
        if not seg.get("transcript"):
            # 【物理拦截】无文字绝对不可能是正片内容
            if seg.get("audio_type") == "music":
                seg["content_category"] = "Transition/Intermission" # 后续可能会被合并或平滑
            else:
                seg["content_category"] = "Dead Air/Filler"
            # 规则强判，置信度 100%
            seg["llm_confidence"] = 1.0 
        else:
            # 纯文本发送给大模型
            llm_inputs.append({
                "id": i,
                "transcript": seg["transcript"]
            })

    # 4. 执行大模型批量推理
    for i in range(0, len(llm_inputs), BATCH_SIZE):
        batch = llm_inputs[i : i + BATCH_SIZE]
        batch_results = analyze_batch_with_llm(batch, global_summary, batch_index=(i//BATCH_SIZE)+1)
        
        # 将分类结果和 llm_confidence 回填
        result_dict = {res["id"]: res for res in batch_results if "id" in res}
        
        for input_item in batch:
            idx = input_item["id"]
            if idx in result_dict:
                segments[idx]["content_category"] = result_dict[idx].get("category", "Content")
                # 提取出 llm 主观推断的置信度
                segments[idx]["llm_confidence"] = float(result_dict[idx].get("llm_confidence", 0.9))
            else:
                segments[idx]["content_category"] = "Content"
                segments[idx]["llm_confidence"] = 0.5
                
        print(f"  ➜ 已完成文本段 {min(i + BATCH_SIZE, len(llm_inputs))} / {len(llm_inputs)}")

    # 5. 后期去噪
    segments = robust_smooth_labels(segments, window_size=5)
    macro_blocks = generate_macro_blocks(segments)

    # 6. 生成最终输出
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

    print(f"🎉 任务完美完成！完整数据已保存至 {output_json}")

if __name__ == "__main__":
    main()