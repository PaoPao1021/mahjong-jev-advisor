(() => {
  "use strict";
  const bridge = "http://127.0.0.1:8765";
  const extensionVersion = chrome.runtime.getManifest().version;
  const post = async (path, body, type = "application/json") => {
    try {
      const response = await fetch(bridge + path, {
        method: "POST",
        headers: { "Content-Type": type },
        body: typeof body === "string" ? body : JSON.stringify(body),
        cache: "no-store"
      });
      return response.ok;
    } catch (_) {
      return false;
    }
  };

  // Fallback injection in case world: MAIN is not supported by environment
  const inject = () => {
    if (window.__mahjongJevHookInstalled) return;
    const root = document.documentElement || document.head;
    if (!root) return setTimeout(inject, 0);
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("page-hook.js");
    script.onload = () => script.remove();
    root.prepend(script);
  };
  inject();

  let frameQueue = Promise.resolve();
  let lastSeq = -1;
  let bridgeReachable = false;
  let hookStats = {
    hookReady: false, socketCount: 0, openSocketCount: 0,
    sentFrames: 0, receivedFrames: 0, lastSocketHost: ""
  };

  const handlePacket = raw => {
    if (!raw) return;
    let packet = raw;
    if (typeof packet === "string") {
      try {
        packet = JSON.parse(packet);
      } catch (_) {
        return;
      }
    }
    if (!packet || typeof packet.data !== "string") return;
    if (typeof packet.seq === "number") {
      if (packet.seq <= lastSeq) return;
      lastSeq = packet.seq;
    }
    const payload = JSON.stringify({
      direction: packet.direction || "receive",
      url: packet.url || "",
      data: packet.data
    });
    frameQueue = frameQueue.then(async () => {
      bridgeReachable = await post("/frame", payload);
    });
  };

  const handleMeta = raw => {
    try {
      const value = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (!value || typeof value !== "object") return;
      hookStats = {...hookStats, ...value};
    } catch (_) {}
  };

  // Channel 1: CustomEvent (handles stringified detail)
  window.addEventListener("mahjong-jev-frame", event => {
    handlePacket(event.detail);
  });
  window.addEventListener("mahjong-jev-meta", event => handleMeta(event.detail));

  // Channel 2: postMessage (standard cross-realm)
  window.addEventListener("message", event => {
    if (event.source !== window) return;
    if (event.data && event.data.type === "mahjong-jev-frame-msg") {
      handlePacket(event.data.payload);
    } else if (event.data && event.data.type === "mahjong-jev-meta-msg") {
      handleMeta(event.data.payload);
    }
  });

  window.dispatchEvent(new CustomEvent("mahjong-jev-probe"));

  const reportDiagnostic = async () => {
    const ok = await post("/diagnostic", {
      ...hookStats,
      extensionVersion,
      bridgeReachable,
      pageHost: location.host,
      unity: Boolean(document.querySelector("#unity-canvas"))
    });
    bridgeReachable = ok;
  };
  setInterval(reportDiagnostic, 2000);
  setTimeout(reportDiagnostic, 300);

  let schemaUrl = "";
  const discoverSchema = async () => {
    post("/ping", "{}");
    const entry = performance.getEntriesByType("resource").find(item => /\/liqi\.json(?:\?|$)/.test(item.name));
    if (!entry || entry.name === schemaUrl) return;
    try {
      const text = await fetch(entry.name, { cache: "force-cache" }).then(response => {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      });
      JSON.parse(text);
      if (await post("/schema", text)) {
        schemaUrl = entry.name;
      }
    } catch (_) {}
  };
  setInterval(discoverSchema, 2000);
  setTimeout(discoverSchema, 200);
})();
