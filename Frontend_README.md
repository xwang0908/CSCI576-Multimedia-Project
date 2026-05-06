# Frontend Guide: Segmented Video Player


This document explains how to run and test the frontend for the segmented video player.


The frontend reads the final integrated segmentation output from the backend and uses it to display a video player with semantic segments, a segment timeline, skip controls, content-only playback, and test video selection buttons.


---


## 1. Project Purpose


This frontend is a semantic video player. It loads:


1. A video file, such as `test_001.mp4`
2. A generated segmentation file, such as `segments.json`


Then it uses the segment information to:


- Display a color-coded segment timeline
- Show a segment overview list
- Jump to a selected segment
- Show the current active segment while the video plays
- Skip non-content parts such as intro, ads, transitions, and outro
- Play only the main content when `Play Content Only` is enabled
- Switch between multiple test videos using frontend buttons


---


## 2. Required Folder Structure


Please keep the project structure like this:


```txt
CSCI576-Multimedia-Project/
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── script.js
│   └── background.jpg          optional
├── output/
│   ├── test_001/
│   │   └── segments.json
│   ├── test_002/
│   │   └── segments.json
│   ├── test_003/
│   │   └── segments.json
│   ├── test_004/
│   │   └── segments.json
│   └── test_005/
│       └── segments.json
└── test/
    └── videos/
        ├── test_001.mp4
        ├── test_002.mp4
        ├── test_003.mp4
        ├── test_004.mp4
        └── test_005.mp4
```


Important:


- `frontend/index.html` is the page to open.
- `frontend/script.js` reads the segmentation JSON and controls the video player.
- `frontend/style.css` styles the player, timeline, buttons, and segment list.
- `output/<test_id>/segments.json` is the final segmentation output from the backend.
- `test/videos/<test_id>.mp4` is the actual video file.
- For example, selecting `test_003` requires `output/test_003/segments.json` and `test/videos/test_003.mp4`.


The file `test/ground_truth/test_001.json` is only for backend evaluation/reference. It is not used by the frontend player.


---


## 3. Important Paths Used by the Frontend


The frontend expects each selected test video to have:


```txt
../output/<test_id>/segments.json
../test/videos/<test_id>.mp4
```


Because `index.html` is inside the `frontend/` folder, `../` means going one folder up to the project root.


For example:


```txt
frontend/index.html
../output/test_001/segments.json
../test/videos/test_001.mp4
```


If the user selects another video, such as `test_003`, the frontend loads:


```txt
../output/test_003/segments.json
../test/videos/test_003.mp4
```


The current frontend provides buttons for `test_001` through `test_005`.


---


## 4. How to Run the Frontend


The frontend should be opened through a local development server.


Do not open `index.html` directly by double-clicking it, because the browser may block loading local JSON/video files.


Incorrect:


```txt
file:///.../frontend/index.html
```


Correct:


```txt
http://127.0.0.1:5500/frontend/index.html
```


---


## 5. Recommended Method: VS Code Live Server


### Step 1: Open the Project in VS Code


Open the folder that directly contains:


```txt
frontend
output
test
```


For example, open:


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


### Step 3: Open the Page


In VS Code:


1. Open `frontend/index.html`
2. Right click inside the file
3. Click `Open with Live Server`


The browser should open a page similar to:


```txt
http://127.0.0.1:5500/frontend/index.html
```


The exact port number may be different, which is okay.


---


## 6. Alternative Method: Python Local Server


If Live Server is not available, use Python from the project root.


First, open a terminal in the folder that directly contains:


```txt
frontend
output
test
```


Then run:


### Windows PowerShell


```powershell
py -m http.server 5173
```


### macOS / Linux


```bash
python3 -m http.server 5173
```


Then open:


```txt
http://localhost:5173/frontend/index.html
```


Note: Live Server is preferred because it works more reliably for video seeking and timestamp jumping.


---


## 7. How the Frontend Works


### Step 1: Choose a Test Video and Load `segments.json`


The frontend shows buttons for the available test videos, such as `Test 001`, `Test 002`, `Test 003`, `Test 004`, and `Test 005`.


After the user selects a test video, the frontend loads the matching `segments.json`.


For example, selecting `test_001` loads:


```txt
../output/test_001/segments.json
```


Selecting `test_003` loads:


```txt
../output/test_003/segments.json
```


This file contains metadata like:


```json
{
  "videoTitle": "test_001",
  "videoFilename": "test_001.mp4",
  "duration_seconds": 1458.425,
  "segments": []
}
```


---


### Step 2: Load the Video


The frontend reads the `videoFilename` field from `segments.json`.


For example:


```json
"videoFilename": "test_001.mp4"
```


Then it loads the matching video from:


```txt
../test/videos/test_001.mp4
```


If the selected video is `test_003`, it loads:


```txt
../test/videos/test_003.mp4
```


---


### Step 3: Render Segment Timeline


Each segment has:


```json
{
  "label": "Ad Break",
  "type": "non_content",
  "start": 106.82,
  "end": 224.0,
  "subtype": "ad",
  "confidence": 0.8,
  "skip_recommended": true
}
```


The frontend uses this to render:


- A colored block in the segment timeline
- A segment card in the sidebar
- A timestamp range
- A confidence score
- A skip recommendation label


---


### Step 4: Detect Current Segment


While the video plays, the frontend checks the current video time.


For example, if the video is at `120s`, the frontend finds the segment where:


```txt
segment.start <= 120 < segment.end
```


Then it updates the current segment display.


---


### Step 5: Jump to Segment


When the user clicks a segment card or timeline block, the frontend sets:


```js
video.currentTime = segment.start;
```


Then the video jumps to that segment.


---


### Step 6: Skip Non-Content


When the current segment is non-content, such as:


- Intro
- Ad Break
- Transition
- Outro


the `Skip Non-Content` button jumps to the next meaningful content segment.


---


### Step 7: Play Content Only


When `Play Content Only` is turned on, the player automatically skips non-content segments during playback.


This allows the user to watch only the main content parts of the video.


---


## 8. Features to Test


After opening the page, test the following:


1. The video loads and plays.
2. The test video selector buttons appear.
3. Clicking a test video button loads the matching video and `segments.json`.
4. The page title updates to the video title from `segments.json`.
5. The right sidebar shows segment cards.
6. The color-coded segment timeline appears under the controls.
7. Clicking a segment card jumps to the correct timestamp.
8. Clicking a timeline block jumps to the correct timestamp.
9. The progress bar can be dragged to seek in the video.
10. The current segment text updates while the video plays.
11. `Skip Non-Content` works during intro, ads, transitions, and outro.
12. `Play Content Only` automatically skips non-content segments.
13. Switching between test videos resets the player and updates the timeline/list correctly.


---


## 9. Notes About Captions


Captions are optional.


The segmentation feature does not require:


```txt
captions.vtt
```


The current player uses `segments.json`, not caption files.


A `.vtt` file is only needed if the project wants to display subtitles or transcript captions.


---


## 10. Common Problems and Fixes


### Problem: The page shows 404 for `frontend/index.html`


This usually means the local server was started from the wrong folder.


Make sure the server starts from the folder that directly contains:


```txt
frontend
output
test
```


---


### Problem: `segments.json` cannot be loaded


Check that the selected test video has a matching `segments.json`.


For example:


```txt
output/test_001/segments.json
output/test_003/segments.json
```


Also make sure the page is opened through Live Server or a local server, not by double-clicking the HTML file.


---


### Problem: The video cannot be loaded


Check that the selected video exists.


For example:


```txt
test/videos/test_001.mp4
test/videos/test_003.mp4
```


Also check that `segments.json` contains the matching filename.


For example:


```json
"videoFilename": "test_001.mp4"
```


The filename must match exactly.


---


### Problem: The video plays but cannot jump or seek


Use VS Code Live Server instead of directly opening the HTML file.


If seeking still does not work, the video may need to be optimized for browser playback using FFmpeg:


```bash
ffmpeg -i test/videos/test_001.mp4 -c copy -movflags +faststart test/videos/test_001_faststart.mp4
```


Then either rename the generated video to `test_001.mp4`, or update `videoFilename` in `segments.json`. For another test video, replace the filename accordingly.


---


## 11. Submission Notes


For grading or review, please open:


```txt
frontend/index.html
```


using VS Code Live Server.


The expected working URL should look similar to:


```txt
http://127.0.0.1:5500/frontend/index.html
```


The frontend does not require running the backend if the following files already exist for the selected test video:


```txt
output/<test_id>/segments.json
test/videos/<test_id>.mp4
```


For example:


```txt
output/test_001/segments.json
test/videos/test_001.mp4
```


Backend scripts are only needed if the segmentation output needs to be regenerated.


---


## 12. Summary


The frontend is a standalone semantic video player. As long as the selected video and matching `segments.json` are present in the expected folders, the page can be run with Live Server and should display the segmented video experience correctly.


The frontend now supports switching between multiple test videos using buttons, so users do not need to manually edit `script.js` each time they want to test a different video.



