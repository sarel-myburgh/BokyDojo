import assert from "node:assert/strict";
import test from "node:test";

import { createSyncManager } from "../../static/js/attendance-sync.js";

function memoryStore() {
  const items = new Map();
  return {
    async list() {
      return [...items.values()];
    },
    async put(item) {
      items.set(item.id, structuredClone(item));
    },
    async remove(id) {
      items.delete(id);
    },
  };
}

function roster(size = 20) {
  return {
    marks: Array.from({ length: size }, (_, index) => ({
      student_id: `student-${index}`,
      status: "present",
      client_generated_id: crypto.randomUUID(),
      base_version: "",
    })),
  };
}

test("a full offline class remains queued and syncs unchanged on reconnect", async () => {
  const store = memoryStore();
  const delivered = [];
  let online = false;
  const manager = createSyncManager({
    store,
    async transport(payload) {
      if (!online) throw new Error("network disabled");
      delivered.push(structuredClone(payload));
      return { conflicts: 0, results: [] };
    },
  });
  const payload = roster();

  await manager.enqueue(payload);
  assert.deepEqual(await manager.flush(), { pending: 20, conflicts: 0 });
  assert.equal(delivered.length, 0);

  online = true;
  assert.deepEqual(await manager.flush(), { pending: 0, conflicts: 0 });
  assert.deepEqual(delivered, [payload]);
});

test("conflicts stay visible until the instructor explicitly discards them", async () => {
  const manager = createSyncManager({
    store: memoryStore(),
    async transport() {
      return { conflicts: 2, results: [] };
    },
  });

  await manager.enqueue(roster(2));
  assert.deepEqual(await manager.flush(), { pending: 0, conflicts: 2 });
  assert.deepEqual(await manager.state(), { pending: 0, conflicts: 2 });
  assert.deepEqual(await manager.discardConflicts(), { pending: 0, conflicts: 0 });
});
test("a shared device never exposes or flushes another user's queue", async () => {
  const store = memoryStore();
  const delivered = [];
  const first = createSyncManager({
    store,
    scope: "instructor-a",
    async transport(payload) {
      delivered.push(payload);
      return { conflicts: 0, results: [] };
    },
  });
  const second = createSyncManager({
    store,
    scope: "instructor-b",
    async transport(payload) {
      delivered.push(payload);
      return { conflicts: 0, results: [] };
    },
  });
  const payload = { endpoint: "/api/attendance/sessions/original/sync/", ...roster(2) };

  await first.enqueue(payload);
  assert.deepEqual(await second.state(), { pending: 0, conflicts: 0 });
  assert.deepEqual(await second.flush(), { pending: 0, conflicts: 0 });
  assert.deepEqual(delivered, []);

  assert.deepEqual(await first.flush(), { pending: 0, conflicts: 0 });
  assert.deepEqual(delivered, [payload]);
});