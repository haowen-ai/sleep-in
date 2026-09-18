#!/usr/bin/env python3
"""Build an Apple silicon preview app; does not launch/register it."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess


def build(destination):
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
           'NSHumanReadableCopyright':'Sleep In — local development preview, not notarized.'}
    with (contents/'Info.plist').open('wb') as stream: plistlib.dump(plist,stream)
    subprocess.run(['/usr/bin/swiftc','-target','arm64-apple-macos13.0','-framework','AppKit','-framework','ServiceManagement',
                    str(root/'packaging/SleepIn.swift'),'-o',str(executable/'SleepIn')],check=True)
    # Ad-hoc signing is local integrity only, NOT Developer ID signing/notarization.
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(destination)],check=True)
    subprocess.run([str(executable/'SleepIn'),'--self-test'],check=True)
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('destination')
    print(build(parser.parse_args().destination))
