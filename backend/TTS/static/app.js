const textInput = document.querySelector("#text-input");
const characterCount = document.querySelector("#character-count");
const speedRange = document.querySelector("#speed-range");
const speedValue = document.querySelector("#speed-value");
const generateButton = document.querySelector("#generate-button");
const generateLabel = document.querySelector("#generate-label");
const voiceSelect = document.querySelector("#voice-select");
const errorMessage = document.querySelector("#error-message");
const resultCard = document.querySelector("#result-card");
const audioPlayer = document.querySelector("#audio-player");
const downloadLink = document.querySelector("#download-link");
const resultMeta = document.querySelector("#result-meta");
const statusPill = document.querySelector("#status-pill");
const statusText = document.querySelector("#status-text");

const exampleText = "امروز یک meeting مهم دارم. لینک https://example.com را هم بررسی کن و نتیجه را در یک report بنویس.";
let currentAudioUrl = null;

function updateCount() {
  characterCount.textContent = `${textInput.value.length} / 5000`;
}

function showError(message = "") {
  errorMessage.textContent = message;
}

async function checkStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    if (data.ready) {
      statusPill.classList.add("ready");
      statusText.textContent = `Ready · ${data.english_voice_count} English voices`;
    } else {
      statusText.textContent = data.error || "Models are not ready";
    }
  } catch {
    statusText.textContent = "Server is unavailable";
  }
}

speedRange.addEventListener("input", () => { speedValue.textContent = `${Number(speedRange.value).toFixed(1)}×`; });
textInput.addEventListener("input", updateCount);
document.querySelector("#example-button").addEventListener("click", () => { textInput.value = exampleText; updateCount(); textInput.focus(); });

generateButton.addEventListener("click", async () => {
  const text = textInput.value.trim();
  if (!text) { showError("Enter some text first."); textInput.focus(); return; }
  showError();
  generateButton.disabled = true;
  generateLabel.textContent = "Generating…";
  try {
    const response = await fetch("/api/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: voiceSelect.value, speed: Number(speedRange.value) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Speech generation failed.");
    }
    const blob = await response.blob();
    if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = URL.createObjectURL(blob);
    audioPlayer.src = currentAudioUrl;
    downloadLink.href = currentAudioUrl;
    resultMeta.textContent = `${response.headers.get("X-TTS-Segments") || "Mixed"} language segment(s) · 24 kHz WAV`;
    resultCard.hidden = false;
    resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showError(error.message);
  } finally {
    generateButton.disabled = false;
    generateLabel.textContent = "Generate speech";
  }
});

updateCount();
checkStatus();
