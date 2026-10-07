"""Patch the decoded upstream manifest into our system-installer manifest.

Four edits, all idempotent (safe to run repeatedly on the same tree):

  1. rename the package to TARGET_PACKAGE.

     The upstream APK ships as `com.rosan.installer.x.revived`; a system
     installer has to be `com.android.packageinstaller`.  Renaming is safe as a
     plain string replacement here because the *class* names live under
     `com.rosan.installer.*` and never under the package name itself -- in the
     26.09 manifest the package string occurs in exactly six places, all of them
     self-references (the `package` attribute, the DYNAMIC_RECEIVER permission
     declaration and its use, and three provider authorities).

     The matching rename inside resources.arsc is done separately by
     patch_arsc.py, because that one is a binary field, not text.

  2. drop the four permissions that are plausibly `signature|privileged` on this
     ROM but absent from the stock installer's request set (== the ROM's proven
     allowlist surface).  Two of them belong to Shizuku / Dhizuku; a privileged
     system installer holding INSTALL_PACKAGES has no need for those backends,
     so dropping them buys certainty at no real cost.

  3. add four FILTERLESS, exported activity-aliases mirroring the component
     names of the stock installer that other apps launch explicitly.

     Filterless is deliberate: `getRequiredInstallerLPr()` demands exactly one
     SYSTEM component matching ACTION_INSTALL_PACKAGE + DEFAULT + content/apk.
     The base APK's InstallerActivity already matches that, so the aliases must
     not add a second matching component.  Explicit `cmp=` callers need only the
     component to exist and be exported -- they do not consult intent filters.

  4. stop InstallerX from being a candidate for every "open this file" intent.

     InstallerActivity's first intent-filter carries

         mime     application/vnd.android.package-archive
         mime     */*            <-- greedy

     As an ordinary data app that is harmless.  As the *system* installer it
     means every `content://` or `file://` VIEW intent has an extra candidate,
     so the `*/*` alternative is dropped while the APK-typed alternative is
     kept.  Install requests whose MIME is unusual or missing would then no
     longer match, so a second filter mirroring the stock installer's -- action
     INSTALL_PACKAGE only, content/file schemes, *no* mimeType -- is added.

     The added filters keep `android:priority="10"`, matching what the previous
     verified build shipped, so implicit APK opens behave as before rather than
     merely as upstream happens to behave.  With `*/*` gone the priority is no
     longer dangerous: the filter only matches the APK MIME type.

     Boot safety of the added filter, without needing to know whether
     IntentResolver dedupes a component's filters:
       * if a scheme-only filter DOES match a typed intent, then the stock
         installer (which has exactly that filter, alongside a mime-typed one)
         already produces two matches and its device boots anyway -- so the
         resolver counts components, not filters, and one more filter is free;
       * if it does NOT match, the filter adds no match at all.
     Either way the self-check still sees the single component
     com.rosan.installer.ui.activity.InstallerActivity.

Reads/writes work/sysdec/AndroidManifest.xml by default; override with argv[1]
or $MANIFEST.  The target package name comes from $TARGET_PACKAGE.
"""
import os
import re
import sys

# Path to the decoded manifest; override with argv[1] or $MANIFEST.
MANIFEST = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get('MANIFEST', 'work/sysdec/AndroidManifest.xml'))

TARGET_PACKAGE = os.environ.get('TARGET_PACKAGE', 'com.android.packageinstaller')

DROP_PERMISSIONS = [
    'android.permission.POST_PROMOTED_NOTIFICATIONS',
    'android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION',
    # Third-party APIs.  ro.control_privapp_permissions=enforce only rejects
    # permissions whose protectionLevel carries PROTECTION_FLAG_PRIVILEGED, and
    # both of these are declared `signature` by Shizuku / Dhizuku -- but their
    # protection level lives in *those* apps' manifests, so it cannot be proven
    # from this ROM.  Dropping them leaves our request set = stock's + 6 normal
    # permissions, every one of which is protectionLevel="normal".
    'com.rosan.dhizuku.permission.API',
    'moe.shizuku.manager.permission.API_V23',
]

# name -> target activity in the same package
ALIASES = [
    ('com.android.packageinstaller.InstallStart',
     'com.rosan.installer.ui.activity.InstallerActivity'),
    ('com.android.packageinstaller.UninstallerActivity',
     'com.rosan.installer.ui.activity.UninstallerActivity'),
    ('com.android.packageinstaller.UnarchiveActivity',
     'com.rosan.installer.ui.activity.InstallerActivity'),
    ('com.android.packageinstaller.UnarchiveErrorActivity',
     'com.rosan.installer.ui.activity.InstallerActivity'),
]

INSTALLER_TAG = 'android:name="com.rosan.installer.ui.activity.InstallerActivity"'
GREEDY_MIME_LINE = '                <data android:mimeType="*/*"/>\n'
PLAIN_FILTER_OPEN = '            <intent-filter>\n'
PRIORITY_FILTER_OPEN = '            <intent-filter android:priority="10">\n'

# Mirrors the stock installer's second filter: INSTALL_PACKAGE only, no mimeType.
ADDED_FILTER = '''            <intent-filter android:priority="10">
                <action android:name="android.intent.action.INSTALL_PACKAGE"/>
                <category android:name="android.intent.category.DEFAULT"/>
                <data android:scheme="content"/>
                <data android:scheme="file"/>
            </intent-filter>
'''

rm_re = re.compile(
    r'^\s*<uses-permission\s+android:name="(?:' +
    '|'.join(re.escape(p) for p in DROP_PERMISSIONS) +
    r')"\s*/>\s*$')


def rename_package(src):
    m = re.search(r'<manifest[^>]*?\spackage="([^"]+)"', src)
    if not m:
        sys.exit('could not find the manifest package attribute')
    old = m.group(1)
    if old == TARGET_PACKAGE:
        print(f'[0 renamed] package is already {TARGET_PACKAGE}')
        return src
    count = src.count(old)
    src = src.replace(old, TARGET_PACKAGE)
    if old in src:
        sys.exit(f'package name still present after rename: {old}')
    print(f'[1 renamed] {old} -> {TARGET_PACKAGE} ({count} occurrence(s))')
    return src


def drop_permissions(src):
    kept, dropped = [], []
    for line in src.split('\n'):
        if rm_re.match(line):
            dropped.append(line.strip())
            continue
        kept.append(line)
    src = '\n'.join(kept)
    still_there = [p for p in DROP_PERMISSIONS if p in src]
    if still_there:
        sys.exit(f'permission(s) still present after edit: {still_there}')
    print(f'[{len(dropped)} removed this run] now absent: '
          + ', '.join(p.rsplit(".", 1)[-1] for p in DROP_PERMISSIONS))
    return src


def insert_aliases(src):
    missing = [(n, t) for n, t in ALIASES if f'android:name="{n}"' not in src]
    if not missing:
        print(f'[0 inserted] all {len(ALIASES)} aliases already present')
        return src

    anchor = 'android:name="com.rosan.installer.ui.activity.UninstallerActivity"'
    at = src.index(anchor)
    close = src.index('</activity>', at) + len('</activity>')
    block = ''.join(
        f'\n        <activity-alias android:enabled="true" android:exported="true"'
        f' android:name="{name}" android:targetActivity="{target}"/>'
        for name, target in missing)
    print(f'[{len(missing)} inserted] aliases: '
          + ', '.join(n for n, _ in missing))
    return src[:close] + block + src[close:]


def narrow_greedy_filter(src):
    """Drop `*/*` from InstallerActivity's first filter and add the extra one."""
    if ADDED_FILTER in src:
        print('[0 replaced] greedy VIEW filter already narrowed')
        return src

    try:
        at = src.index(INSTALLER_TAG)
        fs = src.index('<intent-filter', at)
        fe = src.index('</intent-filter>', fs) + len('</intent-filter>')
    except ValueError:
        sys.exit('could not locate InstallerActivity and its first intent-filter')

    block = src[fs:fe]
    if '*/*' not in block:
        sys.exit('InstallerActivity\'s first filter has no */* -- upstream changed; '
                 're-read the manifest before patching')

    block = block.replace(GREEDY_MIME_LINE, '', 1)
    if block.startswith(PLAIN_FILTER_OPEN):
        block = block.replace(PLAIN_FILTER_OPEN, PRIORITY_FILTER_OPEN, 1)

    print('[1 replaced] VIEW */* dropped + INSTALL_PACKAGE-only filter added')
    return src[:fs] + block + '\n' + ADDED_FILTER + src[fe:]


def main():
    src = open(MANIFEST, encoding='utf-8').read()
    src = rename_package(src)
    src = drop_permissions(src)
    src = insert_aliases(src)
    src = narrow_greedy_filter(src)
    open(MANIFEST, 'w', encoding='utf-8').write(src)
    print(f'wrote {MANIFEST}')


main()
