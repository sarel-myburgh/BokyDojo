export function createSyncManager({ store, transport, scope = "default", onState = () => {} }) {
  async function state() {
    const items = (await store.list()).filter((item) => item.scope === scope);
    const pending = items
      .filter((item) => item.state === "pending")
      .reduce((total, item) => total + item.payload.marks.length, 0);
    const conflicts = items
      .filter((item) => item.state === "conflict")
      .reduce((total, item) => total + (item.conflicts || 0), 0);
    onState({ pending, conflicts });
    return { pending, conflicts };
  }

  async function enqueue(payload) {
    const item = {
      id: crypto.randomUUID(),
      scope,
      state: "pending",
      createdAt: new Date().toISOString(),
      payload,
    };
    await store.put(item);
    await state();
    return item;
  }

  async function flush() {
    const items = (await store.list()).filter((item) => item.scope === scope);
    for (const item of items.filter((candidate) => candidate.state === "pending")) {
      let response;
      try {
        response = await transport(item.payload);
      } catch (_error) {
        break;
      }
      if (response.conflicts > 0) {
        item.state = "conflict";
        item.conflicts = response.conflicts;
        item.result = response;
        await store.put(item);
      } else {
        await store.remove(item.id);
      }
    }
    return state();
  }

  async function discardConflicts() {
    const items = (await store.list()).filter((item) => item.scope === scope);
    for (const item of items.filter((candidate) => candidate.state === "conflict")) {
      await store.remove(item.id);
    }
    return state();
  }

  return { discardConflicts, enqueue, flush, state };
}