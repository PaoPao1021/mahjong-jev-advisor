"""Production-grade read-only Mahjong Soul Steam WebSocket protocol capture engine.

Supports two zero-screenshot, millisecond-latency capture modes:
1. OS Process Capture (`mode="process"`, `local:Jantama_MahjongSoul`):
   Transparently intercepts only the Mahjong Soul Steam desktop client process via
   `mitmproxy`'s OS redirector (`mitmproxy-windows` / `mitmproxy-macos`) without
   changing system proxy settings. Automatically falls back to local proxy mode if
   OS redirector privileges are unavailable.
2. Local Proxy Capture (`mode="proxy"`, `regular@127.0.0.1:10999`):
   Listens on a loopback HTTP/HTTPS/WebSocket proxy port (with optional upstream
   proxy chaining) for zero-admin capture via PAC (`/proxy.pac`), Clash, Surge, or
   Proxifier rules.

All interception is strictly READ-ONLY: packets and WebSocket frames are never
modified, dropped, delayed, or injected.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .hook import (
    DEFAULT_STEAM_PROXY_PORT,
    HOOK_PORT,
    MAJSOUL_DOMAINS,
    HookProtocol,
    build_pac_script,
)
from .state import GameState

DEFAULT_STEAM_PROCESS = "Jantama_MahjongSoul"
KNOWN_STEAM_PROCESS_NAMES = (
    "Jantama_MahjongSoul",
    "MahjongSoul",
    "雀魂麻將",
    "雀魂麻将",
    "majsoul",
)

# Third-party non-game domains that should bypass TLS MITM inspection when
# process capture intercepts the entire game executable.
BYPASS_TLS_DOMAINS = (
    "steampowered.com",
    "steamcommunity.com",
    "steamstatic.com",
    "steamcontent.com",
    "steamusercontent.com",
    "valve.net",
    "googleapis.com",
    "google.com",
    "gstatic.com",
    "microsoft.com",
    "live.com",
    "apple.com",
    "icloud.com",
)


def is_majsoul_host(host: str) -> bool:
    """Return True if the host belongs to a known Mahjong Soul / Yostar domain."""
    clean = (host or "").strip().lower().split(":", 1)[0].rstrip(".")
    if not clean:
        return False
    return any(clean == domain or clean.endswith("." + domain) for domain in MAJSOUL_DOMAINS)


def is_bypass_tls_host(host: str) -> bool:
    """Return True if TLS interception should be skipped for a third-party domain."""
    clean = (host or "").strip().lower().split(":", 1)[0].rstrip(".")
    if not clean:
        return False
    return any(clean == domain or clean.endswith("." + domain) for domain in BYPASS_TLS_DOMAINS)


def is_likely_liqi_frame(data: bytes) -> bool:
    """Inspect a binary WebSocket payload to determine if it uses Liqi framing.

    Liqi frames begin with:
    - 0x01 (Notify): followed by protobuf `lq.Wrapper` (field 1 string `.lq.*`)
    - 0x02 (Request): 2-byte little-endian id + `lq.Wrapper` (field 1 string `.lq.*`)
    - 0x03 (Response): 2-byte little-endian id + `lq.Wrapper` (field 1 empty, field 2 bytes)
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 2:
        return False
    category = data[0]
    if category == 1:
        wrapper = data[1:36]
        return wrapper.startswith(b"\x0a") and b"lq." in wrapper
    if category == 2:
        if len(data) < 5:
            return False
        wrapper = data[3:40]
        return wrapper.startswith(b"\x0a") and b"lq." in wrapper
    if category == 3:
        if len(data) < 4:
            return False
        wrapper = data[3:24]
        return (
            wrapper.startswith(b"\x12")
            or wrapper.startswith(b"\x0a\x00\x12")
            or (wrapper.startswith(b"\x0a") and b"lq." in wrapper)
        )
    return False


def mitm_confdir() -> Path:
    return Path.home() / ".mitmproxy"


def mitm_cert_path(confdir: Path | None = None) -> Path:
    base = confdir or mitm_confdir()
    if sys.platform.startswith("win"):
        cer = base / "mitmproxy-ca-cert.cer"
        if cer.exists():
            return cer
    return base / "mitmproxy-ca-cert.pem"


def ensure_mitm_ca_generated(confdir: Path | None = None) -> Path:
    """Ensure the mitmproxy CA certificate files exist in `confdir`."""
    base = confdir or mitm_confdir()
    base.mkdir(parents=True, exist_ok=True)
    pem = base / "mitmproxy-ca-cert.pem"
    if not pem.exists():
        from mitmproxy.certs import CertStore

        CertStore.from_store(base, "mitmproxy", 2048)
    return mitm_cert_path(base)


def is_mitm_cert_installed(confdir: Path | None = None) -> bool:
    """Check whether the local mitmproxy CA exists and is trusted by the OS."""
    pem = (confdir or mitm_confdir()) / "mitmproxy-ca-cert.pem"
    if not pem.exists():
        return False
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(
                ["certutil", "-user", "-verifystore", "Root", "mitmproxy"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
            return proc.returncode == 0
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["security", "find-certificate", "-c", "mitmproxy"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return proc.returncode == 0
    except Exception:
        return False
    return True


def install_mitm_cert(confdir: Path | None = None) -> tuple[bool, str]:
    """Generate and install the mitmproxy root CA into the current user's trust store."""
    try:
        cert_file = ensure_mitm_ca_generated(confdir)
    except Exception as error:
        return False, f"生成本地 CA 证书失败：{error}"

    if is_mitm_cert_installed(confdir):
        return True, f"本地 MITM CA 证书已安装并受信任（{cert_file}）"

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if sys.platform.startswith("win"):
            cer_file = cert_file.with_suffix(".cer")
            target = cer_file if cer_file.exists() else cert_file
            proc = subprocess.run(
                ["certutil", "-user", "-addstore", "Root", str(target)],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=creationflags,
            )
            if proc.returncode == 0:
                return True, "已成功将 mitmproxy 根证书安装到当前用户受信任根证书颁发机构"
            detail = (proc.stderr or proc.stdout or "").strip()
            return False, f"证书安装未完成（{detail or proc.returncode}），请确认系统弹窗或手动双击安装 {target}"
        if sys.platform == "darwin":
            login_keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
            cmd = ["security", "add-trusted-cert", "-d", "-r", "trustRoot"]
            if login_keychain.exists():
                cmd.extend(["-k", str(login_keychain)])
            cmd.append(str(cert_file))
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and is_mitm_cert_installed(confdir):
                return True, "已成功将 mitmproxy 根证书安装并信任到 macOS 登录钥匙串"
            subprocess.Popen(["open", str(cert_file)])
            return (
                False,
                f"已在『钥匙串访问』打开证书 {cert_file}，请双击并将『使用此证书时』设为『始终信任』",
            )
    except Exception as error:
        return False, f"安装证书失败：{error}（证书路径：{cert_file}）"
    return False, f"请手动将证书导入系统受信任根证书库：{cert_file}"


def detect_steam_game_processes() -> list[dict[str, Any]]:
    """Return running processes that match known Mahjong Soul Steam client names."""
    results: list[dict[str, Any]] = []
    seen_pids: set[int] = set()
    keywords = tuple(name.lower() for name in KNOWN_STEAM_PROCESS_NAMES)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
            reader = csv.reader(io.StringIO(proc.stdout or ""))
            for row in reader:
                if len(row) < 2:
                    continue
                image_name = row[0].strip()
                try:
                    pid = int(row[1].strip())
                except ValueError:
                    continue
                lower = image_name.lower()
                if any(k in lower for k in keywords) and pid not in seen_pids:
                    seen_pids.add(pid)
                    spec = image_name[:-4] if lower.endswith(".exe") else image_name
                    results.append({"pid": pid, "name": image_name, "spec": spec})
        else:
            proc = subprocess.run(
                ["ps", "-axo", "pid=,comm="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in (proc.stdout or "").splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                comm = parts[1].strip()
                base = Path(comm).name
                lower = base.lower()
                if any(k in lower for k in keywords) and pid not in seen_pids:
                    seen_pids.add(pid)
                    spec = base[:-4] if lower.endswith(".exe") else base
                    results.append({"pid": pid, "name": base, "spec": spec})
    except Exception:
        pass
    return results


def generate_pac_script(proxy_port: int = DEFAULT_STEAM_PROXY_PORT, proxy_host: str = "127.0.0.1") -> str:
    """Return a PAC script routing Mahjong Soul domains through the local proxy."""
    return build_pac_script(proxy_port=proxy_port, proxy_host=proxy_host)


def generate_clash_rules(
    proxy_port: int = DEFAULT_STEAM_PROXY_PORT,
    process_name: str = DEFAULT_STEAM_PROCESS,
) -> str:
    """Return a Clash / Mihomo YAML configuration snippet for Steam Mahjong Soul."""
    exe_name = process_name if process_name.lower().endswith(".exe") else f"{process_name}.exe"
    domain_lines = "\n".join(f"  - DOMAIN-SUFFIX,{domain},mahjong-jev-local" for domain in MAJSOUL_DOMAINS)
    return (
        "# 1. 在 Clash / Mihomo 的 proxies 下添加本地只读监听节点：\n"
        "proxies:\n"
        "  - name: mahjong-jev-local\n"
        "    type: http\n"
        "    server: 127.0.0.1\n"
        f"    port: {int(proxy_port)}\n\n"
        "# 2. 在 rules 最上方添加雀魂 Steam 进程与域名分流规则：\n"
        "rules:\n"
        f"  - PROCESS-NAME,{exe_name},mahjong-jev-local\n"
        "  - PROCESS-NAME,雀魂麻將.exe,mahjong-jev-local\n"
        f"{domain_lines}\n"
    )


def generate_surge_rules(
    proxy_port: int = DEFAULT_STEAM_PROXY_PORT,
    process_name: str = DEFAULT_STEAM_PROCESS,
    hook_port: int = HOOK_PORT,
) -> str:
    """Return Surge / Proxifier / PAC configuration instructions for Steam Mahjong Soul."""
    exe_name = process_name if process_name.lower().endswith(".exe") else f"{process_name}.exe"
    domain_lines = "\n".join(f"DOMAIN-SUFFIX,{domain},mahjong-jev-local" for domain in MAJSOUL_DOMAINS)
    return (
        f"# 方式 A：系统自动代理 PAC 地址（零第三方软件）\n"
        f"http://127.0.0.1:{int(hook_port)}/proxy.pac\n\n"
        f"# 方式 B：Steam 启动选项（右键雀魂 -> 属性 -> 通用 -> 启动选项）\n"
        f"--proxy-server=http://127.0.0.1:{int(proxy_port)}\n\n"
        "# 方式 C：Surge / Proxifier 规则\n"
        "[Proxy]\n"
        f"mahjong-jev-local = http, 127.0.0.1, {int(proxy_port)}\n\n"
        "[Rule]\n"
        f"PROCESS-NAME,{exe_name},mahjong-jev-local\n"
        f"{domain_lines}\n"
    )


class ReadOnlyLiqiAddon:
    """Strictly read-only mitmproxy addon that observes Mahjong Soul Liqi WebSocket frames."""

    def __init__(
        self,
        on_frame: Callable[[str, bytes, str, str], None],
        on_schema: Callable[[dict[str, Any]], None] | None = None,
        on_socket_change: Callable[[int, str], None] | None = None,
        on_socket_end: Callable[[str], None] | None = None,
    ):
        self._on_frame = on_frame
        self._on_schema = on_schema
        self._on_socket_change = on_socket_change
        self._on_socket_end = on_socket_end
        self.active_sockets: dict[str, str] = {}
        self.confirmed_liqi_sockets: set[str] = set()
        self.last_host: str = ""

    def tls_clienthello(self, data: Any) -> None:
        """Bypass TLS interception for known non-game third-party domains."""
        try:
            sni = getattr(data.client_hello, "sni", "") or ""
            if sni and is_bypass_tls_host(sni):
                data.ignore_connection = True
        except Exception:
            pass

    def response(self, flow: Any) -> None:
        """Read-only inspection of `liqi.json` HTTP responses to auto-update schema."""
        if self._on_schema is None:
            return
        try:
            path = (flow.request.path or "").split("?", 1)[0].lower()
            if not path.endswith("liqi.json"):
                return
            if getattr(flow.response, "status_code", 0) != 200:
                return
            raw = flow.response.get_content(strict=False)
            if not raw:
                return
            schema = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(schema, dict) and isinstance(schema.get("nested"), dict):
                self._on_schema(schema)
        except Exception:
            pass

    def websocket_start(self, flow: Any) -> None:
        try:
            conn_id = str(getattr(flow, "id", ""))
            host = str(getattr(flow.request, "pretty_host", "") or getattr(flow.request, "host", ""))
            self.active_sockets[conn_id] = host
            if is_majsoul_host(host):
                self.confirmed_liqi_sockets.add(conn_id)
                self.last_host = host
            if self._on_socket_change is not None:
                self._on_socket_change(len(self.active_sockets), self.last_host or host)
        except Exception:
            pass

    def websocket_message(self, flow: Any) -> None:
        """Read-only copy of binary Liqi WebSocket messages. Never mutates or drops."""
        try:
            ws = getattr(flow, "websocket", None)
            if ws is None or not ws.messages:
                return
            message = ws.messages[-1]
            if getattr(message, "is_text", False):
                return
            content = getattr(message, "content", b"")
            if not isinstance(content, (bytes, bytearray)) or not content:
                return
            raw = bytes(content)
            conn_id = str(getattr(flow, "id", ""))
            host = self.active_sockets.get(conn_id) or str(
                getattr(flow.request, "pretty_host", "") or getattr(flow.request, "host", "")
            )
            is_confirmed = conn_id in self.confirmed_liqi_sockets
            if not is_confirmed:
                if is_majsoul_host(host) or is_likely_liqi_frame(raw):
                    self.confirmed_liqi_sockets.add(conn_id)
                    is_confirmed = True
            if not is_confirmed:
                return
            if host:
                self.last_host = host
            direction = "send" if getattr(message, "from_client", False) else "receive"
            self._on_frame(direction, raw, conn_id or host, host)
        except Exception:
            pass

    def websocket_end(self, flow: Any) -> None:
        try:
            conn_id = str(getattr(flow, "id", ""))
            self.active_sockets.pop(conn_id, None)
            self.confirmed_liqi_sockets.discard(conn_id)
            if self._on_socket_end is not None:
                self._on_socket_end(conn_id)
            if self._on_socket_change is not None:
                self._on_socket_change(len(self.active_sockets), self.last_host)
        except Exception:
            pass


class SteamCaptureSignals(QObject):
    state = Signal(object)
    status = Signal(str)
    diagnostic = Signal(object)
    unavailable = Signal(str)


class SteamCaptureBridge:
    """Manages real-time Steam WebSocket protocol capture and decodes Liqi GameStates."""

    def __init__(
        self,
        mode: str = "process",
        process_name: str = DEFAULT_STEAM_PROCESS,
        proxy_port: int = DEFAULT_STEAM_PROXY_PORT,
        upstream_proxy: str = "",
        confdir: Path | None = None,
    ):
        self.mode = mode if mode in {"process", "proxy"} else "process"
        self.process_name = (process_name or DEFAULT_STEAM_PROCESS).strip() or DEFAULT_STEAM_PROCESS
        self.proxy_port = int(proxy_port or DEFAULT_STEAM_PROXY_PORT)
        self.upstream_proxy = (upstream_proxy or "").strip()
        self.confdir = confdir or mitm_confdir()

        self.signals = SteamCaptureSignals()
        self.protocol = HookProtocol()
        self._lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_checked_at = 0.0
        self._diagnostic_refreshing = False
        self._system_diagnostic: dict[str, Any] = {
            "cert_installed": False,
            "cert_path": str(mitm_cert_path(self.confdir)),
            "detected_processes": [],
        }
        self._live_ready = False
        self._state_connection = ""

        self.running = False
        self.active_mode_spec = ""
        self.fallback_active = False
        self.ws_connections = 0
        self.frames_seen = 0
        self.states_seen = 0
        self.decode_errors = 0
        self.last_seen = 0.0
        self.last_host = ""
        self.last_error = ""
        self._last_stage = ""

        self._loop: asyncio.AbstractEventLoop | None = None
        self._master: Any = None
        self._thread: threading.Thread | None = None
        self._addon: ReadOnlyLiqiAddon | None = None

    @property
    def connected(self) -> bool:
        return self.running and (self.ws_connections > 0 or (time.monotonic() - self.last_seen < 10.0))

    @property
    def live_ready(self) -> bool:
        return self._live_ready

    def _emit_stage(self, stage: str, message: str) -> None:
        if stage != self._last_stage:
            self._last_stage = stage
            self.signals.status.emit(message)
        self.signals.diagnostic.emit(self.diagnostic_snapshot())

    def refresh_system_diagnostic(self) -> None:
        """Refresh slow OS probes off the proxy/UI threads, at most every 30 seconds."""
        with self._diagnostic_lock:
            if self._diagnostic_refreshing or (
                self._diagnostic_checked_at and time.monotonic() - self._diagnostic_checked_at < 30
            ):
                return
            self._diagnostic_refreshing = True

        def refresh() -> None:
            try:
                snapshot = {
                    "cert_installed": is_mitm_cert_installed(self.confdir),
                    "cert_path": str(mitm_cert_path(self.confdir)),
                    "detected_processes": detect_steam_game_processes(),
                }
                with self._diagnostic_lock:
                    self._system_diagnostic = snapshot
            finally:
                with self._diagnostic_lock:
                    self._diagnostic_checked_at = time.monotonic()
                    self._diagnostic_refreshing = False

        threading.Thread(target=refresh, daemon=True, name="steam-system-diagnostic").start()

    def diagnostic_snapshot(self) -> dict[str, Any]:
        with self._diagnostic_lock:
            system_diagnostic = dict(self._system_diagnostic)
        return {
            "running": self.running,
            "mode": self.mode,
            "active_mode_spec": self.active_mode_spec,
            "fallback_active": self.fallback_active,
            "process_name": self.process_name,
            "proxy_port": self.proxy_port,
            "upstream_proxy": self.upstream_proxy,
            "ws_connections": self.ws_connections,
            "frames_seen": self.frames_seen,
            "states_seen": self.states_seen,
            "decode_errors": self.decode_errors,
            "last_host": self.last_host,
            "last_error": self.last_error,
            "last_message_name": self.protocol.last_message_name,
            "last_action_name": self.protocol.last_action_name,
            "own_seat": self.protocol.builder.own_seat,
            **system_diagnostic,
        }

    def configure(
        self,
        *,
        mode: str | None = None,
        process_name: str | None = None,
        proxy_port: int | None = None,
        upstream_proxy: str | None = None,
    ) -> None:
        restart = self.running
        if restart:
            self.stop()
        if mode in {"process", "proxy"}:
            self.mode = mode
        if process_name is not None:
            self.process_name = process_name.strip() or DEFAULT_STEAM_PROCESS
        if proxy_port is not None:
            self.proxy_port = int(proxy_port)
        if upstream_proxy is not None:
            self.upstream_proxy = upstream_proxy.strip()
        if restart:
            self.start()

    def ingest_schema(self, schema: dict[str, Any]) -> None:
        with self._lock:
            self.protocol.set_schema(schema)
        self._emit_stage("schema-ready", "Steam 协议字典已同步")

    def ingest_frame(
        self,
        direction: str,
        frame: bytes,
        connection: str = "",
        host: str = "",
    ) -> GameState | None:
        try:
            with self._lock:
                self.last_seen = time.monotonic()
                self.frames_seen += 1
                if host:
                    self.last_host = host
                first_frame = self.frames_seen == 1
                state = self.protocol.feed(direction, frame, connection)
                if state is not None:
                    self.states_seen += 1
                    self._live_ready = True
                    self._state_connection = connection
                    self._last_stage = "state-ready"
            if first_frame and state is None:
                self._emit_stage("frames-ready", "Steam 实时通道已捕获雀魂数据帧 · 等待开局状态")
            if state is not None:
                self.signals.status.emit(f"Steam 实时牌局已同步 · 手牌 {len(state.hand)} 张")
                self.signals.state.emit(state)
                self.signals.diagnostic.emit(self.diagnostic_snapshot())
            return state
        except Exception as error:
            with self._lock:
                self.decode_errors += 1
                self.last_error = f"{type(error).__name__}: {str(error)[:160]}"
            self._emit_stage(
                "decode-error",
                f"Steam 数据帧解析异常（{self.decode_errors} 次）· 正在等待下一帧",
            )
            return None

    def _on_socket_change(self, count: int, host: str) -> None:
        self.ws_connections = count
        if host:
            self.last_host = host
        if count == 0:
            self._invalidate_state("Steam 实时连接已断开 · 等待重新同步牌局")
        if count > 0 and not self.live_ready:
            self._emit_stage(
                "socket-ready",
                f"已截获 Steam 雀魂连接（{host or 'WebSocket'}）· 等待牌局数据",
            )
        else:
            self.signals.diagnostic.emit(self.diagnostic_snapshot())

    def _on_socket_end(self, connection: str) -> None:
        # The lobby WebSocket may stay open after the actual game disconnects.
        if connection and connection == self._state_connection:
            self._invalidate_state("Steam 对局连接已断开 · 等待重新同步牌局")

    def _invalidate_state(self, message: str) -> None:
        with self._lock:
            self._live_ready = False
            self._state_connection = ""
            self.last_seen = 0.0
            # Never combine a new connection's actions with the previous hand.
            self.protocol = HookProtocol()
        self.signals.unavailable.emit(message)

    def _build_proxy_mode_spec(self) -> str:
        if self.upstream_proxy:
            upstream = self.upstream_proxy
            if "://" not in upstream:
                upstream = f"http://{upstream}"
            return f"upstream:{upstream}@127.0.0.1:{self.proxy_port}"
        return f"regular@127.0.0.1:{self.proxy_port}"

    def _build_process_mode_spec(self) -> str:
        spec = self.process_name.strip() or DEFAULT_STEAM_PROCESS
        if spec.lower().endswith(".exe"):
            spec = spec[:-4]
        return f"local:{spec}"

    def start(self) -> bool:
        if self.running:
            return True

        self.refresh_system_diagnostic()
        try:
            ensure_mitm_ca_generated(self.confdir)
        except Exception as error:
            self.last_error = f"CA init error: {error}"

        ready_event = threading.Event()
        start_result: dict[str, Any] = {"ok": False, "error": ""}

        def _thread_main() -> None:
            from mitmproxy import options
            from mitmproxy.addons import core, dns_resolver, next_layer, proxyserver, tlsconfig
            from mitmproxy.master import Master

            logging.getLogger("mitmproxy").setLevel(logging.ERROR)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _runner() -> None:
                opts = options.Options()
                master = Master(opts, event_loop=loop)
                ps = proxyserver.Proxyserver()
                addon = ReadOnlyLiqiAddon(
                    on_frame=self.ingest_frame,
                    on_schema=self.ingest_schema,
                    on_socket_change=self._on_socket_change,
                    on_socket_end=self._on_socket_end,
                )
                self._addon = addon
                self._master = master
                master.addons.add(
                    core.Core(),
                    ps,
                    next_layer.NextLayer(),
                    tlsconfig.TlsConfig(),
                    dns_resolver.DnsResolver(),
                    addon,
                )

                proxy_spec = self._build_proxy_mode_spec()
                desired_modes = (
                    [self._build_process_mode_spec()]
                    if self.mode == "process"
                    else [proxy_spec]
                )
                self.fallback_active = False

                async def start_mode(spec: str) -> None:
                    # Configure before Master.run(): Proxyserver.running is only
                    # a lifecycle flag, not evidence that any listener started.
                    opts.update(confdir=str(self.confdir), mode=[spec])
                    await asyncio.wait_for(ps.setup_servers(), timeout=2.0)
                    instance = ps.servers[spec]
                    if not instance.is_running or instance.last_exception is not None:
                        raise RuntimeError(str(instance.last_exception or f"无法启动监听模式 {spec}"))

                try:
                    await start_mode(desired_modes[0])
                    self.active_mode_spec = desired_modes[0]
                    start_result["ok"] = True
                except Exception as primary_error:
                    if self.mode == "process":
                        try:
                            await start_mode(proxy_spec)
                            self.fallback_active = True
                            self.active_mode_spec = proxy_spec
                            self.last_error = f"进程直连未就绪（{primary_error}），已自动回退本地代理"
                            start_result["ok"] = True
                        except Exception as fallback_error:
                            start_result["error"] = f"{primary_error}; fallback: {fallback_error}"
                    else:
                        start_result["error"] = str(primary_error)

                self.running = start_result["ok"]
                ready_event.set()
                if self.running:
                    try:
                        await master.run()
                    finally:
                        await ps.servers.update([])
                else:
                    await ps.servers.update([])

            try:
                loop.run_until_complete(_runner())
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                ready_event.set()
            finally:
                self.running = False
                self._invalidate_state("Steam 捕获引擎已停止 · 等待重新连接")
                try:
                    loop.close()
                except Exception:
                    pass

        self._thread = threading.Thread(
            target=_thread_main,
            daemon=True,
            name="mahjong-jev-steam-capture",
        )
        self._thread.start()
        ready_event.wait(timeout=6.0)

        if not start_result["ok"]:
            self.last_error = start_result["error"] or self.last_error or "启动 Steam 捕获引擎失败"
            self.stop()
            self._emit_stage(
                "start-error",
                f"Steam 实时捕获未就绪 · {self.last_error[:72]}",
            )
            return False

        if self.fallback_active:
            self._emit_stage(
                "fallback-proxy",
                f"进程直连需系统权限 · 已自动启用本地代理 127.0.0.1:{self.proxy_port}",
            )
        elif self.mode == "process":
            self._emit_stage(
                "process-listening",
                f"Steam 进程直连已就绪（{self.process_name}）· 等待雀魂对局数据",
            )
        else:
            self._emit_stage(
                "proxy-listening",
                f"Steam 本地代理已启动（127.0.0.1:{self.proxy_port}）· 等待雀魂连接",
            )
        return True

    def stop(self) -> None:
        loop = self._loop
        master = self._master
        if loop is not None and master is not None:
            try:
                loop.call_soon_threadsafe(master.shutdown)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._loop = None
        self._master = None
        self._addon = None
        self.running = False
        self.ws_connections = 0
        self._invalidate_state("Steam 捕获引擎已停止 · 等待重新连接")


def run_steam_capture_worker(argv: list[str] | None = None) -> int:
    """CLI entry point for running the read-only Steam capture engine as a standalone worker."""
    parser = argparse.ArgumentParser(description="Read-only Steam Mahjong Soul Liqi capture worker")
    parser.add_argument("--mode", choices=["process", "proxy"], default="process")
    parser.add_argument("--process", default=DEFAULT_STEAM_PROCESS)
    parser.add_argument("--port", type=int, default=DEFAULT_STEAM_PROXY_PORT)
    parser.add_argument("--upstream", default="")
    args = parser.parse_args(argv)

    bridge = SteamCaptureBridge(
        mode=args.mode,
        process_name=args.process,
        proxy_port=args.port,
        upstream_proxy=args.upstream,
    )

    def _emit_json(event: str, payload: Any) -> None:
        line = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    bridge.signals.status.connect(lambda msg: _emit_json("status", msg))
    bridge.signals.state.connect(lambda state: _emit_json("state", state.to_dict()))
    if not bridge.start():
        _emit_json("error", bridge.last_error)
        return 1
    try:
        while bridge.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_steam_capture_worker())
