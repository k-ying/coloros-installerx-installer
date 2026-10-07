# InstallerX 系统安装器替换模块 — 安装说明与项目报告（ColorOS 17 / 一加 15 PLK110）

**状态速览（2026-10-07）**：模块 + **Hybrid Mount（我们的模块单独用 VFS 后端）** 已在真机验证：
- ✅ NP管理器 / 文件管理器调用安装 → 正常弹出 InstallerX
- ✅ Hunter 的挂载层告警 `ACTIVE_OVERLAY_OVER_SYSTEM:/system_ext/priv-app` 消失
- ✅ Hunter 侧**已无可修项**：它那份报告全部由 `/proc/mounts`（`parsedMounts=222`）推导，唯一强告警就是上面那条挂载层代码，已消失；其余 10 条弱告警指向 `/product/*`、`/vendor/*`，是**别的模块**产生的，与本文档这套无关。第九节已降级为**可选**打磨
- ✅ **v1.1（对外分发版）**：修掉 `module.prop` / `customize.sh` 里仍指向 `meta-overlayfs` 的旧文案，改为 Hybrid Mount + VFS，并补上中英文说明；模块本身与 v1 功能一致（APK 未改动，sha256 不变）

**交付物**（两个 ZIP 都在本仓库的 **Releases** 页，仓库内不存放二进制）

| 文件 | 大小 | sha256 |
|---|---|---|
| `InstallerX-coloros-system-installer-v1.1.zip` | 3834891 | `879e98d9fbd63ebd79f7ccc0e2bfd21ee6553d6b77dbbc54e941c15b060f5f0a` |
| `Hybrid-Mount-6.2.2-2053.zip`（元模块） | 6713210 | `52f067cfea4fafc2bde333bf717bf5338e244faca156caaf557a0288ce1b963c` |

---

## ⚠️ 适用条件与装前自查（先读）

**这是一个"针对特定固件构建"的产物，不是通用模块。** 它的做法是把一份嫁接好的 APK 覆盖到系统的
`/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk` 上，因此只有在**你机器上那份原版 APK 与构建时所用的那一份逐字节一致**时，行为才是被验证过的。

下面命令请在**有 root 的终端**里执行（KSU/APatch 管理器的终端，或 Termux 里 `su`）。注意 **adb shell 本身没有 root**（见第七节）。

### 1. 原版安装器必须完全一致（最重要）

```sh
su -c 'sha256sum /system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk'
```

| 项 | 期望值 |
|---|---|
| 大小 | `8979504` 字节 |
| sha256 | `aeb253b93289bc3d10c46747b31358e58ea2325feb8a2e6143545f5e3b62b068` |

**对不上就不要装。** 这不是"保守"，而是两条真实机制：

- 本模块的 APK 只移植了原版 APK 的**签名块/证书**。证书不一致时 `PackageManagerService` 的升级签名校验会判为不兼容。
- 更关键的是：这是一份**系统包**，扫包发生在开机早期。**如果替换后的 APK 在你的框架上解析失败，PMS 会把这个包整个丢弃 → 系统里 0 个系统安装器 → 安装器自检失败**，其后果与"把 `InstallStart` 禁用掉"完全同一个机制（见第一节、第三节）。

对不上时的正路：由构建者用**你机器上那份原版 APK**重新移植一版，不要硬装。

### 2. 管理器必须是 KernelSU 或 APatch

Hybrid Mount 官方只支持这两个（`metamodule=1` 机制）。**Magisk 不适用。**

### 3. 内核线必须落在 Hybrid Mount 自带的 `.ko` 清单里

```sh
su -c uname -r
```

需命中 `android12-5.10` / `android13-5.10` / `android13-5.15` / `android14-5.15` / `android14-6.1` / `android15-6.6` / `android16-6.12` 之一。
不命中时 VFS 的内核模块被拒绝 → 规则**静默降级为"忽略"** → 安装器仍是原版（**不会卡机**，只是替换没生效）。详见 5.6。

### 4. 换元模块会波及你其它模块

同一时间只能有一个元模块。若当前用的是 `meta-overlayfs` / `mountify` 等，装 Hybrid Mount 前必须先卸掉它 —— 这**会改变你现有所有依赖挂载的模块的行为**，影响的不是本模块一个。

### 5. 先确认退路可用再动手

手边有电脑 + 数据线；知道 KSU 安全模式怎么进（开机第一屏后连按「音量下」3 次）；知道 recovery 里该删哪个目录（第七节）。

### 三条纪律

1. **不要改动任何系统组件的启用状态。** 把安装器组件 `pm disable` 掉会让系统里出现 0 个系统安装器，**直接卡开机**（我们踩过，见第一节、第三节）。
2. **不要把 `vfs_strict` 设为 `true`。** 保持默认 `false`：VFS 不可用时降级为「忽略」，机器照常启动。
3. **不要打开「禁用卸载注册」。** 保持默认关闭；它只在临时退回 OverlayFS/Magic 方案时才需要（见 5.5）。

> **未在其它机型上验证。** 本文档与构建针对 **一加 15（PLK110）/ ColorOS 17 / `PLK110_17.0.0.102(CN01)`**。同型号但固件版本不同，请先完成上面第 1 条自查。

---

## 一、这个模块做什么

把官方安装器 `/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk` 在**运行时**换成 InstallerX，但：

- **包名保持 `com.android.packageinstaller`**（不是另装一个包）；
- **补齐官方安装器的 4 个组件名**：`InstallStart`、`UninstallerActivity`、`UnarchiveActivity`、`UnarchiveErrorActivity`，全部指向 InstallerX 的界面。

于是任何应用发出的安装请求都会落到 InstallerX —— 包括**硬编码** `cmp=com.android.packageinstaller/.InstallStart` 的 NP管理器（这是实测路径，也是早期 priority 方案无效的原因）。卸载同样接管。

**不改分区、不删文件、不禁用任何组件** —— 不会碰到上次变砖的路径（禁用 `InstallStart` → 开机自检数到 0 个安装器 → 卡开机）。

---

## 二、原理：为什么必须有元模块，以及为什么必须用 VFS 后端

### 2.1 模块从不写分区

`/system_ext` 是只读、受 dm-verity 保护的分区，物理写不进去。模块做的事只是**在开机时让系统"看到"另一份文件**，而这个动作**不是 KSU 自己做的，是交给元模块（metamodule）做的**。

- **没装/没启用元模块** → 没有人执行这件事 → `/system_ext/...` 还是原版 8979504 字节 → 系统按原版正常开机，和没刷过一样（只有 `post-fs-data.sh` 清缓存会跑，无害）。
- **装了** → 每次开机重新计算，重启即还原，**不存在"替换掉就回不来"**。

判断是否生效（**不需要 root**）：

```bash
adb shell ls -l /system_ext/priv-app/OppoPackageInstaller/
# 8979504 = 原版（模块没生效）   5006445 = 我们的（生效了）
```

### 2.2 三种后端，为什么只有 VFS 可行

HyM 提供三种把模块内容"送进"系统的方式，它们的**可被检测性**和**可被 KSU 卸载性**完全不同：

| 后端 | 本质 | `/proc/self/mountinfo` 里 | 会不会被 KSU 从 App 进程里卸载 |
|---|---|---|---|
| **OverlayFS** | 真挂载（一条路径一条） | **有**：`overlay … src=KSU lowerdir=…` | **会** |
| **Magic Mount** | 真挂载（**每个文件一条** bind mount） | 有，而且条数暴涨 | **会** |
| **VFS** | **不是挂载**：规则经 keyring 下发给 `hybridmount` 内核子系统，在 VFS 层重定向 | **完全没有** | **不会**（不参与注册） |

这解释了整段折腾史：

1. 用 OverlayFS + HyM 默认（「禁用卸载注册」关）→ HyM 把挂载点提交进 **KernelSU 的 try-umount 列表** → KSU 在**每个 App 进程**里把它卸掉 → **安装器进程看到的是原版 OPPO APK**，而系统启动时是按我们的 manifest 解析的，两边对不上 → 崩在 `Resources$NotFoundException: Unable to find resource ID #0x7f120004` → **"调用安装没反应也没报错"**。
2. 打开「禁用卸载注册」→ 挂载对 App 可见 → 安装器**能用**了，但 Hunter 立刻报 `ACTIVE_OVERLAY_OVER_SYSTEM:/system_ext/priv-app`（它读自己的 mountinfo，看到 `rw + src=KSU + lowerdir 落在数据分区`）。
3. 想两全：把「禁用卸载注册」关回去，再在 KSU 里给 `com.android.packageinstaller` 单独关「卸载模块」（官方文档说的白名单，UID 10168 也确实设成了"关闭"）—— **实测无效**。原因我没有查到确定解释（可能是 HyM 注册的是**目录**挂载点 `/system_ext/priv-app`，而 KSU 那侧"还原"按自己的 try-umount 记录逐项处理，整目录覆盖没被正确还原）。**这条不必再追**。
4. **只有 VFS 同时满足两件事**：不产生 mount 条目（检测看不到），也不进 try-umount 列表（KSU 管不到）⇒ 安装器能用，Hunter 安静。

---

## 三、签名是怎么处理的（为什么不会卡开机）

| 风险点 | 处理方式 |
|---|---|
| 新旧证书不一致 → `INSTALL_FAILED_UPDATE_INCOMPATIBLE` → 包被丢弃 → 卡开机 | **逐字节沿用 OPPO 原 APK 的签名块**，PMS 读到的证书与 `packages.xml` 里记录的一致；同时保住 `seinfo=platform` |
| 版本被判定为降级 | `versionCode=17000001`、`versionName=17.0.1`，与官方**完全一致** |
| `ro.control_privapp_permissions=enforce` 下白名单缺权限 | 请求集是官方权限集的**子集**（少了 6 条 `normal` 权限），没多要一条 |
| 开机自检「必须有且只有 1 个安装器」 | 全清单模拟确认：安装器查询命中 **1** 个组件，卸载器查询命中 **1** 个组件 |

（注意：这个 APK 的签名在密码学上是**无效**的 —— 摘要是对原版内容算的 —— 系统分区 APK 本来就只读证书不校验内容，所以系统接受；但它不能拿去 `pm install`、不能分发，也不含任何可迁移的签名能力。）

---

## 四、元模块：Hybrid Mount

### 4.1 下载与核对

- **本仓库 Releases 页提供一份核对过的** `Hybrid-Mount-6.2.2-2053.zip`（6713210 字节，sha256 `52f067cfea4fafc2bde333bf717bf5338e244faca156caaf557a0288ce1b963c`）。也可从上游自行下载，两份应当一致。
- 官方 Releases：<https://github.com/Hybrid-Mount/meta-hybrid_mount/releases>（用 **稳定版**，别用 `-rc`）

`module.prop` 原文（可自行核对）：

```
id=hybrid_mount
name=Hybrid Mount
version=6.2.2
versionCode=602002999
author=Hybrid Mount Developers
metamodule=1
```

### 4.2 安装

**同时只能有一个元模块。** 如果现在装的是 `meta-overlayfs`（或 `mountify` 等），先卸载它 → 重启 → 再装 HyM。

装法同普通模块：**KSU 管理器 → ➕ → 选 ZIP → 重启**。首装时 10 秒内可用音量键选**全局**默认后端：

- 音量上 / 不按 = OverlayFS
- 音量下 = Magic Mount
- **向导里没有 VFS**（VFS 是逐模块设置的，见第五节）

> 卸载 meta-overlayfs 不会丢我们的 payload：它当年只是 `cp -af` 把内容拷进自己的 `modules.img`，**没有 `mv`、没有 `rm`**，所以 `/data/adb/modules/installerx-coloros-installer/system/system_ext/...` 里的 APK 一直都在。

---

## 五、【重点】把我们的模块切到 VFS 后端

### 5.1 只改这一个模块，不要动全局

HyM 里有**两个**看起来都能"选后端"的地方，别搞混：

| 位置 | 作用范围 | 我们怎么用 |
|---|---|---|
| 底部「**配置**」→「默认后端」 | **全局**，对所有没有单独规则的模块生效 | **保持 OverlayFS**（不要改成 VFS） |
| 「**模块**」→ 点进某个模块 →「**模块默认**」 | **只对该模块** | 我们的模块设 **VFS**；不需要的模块设 **忽略** |

（WebUI 这两个开关最终都写进 `/data/adb/hybrid-mount/config.toml`：全局的是顶层 `default_mode`，逐模块的是 `[rules.<模块id>] default_mode`。所以下面 5.2 的操作和 5.4 的文本是完全等价的，**保存后都要重启才生效**。）

### 5.2 操作步骤（真机验证过的路径）

1. 打开 **Hybrid Mount**（KSU 管理器里的模块 → WebUI）。
2. 点底部 **「模块」** 标签。
3. 在列表里找到我们的模块 —— 它**名字很长**：
   - 标题显示为 `InstallerX as system package instal…`
   - 副标题/id：`installerx-coloros-installer`
   - 版本行：`1.1 (InstallerX 26.04.9d7dc1f)`，作者 `k_ying`
   - 右侧会有一个后端徽标（如果之前是 `OverlayFS`，就是它）
4. 在该卡片的 **「模块默认」** 一行，点 **`VFS`**（同一行还有 继承全局 / OverlayFS / Magic Mount / 忽略）。
5. 点**右下角保存**按钮（软盘图标）。
6. **重启手机。**

> 顺便：同一页面里，把**不需要生效**的模块（例如 Front Setting 之类字体模块、或任何自动落在 OverlayFS 的模块）在「模块默认」里点成 **忽略**。忽略 = 完全不挂载/不注入 —— 既少一份痕迹，也不会出现"半生效"状态。

### 5.3 切完之后要确认

```bash
adb shell ls -l /system_ext/priv-app/OppoPackageInstaller/
# 5006445 = VFS 生效（正确）
# 8979504 = VFS 没起来/被降级成忽略（此安装器是原版，机器一切正常，但我们的替换没生效）
```

还想看细节（需要 root shell，例如 MT管理器 的终端）：

```bash
/data/adb/modules/hybrid_mount/hybrid-mount vfs doctor     # provider 在不在、版本、隔离的 UID
/data/adb/modules/hybrid_mount/hybrid-mount runtime status # 各模块后端与挂载状态
```

HyM 的「**状态**」页也能看：`vfs_provider` / `vfs_active_mounts` / 每个模块的 `is_mounted`。

### 5.4 等价的 `config.toml` 写法（不想用 WebUI 时）

编辑 `/data/adb/hybrid-mount/config.toml`：

```toml
moduledir = "/data/adb/modules"
overlay_mode = "ext4"
disable_umount = false          # 保持默认 false（见下）
default_mode = "overlay"        # 全局仍是 overlay

[rules.installerx-coloros-installer]
default_mode = "vfs"            # 只让我们的模块走 VFS
```

改完**重启**生效。

### 5.5 「禁用卸载注册」要放回关闭（默认）

切到 VFS 之后，把 HyM「配置」页里的 **「禁用卸载注册」关回去**（即 `disable_umount = false`，默认值）。理由：我们的模块走 VFS、本来就不在卸载列表里，这个开关对我们没影响；但它开着意味着 **overlay/magic 类模块的挂载对所有 App 可见** —— 那正是 Hunter 会抓的东西。关回去 = 其他模块继续受 KSU 的"按 App 卸载"保护。

### 5.6 VFS 的前提与纪律（**必读**）

1. **需要内核模块 `hybridmount`**。HyM 自带 aarch64 预编译，但**一条内核线一个**：`android12-5.10 / android13-5.10 / android13-5.15 / android14-5.15 / android14-6.1 / android15-6.6 / android16-6.12`。选择要求**内核线 + Android/GKI 标签精确匹配**，未知组合直接拒绝。
   - **本机命中**：`adb shell uname -r` = `6.12.69-android16-6-g…` ⇒ 用 `hybridmount-android16-6.12.ko`。（平台是 Android 17、内核分支却是 android16-6.12，这是 GKI 的正常设计，见第十一节。）
2. **`vfs_strict` 永远保持 false**（默认，配置文件里是注释状态）。false ⇒ VFS 不可用时规则**降级为"忽略"**，机器照常启动，只是我们的替换静默失效；设成 `true` ⇒ VFS 不可用时**启动失败 = 卡开机**。
3. **insmod 崩溃熔断**：万一加载内核模块时内核崩了，HyM 会留下 guard 标记（`/data/adb/hybrid-mount/vfs_boot_guard`），下次开机不再自动重试（其余功能保留）。清掉标记（WebUI 状态卡里的清除动作）后重启再试。
4. **与 NoMount 互斥**：两者都劫持 inode 操作。若以后换到带 NoMount 的内核/模块，先卸掉 NoMount。
5. **不保证 ABI 兼容**：HyM 的内置加载器会在内核明确报 vermagic 不匹配时"在内存里适配后重试一次"，官方自己声明这不保证兼容 —— 所以请保留第 5.3 节的复核习惯。

### 5.7 每次系统更新（OTA）后必做的一件事

```bash
adb shell uname -r                                            # 记下来，和上次对比
adb shell ls -l /system_ext/priv-app/OppoPackageInstaller/    # 期望 5006445
```

平台升级/内核换线可能让 `.ko` 不再匹配 ⇒ VFS 静默降级 ⇒ 安装器悄悄回到原版（**不会卡机**，只是功能没了）。此时二选一：等/找匹配的 HyM 构建，或临时把我们的模块切回 **OverlayFS + 打开「禁用卸载注册」**（能用，但 Hunter 会报挂载项）。

---

## 六、安装本模块

1. ```bash
   adb push InstallerX-coloros-system-installer-v1.1.zip /sdcard/Download/   # 先在 Releases 页下载
   ```
2. **KSU 管理器 → 模块 → 从本地安装** → 选这个 zip。（KSU 模块**不能**在 recovery 里刷。）
3. 重启，然后按第五节把它的后端设成 **VFS**，再重启一次。

> 顺序提示：**先装 HyM 并重启 → 再装我们的模块 → 再设 VFS → 再重启**。如果在 meta-overlayfs 时代就装过我们的模块，也不必重装（payload 还在，见 4.2 的说明），但**建议重装一次**：HyM 的 `metainstall.sh` 会在安装时给模块根补一个 `system_ext -> ./system/system_ext` 的"分区提升"链接，重装最省事。

---

## 七、回滚

> ⚠️ adb shell **没有 root**（`adb root` 被正式版拒绝、`su` 在 adb shell 里不可用），**不要**用 `adb shell su -c ...`。按顺序选：

1. **只让本次替换失效（最轻）**：HyM →「模块」→ 我们的模块 → 「模块默认」→ **忽略** → 保存 → 重启。原版安装器立即回来。
2. **KSU 管理器**：把 `installerx-coloros-installer` 停用或卸载 → 重启。
3. **进不去系统 —— KSU 安全模式**：开机第一屏出现后，**连续按「音量下」键 3 次**（按下-松开 ×3，不是长按）。官方文档：进入安全模式后**所有模块都被禁用**，可在管理器里直接卸载出问题的模块。该模式在内核里实现，不会被按键拦截；时机是从内核模块初始化到 `on_post_fs_data` 之间（大约开机动画之前）。
   - ⚠️ 官方唯一例外警告：模块写在 **initrc** 里的代码在安全模式**仍会执行**。**我们的模块没有 initrc**（只有 `post-fs-data.sh` + payload），与这条无关。
4. **进不去系统也进不了安全模式 —— Recovery**：
   ```bash
   mount /data
   rm -rf /data/adb/modules/installerx-coloros-installer
   ```
   终极手段（让 KSU 完全不加载任何模块）：
   ```bash
   mount /data
   rm -f /data/adb/ksud
   # 可选：
   mount /metadata
   rm -f /metadata/ksu/modules.rc
   rm -f /metadata/watchdog/ksu/modules.rc
   ```
5. **有 root shell 时**（例如 recovery 挂了 data/metadata 后跑 `/data/adb/ksud`）：
   ```bash
   /data/adb/ksud module list
   /data/adb/ksud module disable installerx-coloros-installer
   /data/adb/ksud module uninstall installerx-coloros-installer
   ```

**结论**：最坏后果只是"开机卡住"或"替换不生效"，上面几条都能在**不重刷系统、不丢数据**的前提下恢复 —— 模块没碰任何分区，也没禁用任何组件。

---

## 八、设置入口（装好之后怎么改 InstallerX 的设置）

改的是**系统里那一份**（包名 `com.android.packageinstaller`，versionName 17.0.1），它与用户版的 `com.rosan.installer.x.revived`（若另行安装）是**两个独立 App、两套独立设置**（数据目录分别是 `/data/data/com.android.packageinstaller/` 和 `/data/data/com.rosan.installer.x.revived/`）。

三条入口：

1. **拨号盘 `*#*#46789#*#*`** —— 任何状态下都能用（对应 manifest 里的 `SecretCodeReceiver`）。
2. **桌面图标**：当前 v1 里 `LauncherAlias` 还在，应用列表里能看到一个 InstallerX Revived 图标，点开就是设置。建议进去后打开 **「隐藏桌面图标」**（它自带的提示就是"Dial `*#*#46789#*#*` to open settings"），图标消失、拨号码照用。
3. **adb**：
   ```bash
   adb shell am start -n com.android.packageinstaller/com.rosan.installer.ui.activity.SettingsActivity
   # 或走标准"应用偏好设置"入口：
   adb shell am start -a android.intent.action.APPLICATION_PREFERENCES -d package:com.android.packageinstaller
   ```

第一次进设置若问"安装器模式/权限方式"，选**系统/特权**那条（我们这份有 `INSTALL_PACKAGES` + platform 签名，不需要 Shizuku）。两份同名图标容易混：系统那份在"设置 → 应用 → 显示系统应用"里，或直接看版本号 **17.0.1**。

---

## 九、【可选】v2：APK 内部的"一眼假"痕迹 + 16KB 对齐

**先说结论：这一节不是待修项，不做也完全能用。**

Hunter 那份报告是解析 `/proc/mounts` 得出的（`parsedMounts=222`），所有代码都在挂载层：唯一强告警 `ACTIVE_OVERLAY_OVER_SYSTEM:/system_ext/priv-app` 已被 VFS 清掉；剩下 10 条弱告警是 `/product/*`、`/vendor/*`，来自别的模块。**没有任何证据表明 Hunter 会读 APK 内容（签名、应用名、provider、类名）。** 下表纯属"顺手让它更干净"的卫生工作，降级为可选：

| # | 要清掉的东西 | 现状（v1） | 计划（v2） |
|---|---|---|---|
| 1 | **v1 调试证书** | `META-INF/ANDROIDD.SF` / `ANDROIDD.RSA` / `MANIFEST.MF`，`keytool` 显示 `CN=Android Debug, OU=Android, O=US…`（SHA1 `5D:08:26:…`）。**OPPO 原版根本没有 v1 签名** | 直接删掉这三个条目（系统分区 APK 走 `skipVerify`，生效的是嫁接的 v2/v3 块） |
| 2 | **Shizuku / Dhizuku 声明** | `rikka.shizuku.ShizukuProvider`（exported provider，authority `com.android.packageinstaller.shizuku`）、`<queries>` 里的 `com.rosan.dhizuku.server.provider`、meta-data `moe.shizuku.client.V3_SUPPORT` | 全删（我们已是特权系统安装器，用不到 Shizuku） |
| 3 | **应用名与桌面入口** | 显示名 `InstallerX Revived`；`LauncherAlias` 带 MAIN/LAUNCHER | 显示名改成原版；删 `LauncherAlias`（**保留** `SecretCodeReceiver`，否则就没设置入口了） |
| 4 | 多余组件 | `SecretCodeReceiver`、`SettingsTileService`（QS 磁贴）、`BiometricsAuthenticationActivity` | 保留 `SecretCodeReceiver`；其余可选删（生物识别相关删了会让"锁指纹"设置失效） |
| 5 | 字符串 | `classes.dex`/`resources.arsc` 里有 `magisk` `xposed` `kernelsu` `shizuku` `dhizuku` `rosan` | 后续可选：抹掉 dex 字符串、把 `com.rosan.installer.*` 类名整体改名为 `com.android.packageinstaller.*` |
| 6 | **`lib/**/*.so` 的 16KB 对齐** | v1 产物只有 4096 对齐（上游 base 和 OPPO 原版都是 16384） | 构建脚本改成自己做 16384 对齐（`.so` 16384、其余 4 字节），不再依赖 `signer.jar` 的 zipalign。**构建所用机型是 4096 页，因此不影响本文档对应机型的使用** |

> 说明：这些属于"一眼假"级别的观感问题，清掉只是更整洁；**当前没有任何检测读到这一层**。而且无论怎么清，替换后 APK 的文件 hash 与原版必然不同 —— 内容层永远做不到"完全看不出"，所以不值得为它冒任何风险。第 6 项（16KB 对齐）与 Hunter 无关，是构建脚本的工程债，本机 4096 页不受影响。

---

## 十、出问题时的判断

| 现象 | 原因 | 处理 |
|---|---|---|
| 调用安装**没反应也没报错**；logcat 有 `Resources$NotFoundException: Unable to find resource ID #0x7f120004` | 后端是 OverlayFS / Magic Mount，挂载被 KSU 从安装器进程里卸载了 | 把该模块后端改成 **VFS**，重启（第五节） |
| 安装器能用，但 Hunter 报 `ACTIVE_OVERLAY_OVER_SYSTEM:/system_ext/priv-app` | 挂载是真实 overlay，且对 App 可见（「禁用卸载注册」被打开） | 切 **VFS**；并把「禁用卸载注册」关回去 |
| `ls -l` 显示 **8979504** | VFS 未生效：内核模块不匹配 / 被降级为忽略 / 模块未启用 | `uname -r` 对比 `.ko` 清单；`hybrid-mount vfs doctor`；确认模块默认后端是 VFS；重启 |
| 装上后仍是 OPPO 安装器 | 没装元模块，或模块被设为"忽略" | 装 HyM；把模块后端设为 VFS |
| InstallerX 界面闪退 | 需要看日志 | `adb logcat -b crash -d \| tail -120`，把 `Caused by:` 那段发出来 |
| 打开 PDF/图片也弹 InstallerX | 不应该发生 | 已把贪心 `*/*` 从 VIEW 过滤器摘掉，只保留 apk 类型 |
| 卡开机动画 | 罕见 | 按第七节回滚（安全模式音量下 ×3 / 管理器设忽略 / recovery 删目录），不丢数据 |

---

## 十一、附注：Android 17 却是 `android16-6.12` 内核，正常吗？

**正常。** `uname -r` 的格式是 `<内核版本>-<GKI 分支>-<KMI 代数>-g<hash>`：

```
6.12.69 - android16 - 6 - g…
   │         │        └── KMI（内核模块接口）代数
   │         └────────── GKI 分支：为 Android 16 创建的 6.12 分支
   └──────────────────── 内核大版本（不换线就不动）
```

GKI 的设计前提就是**内核与平台解耦**：内核 + vendor 在机型**上市时冻结**，平台层可以跨大版本单独升级。一加 15 是以 Android 16 上市的，ColorOS 17 只是平台升级 ⇒ 内核仍在这条线上打 ACK/LTS 安全补丁（`.69` 就是这个意思）。已知 GKI 分支：`android12-5.10`、`android13-5.10/5.15`、`android14-5.15/6.1`、`android15-6.6`、`android16-6.12`。

**对我们的直接影响**：HyM 的 VFS `.ko` 就是按这个标签匹配的，我们正好命中 `android16-6.12` —— 这也是 VFS 能起来的原因；将来 OTA 若把标签换成 `android17-*`，就会降级失效（见 5.7）。

---

## 十二、附注（与本模块无关）：设备上的「伪回锁」模块 `fake_bl_efisp` —— 按源码核查过

结论：**KSU 安全模式不会让它拒绝开机**。

### 1. 这个"假回锁"不在 KSU 里实现，在 bootloader 里

> 真实 ABL 通过 GBL 漏洞从原始 `efisp` 分区加载内嵌的 superfastboot BDS，BDS 再扫描兼容分区获取启动项并链式启动。
> 本设备的启动根目录是 `persist` 分区（挂载到 `/mnt/vendor/persist`）下的 `efisp/` 目录：`boot.efi` = 破解版 ABL，带假回锁（`ANDROID` 启动项）
> —— `wiki/docs/zh/install.md`

启动链：BootROM → `abl_a`/`abl_b` 上的真实 ABL（必须带 GBL 漏洞）→ 从**裸分区 `efisp`** 加载 `BDS.efi` → 读 `/mnt/vendor/persist/efisp/BOOTENTRIES` → 链式启动 `boot.efi`（打补丁的 ABL）→ 这之后才轮到 Android。**全在 Linux/init/KernelSU 之前结束。**

对应代码：`customize.sh:277`（`dd … of=$BY_NAME_DIR/efisp`）、`customize.sh:250`（降级 `abl`）、`bin/bl_flasher.sh:221`（生成 `boot.efi`）。

### 2. 这个模块在开机时不执行任何东西

发布包（release 6.2.192 的 `magisk_module.zip`，3882261 字节）共 58 个条目，shell 脚本只有 3 个：`customize.sh`（安装时）、`uninstall.sh`（卸载时）、`bin/bl_flasher.sh`（WebUI 按需调用）。**没有** `post-fs-data.sh` / `post-mount.sh` / `service.sh` / `boot-completed.sh` / `system.prop` / `sepolicy.rule` / `initrc/*.rc`。

### 3. 作者自己说：卸载模块不会移除回锁

`uninstall.sh` 原文：`ui_print "卸载完成,仅卸载OTA更新辅助，假回锁请自行卸载（因为需要清数据）"`；协作者在 issue #48：`efisp loads regardless of lock state.`

### 4. 真正会导致"卡一屏 / 黑砖"的是这些（都与安全模式无关）

| 风险 | 证据 |
|---|---|
| **OTA / anti-rollback 熔断 → 黑砖** | `wiki/docs/zh/ota.md`：小米"一旦 abl avb 版本变化，该方法会**黑砖**"；"**非必要/主力机器不要更新**"×4；一加"版本 **761** 修复" |
| **跨版本 OTA 后 efisp/ABL 不匹配 → 卡一屏** | README："跨版本升级时请保持开启（更新 efisp），否则可能卡一屏" |
| **TWRP / 第三方 recovery → data 分区不可逆损坏** | wiki："❌ 不要安装 TWRP"；维护者在 issue #15："**只会造成 data 分区不可逆损坏**" |
| **误刷 `ABL_original.efi` → 变砖** | issue #25 即如此变砖；恢复方式是 `fastboot erase efisp`（EFISP 默认是空的） |
| 前置：内核**不能有 Baseband Guard** | README 设备要求 |

---

## 十三、备注

- 设备上原有的 `com.rosan.installer.x.revived`（用户版）与我们的系统版**不冲突**，可留可删；它也可作为"安装器出问题时的临时替代"。
- 尺寸对照：我们的 APK **5006445** 字节 / 官方原版 **8979504** 字节。
- 模块内含 `post-fs-data.sh` 清理 `/data/system/package_cache/*`，`uninstall.sh` 同样清理。
- 后端三选一的速查：**要能用又要安静 → VFS**；能用但会被检测 → OverlayFS/Magic + 关「禁用卸载注册」；**完全回原版 → 忽略**。

---

## 十四、构建、来源与许可

### 14.1 来源，以及与上游那个模块的区别

- 本模块内的 APK 是 **[InstallerX Revived](https://github.com/wxxsfxyzm/InstallerX-Revived)**（`wxxsfxyzm`，**GPL-3.0**）的修改版再打包。
- 上游另有一个 **AOSP 路线**的系统安装器模块 [`InstallerX-Revived_Module`](https://github.com/wxxsfxyzm/InstallerX-Revived_Module)。**两者解决的问题不同**：上游那个面向 AOSP；本模块面向 **ColorOS/OPPO**，必须**沿用 `com.android.packageinstaller` 这个既有包名**、并**移植原厂 APK 的签名块**，才能同时通过开机的"有且只有 1 个系统安装器"自检与签名一致性校验（原因见第三节）。请按自己的 ROM 选。

### 14.2 相对上游改了什么

只动 `AndroidManifest.xml`（`classes.dex` 与 `resources.arsc` 保持逐字节不变）：

1. **去掉 4 个权限**（其中 2 个是 Shizuku / Dhizuku 的第三方 API），使权限请求集不超出原厂安装器已证明可行的范围；
2. **新增 4 个无 intent-filter 的 `activity-alias`**，对齐原厂安装器里被其它 App 以显式 `cmp=` 调用的组件名（`InstallStart` 等）；
3. **收窄一个过于贪婪的 `VIEW` 过滤器** —— 原过滤器带 `*/*`，成为系统安装器后会把打开 PDF、图片等普通 `VIEW` 意图一并抢走。

构建脚本在 [`build/`](build/)：

| 文件 | 作用 |
|---|---|
| `build/patch_sys.py` | 对反编译出的 `AndroidManifest.xml` 做上面三处改动（幂等，可重复运行） |
| `build/build_sysapk.sh` | 完整流程：patch manifest → 回编译 → 只把 `AndroidManifest.xml` 拼回原 APK → 对齐 → 移植签名块 |
| `build/graftsig.py` | 移植签名块并自动校验（块逐字节一致、所有条目偏移不变、CRC 通过、`.so` 对齐） |

**重建所需的原料不在本仓库内**，需自备：

- 上游 InstallerX Revived 的 APK（对应版本 `26.04.9d7dc1f`）；
- **你自己机器上那份** `/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk`（脚本里的 "donor"；这里刻意不转存原厂文件，见下方自查节）；
- JDK 21、apktool 3.x，以及 [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer)（脚本里的 `signer.jar`，仅用于 zipalign，其签名会在下一步被整体丢弃）。

细节见 [`build/README.md`](build/README.md)。

### 14.3 许可

- 再打包的 APK 来自 GPL-3.0 的 InstallerX Revived，因此**按 GPL-3.0 分发**；许可证全文见 [`LICENSE`](LICENSE)，上游源码：<https://github.com/wxxsfxyzm/InstallerX-Revived>。
- 本仓库的构建脚本与文档同样以 GPL-3.0 提供。
- 本模块**不包含 OPPO 原厂 APK**，只在再打包的 APK 中保留了从设备上提取的 APK Signing Block（其中是证书等公开材料）。再分发该材料的合规性请自行评估。
