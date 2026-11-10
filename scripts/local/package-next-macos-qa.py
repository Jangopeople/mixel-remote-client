#!/usr/bin/env python3
"""Build and package the isolated native QA app. No installer/store/production writes."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[2]
def public_service_configuration(path):
    data=path.read_bytes()
    if len(data)>32768:raise ValueError('Service configuration exceeds size limit')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate service configuration field')
            result[key]=value
        return result
    value=json.loads(data,object_pairs_hook=unique)
    required={'schema','identity_url','public_key','signaling_url','authority_url','authority_keys'}
    if not isinstance(value,dict) or not required.issubset(value) or set(value)-required-{'oauth_provider','sso_domain','relay_only','customer_signup'}:raise ValueError('Unsupported service configuration fields')
    if type(value['schema']) is not int or value['schema']!=2:raise ValueError('Unsupported configuration schema')
    for name,scheme in [('identity_url','https'),('signaling_url','wss'),('authority_url','https')]:
        raw=value[name]
        if not isinstance(raw,str) or len(raw)>2048 or any(ord(character)<=32 or ord(character)==127 for character in raw):raise ValueError('Invalid service URL')
        url=urlsplit(raw)
        if url.port==0 or url.scheme!=scheme or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment or (name!='signaling_url' and url.path not in ('','/')):raise ValueError('Invalid secure service URL')
    roots=value['authority_keys']
    if not isinstance(roots,list) or not 1<=len(roots)<=4 or any(not isinstance(root,str) or not re.fullmatch(r'[A-Fa-f0-9]{64}',root) for root in roots) or len({root.lower() for root in roots})!=len(roots):raise ValueError('Invalid public identity authority keys')
    # The native loader additionally rejects invalid/weak Ed25519 points. Never
    # accept private seed/key fields in this publicly distributed configuration.
    key=value['public_key']
    if not isinstance(key,str) or not key or len(key)>16384 or any(ord(character)<33 or ord(character)>126 for character in key):raise ValueError('Invalid public API key')
    public=key.startswith('sb_publishable_')
    if not public:
        try:
            parts=key.split('.')
            public=len(parts)==3 and all(parts) and json.loads(base64.urlsafe_b64decode(parts[1]+'==='))['role']=='anon'
        except (ValueError,KeyError,TypeError):public=False
    if not public:raise ValueError('Only a public anonymous or publishable API key may be bundled')
    provider=value.get('oauth_provider','keycloak')
    if not isinstance(provider,str) or not re.fullmatch(r'[a-z0-9_-]{1,80}',provider):raise ValueError('Invalid sign-in provider')
    domain=value.get('sso_domain')
    if domain is not None and (not isinstance(domain,str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',domain)):raise ValueError('Invalid SSO domain')
    if any(type(value.get(flag,False)) is not bool for flag in ('relay_only','customer_signup')):raise ValueError('Invalid relay policy')
    return data
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--signing-identity',default='-',help='Existing local Developer ID identity, or - for ad-hoc QA')
    parser.add_argument('--service-config',type=Path,help='Public identity/WSS configuration to include inside the signed app; never a service-role key')
    parser.add_argument('--universal',action='store_true',help='Build both macOS architectures and combine with lipo before signing')
    parser.add_argument('--qa-controller',action='store_true',help='Package a second Mac QA instance with its own fixed Keychain identity namespace')
    args=parser.parse_args()
    output=args.output.resolve()
    if sys.platform!='darwin':parser.error('macOS is required')
    if not output.is_relative_to((ROOT/'_scratch').resolve()) or output==(ROOT/'_scratch').resolve():parser.error('Choose a new evidence directory inside this repo _scratch')
    try:configuration=public_service_configuration(args.service_config) if args.service_config else None
    except (OSError,ValueError,TypeError):parser.error('Service configuration is invalid or contains a non-public key; nothing was packaged')
    icons={size:(ROOT/f'branding/icon-{size}.png').read_bytes() for size in [16,32,64,128,256,512,1024]}
    output.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('workspace_qa',ROOT/'scripts/local/test-next-workspace.py')
    qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)
    before=qa.inventory()
    def run(argv,name):
        environment=dict(os.environ,MACOSX_DEPLOYMENT_TARGET='13.0')
        with (output/name).open('w') as log:subprocess.run(argv,cwd=ROOT/'next',env=environment,stdout=log,stderr=subprocess.STDOUT,check=True)
    # Tauri embeds frontendDist. Rebuild it from checked sources instead of
    # accidentally signing an older generated UI left by a previous run.
    run(['pnpm','--dir',str(ROOT/'next/frontends/desktop'),'run','build'],'frontend-build.log')
    if args.universal:
        slices=[]
        for target in ['aarch64-apple-darwin','x86_64-apple-darwin']:
            run(['cargo','build','-p','desktop_app','--release','--locked','--target',target],f'build-{target}.log')
            slices.append(ROOT/'next/target'/target/'release/desktop_app')
        source=output/'Mixel-Remote-universal'
        run(['lipo','-create',*[str(path) for path in slices],'-output',str(source)],'universal-build.log')
        run(['lipo','-archs',str(source)],'universal-verify.log')
        architectures=(output/'universal-verify.log').read_text().split()
        if len(architectures)!=2 or set(architectures)!={'arm64','x86_64'}:raise RuntimeError('Universal executable is missing a required Mac architecture')
    else:
        run(['cargo','build','-p','desktop_app','--release','--locked'],'build.log')
        source=ROOT/'next/target/release/desktop_app'
    if configuration is not None:
        with (output/'configuration-verify.log').open('w') as log:
            subprocess.run([str(source),'--validate-public-config'],input=configuration,stdout=log,stderr=subprocess.STDOUT,timeout=10,check=True)
    app=output/'Mixel-Remote.app';mac=app/'Contents/MacOS';mac.mkdir(parents=True)
    resources=app/'Contents/Resources';resources.mkdir()
    iconset=output/'Mixel-Remote.iconset';iconset.mkdir()
    for size in [16,32,128,256,512]:
        (iconset/f'icon_{size}x{size}.png').write_bytes(icons[size])
        (iconset/f'icon_{size}x{size}@2x.png').write_bytes(icons[size*2])
    run(['iconutil','-c','icns',str(iconset),'-o',str(resources/'Mixel-Remote.icns')],'icon-build.log')
    if configuration is not None:(resources/'service-config.json').write_bytes(configuration)
    binary=mac/'Mixel-Remote';shutil.copy2(source,binary)
    info={'CFBundleName':'Mixel Remote','CFBundleDisplayName':'Mixel Remote','CFBundleIdentifier':'ch.mixel.remote.next.qa','CFBundleExecutable':'Mixel-Remote','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'2','CFBundleIconFile':'Mixel-Remote.icns','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication'}
    if args.qa_controller:info['CFBundleIdentifier']='ch.mixel.remote.next.qa.controller'
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    sign=['codesign','--force','--sign',args.signing_identity]
    if args.signing_identity!='-':sign.extend(['--options','runtime','--timestamp'])
    run(sign+[str(app)],'sign.log')
    run(['codesign','--verify','--all-architectures','--deep','--strict',str(app)],'signature-verify.log')
    run(['codesign','--display','--verbose=4',str(app)],'signature-display.log')
    signature=(output/'signature-display.log').read_text()
    if f"Identifier={info['CFBundleIdentifier']}\n" not in signature:raise RuntimeError('Signed bundle identity does not match the packaged app')
    run([str(binary),'--validate-identity-scope'],'identity-scope-verify.log')
    scope='ch.mixel.remote.next.qa'+('.controller' if args.qa_controller else '')+'.device-identity.v1'
    if (output/'identity-scope-verify.log').read_text().strip()!=f'PASS: {scope}':raise RuntimeError('Native Keychain namespace does not match the packaged QA instance')
    after=qa.inventory()
    if before!=after:raise RuntimeError('Source changed during packaging; app is not qualified')
    if configuration is not None and args.service_config.read_bytes()!=configuration:raise RuntimeError('Public configuration changed during packaging')
    if any((ROOT/f'branding/icon-{size}.png').read_bytes()!=data for size,data in icons.items()):raise RuntimeError('Branding changed during packaging')
    result={'sourceUnchanged':True,'sourceSha256':after,'developerIDSigned':'Authority=Developer ID Application:' in signature,'buildQualified':True,'runtimeQualified':False,'app':str(app),'sourceBinarySha256':hashlib.sha256(source.read_bytes()).hexdigest(),'mainSha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'developmentAdHocSigned':args.signing_identity=='-','storeSigned':False,'notarized':False,'serviceConfigured':configuration is not None,'serviceConfigurationSha256':hashlib.sha256(configuration).hexdigest() if configuration is not None else None,'iconSourceSha256':{str(size):hashlib.sha256(data).hexdigest() for size,data in icons.items()},'iconSha256':hashlib.sha256((resources/'Mixel-Remote.icns').read_bytes()).hexdigest()}
    result['universal']=args.universal
    result['bundleId']=info['CFBundleIdentifier']
    result['qaController']=args.qa_controller
    (output/'package.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: native release QA app built; local development signature verified; runtime qualification remains separate')
if __name__=='__main__':main()
