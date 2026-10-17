#!/usr/bin/env python3
"""Exact terminal same-token Windows custody layer after Windows custodian."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

HERE = Path(__file__).absolute().parent
PLAN = HERE / 'support-receiver-custodian-plan.json'
PLAN_SHA = 'f60924e6318b12a2247d45b7b138cde93c007182e5be4b0551481323fac37eac'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    logical = text.replace('\r\n', '\n')
    newline = '\r\n' if '\r\n' in text else '\n'
    if '\r' in logical or logical.replace('\n', newline) != text:
        raise ValueError('Mixed Windows custodian source newlines')
    return logical, newline


def root_path(root):
    root = Path(root).absolute()
    if 'worktrees' in root.parts or root.resolve(strict=True) != root:
        raise ValueError('Ordinary canonical Windows custodian source required')
    for part in (root, *root.parents):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked Windows custodian source ancestry')
    return root


def read(root, name, absent=False):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or any(p in ('.', '..', 'worktrees') for p in parts):
        raise ValueError('Unsafe Windows custodian target name')
    path = root
    for index, part in enumerate(parts):
        path /= part
        try:
            info = path.lstat()
        except FileNotFoundError:
            if absent and index == len(parts) - 1:
                return path, None, None
            raise ValueError('Missing Windows custodian source: ' + name) from None
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Linked Windows custodian source parent')
        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
              or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('Unsafe Windows custodian source: ' + name)
    raw = path.read_bytes()
    after = path.lstat()
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode, s.st_nlink)
    if fields(info) != fields(after):
        raise ValueError('Windows custodian source changed while reading')
    return path, raw, stat.S_IMODE(info.st_mode)


def unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError('Duplicate Windows custodian plan key')
        value[name] = item
    return value


def plan():
    _, raw, _ = read(HERE, PLAN.name)
    logical, _ = canonical(raw)
    if sha(logical.encode()) != PLAN_SHA:
        raise ValueError('Custodian plan pin drift')
    value = json.loads(logical, object_pairs_hook=unique)
    if value['schema'] != 'mixel-receiver-Windows-custodian-v1' or len(value['files']) != 5:
        raise ValueError('Custodian complete plan shape changed')
    names = [row['name'] for row in value['files']] + [row['name'] for row in value['owned']]
    if len(set(names)) != 7 or set(value['readOnlyProfiles']) != {'fresh','prior','preUinput'}:
        raise ValueError('Custodian complete prefix set changed')
    templates={}
    for owned in value['owned']:
        _,raw,_=read(HERE,owned['template']);raw=canonical(raw)[0].encode()
        if sha(raw)!=owned['sha256']: raise ValueError('Custodian complete leaf drift')
        templates[owned['name']]=raw
    for row in value['files']:
        if sha(row['beforeSource'].encode()) != row['beforeSha256'] or sha(row['afterSource'].encode()) != row['afterSha256']:
            raise ValueError('Custodian exact source body pin drift')
    return value, templates


def inspect(root):
    root = root_path(root)
    value, template = plan()
    profiles = {'before','after',*value['readOnlyProfiles']}
    current = {}
    for row in value['files']:
        item = read(root,row['name'],True); current[row['name']] = item
        if item[1] is not None and item[2] != (0o666 if os.name=='nt' else row['mode']):
            raise ValueError('Custodian inherited source mode changed')
        digest = None if item[1] is None else sha(canonical(item[1])[0].encode())
        def expected(profile):
            return row[profile+'Sha256'] if profile in ('before','after') else value['readOnlyProfiles'][profile][row['name']]
        profiles &= {profile for profile in profiles if digest==expected(profile)}
    for owned in value['owned']:
        item=read(root,owned['name'],True);current[owned['name']]=item
        if item[1] is None: profiles.discard('after')
        elif sha(canonical(item[1])[0].encode())==owned['sha256'] and item[2]==(0o666 if os.name=='nt' else owned['mode']): profiles &= {'after'}
        else: raise ValueError('Custodian owned source changed')
    if len(profiles)!=1: raise ValueError('Changed, mixed or partial custodian source')
    return root,value,template,profiles.pop(),current


def transform(raw,row,restore=False):
    text,newline=canonical(raw)
    left,right=('afterSha256','beforeSha256') if restore else ('beforeSha256','afterSha256')
    if sha(text.encode())!=row[left]: raise ValueError('Custodian whole source input changed')
    for pair in reversed(row['changes']) if restore else row['changes']:
        old,new=(pair['after'],pair['before']) if restore else (pair['before'],pair['after'])
        offset=pair['offset']
        if text[offset:offset+len(old)]!=old or (old and text.count(old)!=1):
            raise ValueError('Custodian exact hunk coordinate/cardinality changed')
        text=text[:offset]+new+text[offset+len(old):]
    if sha(text.encode())!=row[right]: raise ValueError('Custodian whole source inverse changed')
    return text.replace('\n',newline).encode()

def recheck(root, value, current):
    for name, old in current.items():
        if read(root, name, old[1] is None)[1:] != old[1:]:
            raise ValueError('Custodian destination changed before mutation')


def atomic(path, raw, mode):
    fd, temporary = tempfile.mkstemp(prefix='.mixel-custodian-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root, restore=False, check=False):
    root, value, template, profile, current = inspect(root)
    if profile in value['readOnlyProfiles']:
        if not restore:
            raise ValueError('Custodian requires complete predecessor branding')
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    wanted = 'before' if restore else 'after'
    if profile == wanted:
        return {'profile': profile, 'writes': 0, 'files': len(current)}
    output = {row['name']: transform(current[row['name']][1], row, restore) for row in value['files'] if row['changes']}
    owners={row['name']:row for row in value['owned']}
    newline=canonical(current['src/core_main.rs'][1])[1]
    for name in owners:
        output[name]=None if restore else template[name].decode().replace('\n',newline).encode()
    recheck(root, value, current)
    if check:
        return {'profile': profile, 'plannedProfile': wanted, 'writes': 0, 'plannedWrites': len(output)}
    for name, raw in output.items():
        _, old, mode = current[name]
        if read(root, name, old is None)[1:] != (old, mode):
            raise ValueError('Custodian target changed during commit')
        if raw is None:
            (root / name).unlink()
        else:
            atomic(root / name, raw, owners[name]['mode'] if mode is None else mode)
    if inspect(root)[3] != wanted:
        raise ValueError('Custodian did not produce complete output')
    return {'profile': wanted, 'writes': len(output), 'files': len(current)}


def is_current(root):
    return inspect(root)[3]=='after'


@contextlib.contextmanager
def previous(root):
    root,value,_,profile,current=inspect(root)
    # Only a finite private copy is changed; caller source is rechecked afterward.
    with tempfile.TemporaryDirectory(prefix='mixel-custodian-prior-') as temporary:
        stage=Path(temporary).resolve()/'source'
        def ignored(directory,names):
            relative=Path(directory).relative_to(root).as_posix()
            result={name for name in names if name.casefold()=='worktrees'}
            if relative=='.': result.update(set(names)&{'build','target','.dart_tool','__pycache__'})
            elif relative=='flutter': result.update(set(names)&{'build','.dart_tool'})
            elif relative in ('flutter/macos/Flutter','flutter/windows/flutter','flutter/linux/flutter'):
                result.update(set(names)&{'ephemeral'})
            return result
        shutil.copytree(root,stage,symlinks=True,ignore=ignored)
        if profile=='after': apply(stage,restore=True)
        recheck(root,value,current)
        try: yield stage
        finally: recheck(root,value,current)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--restore',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    print(json.dumps(apply(args.root,args.restore,args.check),sort_keys=True))


if __name__=='__main__': main()
