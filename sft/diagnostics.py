"""Check native GUI dependencies before QApplication can abort the process."""

import ctypes
import json
import logging
import os
import re
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path

from .protocol import configure_listener


def hide_x11_hostname(window):
    """Keep XWin/MobaXterm from appending @host to this window's title."""
    if sys.platform != "linux":
        return
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None or app.platformName() != "xcb":
        return
    try:
        # Use Qt's existing connection so window creation and property deletion
        # are ordered before mapping. XCB_ATOM_WM_CLIENT_MACHINE is predefined.
        window_id = int(window.winId())
        connection = app.nativeInterface().connection()
        if not connection:
            return

        class VoidCookie(ctypes.Structure):
            _fields_ = [("sequence", ctypes.c_uint)]

        xcb = ctypes.CDLL("libxcb.so.1")
        xcb.xcb_delete_property.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        xcb.xcb_delete_property.restype = VoidCookie
        xcb.xcb_flush.argtypes = [ctypes.c_void_p]
        xcb.xcb_flush.restype = ctypes.c_int
        xcb.xcb_delete_property(connection, window_id, 36)
        xcb.xcb_flush(connection)
    except (AttributeError, OSError, TypeError) as exc:
        # Cosmetic metadata must never prevent the client from starting.
        logging.getLogger(__name__).warning("Could not hide the X11 hostname suffix: %s", exc)


def prepare_gui_libraries():
    """Use an optional project-local Ubuntu runtime library without root access."""
    if sys.platform != "linux":
        return
    multiarch = sysconfig.get_config_var("MULTIARCH")
    if not multiarch:
        return
    directory = Path(__file__).resolve().parents[1] / ".local/qt/usr/lib" / multiarch
    cursor = directory / "libxcb-cursor.so.0"
    if cursor.is_file():
        # Preload by absolute path because changing LD_LIBRARY_PATH in an already
        # running Python process does not update the dynamic loader's search path.
        ctypes.CDLL(str(cursor), mode=ctypes.RTLD_GLOBAL)
        paths = os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
        if str(directory) not in paths:
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join([str(directory), *filter(None, paths)])


def gui_environment_issues():
    issues = []
    try:
        from PySide6.QtCore import QLibraryInfo
    except ImportError:
        setup = r".\scripts\setup.ps1" if sys.platform == "win32" else "bash scripts/setup.sh"
        return [
            f'缺少 PySide6：请先运行 {setup}，或在项目环境执行 python -m pip install -c requirements.txt -e ".[gui]"。'
        ]
    if sys.platform != "linux":
        return issues
    try:
        prepare_gui_libraries()
    except OSError as exc:
        issues.append(f"项目内 Qt 运行库加载失败：{exc}")
    backend = os.environ.get("QT_QPA_PLATFORM", "").split(":")[0]
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if not backend and not display and not wayland:
        issues.append(
            "当前会话没有 DISPLAY / WAYLAND_DISPLAY，无法显示桌面窗口。\n"
            "  普通 SSH 终端只能直接运行服务端/CLI；请在自己的电脑运行 GUI，"
            "或使用配置好的 X11 转发/远程桌面。\n"
            "  offscreen/Xvfb 仅用于自动测试，不会在你的电脑上弹出窗口。"
        )
    elif backend == "xcb" and not display:
        issues.append("指定了 xcb，但 DISPLAY 未设置；请使用有效的 X11 桌面或转发会话。")
    elif backend.startswith("wayland") and not wayland:
        issues.append("指定了 Wayland，但 WAYLAND_DISPLAY 未设置。")
    use_xcb = backend == "xcb" or (
        not backend and not (wayland and os.environ.get("XDG_SESSION_TYPE") == "wayland")
    )
    if use_xcb:
        plugin = (
            Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/libqxcb.so"
        )
        try:
            result = subprocess.run(["ldd", str(plugin)], capture_output=True, text=True, timeout=5)
            missing = sorted(set(re.findall(r"(\S+)\s+=>\s+not found", result.stdout)))
            if missing:
                issues.append("Qt xcb 缺少系统动态库：" + ", ".join(missing))
                if "libxcb-cursor.so.0" in missing:
                    issues.append(
                        "  Ubuntu/Debian：sudo apt-get install libxcb-cursor0\n"
                        "  没有 sudo 权限：bash scripts/install-linux-gui-libs.sh"
                    )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"无法检查 Qt 动态库：{exc}")
    return issues


def ensure_gui_environment():
    issues = gui_environment_issues()
    if issues:
        raise RuntimeError("图形客户端启动条件不满足：\n" + "\n".join(issues))
    # Check that the native plugin can actually connect to the selected display.
    # Isolate Qt's fatal aborts in a short-lived subprocess with core dumps disabled.
    # A frozen executable is not a Python interpreter: passing '-c' would
    # relaunch the application instead of running the probe.
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "sft"]
    command += ["probe-gui"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("图形显示连接超时，请检查当前桌面会话或 X11 转发。") from exc
    if result.returncode:
        raise RuntimeError(
            "Qt 无法连接当前图形显示，请检查桌面会话、X11 转发及 Qt 插件路径。\n"
            + result.stderr[-2000:]
        )


def probe_gui():
    """Subprocess entry point, also works inside the packaged executable."""
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    prepare_gui_libraries()
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication([])
    widget = QWidget()
    widget.setWindowTitle("Secure Transfer display check")
    widget.show()
    app.processEvents()
    print(json.dumps({"platform": app.platformName(), "visible": widget.isVisible()}))
    widget.close()
    app.processEvents()
    return 0


def port_issue(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        configure_listener(sock)
        try:
            sock.bind((host, port))
        except OSError as exc:
            return f"{host}:{port} 无法监听：{exc}；可能已有服务运行或端口被其他程序占用。"
    return None
