"""Read-only readiness checks; never print credentials or save screenshots."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

from .resources import extension_dir


def inspect(*, online: bool = False, capture: bool = True) -> dict:
    checks = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    add("Python", "ok" if sys.version_info >= (3, 12) else "blocked", sys.version.split()[0])
    for package, module in (
        ("PySide6", "PySide6.QtWidgets"),
        ("mahjong", "mahjong"),
        ("mitmproxy", "mitmproxy"),
    ):
        try:
            importlib.import_module(module)
            add(package, "ok", importlib.metadata.version(package))
        except Exception as error:
            add(package, "blocked", f"无法加载依赖：{type(error).__name__}；请运行启动脚本安装依赖")
    if any(c["status"] == "blocked" for c in checks):
        return {"ready": False, "checks": checks}

    from .hook import HookProtocol
    from .settings import Settings
    from .steam_capture import ensure_mitm_ca_generated, is_mitm_cert_installed

    settings = Settings.load()
    extension = extension_dir()
    hook_files = all((extension / name).is_file() for name in ("manifest.json", "content.js", "page-hook.js"))
    browser = settings.input_source == "browser"
    add("输入来源", "ok", "浏览器（网页 Hook 实时协议）" if browser else "Steam 客户端（实时协议直连）")

    protocol = HookProtocol()
    add(
        "协议字典",
        "ok" if protocol.decoder is not None else "blocked",
        f"已加载 {len(protocol.decoder.types)} 个 Liqi 协议消息定义" if protocol.decoder else "内置 liqi.json 加载失败",
    )

    if browser:
        add(
            "网页 Hook",
            "ok" if hook_files else "blocked",
            "扩展文件齐全；需在 Edge/Chrome 加载后刷新雀魂" if hook_files else "浏览器扩展文件缺失",
        )
    else:
        add(
            "Steam 捕获引擎",
            "ok",
            f"模式：{settings.steam_capture_mode}；进程：{settings.steam_process_name}；代理端口：{settings.steam_proxy_port}",
        )
        try:
            cert_path = ensure_mitm_ca_generated()
            installed = is_mitm_cert_installed()
            add(
                "本地 TLS 证书",
                "ok" if installed else "warning",
                f"系统已信任本地 CA（{cert_path.name}）"
                if installed
                else f"证书已生成（{cert_path}），首次使用请在【桌】→【Steam 实时连接与证书设置】一键安装信任",
            )
        except Exception as error:
            add("本地 TLS 证书", "warning", f"证书检查提示：{type(error).__name__}")

    add("配置文件", "blocked" if settings.load_warning else "ok", settings.load_warning or "可读取；诊断报告不含 Key")
    try:
        settings.connection().validate()
        add("模型配置", "ok", f"协议：{settings.model_protocol}；模型：{settings.model_name}")
    except Exception:
        add("模型配置", "blocked", "地址、模型或协议无效，请打开 Jev 设置修正")
    add("API Key", "ok" if settings.api_key.strip() else "blocked", "已配置（隐藏）" if settings.api_key.strip() else "未配置")

    if online and settings.api_key.strip():
        from .jev import JevError, test_connection

        try:
            advice = test_connection(settings.api_key, settings.connection())
            add("在线推断", "ok", f"HTTP 200，ping 校验通过；模型 {advice.model}；{advice.latency_ms} ms")
        except JevError as error:
            add("在线推断", "blocked", str(error).replace(settings.api_key.strip(), "[隐藏]"))
        except Exception as error:
            add("在线推断", "blocked", f"请求异常：{type(error).__name__}")
    else:
        add("在线推断", "warning", "尚未验证；在设置中测试连接，或运行自检 --online")
    return {"ready": all(c["status"] == "ok" for c in checks), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="使用前自检；只读配置，不保存截图或 Key")
    parser.add_argument("--online", action="store_true", help="向当前配置发送一次真实推断，可能产生少量费用")
    parser.add_argument("--no-capture", action="store_true", help="兼容保留参数")
    parser.add_argument("--output", type=Path, help="保存不含 Key 的 JSON 诊断报告")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = inspect(online=args.online, capture=not args.no_capture)
    # Redact exact credentials even if a provider echoes them in model metadata.
    from .settings import Settings

    text = json.dumps(report, ensure_ascii=False, indent=2)
    settings = Settings.load()
    for secret in (settings.api_key, settings.llm_api_key):
        if secret:
            text = text.replace(secret, "[隐藏]")
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
