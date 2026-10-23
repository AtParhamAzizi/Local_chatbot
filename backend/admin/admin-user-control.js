(function () {
  const form = document.getElementById("userForm");
  const message = document.getElementById("message");
  const users = document.getElementById("users");
  const directoryCount = document.getElementById("directoryCount");

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (character) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character];
    });
  }

  function showMessage(text, isError) {
    message.textContent = text || "";
    message.classList.toggle("error", Boolean(isError));
  }

  async function loadUsers() {
    users.innerHTML = '<tr><td class="empty" colspan="4">Loading users…</td></tr>';
    const response = await fetch("/admin/api/admin-users");
    const payload = await response.json();
    if (!response.ok) {
      showMessage(payload.error || "Unable to load users.", true);
      return;
    }
    directoryCount.textContent = `${payload.users.length} user${payload.users.length === 1 ? "" : "s"}`;
    users.innerHTML = payload.users.length ? payload.users.map(function (user) {
      const action = user.username === "administrator" ? "" : `<button class="btn-danger" data-id="${user.id}" type="button">Delete</button>`;
      return `<tr><td>${escapeHtml(user.username)}</td><td>${user.is_admin ? "Administrator" : "Menu user"}</td><td>${user.is_active ? "Active" : "Disabled"}</td><td>${action}</td></tr>`;
    }).join("") : '<tr><td class="empty" colspan="4">No users found.</td></tr>';
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const data = new FormData(form);
    const submitButton = form.querySelector("button[type=submit]");
    submitButton.disabled = true;
    showMessage("Creating user…", false);
    try {
      const response = await fetch("/admin/api/admin-users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: data.get("username"),
          password: data.get("password"),
          permissions: data.getAll("permissions")
        })
      });
      const payload = await response.json();
      showMessage(payload.error || "User created.", !response.ok);
      if (response.ok) { form.reset(); await loadUsers(); }
    } catch (_) {
      showMessage("Unable to reach the admin service.", true);
    } finally {
      submitButton.disabled = false;
    }
  });

  users.addEventListener("click", async function (event) {
    const button = event.target.closest("button[data-id]");
    if (!button) return;
    button.disabled = true;
    const response = await fetch(`/admin/api/admin-users/${button.dataset.id}`, { method: "DELETE" });
    const payload = await response.json();
    showMessage(payload.error || "User deleted.", !response.ok);
    await loadUsers();
  });

  loadUsers().catch(function () { showMessage("Unable to load users.", true); });
})();