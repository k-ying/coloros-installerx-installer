#!/system/bin/sh
# The replacement APK lives inside this module, so removing the module restores the
# original OppoPackageInstaller.apk on the next boot; nothing else was modified.
# Drop the parse cache so PackageManagerService re-reads the original APK.
if [ -d /data/system/package_cache ]; then
    rm -rf /data/system/package_cache/* 2>/dev/null
fi

exit 0
