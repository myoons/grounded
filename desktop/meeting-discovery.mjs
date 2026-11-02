export class MeetingDiscovery {
  constructor(onClose = () => {}) {
    this.rooms = new Map();
    this.windows = new Map();
    this.onClose = onClose;
    this.status = "starting";
  }

  update(window) {
    if (!window || typeof window.id !== "string" || window.id.length > 256) return;
    let url;
    try { url = new URL(window.url || this.windows.get(window.id)); } catch { this.close(window.id); return; }
    const code = url.pathname.match(/^\/(?:meet\/)?([a-z]{3}-[a-z]{4}-[a-z]{3})$/)?.[1];
    if (url.protocol !== "https:" || url.hostname !== "meet.google.com" || url.port || url.username || url.password || !code) { this.close(window.id); return; }
    const key = url.origin + "/" + code;
    const previous = this.windows.get(window.id);
    if (previous && previous !== key) this.close(window.id);
    if (!this.rooms.has(key) && this.rooms.size >= 32) return;
    const room = this.rooms.get(key) || { id: key, url: key, title: "Google Meet", windows: new Set() };
    if (typeof window.title === "string" && window.title.trim()) room.title = window.title.replace(/[\x00-\x1f\x7f]/g, "").slice(0, 160);
    room.windows.add(window.id);
    this.windows.set(window.id, key);
    this.rooms.set(key, room);
    this.status = "ready";
  }

  close(windowId) {
    const key = this.windows.get(windowId);
    this.windows.delete(windowId);
    const room = this.rooms.get(key);
    if (!room) return;
    room.windows.delete(windowId);
    if (!room.windows.size) {
      this.rooms.delete(key);
      this.onClose(key);
    }
  }

  get(identifier) {
    const room = this.rooms.get(identifier);
    return room ? { id: room.id, title: room.title, url: room.url } : null;
  }

  snapshot() {
    return { status: this.status, meetings: [...this.rooms.keys()].map((key) => this.get(key)) };
  }

  invalidate() {
    for (const key of this.rooms.keys()) this.onClose(key);
    this.rooms.clear();
    this.windows.clear();
    this.status = "unavailable";
  }
}

export async function startDiscovery(sdk, discovery) {
  sdk.addEventListener("meeting-detected", ({ window }) => discovery.update(window));
  sdk.addEventListener("meeting-updated", ({ window }) => discovery.update(window));
  sdk.addEventListener("meeting-closed", ({ window }) => discovery.close(window?.id));
  sdk.addEventListener("permission-status", ({ permission, status }) => {
    if (permission !== "accessibility") return;
    if (status !== "granted") discovery.invalidate();
    discovery.status = status === "granted" ? "ready" : "permission_required";
  });
  sdk.addEventListener("error", () => discovery.invalidate());
  await sdk.init({ apiUrl: "https://us-west-2.recall.ai", acquirePermissionsOnStartup: ["accessibility"] });
}
