(() => {
  "use strict";
  if (window.__mahjongJevHookInstalled) return;
  window.__mahjongJevHookInstalled = true;
  console.log("[Mahjong Jev Bridge] Installing page WebSocket hook...");
  const NativeWebSocket = window.WebSocket;
  const stats = {
    hookReady: true,
    socketCount: 0,
    openSocketCount: 0,
    sentFrames: 0,
    receivedFrames: 0,
    lastSocketHost: ""
  };

  const safeHost = value => {
    try { return new URL(String(value || "")).host; } catch (_) { return ""; }
  };

  const emitMeta = () => {
    const detail = JSON.stringify(stats);
    window.dispatchEvent(new CustomEvent("mahjong-jev-meta", {detail}));
    window.postMessage({type: "mahjong-jev-meta-msg", payload: {...stats}}, "*");
  };

  const encode = bytes => {
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  };

  let seq = 0;
  const emit = async (direction, url, value) => {
    try {
      let bytes;
      if (value instanceof ArrayBuffer) bytes = new Uint8Array(value);
      else if (ArrayBuffer.isView(value)) bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
      else if (value instanceof Blob) bytes = new Uint8Array(await value.arrayBuffer());
      else return;
      if (!bytes.length || bytes.length > 3 * 1024 * 1024) return;

      if (direction === "send") stats.sentFrames++;
      else stats.receivedFrames++;

      seq++;
      const packet = {
        seq,
        direction,
        url: String(url || ""),
        data: encode(bytes)
      };
      const packetStr = JSON.stringify(packet);

      // Channel 1: CustomEvent with JSON string (safe across V8 world boundary)
      window.dispatchEvent(new CustomEvent("mahjong-jev-frame", { detail: packetStr }));

      // Channel 2: postMessage (standard HTML5 cross-realm channel)
      window.postMessage({ type: "mahjong-jev-frame-msg", payload: packet }, "*");
      if ((stats.sentFrames + stats.receivedFrames) <= 3 ||
          (stats.sentFrames + stats.receivedFrames) % 50 === 0) emitMeta();
    } catch (e) {
      console.error("[Mahjong Jev Bridge] Error in emit:", e);
    }
  };

  class ReadOnlyHookWebSocket extends NativeWebSocket {
    constructor(url, protocols) {
      if (protocols !== undefined) {
        super(url, protocols);
      } else {
        super(url);
      }
      this._hookUrl = String(url || "");
      stats.socketCount++;
      stats.lastSocketHost = safeHost(url);
      console.log("[Mahjong Jev Bridge] Intercepted WebSocket connection:", url);
      emitMeta();
      this.__mahjongJevQueue = Promise.resolve();
      this.addEventListener("open", () => {
        stats.openSocketCount++;
        emitMeta();
      });
      this.addEventListener("close", () => {
        stats.openSocketCount = Math.max(0, stats.openSocketCount - 1);
        emitMeta();
      });
      this.addEventListener("message", event => {
        const u = this._hookUrl || this.url;
        this.__mahjongJevQueue = this.__mahjongJevQueue.then(() => emit("receive", u, event.data));
      });
    }
    send(data) {
      const u = this._hookUrl || this.url;
      emit("send", u, data);
      return super.send(data);
    }
  }

  Object.defineProperties(ReadOnlyHookWebSocket, {
    CONNECTING: {value: NativeWebSocket.CONNECTING},
    OPEN: {value: NativeWebSocket.OPEN},
    CLOSING: {value: NativeWebSocket.CLOSING},
    CLOSED: {value: NativeWebSocket.CLOSED}
  });

  window.WebSocket = ReadOnlyHookWebSocket;
  window.addEventListener("mahjong-jev-probe", emitMeta);
  emitMeta();
  console.log("[Mahjong Jev Bridge] Page WebSocket hook ready.");
})();
