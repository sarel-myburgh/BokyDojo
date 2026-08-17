"use strict";

/* Check-in grid — TODO 1.7, decision D1.
 *
 * A tap marks one student and turns the tile green. There is no submit button:
 * the queue moves at the speed of the line, and an instructor holding the phone
 * is not going to remember to press Save at the end.
 *
 * ⚠ Marks go through the same offline queue as the roster (1.6.2) and the same
 * sync endpoint, so a tap in a hall with no wifi is queued rather than lost. The
 * tile turns green immediately either way — optimistic, because the student is
 * standing there and the truthful answer to "am I checked in" is yes, somebody
 * recorded it. The status line says whether it has reached the server yet. */

import { createSyncManager } from "./attendance-sync.js";
import { store } from "./attendance-queue.js";

function csrfToken(form) {
  return form.querySelector("[name=csrfmiddlewaretoken]").value;
}

document.addEventListener("DOMContentLoaded", function () {
  const grid = document.getElementById("kiosk-grid");
  if (!grid) return;
  const status = document.getElementById("kiosk-status");
  const markUrl = grid.dataset.markUrl;

  function showState({ pending }) {
    if (pending) {
      status.textContent = pending + " waiting to sync";
    } else {
      status.textContent = navigator.onLine ? "" : "Offline — taps are saved";
    }
  }

  /* Student ids the server refused outright, collected by the transport and
   * drained after each flush. */
  const failed = new Set();

  function revert(studentId) {
    const tile = grid.querySelector('[data-student-id="' + studentId + '"]');
    if (!tile) return;
    tile.dataset.checked = "0";
    tile.setAttribute("aria-pressed", "false");
    tile.classList.remove("border-green-700");
    tile.classList.add("border-red-700");
    const check = tile.querySelector(".kiosk-check");
    if (check) check.classList.add("invisible");
  }

  function drainFailures() {
    if (!failed.size) return;
    failed.forEach(revert);
    failed.clear();
    status.textContent = "Some taps were not accepted — see the instructor";
  }

  const manager = createSyncManager({
    store,
    scope: markUrl,
    onState: showState,
    async transport(payload) {
      /* Same-origin, same shape as the roster's transport. The endpoint is
       * checked rather than trusted because it comes back out of IndexedDB,
       * which page script could have written to. */
      if (!payload.endpoint.startsWith("/sessions/")) {
        throw new Error("Invalid mark endpoint");
      }
      const body = new URLSearchParams();
      body.set("student_id", payload.studentId);
      body.set("status", "present");
      body.set("client_generated_id", payload.clientId);
      const response = await fetch(payload.endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": csrfToken(grid),
        },
        body,
      });
      /* ⚠ Two kinds of failure, and treating them alike is a lie to the student
       * standing there.
       *
       * A network error (fetch throws) means "not yet" — the item stays queued
       * and flushes when the signal comes back. That is the whole point of the
       * offline queue.
       *
       * A 4xx means "no, and not ever": the class is too old to mark
       * retroactively, the student is not on this roster, the session expired.
       * Retrying forever would leave a green tile that recorded nothing, which
       * is exactly the silent failure the tile is supposed to prevent. So the
       * item is dropped and the tile is put back. */
      if (response.status >= 400 && response.status < 500) {
        failed.add(payload.studentId);
        return { conflicts: 0 };
      }
      if (!response.ok) throw new Error("Mark failed (" + response.status + ")");
      return { conflicts: 0 };
    },
  });

  grid.addEventListener("click", async function (event) {
    const tile = event.target.closest(".kiosk-tile");
    if (!tile || tile.dataset.checked === "1") return;

    tile.dataset.checked = "1";
    tile.setAttribute("aria-pressed", "true");
    tile.classList.remove("border-gray-200");
    tile.classList.add("border-green-700");
    const check = tile.querySelector(".kiosk-check");
    if (check) check.classList.remove("invisible");

    await manager.enqueue({
      endpoint: markUrl,
      studentId: tile.dataset.studentId,
      clientId: crypto.randomUUID(),
    });
    if (navigator.onLine) {
      await manager.flush();
      drainFailures();
    }
  });

  window.addEventListener("online", async () => {
    await manager.flush();
    drainFailures();
  });
  manager.state();
});
