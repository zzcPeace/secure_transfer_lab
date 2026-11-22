"""Create only new, synthetic demonstration files; never overwrite existing files."""

import argparse
import datetime as dt
import os
import uuid
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dir")
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--concurrency", action="store_true", help="生成并发文件集，默认每个 16 MiB")
mode.add_argument("--bandwidth", action="store_true", help="生成 VIP 对比单文件，默认 128 MiB")
parser.add_argument("--size-mib", type=int, help="并发/限速测试单文件大小，1～256 MiB")
args = parser.parse_args()
if args.size_mib is not None and (
    not 1 <= args.size_mib <= 256 or not (args.concurrency or args.bandwidth)
):
    parser.error("--size-mib requires --concurrency or --bandwidth and must be between 1 and 256")
suffix = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
category = "concurrency" if args.concurrency else "bandwidth"
root = Path(
    args.dir
    or (
        f"runtime/demo-{category}-{suffix}"
        if args.concurrency or args.bandwidth
        else "runtime/demo-files"
    )
)
root.mkdir(parents=True, exist_ok=True)
files = (
    ("small.bin", 128 * 1024),
    ("medium.bin", 2 * 1024**2),
    ("large.bin", 8 * 1024**2),
)
if args.concurrency:
    files = tuple(
        (f"{name}-{suffix}.bin", (args.size_mib or 16) * 1024**2)
        for name in ("upload-a", "upload-b", "seed-download", "user-charlie", "vip-compare")
    )
if args.bandwidth:
    files = ((f"vip-compare-{suffix}.bin", (args.size_mib or 128) * 1024**2),)
for name, size in files:
    path = root / name
    with path.open("xb") as stream:
        for _ in range(size // (64 * 1024)):
            stream.write(os.urandom(64 * 1024))
with (root / "说明.txt").open("x", encoding="utf-8") as stream:
    stream.write("软件与系统安全实验 1B：中文文件名、UTF-8 内容与完整性校验。\n")
with (root / "empty.txt").open("x"):
    pass
print(f"Created synthetic demo files in {root.absolute()}")
for name, size in files:
    print(f"  {name}: {size / 1024**2:g} MiB")
