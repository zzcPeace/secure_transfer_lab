# Secure Transfer Lab 实验

Python（3.12 及以上）+ PySide6 桌面客户端 + asyncio 服务端 + TLS 1.3 双向证书 + SQLite。
不需要 Docker、浏览器前端、外部数据库或云服务。

代码仓库：[zzcPeace/secure_transfer_lab](https://github.com/zzcPeace/secure_transfer_lab)。可在仓库页面选择 **Code → Download ZIP** 后解压，或执行：

```bash
git clone https://github.com/zzcPeace/secure_transfer_lab.git
cd secure_transfer_lab
```

项目目录是包含 `README.md`、`sft/` 和 `scripts/` 的文件夹。
首次执行所选方案的 `setup` 脚本会自动创建 `runtime/`，生成本机证书和 alice/bob 用户配置，无须单独下载或手工创建该目录。

## 已实现的功能

- TLS 1.3 双向证书验证；客户端验证 CA 和服务端名称，服务端将客户端证书 SHA-256 指纹映射到用户。
- 一个控制长连接和默认三个传输长连接；TCP `SO_KEEPALIVE` + PING/PONG，文件结束后不关闭连接。
- 自定义有界二进制帧协议；JSON 元数据 + 原始二进制数据块，处理拆包/合包和异常长度。
- 多用户、多文件并发上传和下载；普通用户和 VIP 的文件目录逻辑隔离。
- 上传、下载双向断点续传；进度落盘，支持应用重启、服务端重启后继续。
- 全文件 SHA-256 校验；上传完成后原子发布，下载不覆盖已有不同内容文件。
- 按用户、按上传/下载方向共享限速；多个连接不会各自获得一份完整带宽。
- GUI 文件列表、任务进度、速度、暂停、继续/重试、取消、删除远端文件和持久化任务历史。
- SQLite 参数化查询、路径穿越/符号链接防护、预留空间配额、连接/请求限制、超时、轮转审计日志。
- 本地管理员可以禁用账户（现有连接的下一次请求也会被拒绝）和修改速率。

默认账户只使用证书认证，没有默认弱密码：

| 用户 | 类型 | 上传总限速 | 下载总限速 | 存储配额 |
|---|---|---|---|---|
| alice | normal | 512 KiB/s | 512 KiB/s | 1 GiB |
| bob | vip | 4 MiB/s | 4 MiB/s | 4 GiB |

默认单文件上限 256 MiB、每用户最多 16 个未完成上传、最多 1000 个已完成/未完成文件。服务端配置在 `runtime/server/server.toml`。
同名同内容上传会返回已有文件；同名不同内容会报错，不悄悄覆盖。

普通用户可以有多个，VIP 也可以有多个。`normal` / `vip` 是账户类型，不是账户本身。
每个独立用户使用自己的证书、文件列表、任务历史和带宽额度；多个窗口使用同一份 alice 配置，仍属于同一个用户。

正式报告的 Word/PDF 版本随实验材料单独提供，`docs/` 不上传至 GitHub。部署和操作步骤见下文。

## 运行

### 部署方案一：Linux服务器端运行

**文件选择对话框浏览的是服务器上的文件，不是 Windows 本地磁盘。**

#### 1. MobaXterm 登录与 X11 转发

在 Windows 启动 MobaXterm 的 X Server，新建目标 Linux 主机的 SSH 会话并勾选 **X11-Forwarding**。如果需要经过跳板机，先连接跳板机，再在其终端执行以下命令，将用户名和主机地址替换为实际值：

```bash
ssh -X 用户名@目标主机地址
```

`-X` 开启 X11 转发；自定义 SSH 端口使用 `-p` 指定。程序和文件位于目标 Linux 主机，MobaXterm 负责在 Windows 显示窗口。

之后，在**同一个终端**执行：

```bash
hostname
echo "$DISPLAY"
xdpyinfo >/dev/null && echo 'X11 连接成功'
```

检查主机名，`DISPLAY` 应非空，并输出“X11 连接成功”。检查通过后再启动 GUI；

#### 2. 准备项目环境

首次运行时，在终端执行：

```bash
cd /path/to/secure_transfer_lab
bash scripts/setup.sh
```

将 `/path/to/secure_transfer_lab` 替换为项目实际路径。脚本安装 `requirements.txt` 中的依赖，并初始化证书和用户配置。

#### 3. 检查并启动服务端

打开另外一个终端，执行并保持运行：

```bash
cd /path/to/secure_transfer_lab
bash scripts/start-server.sh
```

#### 4. 终端启动客户端

在刚才有 X11 转发连接的终端执行：

```bash
cd /path/to/secure_transfer_lab
bash scripts/start-client.sh
```

默认启动普通用户 alice。

#### 功能展示

完成上方部署后，按以下顺序演示。

##### 1. 准备演示文件

在服务器的新终端中进入项目目录，创建接收下载文件的目录：

```bash
mkdir -p runtime/downloads
```

在项目目录执行以下命令生成基础演示文件；已有文件时无需重复执行：

```bash
.venv/bin/python scripts/make-demo-files.py
```

文件位于 `runtime/demo-files`：

| 文件 | 大小 | 用途 |
|---|---|---|
| `small.bin` | 128 KiB | 第一次上传和下载 |
| `medium.bin` | 2 MiB | 较大文件传输 |
| `large.bin` | 8 MiB | 观察传输进度，演示暂停和续传 |
| `说明.txt` | UTF-8 文本 | 中文文件名和文本内容 |
| `empty.txt` | 0 字节 | 空文件传输 |

生成脚本不会覆盖已有文件。

##### 2. 连接服务器

客户端窗口打开后不会自动连接。顶部“客户端配置”默认指向 `runtime/clients/alice/client.json`，保持默认并点击 **“连接服务器”**。

成功后，身份栏应显示：

```text
已连接 · alice / NORMAL · TLS 1.3 双向认证 · 上传 512.0 KiB/s · 下载 512.0 KiB/s
```

底部日志框显示“证书验证通过，控制连接及传输连接已建立。”。
应用使用客户端证书认证，不需要输入账号密码。

点击“断开”后，“我的远端文件”和“传输任务”同时清空显示；重新连接成功后恢复当前用户的文件列表和任务历史。
未完成任务的进度会保存，重新连接后可选中任务点击“继续 / 重试”。

##### 3. 公钥身份认证（加分项）

本项目通过 **TLS 1.3 双向证书认证**实现这一项：客户端使用自己的私钥对握手内容签名，服务端使用证书中的公钥验证，再将证书 SHA-256 指纹映射到已登记用户。私钥保留在本机，身份不由界面输入的用户名决定。

1. 在 alice 窗口点击“连接服务器”，确认身份栏显示 `alice / NORMAL` 和“TLS 1.3 双向认证”，日志显示“证书验证通过”。
2. 保持服务端运行，另开终端进入项目目录，执行下面的认证对照演示：

```bash
.venv/bin/python scripts/demo-auth.py
```

脚本使用默认 alice 配置，仅发送身份查询，依次检查：

| 步骤 | 预期结果 |
|---|---|
| 使用有效客户端证书和匹配私钥 | 认证成功，服务端返回 `alice / NORMAL` |
| 保持服务端 CA 和名称校验，不携带客户端证书 | TLS 告警或连接关闭，无法取得已认证身份 |
| 再次使用原有效凭据 | 仍能认证为 alice，确认服务端正常运行 |

最终应输出“**公钥身份认证演示通过**”。操作系统和 TLS 实现不同，拒绝可能显示证书告警、连接重置或连接关闭；服务未启动、超时等不能算认证拒绝的证据。脚本不会修改现有证书、账户、配置或传输文件，也不会输出私钥；服务端正常记录连接审计日志。

3. 按后面的 bob 启动步骤打开 VIP 客户端并连接，确认显示 `bob / VIP`。两个用户共用同一服务端，但各自证书对应不同身份。
4. 展示客户端身份栏、证书验证日志和脚本完整输出，作为本加分项的演示结果。`client.cert.pem` 保存公开证书，`client.key.pem` 保存私钥；仅有公钥证书不能完成私钥签名认证。演示无需移动或删除现有私钥。

##### 4. 上传文件

1. 点击 **“＋ 上传文件…”**。
2. 在文件选择对话框中进入项目下的 `runtime/demo-files` 目录，选择 `small.bin`。
3. 观察下方“传输任务”的进度、速度和状态。

完成后应看到：

- 任务进度为 100%，状态为“已完成”，详情为“SHA-256 校验通过”。
- 上方“我的远端文件”列表出现 `small.bin`，大小为 128 KiB，并显示 SHA-256 和文件 ID。

##### 5. 下载文件并验证完整性

1. 在**上方远端文件列表**选中 `small.bin`。
2. 点击 **“↓ 下载选中文件…”**。
3. 选择项目下的 `runtime/downloads` 作为下载目录。
4. 等待下方下载任务完成，确认状态为“已完成”，详情为“SHA-256 校验通过”。

下载后的文件位于 `runtime/downloads/small.bin`。至此完成一次通过 TLS 的上传、下载和完整性校验。
上传源目录和下载目录分别选择 `runtime/demo-files`、`runtime/downloads`，便于核对实际生成的下载文件。

##### 6. 演示上传和下载断点续传

先使用普通用户 alice 演示，8 MiB 的 `large.bin` 能留出观察和操作时间。

**上传续传：**

1. 点击“＋ 上传文件…”，选择 `runtime/demo-files/large.bin`。
2. 进度尚未到 100% 时，在**下方传输任务列表**选中该上传任务，点击 **“暂停选中”**。
3. 等待状态变为“已暂停”，确认“已完成 / 总量”保留已传输的数据量。
4. 点击 **“继续 / 重试”**，观察任务从保存的进度继续，最终显示“已完成”和“SHA-256 校验通过”。

**下载续传：**

1. 上传完成后，在上方远端文件列表选中 `large.bin`，点击“↓ 下载选中文件…”，选择 `runtime/downloads`。
2. 下载过程中，在下方任务列表选中对应任务，点击“暂停选中”，等待“已暂停”。
3. 点击“继续 / 重试”，观察任务从已保存的进度继续，完成后确认“SHA-256 校验通过”。

演示续传使用“暂停选中”，保留当前任务及其进度。
重复演示时，如果同名同内容文件已上传，程序会直接返回已有文件；可改用一个新文件名的副本进行上传测试。
如果下载目录已有相同的完整文件，程序会校验后直接完成；要重新观察下载进度，选择一个新的空目录。

##### 7. 多个普通用户同时使用

例如让 alice 和新建的 charlie 都作为普通用户，并保留 bob 用于 VIP 对比。
在项目目录执行一次，创建 charlie：

```bash
.venv/bin/python -m sft add-user charlie --role normal
```

该命令为 charlie 签发独立证书并登记到服务端数据库，生成 `runtime/clients/charlie/client.json`。
新用户沿用 alice 配置中的服务器地址和端口。
已有账号或客户端目录不会被覆盖；创建成功后无需重启服务端。

保留 alice 窗口，在另一个已启用 X11 转发的终端中进入项目目录，启动 charlie：

```bash
bash scripts/start-client.sh runtime/clients/charlie/client.json
```

在新窗口点击“连接服务器”，身份栏应显示 `charlie / NORMAL`。
分别在 alice 和 charlie 窗口上传文件，两人只看到各自的文件；也可以上传同名但内容不同的文件。

| 用户 | 类型 | 上传限速 | 下载限速 | 存储配额 |
|---|---|---|---|---|
| alice | normal | 512 KiB/s | 512 KiB/s | 1 GiB |
| charlie | normal | 512 KiB/s | 512 KiB/s | 1 GiB |

两人的限额分别计算；同一用户的多个并发任务共享自己的限额。例如 alice 同时上传两个文件，两项任务合计受 512 KiB/s 限制，
不会占用 charlie 的应用级限额。实际速度还受磁盘和网络性能影响。

继续创建普通用户时，将命令里的 `charlie` 替换为新用户名；创建 VIP 时使用 `--role vip`，默认限速为每个方向 4 MiB/s、配额 4 GiB。
这是本地管理员操作，需要 runtime 中的 CA 私钥；客户端配置只分发对应用户的目录。
可用 `--dir` 指定其他 runtime，用 `--template` 指定其他已有客户端配置作为连接参数模板。

##### 8. 多文件上传、下载与混合并发

下面的“并发”要求任务在同一段时间内推进。依次完成两个文件、或仅打开多个空窗口，不足以展示并发。
每个客户端默认有三个传输连接，上传和下载合计最多三个文件同时传输，更多任务排队。

先在项目目录生成一套新文件，避免重复文件被直接复用：

```bash
.venv/bin/python scripts/make-demo-files.py --concurrency
```

脚本会输出一个带时间戳的独立目录，里面五个 `.bin` 文件各为 16 MiB，文件名也带唯一后缀。
记录这个目录；下面用前缀指代文件，实际选择带后缀的文件。

1. **并发上传**：alice 点击“＋ 上传文件…”，按 Ctrl 同时选择 `upload-a-*`、`upload-b-*`，观察两行都显示“传输中”、已完成字节持续增加。
2. **并发下载**：上述文件上传完成后，在远端列表按 Ctrl 选中这两行，点击“↓ 下载选中文件…”，选择一个新的空目录。观察两项下载同时推进。
3. **上传＋下载混合**：另选尚未上传的 `seed-download-*` 开始上传；在它尚未完成时，选中远端一个已完成文件下载到另一空目录。观察上传、下载两行同时增长。
4. **三项混合**：需要一次展示“两上传＋一下载”时，再生成一套新文件；上传其中的 `upload-a-*`、`upload-b-*`，同时下载先前完成的文件到新的空目录。

普通用户同方向总速率为 512 KiB/s；两个上传合计受此限额限制。上传和下载额度分别计算。
每项进度不必每一帧同步变化，判断依据是同一时间段内都在推进，最后均通过 SHA-256 校验。

##### 9. 多用户并发与普通/VIP 对比

按第 6 节创建 charlie，并排摆放 alice、charlie 两个已连接窗口。
alice 开始上传两个大文件后，立即让 charlie 上传 `user-charlie-*`，保持两个窗口同时显示正在增长的进度。
两人应显示不同用户名、相同 NORMAL 类型，各自拥有 512 KiB/s 的上传额度和独立文件列表。
在多用户并发期间也可让其中一人下载，观察服务端持续响应其他用户。

VIP 对比时，保留 alice 窗口，在另一个已启用 X11 转发的终端中进入项目目录，启动 bob：

```bash
bash scripts/start-client.sh runtime/clients/bob/client.json
```

在新窗口点击“连接服务器”，确认身份为 `bob / VIP`。让 alice 和 bob 各上传同一个新生成的 `vip-compare-*` 文件，各只运行这一项上传，比较传输阶段的耗时和速度。
两人文件命名空间独立，因此同名文件可分别上传。默认限额是 512 KiB/s 与 4 MiB/s，理论额度比为 8，
实际耗时包含磁盘、校验和调度开销，不要求精确达到 8 倍。
两用户完成后各自多选多个文件再测试，可以说明“限额按用户共享”，不会因增加连接数获得成倍带宽。

需要更明显的速率差异时，生成独立 **128 MiB** 文件：

```bash
.venv/bin/python scripts/make-demo-files.py --bandwidth
```

按默认限额计算，128 MiB 的纯传输时间约为普通用户 256 秒、VIP 32 秒；实际还包含校验、磁盘和网络开销。
可通过 `--size-mib 64` 改为 64 MiB，最大允许 256 MiB（当前服务端的单文件上限）。
并发演示也可以用 `--concurrency --size-mib 64` 生成每个 64 MiB 的文件集。

### 部署方案二：在 Windows 运行完整实验

服务端和客户端都在 Windows 本机运行，通过 `127.0.0.1:18765` 通信。
安装 [Python 3.14（64 位）](https://www.python.org/downloads/windows/) 和 [Microsoft Visual C++ x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)。安装 Python 时勾选“Add python.exe to PATH”。

从仓库下载源码并解压，重新打开 PowerShell，在项目目录执行：

```powershell
python --version
python -m venv .venv
.\scripts\setup.ps1
```

`python -m venv .venv` 使用系统 Python 在项目内创建 `.venv` 虚拟环境，单独存放本实验依赖。
`setup.ps1` 安装依赖、首次生成证书和用户配置，并检查 Qt 窗口。这两步只需在首次安装时执行。

打开两个 PowerShell 窗口，分别进入项目目录并启动：

```powershell
# 窗口 1：服务端，保持运行
.\scripts\start-server.ps1
```

```powershell
# 窗口 2：普通用户 alice
.\scripts\start-client.ps1
```

启动脚本使用 `.venv\Scripts\python.exe`，无需手动激活环境或编译源码。
界面操作参照上方“功能展示”，文件选择和下载目录使用 Windows 本机路径。

需要第二个普通用户时，在项目目录创建一次，再在新窗口启动：

```powershell
.\.venv\Scripts\python.exe -m sft add-user charlie --role normal
.\scripts\start-client.ps1 -Profile runtime/clients/charlie/client.json
```

VIP 客户端在另一个窗口启动：

```powershell
.\scripts\start-client.ps1 -Profile runtime/clients/bob/client.json
```

生成基础、并发或带宽比较文件：

```powershell
.\.venv\Scripts\python.exe scripts/make-demo-files.py
.\.venv\Scripts\python.exe scripts/make-demo-files.py --concurrency
.\.venv\Scripts\python.exe scripts/make-demo-files.py --bandwidth
```

基础文件已存在时无需重复生成。后两条命令每次生成新的文件目录。
若 PowerShell 阻止脚本，在当前窗口执行 `Set-ExecutionPolicy -Scope Process Bypass` 后重试。
若 Qt 窗口加载失败，先确认 Python 依赖和 x64 C++ 运行库安装成功，可执行 `.\.venv\Scripts\python.exe -m sft probe-gui` 检查。

#### 功能展示：公钥身份认证（加分项）

Windows 本机同样单独演示此项。在 alice 窗口连接后，确认身份栏的 `alice / NORMAL`、“TLS 1.3 双向认证”以及底部“证书验证通过”日志。保持服务端运行，在另一个 PowerShell 窗口进入项目目录，执行：

```powershell
.\.venv\Scripts\python.exe scripts/demo-auth.py
```

预期依次看到：有效证书和对应私钥认证成功；不携带客户端证书时连接被拒绝；恢复有效凭据后再次认证成功。最终输出“公钥身份认证演示通过”。如果服务未启动或网络超时，先处理启动问题再重试，不能将其当作认证拒绝。

脚本仅查询身份，不修改证书、账户、配置或传输文件，也不会输出私钥。无需安装 OpenSSL 命令行工具。再连接已打开的 bob 窗口，确认 `bob / VIP`，展示不同证书映射到不同用户。

原理是客户端通过私钥对 TLS 1.3 握手内容签名，服务端使用证书中的公钥验证，并按已登记的证书指纹识别账户；仅有公开证书不能冒用身份。验收时展示 GUI 身份、验证日志和认证脚本完整输出。

#### Windows EXE 打包与运行

在已经完成环境安装的 Windows 项目目录执行：

```powershell
.\scripts\build-windows.ps1
```

输出 `dist/windows-时间戳/SecureTransfer-Windows-x64.zip`。解压完整程序目录后：

1. 双击 `01-initialize.cmd`，首次生成实验配置。
2. 双击 `02-server.cmd`，保持服务端窗口运行。
3. 双击 `03-alice.cmd`，在窗口中点击“连接服务器”。
4. 需要 VIP 时双击 `04-bob.cmd`。

接收方无需安装 Python，但需保留 `sftlab.exe` 旁的 `_internal` 目录及所需的 Microsoft Visual C++ x64 运行库。交付前在新目录解压后检查文件传输、续传和并发功能。

### 按需安装依赖

需要单独安装某项功能时，先激活项目的 `.venv`，在项目目录执行对应命令。`requirements.txt` 统一约束依赖版本。

| 用途 | 安装命令 |
|---|---|
| 仅服务端 | `python -m pip install -c requirements.txt -e .` |
| 服务端 + GUI（setup 默认） | `python -m pip install -c requirements.txt -e ".[gui]"` |
| Windows EXE 打包 | `python -m pip install -c requirements.txt -e ".[gui,build]"` |

应用依赖沿用已验证的固定版本；构建工具按声明的版本范围安装，并在 Windows 包中记录实际版本。
Qt 仅使用 PySide6-Essentials 中的 Core/Gui/Widgets。Linux 安装脚本优先使用 `uv pip install`，不会生成额外的锁文件。

## 命令行操作

```bash
.venv/bin/python scripts/make-demo-files.py
.venv/bin/python -m sft upload runtime/demo-files/small.bin runtime/demo-files/medium.bin
.venv/bin/python -m sft list
.venv/bin/python -m sft download <文件ID> runtime/downloads/medium.bin
.venv/bin/python -m sft users
.venv/bin/python -m sft set-user alice --upload-kib 256 --download-kib 256
.venv/bin/python -m sft set-user alice --disable
.venv/bin/python -m sft set-user alice --enable
```

客户端命令均支持 `--profile runtime/clients/bob/client.json`。上传中断后重复上传命令，下载中断后重复相同文件 ID 和目标路径的下载命令，即可续传。
下载会产生 `.sft-part`、`.sft-meta.json`；完成后自动清理。隐藏的 `.文件名.sft-lock` 是可复用的进程锁文件，可在所有相关客户端退出后删除。
GUI 取消上传会清理服务端片段并释放配额；取消下载保留本地片段，重新添加相同下载可继续。

## 两台电脑 / 不使用 Docker 的部署

在新目录中初始化证书时，指定客户端将访问的实际域名或 IP：

```bash
.venv/bin/python -m sft init-demo --dir runtime-lan --host 192.168.1.20 --bind 0.0.0.0
.venv/bin/python -m sft serve --config runtime-lan/server/server.toml
```

将 `runtime-lan/clients/alice/` 单独复制给 alice 客户端，将 bob 的目录单独给 bob；运行客户端时选择对应 `client.json`。
不要给客户端复制 `authority/` 或其他用户的私钥。仅修改连接地址而不重新签发包含该地址 SAN 的证书，会被正确拒绝。

依赖隔离使用 venv；用户凭据按客户端目录分别保存，只分发对应用户的配置和证书。

## 目录说明

```text
sft/                    核心协议、证书、存储、服务端、客户端和 GUI
scripts/                环境准备、启动、认证演示、演示文件生成及 Windows 打包
scripts/windows-package/ Windows 可执行包的双击启动入口
docs/                  本地报告和补充说明，单独交付，不上传 GitHub
requirements.txt        依赖版本清单
pyproject.toml          项目配置与命令入口
runtime/               本机证书、用户配置、任务状态、数据库及演示文件
dist/                  可复制到 Windows 的源码 ZIP
```

`.venv/` 是当前 Python 运行环境，`.local/qt/` 是本机 X11 所需的补充动态库。
`runtime/` 包含 CA、服务端和用户私钥，以及数据库、传输文件和任务记录，因此不上传 GitHub，也不放入源码包。每台电脑首次安装时独立生成，后续启动会继续使用已有数据。

## 报告与源码导出

报告可用 Word 编辑，PDF 用于阅读和提交。第 10 节给出部署、功能验收和程序交付的完整步骤。

在项目目录导出可复制到 Windows 的源码包：

```bash
.venv/bin/python scripts/export-source.py
```

输出 `dist/SecureTransfer-source.zip` 及 SHA-256 校验文件，包含核心源码、运行脚本；本地存在 `docs/` 时也一并收录，便于单独交付。
大文件在目标电脑运行 `scripts/make-demo-files.py --bandwidth` 生成。Windows EXE 按上方“Windows EXE 打包与运行”构建。
