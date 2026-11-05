#!/usr/bin/env python3
"""Build and package the isolated native QA app. No installer/store/production writes."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if sys.platform!='darwin':parser.error('macOS is required')
    if not output.is_relative_to((ROOT/'_scratch').resolve()) or output==(ROOT/'_scratch').resolve():parser.error('Choose a new evidence directory inside this repo _scratch')
    output.mkdir(parents=True,exist_ok=False)
    def run(argv,name):
        with (output/name).open('w') as log:subprocess.run(argv,cwd=ROOT/'next',stdout=log,stderr=subprocess.STDOUT,check=True)
    run(['cargo','build','-p','desktop_app','--release','--locked'],'build.log')
    app=output/'Mixel-Remote.app';mac=app/'Contents/MacOS';mac.mkdir(parents=True)
    source=ROOT/'next/target/release/desktop_app';binary=mac/'Mixel-Remote';shutil.copy2(source,binary)
    info={'CFBundleName':'Mixel Remote','CFBundleDisplayName':'Mixel Remote','CFBundleIdentifier':'ch.mixel.remote.next.qa','CFBundleExecutable':'Mixel-Remote','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'2','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication'}
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run(['codesign','--force','--sign','-',str(app)],'sign.log')
    run(['codesign','--verify','--deep','--strict',str(app)],'signature-verify.log')
    result={'buildQualified':True,'runtimeQualified':False,'app':str(app),'sourceBinarySha256':hashlib.sha256(source.read_bytes()).hexdigest(),'mainSha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'developmentAdHocSigned':True,'storeSigned':False,'notarized':False}
    (output/'package.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: native release QA app built; local development signature verified; runtime qualification remains separate')
if __name__=='__main__':main()
