const API_BASE = window.location.protocol === "file:" ? "http://localhost:8000" : "";
const API = `${API_BASE}/admin/api/controller`;

const FEATURES = [
    { name: "voice", key: "voice_enabled" },
    { name: "chat", key: "chat_enabled" },
];
const MODELS = ["generator", "embedder", "voice"];

let pending = false;

function byId(id) {
    return document.getElementById(id);
}

function setStatus(message, kind = "") {
    const status = byId("status");
    status.textContent = message || "";
    status.className = `status ${kind}`.trim();
}

function setBadge(id, label, kind) {
    const badge = byId(id);
    badge.textContent = label;
    badge.className = `badge ${kind}`.trim();
}

function setSwitch(id, checked, disabled) {
    const input = byId(id);
    input.checked = checked;
    input.disabled = disabled;
    input.closest(".model-card").classList.toggle("on", checked);
}

function render(data) {
    const gpu = data.gpu || {};
    byId("gpuInfo").textContent = gpu.available
        ? `GPU: ${gpu.name} - ${gpu.free_mb} / ${gpu.total_mb} MB free`
        : "GPU: not available";

    FEATURES.forEach(({ name, key }) => {
        const enabled = Boolean(data.features[key]);
        setBadge(`badge-${name}-feature`, enabled ? "Enabled" : "Disabled", enabled ? "on" : "");
        setSwitch(`switch-${name}-feature`, enabled, pending);
    });

    const busy = data.busy || {};
    const errors = data.errors || {};
    const models = data.models;
    const details = {
        generator: [models.generator.loaded, `${models.generator.model_id}${models.generator.device ? ` - ${models.generator.device}` : ""}`],
        embedder: [models.embedder.loaded, `${models.embedder.model_id}${models.embedder.dim ? ` - dim ${models.embedder.dim}` : ""}`],
        voice: [models.voice.state === "loaded", `${models.voice.model} - ${models.voice.state}`],
    };

    const messages = [];
    MODELS.forEach((name) => {
        const [loaded, info] = details[name];
        const unavailable = name === "voice" && models.voice.state === "unavailable";
        const working = Boolean(busy[name]) || (name === "voice" && models.voice.state === "loading");
        if (working) setBadge(`badge-${name}`, "Working...", "busy");
        else setBadge(`badge-${name}`, loaded ? "Loaded" : "Unloaded", loaded ? "on" : "");
        byId(`info-${name}`).textContent = info;
        setSwitch(`switch-${name}`, loaded || working, working || unavailable || pending);
        byId(`card-${name}`).classList.toggle("unavailable", unavailable);
        if (errors[name]) messages.push(`${name}: ${errors[name]}`);
        if (unavailable) messages.push("Speech dependencies are not installed.");
    });

    if (messages.length) setStatus(messages.join(" | "), "error");
}

async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, { credentials: "same-origin", cache: "no-store", ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
}

async function refresh() {
    try {
        render(await request("/status"));
    } catch (error) {
        setStatus(`Cannot reach the server: ${error.message}`, "error");
    }
}

async function sendAction(action) {
    pending = true;
    setStatus(`Running ${action}...`);
    try {
        await request("/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action }),
        });
        setStatus("");
    } catch (error) {
        setStatus(error.message, "error");
    }
    pending = false;
    await refresh();
}

document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.dataset.kind) return;
    const suffix = input.dataset.kind === "feature"
        ? (input.checked ? "enable" : "disable")
        : (input.checked ? "load" : "unload");
    sendAction(`${input.dataset.name}_${suffix}`);
});

byId("refreshBtn").addEventListener("click", refresh);
refresh();
setInterval(refresh, 2000);
