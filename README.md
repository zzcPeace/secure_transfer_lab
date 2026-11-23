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

正式报告以 Word（DOCX）格式单独提供，`docs/` 不上传至 GitHub。两套部署方案、功能展示和 Windows 打包说明统一见本 README。

## 运行

以下两套方案任选其一，各自包含完整的部署与功能展示步骤。Linux 方案通过 MobaXterm 显示远程窗口；Windows 方案在本机运行服务端和客户端。

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

##### 6. 上传和下载断点续传（加分项）

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

##### 8. GUI 多文件上传、下载与混合并发（加分项）

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

##### 9. 多用户并发与普通/VIP 对比（加分项：按身份限速）

按本方案“功能展示”第 7 步创建 charlie，并排摆放 alice、charlie 两个已连接窗口。
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

服务端和客户端都在同一台 Windows 10/11（64 位）电脑运行，通过 `127.0.0.1:18765` 通信。文件选择和下载目录均使用 Windows 本机路径。

#### 1. 安装 Python 并打开项目目录

安装 [Python 3.14（64 位）](https://www.python.org/downloads/windows/) 和 [Microsoft Visual C++ x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)。安装 Python 时勾选 **“Add python.exe to PATH”**。

从 [GitHub 仓库](https://github.com/zzcPeace/secure_transfer_lab) 选择 **Code → Download ZIP**，解压后找到包含 `README.md`、`sft/` 和 `scripts/` 的项目目录。

**项目目录尽量使用短路径**，例如 `C:\sftlab` 或 `D:\secure_transfer_lab`，避免嵌套多层长文件夹名。PySide6 的内部目录较深，完整安装路径过长可能触发 Windows 路径长度限制，导致依赖安装失败，并出现 `No such file or directory` 等错误。

重新打开 **PowerShell**，进入项目目录并检查 Python：

```powershell
cd "D:\secure_transfer_lab"
python --version
```

下文以 `D:\secure_transfer_lab` 为例，请替换为实际解压路径。`.ps1` 脚本均在 PowerShell 中通过命令执行。

#### 2. 准备项目环境

首次运行时，在项目目录执行：

```powershell
python -m venv .venv
.\scripts\setup.ps1
```

`python -m venv .venv` 使用系统 Python，在项目内创建名为 `.venv` 的虚拟环境，用于单独存放本实验依赖。
`setup.ps1` 按 `requirements.txt` 安装依赖，首次生成 `runtime/` 中的证书和 alice/bob 配置，并检查 Qt 窗口；已有 `runtime/` 会保留。

看到 `Ready. Run scripts/start-server.ps1 and scripts/start-client.ps1 in separate terminals.` 后继续。若提示禁止运行脚本，在当前 PowerShell 执行 `Set-ExecutionPolicy -Scope Process Bypass`，再重试原命令。

#### 3. 启动服务端

在第一个 PowerShell 窗口执行，并保持该窗口运行：

```powershell
cd "D:\secure_transfer_lab"
.\scripts\start-server.ps1
```

出现 `Listening on 127.0.0.1:18765` 表示服务端已开始监听。

#### 4. 启动客户端

另开一个 PowerShell 窗口，执行：

```powershell
cd "D:\secure_transfer_lab"
.\scripts\start-client.ps1
```

默认打开普通用户 alice 的客户端。按下方“功能展示”连接并测试。

以后使用时只需重复第 3、4 步。启动脚本直接使用 `.venv\Scripts\python.exe`，无需手动激活环境或编译源码。结束时关闭客户端，在服务端窗口按 **Ctrl+C** 停止服务；保留 `.venv/` 和 `runtime/` 供下次使用。

#### 功能展示

保持服务端和 alice 客户端运行，另开一个 PowerShell 窗口并进入项目目录，执行下面的文件生成、用户创建等辅助命令。

##### 1. 准备演示文件

```powershell
.\.venv\Scripts\python.exe scripts/make-demo-files.py
New-Item -ItemType Directory -Force runtime\downloads
```

基础文件生成在 `runtime\demo-files`，下载文件保存到 `runtime\downloads`。下载目录应位于有写权限的 NTFS 磁盘。

| 文件 | 大小 | 用途 |
|---|---|---|
| `small.bin` | 128 KiB | 第一次上传和下载 |
| `medium.bin` | 2 MiB | 较大文件传输 |
| `large.bin` | 8 MiB | 观察进度，演示暂停和续传 |
| `说明.txt` | UTF-8 文本 | 中文文件名和文本内容 |
| `empty.txt` | 0 字节 | 空文件传输 |

脚本不会覆盖已有文件；基础文件已存在时直接使用。

##### 2. 连接服务器

客户端顶部“客户端配置”默认指向 `runtime/clients/alice/client.json`。点击 **“连接服务器”**，成功后身份栏显示：

```text
已连接 · alice / NORMAL · TLS 1.3 双向认证 · 上传 512.0 KiB/s · 下载 512.0 KiB/s
```

底部日志显示“证书验证通过，控制连接及传输连接已建立。”。应用使用证书认证，无须输入账号密码。

点击“断开”后，“我的远端文件”和“传输任务”同时清空显示；重新连接后恢复当前用户的文件列表与任务历史。未完成任务可选中后点击“继续 / 重试”。

##### 3. 公钥身份认证（加分项）

确认 alice 窗口显示上述身份和证书验证日志，保持服务端运行，在辅助 PowerShell 窗口执行：

```powershell
.\.venv\Scripts\python.exe scripts/demo-auth.py
```

预期依次看到：

1. 使用有效客户端证书和匹配私钥，认证成功并返回 `alice / NORMAL`。
2. 不携带客户端证书，连接被拒绝，无法取得已认证身份。
3. 恢复有效凭据，再次认证成功，最终输出 **“公钥身份认证演示通过”**。

客户端使用私钥对 TLS 握手内容签名，服务端使用证书中的公钥验证，并按登记的证书指纹识别用户。仅有公开证书不能冒用身份。脚本仅查询身份，无须安装 OpenSSL 命令行工具；服务未启动、网络超时不能当作认证拒绝的证据。

完成后面的 bob 演示时，还可对照两个窗口的 `alice / NORMAL` 与 `bob / VIP`，展示不同证书对应不同身份。保留身份栏、验证日志和脚本完整输出作为验收依据。

##### 4. 上传文件

1. 点击 **“＋ 上传文件…”**，选择 `runtime\demo-files\small.bin`。
2. 观察下方“传输任务”的进度和速度。
3. 完成后确认状态为“已完成”、详情为“SHA-256 校验通过”；上方“我的远端文件”出现 `small.bin`，大小为 128 KiB。

可继续上传 `说明.txt` 和 `empty.txt`，检查中文文件名与空文件。

##### 5. 下载文件并验证完整性

1. 在上方远端文件列表选中 `small.bin`，点击 **“↓ 下载选中文件…”**。
2. 选择项目下的 `runtime\downloads` 作为接收目录。
3. 等待下载任务完成，确认“SHA-256 校验通过”。

在辅助 PowerShell 窗口比较源文件与下载文件：

```powershell
Get-FileHash runtime\demo-files\small.bin -Algorithm SHA256
Get-FileHash runtime\downloads\small.bin -Algorithm SHA256
```

两条结果的 `Hash` 应完全一致。

##### 6. 上传和下载断点续传（加分项）

使用普通用户 alice 和 `large.bin` 演示，便于在传输完成前操作。

1. **上传续传**：上传 `runtime\demo-files\large.bin`。完成前，在下方选中该任务，点击“暂停选中”；等待“已暂停”后记录已完成字节，再点击“继续 / 重试”，观察从保存进度继续。
2. **下载续传**：上传完成后，将远端 `large.bin` 下载到新的空目录，同样暂停、记录进度并继续。
3. **客户端重启恢复**：暂停一个未完成任务后关闭客户端，重新执行 `.\scripts\start-client.ps1`，连接服务器，再选中原任务继续。
4. **服务端重启恢复**：传输未完成时，在服务端窗口按 Ctrl+C，再执行 `.\scripts\start-server.ps1`。客户端重连后继续原任务；任务仍未完成时点击“继续 / 重试”。

各项最终均应完成并通过 SHA-256 校验。测试期间保留原 `runtime/`、上传源文件和下载目标目录。续传使用“暂停选中”；“取消任务”会结束上传并清理未完成片段。

重复演示时使用新文件名或新生成的文件，下载选择新的空目录，避免同名同内容文件被直接复用。

##### 7. 多个普通用户同时使用

在辅助 PowerShell 窗口创建一次普通用户 charlie：

```powershell
.\.venv\Scripts\python.exe -m sft add-user charlie --role normal
```

命令生成 charlie 的独立证书及 `runtime/clients/charlie/client.json`，无需重启服务端。已有用户不重复创建。

另开一个 PowerShell 窗口，进入项目目录并启动 charlie：

```powershell
cd "D:\secure_transfer_lab"
.\scripts\start-client.ps1 -Profile runtime/clients/charlie/client.json
```

在新窗口点击“连接服务器”，确认显示 `charlie / NORMAL`。让 alice 和 charlie 同时上传不同文件，观察两个窗口在同一时间段内都有任务进度增长；完成后，两人各自只看到自己的文件。可使用下一步生成的 16 MiB 文件延长观察时间。

两人都是普通用户，各有上传 512 KiB/s、下载 512 KiB/s 的额度。同一用户多个任务共享该用户的额度，不同用户分别计算。继续增加用户时替换命令中的用户名；创建 VIP 使用 `--role vip`。

##### 8. GUI 多文件上传、下载与混合并发（加分项）

在辅助 PowerShell 窗口生成一套新文件：

```powershell
.\.venv\Scripts\python.exe scripts/make-demo-files.py --concurrency
```

脚本输出一个独立目录，其中五个 `.bin` 文件各为 16 MiB，文件名带唯一后缀。按实际输出目录和下面的文件名前缀选择：

1. **并发上传**：alice 点击“＋ 上传文件…”，按 Ctrl 同时选择 `upload-a-*`、`upload-b-*`，观察两行在同一时间段内都显示“传输中”、已完成字节持续增加。
2. **并发下载**：上传完成后，在远端列表按 Ctrl 选中这两个文件，下载到新的空目录，观察两项下载同时推进。
3. **上传与下载混合**：开始上传尚未上传的 `seed-download-*`，同时将一个已完成的远端文件下载到另一空目录，观察上传和下载两行同时增长。
4. **多用户同时传输**：再生成一套新文件，让 alice 上传 `upload-a-*`、`upload-b-*`，同时让 charlie 上传 `user-charlie-*`，观察两个窗口的进度并检查各自文件列表。

每个客户端默认三个传输连接，上传和下载合计最多三个文件同时传输，更多任务排队。所有任务最终均应通过校验；多个任务在同一时间段内推进才体现并发。第 4 项用于检查基础要求中的多用户并发和文件隔离。

##### 9. 普通用户与 VIP 带宽对比（加分项：按用户身份控制带宽）

另开一个 PowerShell 窗口，进入项目目录并启动 bob：

```powershell
cd "D:\secure_transfer_lab"
.\scripts\start-client.ps1 -Profile runtime/clients/bob/client.json
```

点击“连接服务器”，确认身份为 `bob / VIP`，上传、下载限速各为 4 MiB/s。

在辅助 PowerShell 窗口生成一个新的 **128 MiB** 比较文件：

```powershell
.\.venv\Scripts\python.exe scripts/make-demo-files.py --bandwidth
```

1. 在脚本输出目录中找到 `vip-compare-*` 文件，让 alice 和 bob 分别上传它；比较期间每人只运行这一项传输。
2. 比较稳定传输阶段的速度与耗时。默认限额为 alice 512 KiB/s、bob 4 MiB/s；128 MiB 的理论传输时间分别约为 256 秒、32 秒，实际包含磁盘、校验和调度开销。
3. 上传完成后，两人分别下载到各自新的空目录，比较下载方向。
4. 再让同一用户同时传输多个新文件，观察同方向合计速度，确认多个任务共享该用户的限额。

`--bandwidth` 每次生成新的目录和文件；需要缩小文件时可加 `--size-mib 64`。每个用户的上传与下载额度分别计算，多开连接不会获得多份完整额度。

#### 可选：Windows EXE 打包与运行

完成源码方式的安装和传输检查后，如需交付可双击运行的程序，在 Windows 项目目录执行：

```powershell
.\scripts\build-windows.ps1
```

脚本安装打包依赖，生成 `dist/windows-时间戳/SecureTransfer-Windows-x64.zip`。将完整 ZIP 解压到新的有写权限目录，进入其中的 `sftlab` 文件夹：

1. 双击 `01-initialize.cmd`，首次生成证书、账户和配置。
2. 双击 `02-server.cmd`，保持服务端窗口运行。
3. 双击 `03-alice.cmd`，在客户端点击“连接服务器”。
4. 需要 VIP 时双击 `04-bob.cmd`，同样点击连接。

接收方无需安装 Python，但需要安装 Microsoft Visual C++ x64 运行库，并保留 `sftlab.exe` 旁的 `_internal` 目录及其余程序文件。首次初始化后，日常使用从服务端和客户端启动入口开始。

交付前使用解压后的程序检查上传、下载、续传和并发功能；源码修改后重新构建完整程序包。

#### Windows 常见问题

| 现象 | 处理方法 |
|---|---|
| `.ps1` 打开记事本 | 打开 PowerShell，进入项目目录后输入 `.\scripts\setup.ps1` 等命令执行。 |
| 提示禁止运行脚本 | 在当前 PowerShell 执行 `Set-ExecutionPolicy -Scope Process Bypass` 后重试；该设置仅对当前窗口有效。 |
| 找不到 `python` | 确认 Python 已安装并加入 PATH，重新打开 PowerShell，再执行 `python --version`。 |
| 提示先运行 `setup` | 完成本方案第 2 步，看到 `Ready` 后再启动。 |
| 安装 PySide6 时，报错路径很长并提示 `No such file or directory` | 将源码重新解压到 `C:\sftlab` 等短路径，在新位置重新创建 `.venv` 并运行 `setup.ps1`。 |
| Qt DLL 加载失败 | 确认安装了 64 位 Python 和 Microsoft Visual C++ x64 运行库，重新运行 `setup.ps1`；可用下方命令单独检查窗口。 |
| 端口被占用 | 执行 `Get-NetTCPConnection -LocalPort 18765 -State Listen` 查看；若本项目服务端已经运行，直接连接现有服务端。 |
| 连接或证书验证失败 | 确认服务端保持运行，客户端地址为 `127.0.0.1:18765`，配置与证书来自本机同一套 `runtime/`。 |
| 下载目标发布失败 | 选择有写权限的 NTFS 目录，确认文件未被其他程序占用，且不存在同名不同内容文件。 |
| 传输立刻完成，无法观察进度 | 使用新生成的大文件，下载选择新的空目录，避免复用已有完整内容。 |

单独检查图形环境：

```powershell
.\.venv\Scripts\python.exe -m sft probe-gui
```

## 可选：单独安装依赖

正常部署使用上方的 `setup` 或 `build-windows.ps1` 即可；需要手动安装某项功能时，先激活项目的 `.venv`，再执行对应命令。`requirements.txt` 统一约束依赖版本。

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
README.md                Linux / Windows 部署、功能展示、常见问题及 Windows 打包说明
sft/                     核心协议、证书、存储、服务端、客户端和 GUI
scripts/                 环境准备、启动、认证演示、演示文件生成、Windows 打包及源码导出
scripts/windows-package/ Windows 可执行包的双击启动入口
docs/                    本地 Word 实验报告，单独交付，不上传 GitHub
requirements.txt         依赖版本清单
pyproject.toml           项目配置与命令入口
runtime/                 本机证书、用户配置、任务状态、数据库及演示文件
dist/                    导出的源码 ZIP 或在 Windows 构建的程序包
```

`.venv/` 是当前 Python 运行环境，`.local/qt/` 是本机 X11 所需的补充动态库。
`runtime/` 包含 CA、服务端和用户私钥，以及数据库、传输文件和任务记录，因此不上传 GitHub，也不放入源码包。每台电脑首次安装时独立生成，后续启动会继续使用已有数据。

## 报告与源码导出

Word 报告位于 `docs/`，第 10 节分别说明 Linux 与 Windows 部署、功能验收和程序交付，附录列出源码与交付材料。

在项目目录按当前系统选择一条命令，导出源码包：

| 系统 | 命令 |
|---|---|
| Linux | `.venv/bin/python scripts/export-source.py` |
| Windows PowerShell | `.\.venv\Scripts\python.exe scripts/export-source.py` |

输出 `dist/SecureTransfer-source.zip` 及 SHA-256 校验文件，包含核心源码、运行脚本；本地存在 `docs/` 时也一并收录，便于单独交付。
大文件在目标电脑运行 `scripts/make-demo-files.py --bandwidth` 生成。Windows EXE 按上方“可选：Windows EXE 打包与运行”构建。
