"""Patch the decoded yreidev manifest into our system-installer manifest.

Three edits, all idempotent (safe to run repeatedly on the same tree):

  1. drop the two permissions that are plausibly `signature|privileged` on this
     ROM but absent from the stock installer's request set (== the ROM's proven
     allowlist surface). Everything else requested is either proven by the stock
     OPPO installer or a normal/dangerous/unknown permission.

  2. add four FILTERLESS, exported activity-aliases mirroring the component
     names of the stock installer that other apps launch explicitly.

     Filterless is deliberate: `getRequiredInstallerLPr()` demands exactly one
     SYSTEM component matching ACTION_INSTALL_PACKAGE + DEFAULT + content/apk.
     The base APK's InstallerActivity already matches that, so the aliases must
     not add a second matching component. Explicit `cmp=` callers need only the
     component to exist and be exported -- they do not consult intent filters.

  3. stop InstallerX from swallowing every "open this file" intent.

     Its stock filter #1 is

         actions  VIEW, INSTALL_PACKAGE
         scheme   content, file
         mime     application/vnd.android.package-archive
         mime     */*            <-- greedy

     As an ordinary data app that is harmless, but this APK is about to become
     the *system* installer, and IntentResolver orders candidates by
     `priority` first, specificity never. At priority=10 the `*/*` alternative
     would beat every normal handler (a PDF viewer, a photo viewer, ...) for
     ANY content:// or file:// VIEW intent, so tapping a downloaded PDF would
     open the installer.

     Fix: drop `*/*` from that filter and add a second filter that mirrors
     filter #2 of the stock installer -- action INSTALL_PACKAGE (only), schemes
     content/file, and *no* mimeType. Install requests whose MIME is unusual or
     missing are still caught, while plain file-opening intents are not.

     Boot-safety of the added filter, without needing to know whether
     IntentResolver dedupes a component's filters:
       * if a scheme-only filter DOES match a typed intent, then the stock
         installer (which has exactly that filter, alongside a mime-typed one)
         already produces two matches and its device boots anyway -- so the
         resolver counts components, not filters, and one more filter is free;
       * if it does NOT match, the filter adds no match at all.
     Either way the self-check still sees the single component
     com.rosan.installer.ui.activity.InstallerActivity.
"""
import os
import re
import sys

# Path to the decoded manifest; override with argv[1] or $MANIFEST.
MANIFEST = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get('MANIFEST', 'work/sysdec/AndroidManifest.xml'))

DROP_PERMISSIONS = [
    'android.permission.POST_PROMOTED_NOTIFICATIONS',
    'android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION',
    # Third-party APIs.  ro.control_privapp_permissions=enforce only rejects
    # permissions whose protectionLevel carries PROTECTION_FLAG_PRIVILEGED, and
    # both of these are declared `signature` by Shizuku / Dhizuku -- but their
    # protection level lives in *those* apps' manifests, so it cannot be proven
    # from this ROM.  A privileged system installer holding INSTALL_PACKAGES has
    # no need for a Shizuku/Dhizuku backend, so the certainty is worth more than
    # the feature: dropping them leaves our request set = stock's + 6 normal
    # permissions (FOREGROUND_SERVICE_SPECIAL_USE, REQUEST_DELETE_PACKAGES,
    # REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, USE_BIOMETRIC, USE_FINGERPRINT,
    # VIBRATE), every one of which is protectionLevel="normal".
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

GREEDY_FILTER = '''\
            <intent-filter android:priority="10">
                <action android:name="android.intent.action.VIEW"/>
                <action android:name="android.intent.action.INSTALL_PACKAGE"/>
                <category android:name="android.intent.category.DEFAULT"/>
                <data android:scheme="content"/>
                <data android:scheme="file"/>
                <data android:mimeType="application/vnd.android.package-archive"/>
                <data android:pathPattern=".*"/>
                <data android:mimeType="*/*"/>
            </intent-filter>
'''

REPLACEMENT_FILTERS = '''\
            <intent-filter android:priority="10">
                <action android:name="android.intent.action.VIEW"/>
                <action android:name="android.intent.action.INSTALL_PACKAGE"/>
                <category android:name="android.intent.category.DEFAULT"/>
                <data android:scheme="content"/>
                <data android:scheme="file"/>
                <data android:mimeType="application/vnd.android.package-archive"/>
                <data android:pathPattern=".*"/>
            </intent-filter>
            <intent-filter android:priority="10">
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
    if REPLACEMENT_FILTERS in src:
        print('[0 replaced] greedy VIEW filter already narrowed')
        return src
    if GREEDY_FILTER not in src:
        sys.exit('could not find the greedy InstallerActivity filter to replace')
    print('[1 replaced] VIEW /*/* narrowed + INSTALL_PACKAGE only filter added')
    return src.replace(GREEDY_FILTER, REPLACEMENT_FILTERS, 1)


def main():
    src = open(MANIFEST, encoding='utf-8').read()
    src = drop_permissions(src)
    src = insert_aliases(src)
    src = narrow_greedy_filter(src)
    open(MANIFEST, 'w', encoding='utf-8').write(src)
    print(f'wrote {MANIFEST}')


main()
