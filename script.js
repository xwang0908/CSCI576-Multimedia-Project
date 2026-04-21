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

let segments = [];
let activeSegmentIndex = -1;
let contentOnlyMode = false;

function formatTime(seconds) {
  if (isNaN(seconds)) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
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

function getTotalSegmentDuration() {
  if (!segments.length) return 0;
  return segments[segments.length - 1].end;
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
    default:
      return "segment-non-content";
  }
}

function getReadableSubtype(segment) {
  if (segment.type === "content") return "Core content";
  return segment.subtype.replace(/-/g, " ");
}

function findSegmentIndexAtTime(time) {
  return segments.findIndex((segment, index) => {
    const isLast = index === segments.length - 1;
    return time >= segment.start && (time < segment.end || (isLast && time <= segment.end));
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

  const totalDuration = getTotalSegmentDuration();

  segments.forEach((segment, index) => {
    const block = document.createElement("button");
    block.className = `segment-block ${getSegmentCssClass(segment)}`;
    block.style.width = `${((segment.end - segment.start) / totalDuration) * 100}%`;
    block.title = `${segment.label} (${formatTime(segment.start)} - ${formatTime(segment.end)})`;
    block.addEventListener("click", () => jumpToSegment(index, true));
    segmentBar.appendChild(block);

    const item = document.createElement("article");
    item.className = "segment-item";
    item.dataset.index = index;

    item.innerHTML = `
      <div class="segment-item-top">
        <span class="badge ${getSegmentCssClass(segment)}">
          ${segment.type === "content" ? "Content" : "Non-Content"}
        </span>
        <span class="segment-time">
          ${formatTime(segment.start)} - ${formatTime(segment.end)}
        </span>
      </div>
      <h3>${segment.label}</h3>
      <p>${getReadableSubtype(segment)}</p>
      <button class="jump-btn">Jump</button>
    `;

    item.addEventListener("click", (event) => {
      if (!event.target.closest(".jump-btn")) {
        jumpToSegment(index, true);
      }
    });

    item.querySelector(".jump-btn").addEventListener("click", () => {
      jumpToSegment(index, true);
    });

    segmentList.appendChild(item);
  });
}

function updateActiveSegmentUI() {
  const newIndex = findSegmentIndexAtTime(video.currentTime);

  if (newIndex === activeSegmentIndex) return;

  document.querySelectorAll(".segment-block.active").forEach((el) => {
    el.classList.remove("active");
  });

  document.querySelectorAll(".segment-item.active").forEach((el) => {
    el.classList.remove("active");
  });

  activeSegmentIndex = newIndex;

  if (activeSegmentIndex === -1) {
    currentSegmentText.textContent = "Current segment: --";
    return;
  }

  const currentSegment = segments[activeSegmentIndex];
  currentSegmentText.textContent = `Current segment: ${currentSegment.label} (${currentSegment.type})`;

  const activeBarBlock = segmentBar.children[activeSegmentIndex];
  if (activeBarBlock) activeBarBlock.classList.add("active");

  const activeListItem = segmentList.querySelector(`[data-index="${activeSegmentIndex}"]`);
  if (activeListItem) activeListItem.classList.add("active");
}

function getNextContentSegmentIndex(currentIndex) {
  for (let i = currentIndex + 1; i < segments.length; i++) {
    if (segments[i].type === "content") {
      return i;
    }
  }
  return -1;
}

function skipCurrentNonContent() {
  const currentIndex = findSegmentIndexAtTime(video.currentTime);
  if (currentIndex === -1) return;

  const currentSegment = segments[currentIndex];
  if (currentSegment.type === "content") return;

  const nextContentIndex = getNextContentSegmentIndex(currentIndex);
  if (nextContentIndex !== -1) {
    jumpToSegment(nextContentIndex, true);
  } else {
    video.currentTime = currentSegment.end;
  }
}

function handleContentOnlyMode() {
  if (!contentOnlyMode) return;

  const currentIndex = findSegmentIndexAtTime(video.currentTime);
  if (currentIndex === -1) return;

  const currentSegment = segments[currentIndex];
  if (currentSegment.type === "content") return;

  const nextContentIndex = getNextContentSegmentIndex(currentIndex);
  if (nextContentIndex !== -1) {
    video.currentTime = segments[nextContentIndex].start;
  }
}

async function loadSegments() {
  try {
    const response = await fetch("segments.json");
    if (!response.ok) {
      throw new Error("Could not load segments.json");
    }

    const data = await response.json();

    if (data.videoTitle) {
      videoTitleEl.textContent = data.videoTitle;
    }

    segments = data.segments || [];
    renderSegments();
    updateActiveSegmentUI();
  } catch (error) {
    console.error(error);
    segmentList.innerHTML = `
      <div class="error-message">
        Could not load segments.json. Start the project with Live Server.
      </div>
    `;
  }
}

playPauseBtn.addEventListener("click", togglePlay);
bigPlayBtn.addEventListener("click", togglePlay);
video.addEventListener("click", togglePlay);

video.addEventListener("play", updatePlayButton);
video.addEventListener("pause", updatePlayButton);

video.addEventListener("loadedmetadata", () => {
  durationEl.textContent = formatTime(video.duration);
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
  video.volume = volume.value;
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
  playContentOnlyBtn.textContent = `Play Content Only: ${contentOnlyMode ? "On" : "Off"}`;
  playContentOnlyBtn.classList.toggle("active", contentOnlyMode);

  if (contentOnlyMode) {
    handleContentOnlyMode();
  }
});

skipNonContentBtn.addEventListener("click", skipCurrentNonContent);

loadSegments();
updatePlayButton();