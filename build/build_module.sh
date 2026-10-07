#!/usr/bin/env bash
# Assemble the flashable KernelSU/APatch module zip from module/ plus a built APK.
#
#   APKSRC   the APK produced by build_sysapk.sh   [work/PackageInstaller-final.apk]
#   MOD      module source tree                    [module]
#   VERSION  used in the output file name          [v1.2]
#   OUT      output zip                            [dist/InstallerX-coloros-system-installer-$VERSION.zip]
set -euo pipefail
cd "$(dirname "$0")/.."

MOD=${MOD:-module}
APKSRC=${APKSRC:-work/PackageInstaller-final.apk}
VERSION=${VERSION:-v1.2}
OUT=${OUT:-dist/InstallerX-coloros-system-installer-$VERSION.zip}
APKDST="$MOD/system/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk"

[ -f "$APKSRC" ] || { echo "missing $APKSRC -- run build/build_sysapk.sh first" >&2; exit 1; }
mkdir -p "$(dirname "$APKDST")" "$(dirname "$OUT")"
cp -f "$APKSRC" "$APKDST"

# module.prop must have LF line endings only
if LC_ALL=C grep -q $'\r' "$MOD/module.prop"; then
    echo "module.prop contains CR -- refusing to package" >&2; exit 1
fi

rm -f "$OUT"
python3 - "$MOD" "$OUT" <<'PY'
import os, sys, zipfile, hashlib

mod, out = sys.argv[1], sys.argv[2]
mode = {'module.prop': 0o644, 'customize.sh': 0o755,
        'post-fs-data.sh': 0o755, 'uninstall.sh': 0o755,
        'update-binary': 0o755, 'updater-script': 0o644,
        'OppoPackageInstaller.apk': 0o644}
# Fixed timestamp so the zip is reproducible.
STAMP = (2026, 10, 6, 12, 0, 0)

def entry(name, is_dir):
    zi = zipfile.ZipInfo(name + ('/' if is_dir else ''), date_time=STAMP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    perms = 0o755 if is_dir else mode.get(os.path.basename(name), 0o644)
    zi.external_attr = ((0o40000 if is_dir else 0o100000) | perms) << 16
    return zi

with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(mod):
        dirs.sort()
        for d in dirs:
            p = os.path.relpath(os.path.join(root, d), mod)
            z.writestr(entry(p, True), b'')
        for f in sorted(files):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, mod)
            z.writestr(entry(rel, False), open(p, 'rb').read())

sha = hashlib.sha256(open(out, 'rb').read()).hexdigest()
print(f'--- {out}: {os.path.getsize(out)} bytes  sha256 {sha} ---')
with zipfile.ZipFile(out) as z:
    for i in z.infolist():
        print(f'  {i.external_attr >> 16 & 0o777:o}  {i.file_size:>9}  {i.filename}')
    apk = z.read('system/system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk')
    print(f'  APK inside zip: {len(apk)} bytes sha256 {hashlib.sha256(apk).hexdigest()}')
PY
