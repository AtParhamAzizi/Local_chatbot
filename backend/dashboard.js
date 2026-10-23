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