"""Read-only readiness checks; never print credentials or save screenshots."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path


def inspect(*, online: bool = False, capture: bool = True) -> dict:
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    add("Python", "ok" if sys.version_info >= (3, 12) else "blocked", sys.version.split()[0])
    for package, module in (("PySide6", "PySide6.QtWidgets"), ("opencv-python-headless", "cv2"),
                            ("numpy", "numpy"), ("mss", "mss"),
                            ("rapidocr-onnxruntime", "rapidocr_onnxruntime"), ("mahjong", "mahjong")):
        try:
            importlib.import_module(module)
            add(package, "ok", importlib.metadata.version(package))
        except Exception as error:
            add(package, "blocked", f"无法加载依赖：{type(error).__name__}；请运行启动脚本安装依赖")
    if any(c["status"] == "blocked" for c in checks):
        return {"ready": False, "checks": checks}

    from .settings import Settings
    from .vision import REGIONS, TEMPLATE_LABELS, TemplateStore
    settings = Settings.load()
    extension = Path(__file__).resolve().parents[2] / "browser-extension"
    hook_files = all((extension / name).is_file() for name in ("manifest.json", "content.js", "page-hook.js"))
    add("网页 Hook", "ok" if hook_files else "blocked",
        "扩展文件齐全；需在 Edge/Chrome 加载后刷新雀魂" if hook_files else "浏览器扩展文件缺失")
    add("配置文件", "blocked" if settings.load_warning else "ok", settings.load_warning or "可读取；诊断报告不含 Key")
    try:
        settings.connection().validate()
        add("模型配置", "ok", f"协议：{settings.model_protocol}；模型：{settings.model_name}")
    except Exception:
        add("模型配置", "blocked", "地址、模型或协议无效，请打开 Jev 设置修正")
    add("API Key", "ok" if settings.api_key.strip() else "blocked", "已配置（隐藏）" if settings.api_key.strip() else "未配置")
    add("牌桌范围", "ok" if settings.game_box else "blocked", "已选择；移动浏览器后需重新选择" if settings.game_box else "在【桌】选择牌桌画面")
    missing = [label for name, label, _, _, required in REGIONS if required and name not in settings.regions]
    add("基础校准", "blocked" if missing else "ok", "缺少：" + "、".join(missing) if missing else "已保存手牌、摸牌、按钮区域")
    missing_extra = [label for name, label, _, _, required in REGIONS if not required and name not in settings.regions]
    add("扩展校准", "warning" if missing_extra else "ok", "尚未校准：" + "、".join(missing_extra) if missing_extra else "全部区域已配置")
    labels = {label for label, _ in TemplateStore().samples}
    missing_tiles = sorted(set(TEMPLATE_LABELS) - labels)
    add("牌面模板", "warning" if missing_tiles and hook_files else "blocked" if missing_tiles else "ok",
        (f"屏幕备用识别已覆盖 {len(labels)}/{len(TEMPLATE_LABELS)} 类；网页 Hook 模式无需补齐。缺少：{' '.join(missing_tiles)}"
         if missing_tiles else "牌种和空格模板齐全；实际识别仍需牌桌验证"))
    try:
        from rapidocr_onnxruntime import RapidOCR
        RapidOCR()
        add("OCR 模型", "ok", "本地模型加载成功")
    except Exception as error:
        add("OCR 模型", "blocked", f"初始化失败：{type(error).__name__}")
    if capture:
        try:
            import mss
            with mss.MSS() as screen:
                box = settings.game_box
                monitor = dict(zip(("left", "top", "width", "height"), box)) if box else screen.monitors[1]
                shot = screen.grab(monitor)
                size = f"{shot.width} × {shot.height}"
                del shot
            add("屏幕采集", "ok", f"采集成功 {size}；未保存画面")
        except Exception as error:
            add("屏幕采集", "blocked", f"采集失败：{type(error).__name__}；检查系统录屏权限与牌桌范围")
    else:
        add("屏幕采集", "warning", "本次跳过")
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
    parser.add_argument("--no-capture", action="store_true", help="跳过屏幕采集")
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
