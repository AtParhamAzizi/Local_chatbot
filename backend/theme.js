(function () {
    const STORAGE_KEY = "liquid-theme";
    const root = document.documentElement;
    let toggle;

    function readTheme() {
        try {
            return localStorage.getItem(STORAGE_KEY) || "dark";
        } catch (_) {
            return "dark";
        }
    }

    function saveTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (_) {}
    }

    function applyTheme(theme) {
        const nextTheme = theme === "light" ? "light" : "dark";
        root.dataset.theme = nextTheme;
        if (!toggle) return;
        toggle.textContent = nextTheme === "dark" ? "☀️ Light" : "🌙 Dark";
        toggle.setAttribute("aria-label", nextTheme === "dark" ? "Switch to light theme" : "Switch to dark theme");
        toggle.setAttribute("aria-pressed", String(nextTheme === "light"));
    }

    function init() {
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "theme-toggle";
        toggle.addEventListener("click", function () {
            const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
            saveTheme(nextTheme);
            applyTheme(nextTheme);
        });
        document.body.appendChild(toggle);
        applyTheme(readTheme());
    }

    window.addEventListener("storage", function (event) {
        if (event.key === STORAGE_KEY && event.newValue) {
            applyTheme(event.newValue);
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();