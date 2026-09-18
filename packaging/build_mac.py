#!/usr/bin/env python3
"""Build an Apple silicon preview app; does not launch/register it."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess


def build(destination,identity=None,notary_profile=None):
    if notary_profile and not identity:raise ValueError('Notarization requires a Developer ID signing identity')
    if identity and not identity.startswith('Developer ID Application:'):raise ValueError('Use a Developer ID Application identity')
    root=Path(__file__).resolve().parents[1]
    destination=Path(destination).resolve()
    if destination.exists(): raise ValueError('Choose a new output path; refusing to overwrite an app')
    contents=destination/'Contents'; executable=contents/'MacOS'; resources=contents/'Resources'/'app'
    executable.mkdir(parents=True); resources.mkdir(parents=True)
    for filename in ('launch-mac.command','requirements.txt','LICENSE'):
        source=root/filename
        if source.exists(): shutil.copy2(source,resources/filename)
    shutil.copytree(root/'taskconsole',resources/'taskconsole',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    plist={'CFBundleIdentifier':'local.sleepin.companion','CFBundleName':'Sleep In','CFBundleDisplayName':'Sleep In',
           'CFBundleExecutable':'SleepIn','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.2.0',
           'CFBundleVersion':'2','LSMinimumSystemVersion':'13.0','LSUIElement':True,
           'NSHumanReadableCopyright':'Sleep In' if identity else 'Sleep In — local development preview, not notarized.'}
    with (contents/'Info.plist').open('wb') as stream: plistlib.dump(plist,stream)
    subprocess.run(['/usr/bin/swiftc','-target','arm64-apple-macos13.0','-framework','AppKit','-framework','ServiceManagement',
                    str(root/'packaging/SleepIn.swift'),'-o',str(executable/'SleepIn')],check=True)
    # Ad-hoc signing is local integrity only, NOT Developer ID signing/notarization.
    sign=['/usr/bin/codesign','--force','--sign',identity or '-']
    if identity:sign+=['--options','runtime','--timestamp']
    subprocess.run([*sign,str(destination)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(destination)],check=True)
    if notary_profile:
        archive=destination.with_suffix('.notarization.zip')
        subprocess.run(['/usr/bin/ditto','-c','-k','--keepParent',str(destination),str(archive)],check=True)
        subprocess.run(['/usr/bin/xcrun','notarytool','submit',str(archive),'--keychain-profile',notary_profile,'--wait'],check=True)
        subprocess.run(['/usr/bin/xcrun','stapler','staple',str(destination)],check=True)
        subprocess.run(['/usr/bin/xcrun','stapler','validate',str(destination)],check=True)
        subprocess.run(['/usr/sbin/spctl','--assess','--type','execute',str(destination)],check=True)
    subprocess.run([str(executable/'SleepIn'),'--self-test'],check=True)
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('destination')
    parser.add_argument('--identity',help='Developer ID Application signing identity')
    parser.add_argument('--notary-profile',help='Existing notarytool keychain profile; never pass credentials on command line')
    args=parser.parse_args();print(build(args.destination,args.identity,args.notary_profile))
