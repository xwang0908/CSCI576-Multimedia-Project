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

## Test Data

`test/videos/*.mp4` is **gitignored** (large files, ~376 MB total). Obtain the test videos from the team shared drive (or wherever the course distributes them) and place them in `test/videos/` before running the pipeline.

`test/ground_truth/test_*.json` contains the reference labels for accuracy comparison.

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

Batch all 5 tests:

```
for n in 001 002 003 004 005; do
  python backend/video.py        --name test_$n
  python backend/ollama_audio.py --name test_$n
  python backend/integrator.py   --name test_$n
done
```

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

## 中文快速指南

### 安裝步驟

```bash
# 1. 系統工具
brew install ffmpeg ollama git-lfs
brew services start ollama
ollama pull qwen2.5:3b   # 約 2GB 本地 LLM

# 2. Python 環境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 下載 TransNetV2（場景切割模型）
mkdir -p third_party && cd third_party
git clone https://github.com/soCzech/TransNetV2.git
cd ..

# 4. 用 GitHub media CDN 抓 weights（繞過 LFS 配額限制）
BASE=https://media.githubusercontent.com/media/soCzech/TransNetV2/master/inference/transnetv2-weights
DIR=third_party/TransNetV2/inference/transnetv2-weights
curl -sL -o "$DIR/saved_model.pb"                          "$BASE/saved_model.pb"
curl -sL -o "$DIR/variables/variables.data-00000-of-00001" "$BASE/variables/variables.data-00000-of-00001"
curl -sL -o "$DIR/variables/variables.index"               "$BASE/variables/variables.index"
```

### 執行 pipeline（三階段）

```bash
python backend/video.py        --name test_001  # 視訊分析（TransNet + CLIP）
python backend/ollama_audio.py --name test_001  # 音訊分析（Whisper + Ollama）
python backend/integrator.py   --name test_001  # 融合 → segments.json
```

各階段輸出統一放在 `output/test_001/{video,audio,segments}_signals.json`。

### 故障排除

| 問題 | 解法 |
|---|---|
| `ModuleNotFoundError: No module named 'tensorflow'` | venv 沒啟動，跑 `source .venv/bin/activate` |
| `ModuleNotFoundError: No module named 'ffmpeg'` | 缺 ffmpeg-python，`pip install ffmpeg-python` |
| TransNetV2 weights 是 132 bytes | LFS 抓失敗，改用上面的 `media.githubusercontent.com` curl |
| Ollama 連線失敗 | `brew services start ollama` 啟動 daemon |
| 跑 video.py 就 crash | `test/videos/test_001.mp4` 沒放好（檔案不在 git 裡，要另外取得） |
