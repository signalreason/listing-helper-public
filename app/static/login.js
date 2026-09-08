const loginForm = document.querySelector("#login-form");
const setupForm = document.querySelector("#setup-form");
const setupResult = document.querySelector("#setup-result");
const heading = document.querySelector("#auth-heading");

async function problem(response) {
  try {
    return (await response.json()).detail || "Something went wrong. Please try again.";
  } catch {
    return "Something went wrong. Please try again.";
  }
}

function showError(id, message) {
  const element = document.querySelector(id);
  element.textContent = message;
  element.hidden = false;
}

async function initialize() {
  const response = await fetch("/api/session");
  if (!response.ok) {
    showError("#login-error", await problem(response));
    return;
  }
  const session = await response.json();
  if (session.authenticated) {
    window.location.assign("/");
    return;
  }
  if (session.setup_required) {
    heading.textContent = "Set up the owner";
    loginForm.hidden = true;
    setupForm.hidden = false;
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: document.querySelector("#login-email").value,
      password: document.querySelector("#login-password").value,
    }),
  });
  if (!response.ok) {
    showError("#login-error", await problem(response));
    return;
  }
  window.location.assign("/");
});

setupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: document.querySelector("#setup-email").value,
      setup_token: document.querySelector("#setup-token").value,
    }),
  });
  if (!response.ok) {
    showError("#setup-error", await problem(response));
    return;
  }
  const payload = await response.json();
  document.querySelector("#setup-password").textContent = payload.password;
  setupForm.hidden = true;
  setupResult.hidden = false;
  heading.textContent = "Owner created";
});

document.querySelector("#copy-setup-password").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  await navigator.clipboard.writeText(document.querySelector("#setup-password").textContent);
  button.textContent = "Copied";
});

initialize();
