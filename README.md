# Video Semantic Segmentation Pipeline

This project performs video scene segmentation and semantic analysis, then provides a frontend video player that visualizes the generated segments.

The system produces structured segment signals for each video, including content and non-content regions such as intro, ads, transitions, and outro. The frontend reads the final `segments.json` output and allows users to jump between segments, skip non-content, and play only the main content.

---

## Features

### Backend Pipeline

- Scene segmentation using TransNetV2
- Frame sampling per segment
- Motion analysis:
  - low
  - medium
  - high
- CLIP-based semantic classification
- Visual type detection:
  - static
  - talking_head
  - dynamic
- Audio analysis using Whisper + Ollama
- Temporal merging of segments
- Final integrated JSON output: `segments.json`

### Frontend Video Player

- Loads video and segmentation data
- Displays a color-coded segment timeline
- Displays a segment overview list
- Supports jumping to a selected segment
- Shows the currently active segment while the video plays
- Supports skipping non-content segments
- Supports `Play Content Only` mode
- Uses the final backend output file: `output/<test_name>/segments.json`

---

## Project Structure

```txt
.
├── backend/
│   ├── video.py            # Person A: visual analysis (CLIP + TransNetV2)
│   ├── ollama_audio.py     # Person B: audio analysis (Whisper + Ollama)
│   └── integrator.py       # Person C: fuse signals → segments.json
├── frontend/               # Person D: video player UI
│   ├── index.html
│   ├── style.css
│   └── script.js
├── test/
│   ├── videos/             # Input .mp4 files (test_001.mp4 ...)
│   └── ground_truth/       # Reference JSON for accuracy checks
├── output/
│   └── <test_name>/        # Per-video outputs
│       ├── video_signals.json
│       ├── audio_signals.json
│       └── segments.json
├── TransNetV2/             # Vendored scene-cut model
├── blaze_face_short_range.tflite
├── requirements.txt
├── FRONTEND_README.md      # Detailed frontend running guide
└── README.md
```

---

## Installation

### 1. Create a Virtual Environment

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

---

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

### 3. Install System Tools

#### FFmpeg

macOS:

```bash
brew install ffmpeg
```

Windows:

Install FFmpeg from the official website or through a package manager such as Chocolatey:

```powershell
choco install ffmpeg
```

#### Ollama

macOS:

```bash
brew install ollama
brew services start ollama
```

Windows:

Download and install Ollama from the official Ollama website.

Then pull the required model:

```bash
ollama pull qwen2.5:3b
```

The model is approximately 2GB.

---

### 4. Vendor TransNetV2 Scene-Cut Model

The model weights are stored in Git LFS, but the upstream repository's LFS bandwidth quota is sometimes exhausted.

If that happens, bypass it through the GitHub media CDN:

```bash
WEIGHTS_URL=https://media.githubusercontent.com/media/soCzech/TransNetV2/master/inference/transnetv2-weights
WEIGHTS_DIR=TransNetV2/inference/transnetv2-weights

curl -sL -o "$WEIGHTS_DIR/saved_model.pb"                            "$WEIGHTS_URL/saved_model.pb"
curl -sL -o "$WEIGHTS_DIR/variables/variables.data-00000-of-00001"   "$WEIGHTS_URL/variables/variables.data-00000-of-00001"
curl -sL -o "$WEIGHTS_DIR/variables/variables.index"                 "$WEIGHTS_URL/variables/variables.index"
```

Verify the weights file:

```bash
shasum -a 256 TransNetV2/inference/transnetv2-weights/saved_model.pb
```

Expected checksum:

```txt
8ac2a52c5719690d512805b6eaf5ce12097c1d8860b3d9de245dcbbc3100f554
```

---

## Test Data

The folder:

```txt
test/videos/
```

contains the input `.mp4` files, such as:

```txt
test/videos/test_001.mp4
```

These video files may be gitignored because they are large.

If the videos are not included in the repository, obtain them from the team shared drive or the course-provided data source, then place them in:

```txt
test/videos/
```

The folder:

```txt
test/ground_truth/
```

contains reference JSON labels for accuracy checks.

Important:

```txt
test/ground_truth/test_001.json
```

is not the playable video. It is only a reference/evaluation file.

The playable video must be:

```txt
test/videos/test_001.mp4
```

---

## Backend Usage

The pipeline runs in three stages. Each script accepts:

```bash
--name <test_id>
```

to auto-resolve paths.

For example, for `test_001`:

```bash
# Person A: visual signals
python backend/video.py --name test_001

# Person B: audio signals
# Ollama daemon must be running before this step
python backend/ollama_audio.py --name test_001

# Person C: integrated final segments
python backend/integrator.py --name test_001
```

Outputs will be saved in:

```txt
output/test_001/
```

Expected output files:

```txt
output/test_001/video_signals.json
output/test_001/audio_signals.json
output/test_001/segments.json
```

You can also run visual analysis with a custom input and output directory:

```bash
python backend/video.py --input my_video.mp4 --output_dir custom/dir/
```

---

## Frontend Demo

The frontend can be run independently after the final integrated output exists.

Required files:

```txt
frontend/index.html
frontend/style.css
frontend/script.js
output/test_001/segments.json
test/videos/test_001.mp4
```

The frontend reads:

```txt
output/test_001/segments.json
```

and loads the video specified by the `videoFilename` field, such as:

```json
"videoFilename": "test_001.mp4"
```

The matching video should be placed at:

```txt
test/videos/test_001.mp4
```

---

## Recommended Way to Open the Frontend

Use **VS Code Live Server**.

### Step 1: Open the Project Folder

Open the folder that directly contains:

```txt
frontend
output
test
```

For example:

```txt
CSCI576-Multimedia-Project/
```

Do not open only the `frontend/` folder.

---

### Step 2: Install Live Server

In VS Code:

```txt
Extensions → Search "Live Server" → Install
```

The extension is usually named:

```txt
Live Server by Ritwick Dey
```

---

### Step 3: Open the Frontend

Open:

```txt
frontend/index.html
```

Right click inside the file and select:

```txt
Open with Live Server
```

The browser should open a page similar to:

```txt
http://127.0.0.1:5500/frontend/index.html
```

The exact port number may be different.

---

## Frontend Features to Test

After opening the page, test the following:

1. The video loads and plays.
2. The page title updates based on `segments.json`.
3. The segment overview appears in the right sidebar.
4. The color-coded segment timeline appears under the video controls.
5. Clicking a segment card jumps to the correct timestamp.
6. Clicking a timeline block jumps to the correct timestamp.
7. Dragging the progress bar seeks through the video.
8. The current segment text updates while the video plays.
9. `Skip Non-Content` skips intro, ads, transitions, and outro.
10. `Play Content Only` automatically skips non-content segments.

---

## Output Schema Examples

### `output/<name>/video_signals.json`

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

---

### `output/<name>/segments.json`

This is the final integrated output used by the frontend player.

```json
{
  "videoTitle": "test_001",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 120.5,
  "segments": [
    {
      "start": 0.0,
      "end": 5.2,
      "type": "non_content",
      "subtype": "intro",
      "label": "Intro",
      "confidence": 0.85,
      "skip_recommended": true
    }
  ],
  "summary": {
    "content_duration": 90.3,
    "non_content_duration": 30.2,
    "content_percentage": 75.0
  }
}
```

---

## Notes About Captions

Captions are optional.

The frontend segmentation feature does not require:

```txt
captions.vtt
```

The player currently uses:

```txt
segments.json
```

not caption files.

A `.vtt` file is only needed if subtitles or transcript captions are added later.

---

## Common Issues

### 1. Page Shows 404

This usually means the local server was started from the wrong folder.

Start the server from the folder that directly contains:

```txt
frontend
output
test
```

---

### 2. `segments.json` Cannot Be Loaded

Check that this file exists:

```txt
output/test_001/segments.json
```

Also make sure the page is opened using Live Server or a local server.

Do not open `index.html` by double-clicking it.

---

### 3. Video Cannot Be Loaded

Check that the video exists here:

```txt
test/videos/test_001.mp4
```

Also check that `segments.json` contains the matching filename:

```json
"videoFilename": "test_001.mp4"
```

The filename must match exactly.

---

### 4. Video Plays But Cannot Seek or Jump

Use VS Code Live Server instead of opening the HTML file directly.

If seeking still does not work, the video may need to be optimized for browser playback:

```bash
ffmpeg -i test/videos/test_001.mp4 -c copy -movflags +faststart test/videos/test_001_faststart.mp4
```

Then either rename the generated file to `test_001.mp4`, or update `videoFilename` in `segments.json`.

---

## Submission Notes

For grading or review, the frontend can be opened directly through VS Code Live Server:

```txt
frontend/index.html
```

The backend does not need to be rerun if these files already exist:

```txt
output/test_001/segments.json
test/videos/test_001.mp4
```

If the `.mp4` file is too large for GitHub, please upload it separately to the team shared drive and place it in:

```txt
test/videos/test_001.mp4
```

before opening the frontend.

---

## Summary

This project combines backend video/audio semantic segmentation with a frontend segmented video player.

The backend generates structured outputs, especially:

```txt
output/<test_name>/segments.json
```

The frontend reads this final JSON file and provides an interactive video experience with timeline visualization, segment navigation, non-content skipping, and content-only playback.
