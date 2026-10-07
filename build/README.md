# build/ —— 如何重建这个模块

这里是从**上游 APK + 你自己设备上的原厂 APK**做到可刷 ZIP 的完整流程。仓库里**不存放任何 APK**：上游 InstallerX Revived 请自行获取，原厂 APK 请从**你自己**的设备提取（我们刻意不转存 OPPO 的原厂文件）。

```
module/                    可刷模块的源码树（module.prop / customize.sh / post-fs-data.sh / uninstall.sh / META-INF）
build/build_sysapk.sh      产出替换用的 APK
build/patch_sys.py         改 AndroidManifest.xml：包名 / 权限 / 别名 / 过滤器（幂等）
build/patch_arsc.py        改 resources.arsc 里的包名字段（256 字节定长字段，原位改写）
build/graftsig.py          移植原厂 APK 的签名块并自校验
build/build_module.sh      把 APK 和 module/ 打成可刷 ZIP
work/                      构建工作区（git 忽略）
dist/                      产出的 ZIP（git 忽略）
```

## 1. 为什么要移植签名块

跑之前请先读 README 第三节。一句话版：系统分区上的 APK 是按 `skipVerify` 解析的，**签名内容不被校验、但证书会被读出来**，并与 `packages.xml` 里已记录的 `com.android.packageinstaller` 证书做一致性比较。自己重签会不一致 → 扫包失败 → 系统里 0 个系统安装器 → 卡开机。所以只能把**原厂 APK 的签名块整块搬过来**。

## 2. 需要自备的原料

| 原料 | 说明 |
|---|---|
| `work/upstream/PackageInstaller.apk` | 上游 [InstallerX Revived](https://github.com/wxxsfxyzm/InstallerX-Revived) 的**原版、未改名** APK（本模块 1.2 用的是 `26.09`，`versionCode 1553`）。包名交给脚本改，不要事先手工改过 |
| `work/OppoPackageInstaller.apk` | **你自己设备上**的 `/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk` |
| JDK 21 | `JAVA=...` |
| apktool 3.x | `APKTOOL=...` |
| [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) | `SIGNER=...`，**只用来 zipalign**，它产生的签名下一步会被整块丢弃 |

取原厂 APK（需要 root；`adb shell` 本身没有 root）：

```sh
su -c 'cp /system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk /sdcard/Download/'
adb pull /sdcard/Download/OppoPackageInstaller.apk work/OppoPackageInstaller.apk
sha256sum work/OppoPackageInstaller.apk     # 本模块对应的是 8979504 字节
```

## 3. 跑

```sh
cd <repo root>
mkdir -p work/upstream
# 放入 SRC 与 DONOR（见上表）

JAVA=/path/to/jdk-21/bin/java \
APKTOOL=/path/to/apktool.jar \
SIGNER=/path/to/uber-apk-signer.jar \
  bash build/build_sysapk.sh

VERSION=v1.2 bash build/build_module.sh
```

产物：`work/PackageInstaller-final.apk` 和 `dist/InstallerX-coloros-system-installer-v1.2.zip`。

`build_sysapk.sh` 最后一步会打印 `graftsig.py` 的校验结果，四项都应为 `OK`：

- 输出 APK 的签名块与 donor **逐字节一致**
- 所有 zip 条目偏移**未改变**（zipalign 的填充被完整保留）
- zip CRC 全通过
- `.so` 条目 4096 对齐

## 4. 三个必须知道的坑

**① `VERSION_CODE` / `VERSION_NAME` 必须等于设备已记录的值。**
默认 `17000001` / `17.0.1`（本模块对应的 ColorOS 17 固件）。改错会在 `packages.xml` 里记下一个版本变更。查你自己设备：

```sh
su -c 'dumpsys package com.android.packageinstaller | grep -m1 version'
```

**② apktool 3.x 的缓存。**
它把上次的产物缓存在 `<DEC>/build/`，**改 `apktool.yml` 不会让它失效**。脚本里的 `rm -rf "$DEC/build"` 就是为此，别删掉。

**③ 16 KB 页对齐没有修（已知限制，但 26.09 用不到）。**
uber-apk-signer 只按 **4 字节**对齐，所以产出的 `.so` 是 4096 对齐而非 16384。**4 KB 页设备可用**（本模块目标机型就是），16 KB 页设备不够。要修就得绕开 uber-apk-signer、自己在打包时按 16384 对齐 `.so`（其余条目 4 字节）。
26.09 的上游 APK **不含任何原生库**（`graftsig.py` 会打印 `.so entries not 4096-aligned: 0`），所以这条对 1.2 不适用；哪天换回带 `.so` 的上游版本就要重新考虑。

## 5. 两个 patch 脚本各改了什么

四处，都幂等。前三处是文本改动，在 `patch_sys.py` 里；第四处是二进制改动，在 `patch_arsc.py` 里：

1. **改包名。** `package` 属性、1 个自定义 permission 的声明与使用、3 个 provider 的 `authorities` —— 上游 manifest 里这个包名字符串一共出现 6 次，**全是自引用**，所以整体替换是安全的（类名都在 `com.rosan.installer.*` 下，不会被误伤）。脚本自己从 manifest 里读出旧包名，不写死。
2. 去掉 4 个权限（其中 2 个是 Shizuku / Dhizuku 的第三方 API）；
3. 在 `UninstallerActivity` 后面插入 4 个**无 intent-filter** 的 `activity-alias`（`InstallStart` / `UninstallerActivity` / `UnarchiveActivity` / `UnarchiveErrorActivity`）—— 显式 `cmp=` 调用只看组件是否存在且 exported，不看 filter；给它们加 filter 反而会违反"有且只有 1 个安装器"的自检；
4. 把 `InstallerActivity` **第一个** intent-filter 里的 `<data android:mimeType="*/*"/>` 删掉（保留 APK 类型那条），并补一个只有 `INSTALL_PACKAGE`、无 mimeType 的过滤器。这里定位的是"InstallerActivity 的第一个 filter"而不是匹配固定文本块 —— 26.09 把 `android:priority="10"` 去掉了，按固定块的匹配会直接失效。

包名在 `resources.arsc` 里的那一份由 `patch_arsc.py` 处理：它是 `ResTable_package` 里定长的 `char16 name[128]`（256 字节），所以原位改写 + NUL 补齐即可；脚本会断言"只有这 256 字节窗口内发生了变化"，文件长度和其余偏移都不变。

上游换版本时如果锚点找不到，脚本会 `exit` 而不是静默跳过（例如 `*/*` 不在第一个 filter 里、或 manifest 里找不到 `package` 属性）—— 这种情况需要人工比对那段 XML 再改脚本。
