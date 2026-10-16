const API_BASE = window.location.protocol === "file:" ? "http://localhost:8000" : "";
const modelGrid = document.getElementById("modelGrid");
const activeBanner = document.getElementById("activeBanner");
const statusBox = document.getElementById("status");
const refreshBtn = document.getElementById("refreshBtn");

function setStatus(message, kind = "") {
    statusBox.textContent = message;
    statusBox.className = kind ? `status ${kind}` : "status";
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[char]));
}

function renderModels(models, activeId) {
    activeBanner.innerHTML = `Active model: <strong>${escapeHtml(
        models.find(model => model.id === activeId)?.label || activeId
    )}</strong>`;

    modelGrid.innerHTML = models.map(model => {
        const status = model.available
            ? (model.active ? "Active" : "Available")
            : "Files missing";
        const description = model.id === "qwen3-4b"
            ? "Qwen3-4B generation and embedding model paths."
            : "Gemma 4 E2B IT generation and embedding model paths.";
        return `
            <article class="model-card ${model.active ? "active" : ""} ${model.available ? "" : "unavailable"}">
                <div class="card-head">
                    <div>
                        <h2 class="model-name">${escapeHtml(model.label)}</h2>
                        <div class="model-id">${escapeHtml(model.id)}</div>
                    </div>
                    <span class="badge ${model.active ? "active" : ""} ${model.available ? "" : "unavailable"}">${status}</span>
                </div>
                <p class="model-description">${description}</p>
                <div class="model-meta">
                    <span>Embeddings</span>
                    <span>Generation</span>
                    <span>Local files only</span>
                </div>
                <button class="select-btn" data-model-id="${escapeHtml(model.id)}" ${!model.available || model.active ? "disabled" : ""}>
                    ${model.active ? "Currently selected" : "Use this model"}
                </button>
            </article>
        `;
    }).join("");

    modelGrid.querySelectorAll(".select-btn").forEach(button => {
        button.addEventListener("click", () => selectModel(button.dataset.modelId));
    });
}

async function loadModels() {
    setStatus("Loading model availability...");
    try {
        const response = await fetch(`${API_BASE}/admin/api/models`, { credentials: "same-origin" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Unable to load models");
        renderModels(payload.models, payload.active_model);
        setStatus("");
    } catch (error) {
        modelGrid.innerHTML = '<div class="state">Unable to load model settings.</div>';
        setStatus(error.message, "error");
    }
}

async function selectModel(modelId) {
    document.querySelectorAll(".select-btn").forEach(button => { button.disabled = true; });
    setStatus("Switching model and releasing the current model...", "");
    try {
        const response = await fetch(`${API_BASE}/admin/api/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ model_id: modelId })
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Unable to switch model");
        setStatus(payload.message || "Model updated.", "success");
        await loadModels();
    } catch (error) {
        setStatus(error.message, "error");
        await loadModels();
    }
}

refreshBtn.addEventListener("click", loadModels);
loadModels();
