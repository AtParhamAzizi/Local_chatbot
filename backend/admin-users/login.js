(function () {
  const form = document.getElementById("loginForm");
  const password = document.getElementById("password");
  const passwordToggle = document.getElementById("passwordToggle");
  const submitButton = document.getElementById("submitButton");
  const error = document.getElementById("loginError");

  passwordToggle?.addEventListener("click", function () {
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    passwordToggle.textContent = visible ? "Show" : "Hide";
    passwordToggle.setAttribute("aria-label", visible ? "Show password" : "Hide password");
  });

  form?.addEventListener("submit", function (event) {
    if (!form.checkValidity()) {
      event.preventDefault();
      error.textContent = "Enter both your username and password.";
      form.reportValidity();
      return;
    }
    submitButton.disabled = true;
    submitButton.querySelector("span").textContent = "Signing in…";
  });
})();