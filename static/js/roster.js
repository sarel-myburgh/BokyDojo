"use strict";

import { createSyncManager } from "./attendance-sync.js";
import { store } from "./attendance-queue.js";

function csrfToken(form) {
  return form.querySelector("[name=csrfmiddlewaretoken]").value;
}

/* The sync banner's class attribute is replaced wholesale on every state
 * change, so its styling lives here as well as in the template. Keep the base
 * in step with #sync-status in templates/attendance/roster.html.
 *
 * The tone strings are written out literally at each call site rather than
 * assembled from parts — Tailwind's content scanner only sees whole class names
 * in the source, so `"border-" + colour` would be purged from the build. */
function stateClass(tone) {
  return "mb-3 border-l-2 px-3 py-2 text-sm " + tone;
}

document.addEventListener("DOMContentLoaded", async function () {
  const form = document.getElementById("roster-form");
  if (!form) return;
  const status = document.getElementById("sync-status");
  const resolveButton = document.getElementById("sync-resolve");

  function showState({ pending, conflicts }) {
    if (conflicts) {
      status.textContent =
        conflicts + " conflict" + (conflicts === 1 ? "" : "s") + " — reload to review";
      status.className = stateClass("border-red-700 bg-red-50 text-red-900");
      resolveButton.hidden = false;
    } else if (pending) {
      status.textContent = pending + " pending — will sync when online";
      status.className = stateClass("border-amber-500 bg-amber-50 text-amber-900");
      resolveButton.hidden = true;
    } else {
      status.textContent = navigator.onLine
        ? "All attendance synced"
        : "Offline — changes will be queued";
      status.className = stateClass("border-green-700 bg-green-50 text-green-900");
      resolveButton.hidden = true;
    }
  }

  const manager = createSyncManager({
    store,
    scope: form.dataset.syncOwner,
    onState: showState,
    async transport(payload) {
      if (!payload.endpoint.startsWith("/api/attendance/sessions/")) {
        throw new Error("Invalid sync endpoint");
      }
      const response = await fetch(payload.endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(form),
        },
        body: JSON.stringify({ marks: payload.marks }),
      });
      if (!response.ok) throw new Error("Sync failed (" + response.status + ")");
      return response.json();
    },
  });

  resolveButton.addEventListener("click", async function () {
    await manager.discardConflicts();
    window.location.reload();
  });

  form.querySelectorAll("[data-mark-all-status]").forEach(function (button) {
    button.addEventListener("click", function () {
      const requested = button.dataset.markAllStatus;
      form.querySelectorAll("input[type=radio]").forEach(function (radio) {
        if (radio.disabled) return;
        radio.checked = requested !== "" && radio.value === requested;
      });
    });
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const marks = [];
    form.querySelectorAll("input[type=radio]:checked").forEach(function (radio) {
      marks.push({
        student_id: radio.dataset.studentId,
        status: radio.value,
        client_generated_id: crypto.randomUUID(),
        base_version: radio.dataset.baseVersion,
      });
    });
    if (!marks.length) return;
    await manager.enqueue({ endpoint: form.dataset.syncUrl, marks });
    if (navigator.onLine) {
      const result = await manager.flush();
      if (result.pending === 0 && result.conflicts === 0) window.location.reload();
    }
  });

  window.addEventListener("online", () => manager.flush());
  await manager.state();
  if (navigator.onLine) await manager.flush();
});