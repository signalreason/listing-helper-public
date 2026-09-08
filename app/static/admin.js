const userList = document.querySelector("#user-list");
const errorElement = document.querySelector("#admin-error");
const passwordResult = document.querySelector("#password-result");
const passwordElement = document.querySelector("#generated-password");

async function problem(response) {
  try {
    return (await response.json()).detail || "Something went wrong. Please try again.";
  } catch {
    return "Something went wrong. Please try again.";
  }
}

function showError(message) {
  errorElement.textContent = message;
  errorElement.hidden = false;
}

function showPassword(password) {
  passwordElement.textContent = password;
  passwordResult.hidden = false;
  document.querySelector("#copy-password").textContent = "Copy password";
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401 || response.status === 403) {
    window.location.assign("/login");
    return null;
  }
  if (!response.ok) throw new Error(await problem(response));
  return response.status === 204 ? null : response.json();
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary-button";
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function loadUsers() {
  const payload = await api("/api/users");
  if (!payload) return;
  userList.replaceChildren();
  for (const user of payload.users) {
    const row = document.createElement("article");
    row.className = "user-row";
    const details = document.createElement("div");
    const email = document.createElement("strong");
    email.textContent = user.email;
    const status = document.createElement("span");
    status.textContent = user.is_owner ? "Owner" : user.enabled ? "Active" : "Disabled";
    details.append(email, status);
    const actions = document.createElement("div");
    actions.className = "user-actions";
    actions.append(
      actionButton("Reset password", async () => {
        try {
          const result = await api(`/api/users/${user.id}/password-resets`, { method: "POST" });
          if (result) showPassword(result.password);
        } catch (error) {
          showError(error.message);
        }
      }),
    );
    if (!user.is_owner) {
      actions.append(
        actionButton(user.enabled ? "Disable" : "Enable", async () => {
          try {
            await api(`/api/users/${user.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ enabled: !user.enabled }),
            });
            await loadUsers();
          } catch (error) {
            showError(error.message);
          }
        }),
      );
    }
    row.append(details, actions);
    userList.append(row);
  }
}

document.querySelector("#create-user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  errorElement.hidden = true;
  try {
    const result = await api("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: document.querySelector("#new-email").value }),
    });
    if (result) {
      showPassword(result.password);
      form.reset();
      await loadUsers();
    }
  } catch (error) {
    showError(error.message);
  }
});

document.querySelector("#copy-password").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  await navigator.clipboard.writeText(passwordElement.textContent);
  button.textContent = "Copied";
});

loadUsers().catch((error) => showError(error.message));
