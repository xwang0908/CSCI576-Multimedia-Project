# Video Semantic Segmentation Pipeline

This project performs video scene segmentation and semantic analysis, producing structured signals for each segment.

---

## Features

- Scene segmentation using TransNetV2  
- Frame sampling per segment  
- Motion analysis (low / medium / high)  
- CLIP-based semantic classification  
- Visual type detection:
  - static  
  - talking_head  
  - dynamic  
- Temporal merging of segments  
- JSON output (video_signals.json)  

---

## Project Structure

```
.
├── backend/
│   ├── video.py            # Person A: visual analysis (CLIP + TransNetV2)
│   ├── ollama_audio.py     # Person B: audio analysis (Whisper + Ollama)
│   ├── integrator.py       # Person C: fuse signals → segments.json
│   └── mock_generator.py
├── frontend/               # Person D: video player UI
├── test/
│   ├── videos/             # Input .mp4 files (test_001.mp4 ...)
│   └── ground_truth/       # Reference JSON for accuracy checks
├── output/
│   └── <test_name>/        # Per-video outputs
│       ├── video_signals.json
│       ├── audio_signals.json
│       └── segments.json
├── third_party/
│   └── TransNetV2/         # Vendored scene-cut model (cloned from upstream)
├── blaze_face_short_range.tflite
├── requirements.txt
└── README.md
```

---

## Installation

### 1. Create a virtual environment

```
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```
pip install -r requirements.txt
```

### 3. Install system tools

- **FFmpeg**: `brew install ffmpeg`
- **Ollama**: `brew install ollama && brew services start ollama`
- **Pull LLM model**: `ollama pull qwen2.5:3b` (~2GB)

### 4. Vendor TransNetV2 (scene-cut model)

```
mkdir -p third_party && cd third_party
git clone https://github.com/soCzech/TransNetV2.git
cd ..
```

The model weights are stored in Git LFS, but the upstream repo's LFS bandwidth quota is frequently exhausted. Bypass it via the GitHub media CDN:

```
WEIGHTS_URL=https://media.githubusercontent.com/media/soCzech/TransNetV2/master/inference/transnetv2-weights
WEIGHTS_DIR=third_party/TransNetV2/inference/transnetv2-weights

curl -sL -o "$WEIGHTS_DIR/saved_model.pb"                            "$WEIGHTS_URL/saved_model.pb"
curl -sL -o "$WEIGHTS_DIR/variables/variables.data-00000-of-00001"   "$WEIGHTS_URL/variables/variables.data-00000-of-00001"
curl -sL -o "$WEIGHTS_DIR/variables/variables.index"                 "$WEIGHTS_URL/variables/variables.index"
```

Verify with `shasum -a 256 third_party/TransNetV2/inference/transnetv2-weights/saved_model.pb` — expected `8ac2a52c5719690d512805b6eaf5ce12097c1d8860b3d9de245dcbbc3100f554`.

---

## Usage

The pipeline runs in three stages. Each script accepts `--name <test_id>` to auto-resolve paths:

```
# Person A: visual signals
python backend/video.py --name test_001

# Person B: audio signals (Ollama daemon must be running)
python backend/ollama_audio.py --name test_001

# Person C: integrated segments.json
python backend/integrator.py --name test_001
```

Outputs land in `output/test_001/`.

For custom paths, use `--input` / `--output` instead of `--name`:

```
python backend/video.py --input my_video.mp4 --output_dir custom/dir/
```

---

## Output Schema

`output/<name>/video_signals.json`:

```json
{
  "video_filename": "test_001.mp4",
  "duration_seconds": 120.5,
  "segments": [
    {
      "start": 0.0,
      "end": 5.2,
      "visual_type": "static",
      "motion_level": "low",
      "confidence": 0.82,
      "label": "intro"
    }
  ]
}
```

`output/<name>/segments.json` (final integrated output for the player):

```json
{
  "videoTitle": "test_001",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 120.5,
  "segments": [
    { "start": 0.0, "end": 5.2, "type": "non_content", "subtype": "intro", "label": "Intro", "confidence": 0.85, "skip_recommended": true }
  ],
  "summary": { "content_duration": 90.3, "non_content_duration": 30.2, "content_percentage": 75.0 }
}
```

---
---

# 视频语义分析与自动章节生成工具

利用 **Whisper** 进行语音转录，并结合 **Ollama** 本地大模型对视频内容进行深度语义分析

---

## 1. 环境准备

### 1.1 安装 FFmpeg
FFmpeg 用于从视频中提取音频流，是脚本运行的核心基础。
* **Windows**: 
* **macOS**: 
    ```
    brew install ffmpeg
    ```
* **验证**: 在终端输入 `ffmpeg -version`，看到版本信息即代表安装成功。

### 1.2 安装 Ollama (本地大模型引擎)
1.  前往 [Ollama 官网](https://ollama.com/) 下载并安装。
2.  安装完成后，在终端运行以下命令下载并启动模型：
    ```
    ollama run qwen2.5:3b
    ```
    > **注**：如果你的显存较小（< 4G），建议使用 `qwen2.5:1.5b` 或 `qwen2.5:0.5b`，并同步修改脚本中的 `MODEL_NAME` 变量。

### 1.3 安装 Python 及依赖库
确保系统已安装 **Python 3.9+**。

1.  **安装 PyTorch**:
2.  **安装其余 Python 依赖**:
    ```
    pip install openai-whisper ollama numpy
    ```

---

## 2. 脚本配置说明

在运行脚本前，请打开 `ollama_audio.py` 并根据实际情况修改文件开头的配置参数：

```
# ==========================================
# 全局配置参数
# ==========================================
MODEL_NAME = "qwen2.5:3b"       # 必须与你 ollama run 的模型名称一致
VIDEO_FILENAME = "test_001.mp4" # 待分析的视频文件名（需放在同一目录下）
BATCH_SIZE = 12                # 显存较小时可调低此数值（如 4 或 6）
```

---

## 3. 执行流程

脚本将依次执行以下阶段：

阶段 1: 使用 FFmpeg 提取同步音频。

阶段 2: 运行 Whisper进行语音转文字（约 1 分钟）。

阶段 3: 提取文本特征，生成视频全局语义画像。

阶段 4: 将文本分批发送给 Ollama 进行语义标签推理。

阶段 5 & 6: 自动平滑分类噪点，合并生成分类概要。
