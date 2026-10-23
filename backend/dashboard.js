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
        await fetch("/logout", { method: "POST" });
    } finally {
        window.location.href = "/login";
    }
});

const pagePermissions = {
    "admin/admin.html": "json_upload",
    "admin/admin_pdf.html": "pdf_upload",
    "admin/adminmodel.html": "models",
    "admin/adminchat.html": "chats",
    "admin/adminlogger.html": "system_reports",
    "admin/controller.html": "controller",
    "admin/admin-user-control.html": "admin"
};

fetch("/admin/api/session")
    .then(response => response.json())
    .then(({ user }) => {
        if (!user) return;
        const isAdmin = user.is_admin || user.username === "administrator";
        const granted = user.permissions || [];
        const allowed = item => {
            const permission = pagePermissions[item.dataset.page];
            if (!permission) return true;
            if (isAdmin) return true;
            if (permission === "admin") return false;
            return granted.includes("*") || granted.includes(permission);
        };
        let first = null;
        menuItems.forEach(item => {
            if (item === logoutButton) return;
            const visible = allowed(item);
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
