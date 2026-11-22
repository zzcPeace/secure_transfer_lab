"""Demonstrate client-certificate authentication against a running lab server."""

import argparse
import asyncio
import ssl
import sys
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from sft.config import ClientConfig
from sft.protocol import ProtocolError, close_writer, read_frame, write_frame


async def hello(config, context):
    writer = None
    try:
        async with asyncio.timeout(min(config.timeout, 10)):
            reader, writer = await asyncio.open_connection(
                config.host,
                config.port,
                ssl=context,
                server_hostname=config.server_name,
                ssl_handshake_timeout=5,
                ssl_shutdown_timeout=2,
            )
            tls_version = writer.get_extra_info("ssl_object").version()
            request_id = uuid.uuid4().hex
            await write_frame(writer, {"id": request_id, "op": "HELLO"})
            frame = await read_frame(reader)
            if (
                frame.meta.get("id") != request_id
                or frame.meta.get("op") != "OK"
                or not frame.meta.get("user")
                or not frame.meta.get("role")
                or frame.payload
                or tls_version != "TLSv1.3"
            ):
                raise RuntimeError("未收到有效的 TLS 1.3 身份应答，无法完成演示。")
            return frame.meta
    finally:
        await close_writer(writer)


async def demonstrate(config):
    cert = x509.load_pem_x509_certificate(config.cert.read_bytes())
    print(f"客户端证书主体：{cert.subject.rfc4514_string()}", flush=True)
    print(f"证书 SHA-256 指纹：{cert.fingerprint(hashes.SHA256()).hex()}", flush=True)
    print("仅显示公开证书信息；私钥不输出、不通过应用报文发送。", flush=True)

    print("[1/3] 使用配置中的客户端证书和对应私钥连接……", flush=True)
    identity = await hello(config, config.tls_context())
    print(
        f"PASS：TLS 1.3 双向认证成功，服务端返回 "
        f"{identity['user']} / {identity['role'].upper()}。",
        flush=True,
    )

    print("[2/3] 保持服务端 CA 和名称校验，不携带客户端证书连接……", flush=True)
    anonymous = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.ca)
    anonymous.minimum_version = ssl.TLSVersion.TLSv1_3
    # No load_cert_chain: this attempt deliberately sends no client certificate.
    try:
        await hello(config, anonymous)
    except (
        asyncio.IncompleteReadError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        ssl.SSLEOFError,
    ) as exc:
        reason = f"{type(exc).__name__}：连接关闭，未取得已认证身份"
    except ssl.SSLError as exc:
        if exc.reason not in {"TLSV13_ALERT_CERTIFICATE_REQUIRED", "SSLV3_ALERT_HANDSHAKE_FAILURE"}:
            raise
        reason = f"TLS 拒绝：{exc.reason}"
    else:
        raise RuntimeError("FAIL：未携带客户端证书也取得了身份，请检查服务端认证设置。")
    print(f"观察到拒绝：{reason}。正在复核正常凭据仍可用……", flush=True)

    print("[3/3] 再次使用原有有效证书连接……", flush=True)
    restored = await hello(config, config.tls_context())
    if (restored["user"], restored["role"]) != (identity["user"], identity["role"]):
        raise RuntimeError("前后两次认证身份不同，请检查服务端账户配置。")
    print(f"PASS：恢复正常凭据后仍认证为 {restored['user']}。", flush=True)
    print("PASS：无客户端证书的尝试被拒绝，前后有效凭据均可连接。", flush=True)
    print("公钥身份认证演示通过；未上传、删除文件或修改证书、账户及配置。", flush=True)


def main():
    parser = argparse.ArgumentParser(description="演示公钥身份认证：有效证书 / 无证书 / 恢复连接")
    parser.add_argument("--profile", default="runtime/clients/alice/client.json")
    args = parser.parse_args()
    try:
        asyncio.run(demonstrate(ClientConfig.load(args.profile)))
    except (OSError, TimeoutError, ValueError, RuntimeError, ProtocolError, asyncio.IncompleteReadError) as exc:
        print(f"演示未通过：{type(exc).__name__}：{exc}", file=sys.stderr)
        print("确认服务端正在运行、配置和证书有效；普通网络或超时错误不能作为认证拒绝的证据。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
