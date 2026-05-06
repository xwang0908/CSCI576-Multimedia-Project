const video = document.getElementById("video");
const videoWrapper = document.getElementById("videoWrapper");


const playPauseBtn = document.getElementById("playPause");
const bigPlayBtn = document.getElementById("bigPlay");
const progress = document.getElementById("progress");
const currentTimeEl = document.getElementById("currentTime");
const durationEl = document.getElementById("duration");
const volume = document.getElementById("volume");
const fullscreenBtn = document.getElementById("fullscreen");


const videoTitleEl = document.getElementById("videoTitle");
const currentSegmentText = document.getElementById("currentSegmentText");
const segmentBar = document.getElementById("segmentBar");
const segmentList = document.getElementById("segmentList");
const playContentOnlyBtn = document.getElementById("playContentOnly");
const skipNonContentBtn = document.getElementById("skipNonContent");


/*
  This version supports multiple test videos.


  Expected backend output structure:


  output/
  ├── test_001/
  │   └── segments.json
  ├── test_002/
  │   └── segments.json
  ├── test_003/
  │   └── segments.json
  └── ...


  Expected video structure:


  test/
  └── videos/
      ├── test_001.mp4
      ├── test_002.mp4
      ├── test_003.mp4
      └── ...
*/


const AVAILABLE_TESTS = [
  { id: "test_001", label: "Test 001" },
  { id: "test_002", label: "Test 002" },
  { id: "test_003", label: "Test 003" },
  { id: "test_004", label: "Test 004" },
  { id: "test_005", label: "Test 005" }
];


const DEFAULT_TEST_ID = "test_001";


const VIDEO_BASE_PATHS = [
  "./videos/",
  "./",
  "../test/videos/",
  "./test/videos/"
];


const MIN_MEANINGFUL_CONTENT_SECONDS = 2;


let rawSegments = [];
let segments = [];
let activeSegmentIndex = -1;
let contentOnlyMode = false;


let jsonDuration = 0;
let currentVideoFilename = "";
let currentTestId = DEFAULT_TEST_ID;
let videoPathIndex = -1;
let isAutoSkipping = false;


function getSegmentsJsonPaths(testId) {
  return [
    `./data/${testId}/segments.json`,
    `../output/${testId}/segments.json`,
    `./output/${testId}/segments.json`
  ];
}


function getInitialTestId() {
  const params = new URLSearchParams(window.location.search);
  const requestedVideo = params.get("video");


  const exists = AVAILABLE_TESTS.some(test => test.id === requestedVideo);
  return exists ? requestedVideo : DEFAULT_TEST_ID;
}


function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return "0:00";


  const totalSeconds = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;


  return `${mins}:${secs.toString().padStart(2, "0")}`;
}


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function injectVideoSelectorStyles() {
  if (document.getElementById("video-selector-styles")) return;


  const style = document.createElement("style");
  style.id = "video-selector-styles";
  style.textContent = `
    .video-selector {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }


    .video-selector-label {
      width: 100%;
      font-size: 0.85rem;
      opacity: 0.75;
      margin-bottom: 2px;
    }


    .video-select-btn {
      border: 1px solid rgba(255, 255, 255, 0.24);
      background: rgba(255, 255, 255, 0.08);
      color: inherit;
      border-radius: 999px;
      padding: 7px 12px;
      font-size: 0.85rem;
      cursor: pointer;
      transition: 0.15s ease;
    }


    .video-select-btn:hover {
      background: rgba(255, 255, 255, 0.16);
    }


    .video-select-btn.active {
      background: #ffffff;
      color: #111827;
      border-color: #ffffff;
      font-weight: 700;
    }
  `;


  document.head.appendChild(style);
}


function createVideoSelector() {
  injectVideoSelectorStyles();


  const existingSelector = document.getElementById("videoSelector");
  if (existingSelector) return;


  const selector = document.createElement("div");
  selector.id = "videoSelector";
  selector.className = "video-selector";


  selector.innerHTML = `
    <div class="video-selector-label">Choose a test video:</div>
  `;


  AVAILABLE_TESTS.forEach(test => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "video-select-btn";
    button.dataset.testId = test.id;
    button.textContent = test.label;


    button.addEventListener("click", () => {
      if (test.id === currentTestId) return;
      loadSegments(test.id);
    });


    selector.appendChild(button);
  });


  const headerContainer =
    currentSegmentText?.parentElement ||
    videoTitleEl?.parentElement ||
    document.body;


  headerContainer.appendChild(selector);
}


function updateVideoSelectorUI() {
  document.querySelectorAll(".video-select-btn").forEach(button => {
    button.classList.toggle("active", button.dataset.testId === currentTestId);
  });
}


function updateUrlForSelectedVideo(testId) {
  const url = new URL(window.location.href);
  url.searchParams.set("video", testId);
  window.history.replaceState({}, "", url);
}


function updatePlayButton() {
  if (video.paused) {
    playPauseBtn.textContent = "Play";
    bigPlayBtn.classList.remove("hidden");
  } else {
    playPauseBtn.textContent = "Pause";
    bigPlayBtn.classList.add("hidden");
  }
}


function togglePlay() {
  if (video.paused) {
    video.play();
  } else {
    video.pause();
  }
}


function getRawMaxEndTime() {
  if (!rawSegments.length) return 0;
  return Math.max(...rawSegments.map(segment => Number(segment.end) || 0));
}


function getEffectiveDuration() {
  const realVideoDuration =
    Number.isFinite(video.duration) && video.duration > 0
      ? video.duration
      : 0;


  return realVideoDuration || jsonDuration || getRawMaxEndTime();
}


function getTotalSegmentDuration() {
  return getEffectiveDuration() || (segments.length ? segments[segments.length - 1].end : 0);
}


function normalizeSegments(rawItems, duration) {
  const safeDuration = Number.isFinite(duration) && duration > 0
    ? duration
    : getRawMaxEndTime();


  return rawItems
    .map((segment, index) => {
      const rawStart = Number(segment.start);
      const rawEnd = Number(segment.end);


      const start = Math.max(0, Math.min(Number.isFinite(rawStart) ? rawStart : 0, safeDuration));
      const end = Math.max(0, Math.min(Number.isFinite(rawEnd) ? rawEnd : start, safeDuration));


      return {
        ...segment,
        id: segment.id ?? `segment-${index}`,
        label: segment.label || `Segment ${index + 1}`,
        type: segment.type || "unknown",
        subtype: segment.subtype || "unknown",
        confidence: Number.isFinite(Number(segment.confidence))
          ? Number(segment.confidence)
          : 0,
        skip_recommended: Boolean(segment.skip_recommended),
        start,
        end
      };
    })
    .filter(segment => segment.end > segment.start);
}


function getSegmentCssClass(segment) {
  if (segment.type === "content") return "segment-content";


  switch (segment.subtype) {
    case "intro":
      return "segment-intro";


    case "ad":
    case "advertisement":
    case "sponsorship":
      return "segment-ad";


    case "promo":
    case "self-promotion":
    case "channel-promo":
      return "segment-promo";


    case "outro":
      return "segment-outro";


    case "transition":
      return "segment-non-content";


    default:
      return "segment-non-content";
  }
}


function getReadableSubtype(segment) {
  if (segment.type === "content") return "Core content";


  return String(segment.subtype || "non-content")
    .replace(/-/g, " ")
    .replace(/\b\w/g, char => char.toUpperCase());
}


function isSkippableSegment(segment) {
  if (!segment) return false;


  return segment.skip_recommended === true || segment.type === "non_content";
}


function getSegmentDuration(segment) {
  return Math.max(0, segment.end - segment.start);
}


function findSegmentIndexAtTime(time) {
  return segments.findIndex((segment, index) => {
    const isLast = index === segments.length - 1;


    return (
      time >= segment.start &&
      (time < segment.end || (isLast && time <= segment.end))
    );
  });
}


function jumpToSegment(index, shouldPlay = true) {
  const segment = segments[index];
  if (!segment) return;


  video.currentTime = segment.start;
  updateActiveSegmentUI();


  if (shouldPlay) {
    video.play();
  }
}


function renderSegments() {
  segmentBar.innerHTML = "";
  segmentList.innerHTML = "";


  if (!segments.length) {
    segmentList.innerHTML = `
      <div class="error-message">
        No valid segments found.
      </div>
    `;
    return;
  }


  const totalDuration = getTotalSegmentDuration();


  segments.forEach((segment, index) => {
    const segmentDuration = getSegmentDuration(segment);
    const widthPercent = totalDuration > 0
      ? (segmentDuration / totalDuration) * 100
      : 0;


    const cssClass = getSegmentCssClass(segment);
    const confidencePercent = Math.round(segment.confidence * 100);


    const block = document.createElement("button");
    block.className = `segment-block ${cssClass}`;
    block.style.width = `${widthPercent}%`;
    block.title = `${segment.label} (${formatTime(segment.start)} - ${formatTime(segment.end)})`;
    block.addEventListener("click", () => jumpToSegment(index, true));
    segmentBar.appendChild(block);


    const item = document.createElement("article");
    item.className = "segment-item";
    item.dataset.index = index;


    item.innerHTML = `
      <div class="segment-item-top">
        <span class="badge ${cssClass}">
          ${segment.type === "content" ? "Content" : "Non-Content"}
        </span>


        <span class="segment-time">
          ${formatTime(segment.start)} - ${formatTime(segment.end)}
        </span>
      </div>


      <h3>${escapeHtml(segment.label)}</h3>


      <p>
        ${escapeHtml(getReadableSubtype(segment))}
        ${
          segment.skip_recommended
            ? `<span class="skip-note"> · Skip recommended</span>`
            : ""
        }
      </p>


      <p class="segment-confidence">
        Confidence: ${confidencePercent}%
      </p>


      <button class="jump-btn">Jump</button>
    `;


    item.addEventListener("click", event => {
      if (!event.target.closest(".jump-btn")) {
        jumpToSegment(index, true);
      }
    });


    item.querySelector(".jump-btn").addEventListener("click", event => {
      event.stopPropagation();
      jumpToSegment(index, true);
    });


    segmentList.appendChild(item);
  });
}


function updateCurrentSegmentControls(index) {
  if (index === -1) {
    currentSegmentText.textContent = "Current segment: --";


    skipNonContentBtn.disabled = true;
    skipNonContentBtn.textContent = "Skip Non-Content";


    return;
  }


  const currentSegment = segments[index];
  const readableType = currentSegment.type === "content" ? "content" : "non-content";


  currentSegmentText.textContent = `Current segment: ${currentSegment.label} (${readableType}, ${formatTime(currentSegment.start)} - ${formatTime(currentSegment.end)})`;


  const canSkip = isSkippableSegment(currentSegment);


  skipNonContentBtn.disabled = !canSkip;
  skipNonContentBtn.textContent = canSkip
    ? `Skip ${currentSegment.label}`
    : "Skip Non-Content";
}


function updateActiveSegmentUI() {
  const newIndex = findSegmentIndexAtTime(video.currentTime);


  if (newIndex !== activeSegmentIndex) {
    document.querySelectorAll(".segment-block.active").forEach(el => {
      el.classList.remove("active");
    });


    document.querySelectorAll(".segment-item.active").forEach(el => {
      el.classList.remove("active");
    });


    activeSegmentIndex = newIndex;


    if (activeSegmentIndex !== -1) {
      const activeBarBlock = segmentBar.children[activeSegmentIndex];
      if (activeBarBlock) activeBarBlock.classList.add("active");


      const activeListItem = segmentList.querySelector(`[data-index="${activeSegmentIndex}"]`);
      if (activeListItem) activeListItem.classList.add("active");
    }
  }


  updateCurrentSegmentControls(activeSegmentIndex);
}


function getNextContentSegmentIndex(currentIndex, minDuration = MIN_MEANINGFUL_CONTENT_SECONDS) {
  let fallbackContentIndex = -1;


  for (let i = currentIndex + 1; i < segments.length; i++) {
    const segment = segments[i];


    if (segment.type !== "content") continue;


    if (fallbackContentIndex === -1) {
      fallbackContentIndex = i;
    }


    const duration = getSegmentDuration(segment);
    const isUsefulContent = duration >= minDuration && segment.skip_recommended === false;


    if (isUsefulContent) {
      return i;
    }
  }


  return fallbackContentIndex;
}


function skipCurrentNonContent() {
  const currentIndex = findSegmentIndexAtTime(video.currentTime);
  if (currentIndex === -1) return;


  const currentSegment = segments[currentIndex];


  if (!isSkippableSegment(currentSegment)) return;


  const nextContentIndex = getNextContentSegmentIndex(currentIndex);


  if (nextContentIndex !== -1) {
    jumpToSegment(nextContentIndex, true);
  } else {
    video.currentTime = Math.min(currentSegment.end + 0.1, getEffectiveDuration());
  }
}


function handleContentOnlyMode() {
  if (!contentOnlyMode || isAutoSkipping) return;


  const currentIndex = findSegmentIndexAtTime(video.currentTime);
  if (currentIndex === -1) return;


  const currentSegment = segments[currentIndex];


  if (!isSkippableSegment(currentSegment)) return;


  const nextContentIndex = getNextContentSegmentIndex(currentIndex);


  if (nextContentIndex !== -1) {
    isAutoSkipping = true;
    video.currentTime = segments[nextContentIndex].start;


    window.setTimeout(() => {
      isAutoSkipping = false;
    }, 0);
  }
}


function syncContentOnlyButton() {
  playContentOnlyBtn.textContent = `Play Content Only: ${contentOnlyMode ? "On" : "Off"}`;
  playContentOnlyBtn.classList.toggle("active", contentOnlyMode);
}


async function fetchFirstAvailableJson(paths) {
  let lastError = null;


  for (const path of paths) {
    try {
      const response = await fetch(path);


      if (response.ok) {
        return await response.json();
      }


      lastError = new Error(`Failed to load ${path}`);
    } catch (error) {
      lastError = error;
    }
  }


  throw lastError || new Error("Could not load segments JSON.");
}


function tryVideoPath(index) {
  if (!currentVideoFilename) return;
  if (index < 0 || index >= VIDEO_BASE_PATHS.length) return;


  videoPathIndex = index;
  video.src = `${VIDEO_BASE_PATHS[index]}${currentVideoFilename}`;
  video.load();
}


function setVideoSourceFromJson(filename) {
  if (!filename) return;


  currentVideoFilename = filename;
  videoPathIndex = -1;


  video.pause();
  video.removeAttribute("src");
  video.load();


  tryVideoPath(0);
}


function resetPlayerForNewVideo(testId) {
  video.pause();


  rawSegments = [];
  segments = [];
  activeSegmentIndex = -1;
  jsonDuration = 0;
  currentVideoFilename = "";
  videoPathIndex = -1;
  isAutoSkipping = false;
  contentOnlyMode = false;


  progress.value = 0;
  currentTimeEl.textContent = "0:00";
  durationEl.textContent = "0:00";
  segmentBar.innerHTML = "";


  currentSegmentText.textContent = `Loading ${testId}...`;
  segmentList.innerHTML = `
    <div class="error-message">
      Loading ${testId}...
    </div>
  `;


  syncContentOnlyButton();
  updatePlayButton();
  updateCurrentSegmentControls(-1);
}


function prepareAndRenderSegments() {
  const duration = getEffectiveDuration();


  segments = normalizeSegments(rawSegments, duration);
  activeSegmentIndex = -1;


  renderSegments();
  updateActiveSegmentUI();
}


async function loadSegments(testId = DEFAULT_TEST_ID) {
  currentTestId = testId;
  updateVideoSelectorUI();
  updateUrlForSelectedVideo(testId);
  resetPlayerForNewVideo(testId);


  try {
    const data = await fetchFirstAvailableJson(getSegmentsJsonPaths(testId));


    if (data.videoTitle) {
      videoTitleEl.textContent = data.videoTitle;
    } else {
      videoTitleEl.textContent = testId;
    }


    jsonDuration = Number(data.duration_seconds) || 0;
    rawSegments = Array.isArray(data.segments) ? data.segments : [];


    const filename = data.videoFilename || `${testId}.mp4`;
    setVideoSourceFromJson(filename);


    prepareAndRenderSegments();
  } catch (error) {
    console.error(error);


    videoTitleEl.textContent = testId;
    currentSegmentText.textContent = "Current segment: --";


    segmentBar.innerHTML = "";
    segmentList.innerHTML = `
      <div class="error-message">
        Could not load segments.json for ${escapeHtml(testId)}.
        Make sure this file exists: output/${escapeHtml(testId)}/segments.json
      </div>
    `;
  }
}


playPauseBtn.addEventListener("click", togglePlay);
bigPlayBtn.addEventListener("click", togglePlay);
video.addEventListener("click", togglePlay);


video.addEventListener("play", updatePlayButton);
video.addEventListener("pause", updatePlayButton);


video.addEventListener("error", () => {
  if (!currentVideoFilename) return;


  const nextPathIndex = videoPathIndex + 1;


  if (nextPathIndex < VIDEO_BASE_PATHS.length) {
    tryVideoPath(nextPathIndex);
  } else {
    console.error("Could not load video file:", currentVideoFilename);


    segmentList.insertAdjacentHTML(
      "afterbegin",
      `
        <div class="error-message">
          Could not load video file: ${escapeHtml(currentVideoFilename)}.
          Make sure it exists in test/videos/.
        </div>
      `
    );
  }
});


video.addEventListener("loadedmetadata", () => {
  durationEl.textContent = formatTime(getEffectiveDuration());


  if (rawSegments.length) {
    prepareAndRenderSegments();
  }
});


video.addEventListener("timeupdate", () => {
  currentTimeEl.textContent = formatTime(video.currentTime);


  if (video.duration) {
    progress.value = (video.currentTime / video.duration) * 100;
  }


  handleContentOnlyMode();
  updateActiveSegmentUI();
});


progress.addEventListener("input", () => {
  if (!video.duration) return;


  const newTime = (progress.value / 100) * video.duration;
  video.currentTime = newTime;


  updateActiveSegmentUI();
});


volume.addEventListener("input", () => {
  video.volume = Number(volume.value);
});


fullscreenBtn.addEventListener("click", async () => {
  try {
    if (!document.fullscreenElement) {
      await videoWrapper.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
  } catch (error) {
    console.error("Fullscreen error:", error);
  }
});


playContentOnlyBtn.addEventListener("click", () => {
  contentOnlyMode = !contentOnlyMode;
  syncContentOnlyButton();


  if (contentOnlyMode) {
    handleContentOnlyMode();
  }
});


skipNonContentBtn.addEventListener("click", skipCurrentNonContent);


createVideoSelector();
loadSegments(getInitialTestId());
updatePlayButton();
syncContentOnlyButton();
updateCurrentSegmentControls(-1);

