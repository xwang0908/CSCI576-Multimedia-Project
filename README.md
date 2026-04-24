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
├── video.py
├── requirements.txt
├── README.md
├── TransNetV2/
├── blaze_face_short_range.tflite
└── output/
```

---

## Installation

### 1. Create a virtual environment (recommended)

---

### 2. Install dependencies

```
pip install -r requirements.txt
```

---

### 3. Install PyTorch (if needed)

CPU:
```
pip install torch torchvision
```

---

## Usage

```
python video.py --input <video_path> --output_dir <output_directory>
```

Example:

```
python video.py --input test.mp4 --output_dir output
```

---

## Output

The result is saved as:

```
output/video_signals.json
```

Example:

```json
{
  "video_filename": "test.mp4",
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
