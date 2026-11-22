import argparse
import asyncio
import json
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

from .client import TransferClient
from .config import ClientConfig, ServerConfig
from .pki import add_runtime_user, initialize_demo
from .server import TransferServer
from .storage import Storage


def parser():
    result = argparse.ArgumentParser(description="安全文件传输实验 1B / Secure Transfer Lab")
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-demo", help="生成本地实验 CA、服务端和普通/VIP 用户")
    init.add_argument("--dir", default="runtime")
    init.add_argument("--host", default="127.0.0.1", help="客户端访问的域名/IP，也写入证书 SAN")
    init.add_argument("--bind", default="127.0.0.1", help="服务端监听地址；局域网演示可设 0.0.0.0")
    init.add_argument("--port", type=int, default=18765)
    add_user = commands.add_parser("add-user", help="本地管理员：签发证书并添加独立用户")
    add_user.add_argument("username")
    add_user.add_argument("--dir", default="runtime", help="包含 authority/server/clients 的目录")
    add_user.add_argument("--role", choices=("normal", "vip"), default="normal")
    add_user.add_argument(
        "--template", help="参考客户端配置，默认使用 <dir>/clients/alice/client.json"
    )
    server = commands.add_parser("serve", help="运行服务端")
    server.add_argument("--config", default="runtime/server/server.toml")
    server.add_argument("--verbose", action="store_true")
    doctor = commands.add_parser("doctor", help="检查端口、Qt 动态库和图形显示环境")
    doctor.add_argument("--config", default="runtime/server/server.toml")
    commands.add_parser("probe-gui", help="在独立进程检查原生 Qt 窗口（用于安装/打包验收）")
    for name, help_text in (
        ("gui", "桌面客户端"),
        ("list", "列出当前用户的文件"),
        ("upload", "上传一个或多个文件"),
        ("download", "下载或继续下载文件"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--profile", default="runtime/clients/alice/client.json")
        if name == "upload":
            sub.add_argument("files", nargs="+")
        if name == "download":
            sub.add_argument("file_id")
            sub.add_argument("destination")
    users = commands.add_parser("users", help="本地管理员：列出用户")
    users.add_argument("--config", default="runtime/server/server.toml")
    update = commands.add_parser("set-user", help="本地管理员：禁用账户/修改带宽")
    update.add_argument("username")
    update.add_argument("--config", default="runtime/server/server.toml")
    group = update.add_mutually_exclusive_group()
    group.add_argument("--disable", action="store_true")
    group.add_argument("--enable", action="store_true")
    update.add_argument("--upload-kib", type=int)
    update.add_argument("--download-kib", type=int)
    return result


async def serve(args):
    config = ServerConfig.load(args.config)
    config.root.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            config.root / "audit.log", maxBytes=5 * 1024**2, backupCount=3, encoding="utf-8"
        ),
    ]
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    server = TransferServer(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await server.start()
        await stop.wait()
    finally:
        await server.stop()


def progress_printer(label):
    last = 0.0

    def report(event):
        nonlocal last
        now = time.monotonic()
        if now - last > 0.3 or event.get("status") in ("completed", "reconnecting", "verifying"):
            print(
                f"[{label}] {event.get('status')} {event.get('done', 0)}/{event.get('total', 0)}",
                file=sys.stderr,
                flush=True,
            )
            last = now

    return report


async def client_command(args):
    config = ClientConfig.load(args.profile)
    async with TransferClient(config) as client:
        if args.command == "list":
            result = await client.list_files()
        elif args.command == "upload":
            results = await asyncio.gather(
                *(
                    client.upload(path, progress=progress_printer(Path(path).name))
                    for path in args.files
                ),
                return_exceptions=True,
            )
            result = [str(r) if isinstance(r, BaseException) else r for r in results]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(any(isinstance(r, BaseException) for r in results))
        else:
            result = await client.download(
                args.file_id,
                args.destination,
                progress=progress_printer(Path(args.destination).name),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init-demo":
            root = initialize_demo(args.dir, host=args.host, bind=args.bind, port=args.port)
            print(f"初始化完成：{root}\n普通用户 alice：512 KiB/s；VIP 用户 bob：4 MiB/s。")
            print("CA 私钥位于 authority/；运行服务端不需要该目录。")
        elif args.command == "add-user":
            profile = add_runtime_user(
                args.dir, args.username, role=args.role, template=args.template
            )
            print(f"已创建独立用户 {args.username}（{args.role}）：{profile}")
            print("新用户已登记，可使用此配置连接现有服务端。")
        elif args.command == "serve":
            asyncio.run(serve(args))
        elif args.command == "probe-gui":
            from .diagnostics import probe_gui

            return probe_gui()
        elif args.command == "doctor":
            from .diagnostics import gui_environment_issues, port_issue

            config = ServerConfig.load(args.config)
            port_problem = port_issue(config.host, config.port)
            print("服务端：" + (port_problem or f"{config.host}:{config.port} 端口可用"))
            issues = gui_environment_issues()
            print("客户端：" + ("\n".join(issues) if issues else "Qt 依赖和显示环境初步检查通过"))
            return int(bool(port_problem or issues))
        elif args.command == "gui":
            from .gui import main as gui_main

            return gui_main(["--profile", args.profile])
        elif args.command in ("users", "set-user"):
            store = Storage(ServerConfig.load(args.config).root)
            try:
                if args.command == "set-user":
                    store.update_user(
                        args.username,
                        disabled=True if args.disable else False if args.enable else None,
                        upload_bps=args.upload_kib * 1024 if args.upload_kib is not None else None,
                        download_bps=args.download_kib * 1024
                        if args.download_kib is not None
                        else None,
                    )
                print(json.dumps(store.users(), ensure_ascii=False, indent=2))
            finally:
                store.close()
        else:
            return asyncio.run(client_command(args))
        return 0
    except KeyboardInterrupt:
        print("已退出；未完成传输可重新运行以继续。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
