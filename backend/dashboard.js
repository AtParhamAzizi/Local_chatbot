const menuItems = document.querySelectorAll(".menu-item");
const logoutButton = document.getElementById("logoutButton");
const pageFrame = document.getElementById("pageFrame");

menuItems.forEach(item => {
    item.addEventListener("click", () => {
        if (item === logoutButton) return;
        pageFrame.src = item.dataset.page;
        menuItems.forEach(menuItem => menuItem.classList.remove("active"));
        item.classList.add("active");
    });
});

logoutButton?.addEventListener("click", async () => {
    logoutButton.disabled = true;
    logoutButton.textContent = "Signing out…";
    try {
        const response = await adminFetch("/logout", { method: "POST" });
        if (!response.ok && response.status !== 401) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.error || "Unable to sign out. Please try again.");
        }
        window.location.href = "/login";
    } catch (error) {
        if (error.status === 401) {
            window.location.href = "/login";
            return;
        }
        logoutButton.disabled = false;
        logoutButton.textContent = "Log out";
        window.alert(error.message || "Unable to sign out. Please try again.");
    }
});

adminFetch("/admin/api/session")
    .then(response => response.json())
    .then(({ user, allowed_pages }) => {
        if (!user) return;
        const allowedPages = new Set(allowed_pages || []);
        let first = null;
        menuItems.forEach(item => {
            if (item === logoutButton) return;
            const visible = allowedPages.has(item.dataset.page);
            item.hidden = !visible;
            item.style.display = visible ? "" : "none";
            if (visible && !first) first = item;
        });
        const active = document.querySelector(".menu-item.active");
        if (active && active.hidden) {
            active.classList.remove("active");
            if (first) {
                first.classList.add("active");
                pageFrame.src = first.dataset.page;
            } else {
                pageFrame.src = "about:blank";
            }
        }
    })
    .catch(() => {});
