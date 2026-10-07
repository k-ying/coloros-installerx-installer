#!/system/bin/sh
# KernelSU runs this BEFORE the metamodule mounts the modules, i.e. long before
# PackageManagerService scans packages, so this is the only correct place for it.
#
# /data/system/package_cache holds cached ParsedPackage records keyed partly by the
# APK's mtime.  The replacement APK is a different size, so a stale record for
# com.android.packageinstaller must not survive into the scan.
# This directory is a pure cache and PackageManagerService recreates it.
if [ -d /data/system/package_cache ]; then
    rm -rf /data/system/package_cache/* 2>/dev/null
fi

exit 0
