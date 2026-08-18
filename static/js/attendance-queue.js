"use strict";

/* The offline mark queue — TODO 1.6.2, shared by the roster and the kiosk.
 *
 * ⚠ One database, one store, one queue. Extracted from roster.js when the kiosk
 * arrived: a second IndexedDB store would mean marks made on the check-in screen
 * and marks made on the roster flushing independently, and an instructor who did
 * both offline would watch half of them sync. */

function openQueue() {
  /* ⚠ The "dojomaster" name below is deliberate and must not be renamed.
   *
   * It is the IndexedDB database holding attendance marks made offline and not
   * yet synced. Renaming it does not migrate them — it opens a different, empty
   * database and orphans whatever was queued, which is a class of students
   * silently unmarked. It survived the rename to BokyDojo for that reason.
   *
   * If it ever must change, the new version has to read the old store and
   * migrate the queue before switching. */
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("dojomaster-attendance", 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("queue", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export const store = {
  async list() {
    const database = await openQueue();
    return new Promise((resolve, reject) => {
      const request = database.transaction("queue").objectStore("queue").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  },
  async put(item) {
    const database = await openQueue();
    return new Promise((resolve, reject) => {
      const request = database
        .transaction("queue", "readwrite")
        .objectStore("queue")
        .put(item);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  },
  async remove(id) {
    const database = await openQueue();
    return new Promise((resolve, reject) => {
      const request = database
        .transaction("queue", "readwrite")
        .objectStore("queue")
        .delete(id);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  },
};
