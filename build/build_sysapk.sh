#!/usr/bin/env bash
# Build the drop-in replacement for
#   /system_ext/priv-app/OppoPackageInstaller/OppoPackageInstaller.apk
#
# The output keeps:
#   * InstallerX's code and resources (only AndroidManifest.xml and the 256-byte
#     package-name field of resources.arsc are changed)
#   * the STOCK system APK's APK Signing Block, so that PMS reads exactly the same
#     certificates it already has cached for com.android.packageinstaller
#   * the stock versionCode/versionName, so no version change is recorded either
#
# Inputs (override through the environment):
#   SRC          STOCK upstream InstallerX Revived APK  [work/upstream/PackageInstaller.apk]
#   DONOR        stock APK from YOUR OWN device         [work/OppoPackageInstaller.apk]
#   JAVA         java 21 binary                         [java]
#   APKTOOL      apktool jar                            [apktool.jar]
#   SIGNER       uber-apk-signer jar (zipalign only)    [signer.jar]
#   DEC          decoded apktool tree                   [work/sysdec]
#   VERSION_CODE / VERSION_NAME  must match packages.xml on the target device
set -euo pipefail
cd "$(dirname "$0")/.."
HERE=$(cd "$(dirname "$0")" && pwd)

WORK=${WORK:-work}
SRC=${SRC:-$WORK/upstream/PackageInstaller.apk}
DONOR=${DONOR:-$WORK/OppoPackageInstaller.apk}
JAVA=${JAVA:-java}
APKTOOL=${APKTOOL:-apktool.jar}
SIGNER=${SIGNER:-signer.jar}
DEC=${DEC:-$WORK/sysdec}
JHOMEDIR=${JHOMEDIR:-$PWD/$WORK/home}
VERSION_CODE=${VERSION_CODE:-17000001}
VERSION_NAME=${VERSION_NAME:-17.0.1}
TARGET_PACKAGE=${TARGET_PACKAGE:-com.android.packageinstaller}

for f in "$SRC" "$DONOR"; do
    [ -f "$f" ] || { echo "missing input: $f  -- see build/README.md" >&2; exit 1; }
done

mkdir -p "$WORK"

# 0) decode the upstream APK (first run only; later runs reuse the tree)
if [ ! -d "$DEC" ]; then
    "$JAVA" -Duser.home="$JHOMEDIR" -jar "$APKTOOL" d -f -p "$WORK/fw" -o "$DEC" "$SRC"
fi

# 1) manifest patch: package rename + 4 dropped permissions + 4 filterless aliases
#    + narrowed VIEW filter.  patch_sys.py is idempotent, so it is always safe to run.
TARGET_PACKAGE="$TARGET_PACKAGE" python3 "$HERE/patch_sys.py" "$DEC/AndroidManifest.xml"

# 1b) the same package name also lives in resources.arsc, as a fixed 256-byte char16
#     field.  Patch it in the ORIGINAL table -- a byte patch, so nothing else in the
#     file moves -- and splice that in step 4.  apktool's rebuilt arsc is discarded,
#     which keeps resources byte-identical to upstream apart from this one field.
python3 "$HERE/patch_arsc.py" "$SRC" "$WORK/resources-patched.arsc" "$TARGET_PACKAGE"

# 2) versionCode/versionName: apktool stores these in apktool.yml, NOT in the decoded
#    AndroidManifest.xml.  They must equal what packages.xml already records for
#    com.android.packageinstaller, otherwise a version change gets recorded.
#    Check yours with:  su -c 'dumpsys package com.android.packageinstaller | grep -m1 version'
VERSION_CODE="$VERSION_CODE" VERSION_NAME="$VERSION_NAME" DEC="$DEC" python3 - <<'PY'
import os, re
p = os.path.join(os.environ['DEC'], 'apktool.yml')
vc, vn = os.environ['VERSION_CODE'], os.environ['VERSION_NAME']
s = open(p).read()
s = re.sub(r'(\n  versionCode: )\S+', r'\g<1>' + vc, s, count=1)
s = re.sub(r'(\n  versionName: )\S+', r'\g<1>' + vn, s, count=1)
open(p, 'w').write(s)
assert f'\n  versionCode: {vc}' in s, 'versionCode not applied'
assert f'\n  versionName: {vn}' in s, 'versionName not applied'
print(f'apktool.yml -> versionCode {vc} / versionName {vn}')
PY
grep -n "versionCode\|versionName" "$DEC/apktool.yml"

# 3) rebuild the manifest.  apktool 3.x caches its previous output in $DEC/build/ and
#    does NOT invalidate that cache when apktool.yml changes, so the stale build dir
#    must be dropped or the versionCode/versionName edit above is silently ignored.
rm -rf "$WORK/sysbuilt.apk" "$DEC/build"
"$JAVA" -Duser.home="$JHOMEDIR" -jar "$APKTOOL" b -p "$WORK/fw" -o "$WORK/sysbuilt.apk" "$DEC"

# 4) splice the rebuilt AndroidManifest.xml and the byte-patched resources.arsc back
#    into the real InstallerX APK.  Every other entry -- dex, res/, assets -- is
#    copied through untouched.
SRC="$SRC" WORK="$WORK" python3 - <<'PY'
import os, zipfile
work = os.environ['WORK']
src, built = os.environ['SRC'], os.path.join(work, 'sysbuilt.apk')
out = os.path.join(work, 'sysminimal.apk')
repl = {
    'AndroidManifest.xml': zipfile.ZipFile(built).read('AndroidManifest.xml'),
    'resources.arsc': open(os.path.join(work, 'resources-patched.arsc'), 'rb').read(),
}
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, 'w') as zout:
    for item in zin.infolist():
        data = repl[item.filename] if item.filename in repl else zin.read(item.filename)
        zout.writestr(item, data)
print(f'spliced {len(repl["AndroidManifest.xml"])}-byte manifest + '
      f'{len(repl["resources.arsc"])}-byte resources.arsc -> {out}')
PY

# 5) zipalign, by way of uber-apk-signer.  The signature produced here is thrown away
#    in step 6; this step exists only so that lib/*.so lands 4096-aligned, which
#    extractNativeLibs=false requires.
#
#    KNOWN LIMITATION: uber-apk-signer only aligns to 4 bytes, so the .so entries come
#    out 4096- but not 16384-aligned.  That is fine on a 4 KB page device and NOT
#    sufficient for a 16 KB page device.  Fixing it means aligning the zip yourself.
rm -rf "$WORK/sysout" && mkdir -p "$WORK/sysout"
"$JAVA" -Duser.home="$JHOMEDIR" -jar "$SIGNER" \
    -a "$WORK/sysminimal.apk" -o "$WORK/sysout" --allowResign

# 6) graft the STOCK system APK's APK Signing Block in place of ours.  This is what
#    keeps the signer certificates identical to what the device already recorded.
python3 "$HERE/graftsig.py" \
    "$WORK/sysout/sysminimal-aligned-debugSigned.apk" \
    "$DONOR" \
    "$WORK/PackageInstaller-final.apk"
