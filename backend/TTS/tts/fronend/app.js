// frontend/app.js
// Talks to the FastAPI backend running on localhost:8000

const API = "http://localhost:8000";

const $ = (id) => document.getElementById(id);

const textEl      = $("text");
const voiceEl     = $("voice");
const charCount   = $("charCount");
const synthBtn    = $("synthBtn");
const downloadBtn = $("downloadBtn");
const uploadBtn   = $("uploadBtn");
const voiceUpload = $("voiceUpload");
const statusEl    = $("status");
const player      = $("player");
const playerCard  = $("playerCard");

let lastUrl = null;

// ---------- helpers ----------
function setStatus(msg, type = "") {
  statusEl.textContent = msg;
  statusEl.className = "status " + type;
}

function setBusy(busy) {
  synthBtn.disabled    = busy;
  uploadBtn.disabled   = busy;
  downloadBtn.disabled = busy || !lastUrl;
}

// ---------- connection check ----------
async function checkBackend() {
  try {
    const r = await fetch(`${API}/api/health`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (j.status !== "ok") throw new Error("unexpected payload");
    setStatus(`✅ اتصال به بک‌اند برقرار است (${API})`, "ok");
    return true;
  } catch (e) {
    setStatus(
      `❌ اتصال به بک‌اند برقرار نشد — مطمئن شوید سرور روی ${API} در حال اجراست.`,
      "error"
    );
    return false;
  }
}

// ---------- voice list ----------
async function loadVoices() {
  try {
    const r = await fetch(`${API}/api/voices`, { cache: "no-store" });
    if (!r.ok) throw new Error(await r.text());
    const { voices } = await r.json();
    voiceEl.innerHTML = "";
    if (!voices.length) {
      voiceEl.innerHTML = `<option value="">— ابتدا یک فایل صوتی آپلود کنید —</option>`;
      return;
    }
    for (const v of voices) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = v;
      voiceEl.appendChild(o);
    }
  } catch (e) {
    setStatus("خطا در دریافت لیست صداها: " + e.message, "error");
  }
}

// ---------- live char counter ----------
textEl.addEventListener("input", () => {
  charCount.textContent = `${textEl.value.length} کاراکتر`;
});

// ---------- upload a reference voice ----------
uploadBtn.addEventListener("click", async () => {
  const file = voiceUpload.files[0];
  if (!file) return setStatus("ابتدا یک فایل صوتی انتخاب کنید.", "error");

  const fd = new FormData();
  fd.append("file", file);       // field name must match FastAPI's `file: UploadFile = File(...)`

  setBusy(true);
  setStatus("⏳ در حال آپلود ...");
  try {
    const r = await fetch(`${API}/api/voices`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    setStatus("✅ آپلود موفق", "ok");
    voiceUpload.value = "";
    await loadVoices();
  } catch (e) {
    setStatus("خطای آپلود: " + e.message, "error");
  } finally {
    setBusy(false);
  }
});

// ---------- synthesize ----------
synthBtn.addEventListener("click", async () => {
  const text  = textEl.value.trim();
  const voice = voiceEl.value;

  if (!text)  return setStatus("متن خالی است.", "error");
  if (!voice) return setStatus("صدایی انتخاب نشده.", "error");

  const fd = new FormData();
  fd.append("text",  text);
  fd.append("voice", voice);      // must match FastAPI's Form(...) names

  setBusy(true);
  setStatus("⏳ در حال تولید صدا ... (ممکن است چند ثانیه طول بکشد)");

  const t0 = performance.now();
  try {
    const r = await fetch(`${API}/api/synthesize`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());

    const blob = await r.blob();
    if (lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl = URL.createObjectURL(blob);

    player.src = lastUrl;
    playerCard.classList.remove("hidden");
    downloadBtn.disabled = false;

    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus(`✅ آماده شد (${dt} ثانیه)`, "ok");
  } catch (e) {
    setStatus("خطا در تولید صدا: " + e.message, "error");
  } finally {
    setBusy(false);
  }
});

// ---------- download ----------
downloadBtn.addEventListener("click", () => {
  if (!lastUrl) return;
  const a = document.createElement("a");
  a.href = lastUrl;
  a.download = `tts-${Date.now()}.wav`;
  a.click();
});

// ---------- boot ----------
(async () => {
  const ok = await checkBackend();
  if (ok) await loadVoices();
})();