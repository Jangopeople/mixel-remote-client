#!/usr/bin/env python3
"""Exact terminal Linux child-window lifecycle layer; inverse before prior branding owners."""
import argparse,contextlib,hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
PIN='b47e8385e5a75d38319ad706a64b0ead3108b093'
TREE='ee184480a0e519b9f51f7496d3d90674782481d6'
DEPENDENCY='flutter/.mixel-dependencies/desktop_multi_window'
SCRIPTS=Path(__file__).resolve().parent
PRIOR='63a7b235885fc6df8f55b9501e8e299f0500a34f2565b84326065caf699621cd'
MANAGER_SHA='e26f2c8ada368290ca80bfcdd4a54a74539306d2de3b6d6c323282f0d2124822'
ORIGINAL={'linux/flutter_window.cc': 'c738c39a61e52f5986e315db22769693edee577b2ab3c4413560c297ed59cc2a', 'linux/flutter_window.h': 'cbd47bca03fed906f204b27fa8a1af1a58339408cf42c5eb80867d7d4a60c21c'}
FINAL={'linux/flutter_window.cc': '3dcb75359bda38ed77259b36ba99c96128f40a00c75e50843a7fee2a37c65fc8', 'linux/flutter_window.h': 'f93cb6006c69b3b1df1c18ead9b1f9ac1b8d748f8edf3214a104897f9939c54a'}
CHANGES={'linux/flutter_window.cc': [('gboolean onWindowClose(GtkWidget *widget, GdkEvent *, gpointer arg)\n{\n  auto *self = static_cast<FlutterWindow *>(arg);\n  // destory hook\n  if (!self->isPreventClose)\n  {\n    if (auto channel = self->GetWindowChannel())\n    {\n      auto args = fl_value_new_map();\n      channel->InvokeMethodSelfVoid("onDestroy", args);\n    }\n    if (auto callback = self->callback_.lock())\n    {\n      callback->OnWindowClose(self->id_);\n      callback->OnWindowDestroy(self->id_);\n    }\n  }\n  else\n  {\n    _emitEvent("close", self);\n  }\n  return self->isPreventClose;\n}\n', 'gboolean onWindowClose(GtkWidget *widget, GdkEvent *, gpointer arg)\n{\n  auto *self = static_cast<FlutterWindow *>(arg);\n  // Channel and manager callbacks can destroy this window synchronously.\n  const bool preventClose = self->isPreventClose;\n  const auto id = self->id_;\n  auto callback = self->callback_.lock();\n  if (!preventClose)\n  {\n    if (auto channel = self->GetWindowChannel())\n    {\n      auto args = fl_value_new_map();\n      channel->InvokeMethodSelfVoid("onDestroy", args);\n    }\n    if (callback)\n    {\n      callback->OnWindowClose(id);\n      callback->OnWindowDestroy(id);\n    }\n  }\n  else\n  {\n    _emitEvent("close", self);\n  }\n  return preventClose;\n}\n'), ('  g_signal_add_emission_hook(\n            g_signal_lookup("button-release-event", GTK_TYPE_WIDGET), 0,\n            onMouseReleaseHook, this, NULL);', '  this->releasedEmissionHook = g_signal_add_emission_hook(\n            g_signal_lookup("button-release-event", GTK_TYPE_WIDGET), 0,\n            onMouseReleaseHook, this, NULL);'), ('  g_signal_remove_emission_hook(g_signal_lookup("button-press-event", GTK_TYPE_WIDGET), this->pressedEmissionHook);', '  g_signal_remove_emission_hook(g_signal_lookup("button-press-event", GTK_TYPE_WIDGET), this->pressedEmissionHook);\n  g_signal_remove_emission_hook(g_signal_lookup("button-release-event", GTK_TYPE_WIDGET), this->releasedEmissionHook);')], 'linux/flutter_window.h': [('  gulong pressedEmissionHook = 0;', '  gulong pressedEmissionHook = 0;\n\n  gulong releasedEmissionHook = 0;')]}

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(raw):
 text=raw.decode('utf-8');logical=text.replace('\r\n','\n')
 if '\r' in logical or ('\r\n' in text and logical.replace('\n','\r\n')!=text):raise RuntimeError('Mixed or unsupported native source newline')
 return logical,'\r\n' if '\r\n' in text else '\n'
def safe_root(root):
 for path in (root,*root.parents):
  info=path.lstat()
  if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise RuntimeError('Unsafe native source root')
def safe_file(root,name):
 path=root/name
 for parent in reversed(path.parents):
  info=parent.lstat()
  if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise RuntimeError('Unsafe native source ancestor')
 info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400 or not 0<info.st_size<=1000000:raise RuntimeError('Unsafe native source file')
 return path
def prior_namespace():
 path=safe_file(SCRIPTS,'patch-support-linux-window-close.py');text,_=canonical(path.read_bytes())
 if digest(text.encode())!=PRIOR:raise RuntimeError('Changed Linux native close prior verifier')
 namespace={'__name__':'mixel_native_close_prior','__file__':str(path)}
 exec(compile(text,str(path),'exec'),namespace)
 return namespace
def validate_prior(root,texts,state):
 prior=prior_namespace();compiler=prior['prior_namespace']()['prior']('patch-support-compiler-cleanup.py')
 # Check the actual source before copying can conceal hardlinks or reparses.
 compiler['validate_projection_sources'](root)
 if state=='final':
  with tempfile.TemporaryDirectory(prefix='mixel-native-close-prior-') as temporary:
   stage=Path(temporary).resolve()/'source'
   shutil.copytree(root,stage,symlinks=True,ignore=lambda directory,names:compiler['projection_ignored'](root,directory,names))
   for name,(_,text,newline) in texts.items():(stage/DEPENDENCY/name).write_bytes(transform(name,text,True).replace('\n',newline).encode())
   prior['apply'](stage,check=True,restore=True,verify=True)
 else:prior['apply'](root,check=True,restore=True,verify=True)
 return compiler
def transform(name,text,restore=False):
 before,after=(FINAL[name],ORIGINAL[name]) if restore else (ORIGINAL[name],FINAL[name])
 if digest(text.encode())==after:return text
 if digest(text.encode())!=before:raise RuntimeError('Changed pinned native source: '+name)
 pairs=list(reversed(CHANGES[name])) if restore else CHANGES[name]
 for old,new in pairs:
  if restore:old,new=new,old
  if text.count(old)!=1:raise RuntimeError('Changed exact native close literal')
  text=text.replace(old,new,1)
 if digest(text.encode())!=after:raise RuntimeError('Native close inverse mismatch')
 return text
def apply(root,check=False,restore=False,verify=True):
 root=Path(root).absolute();safe_root(root)
 target=root/DEPENDENCY
 if not os.path.lexists(target):
  if not restore:raise RuntimeError('Native close layer requires pinned branded dependency')
  # A fresh upstream checkout has no owned dependency until the prior Mac layer creates it.
  for parent in (root/'flutter',root/'flutter/.mixel-dependencies'):
   if os.path.lexists(parent):safe_root(parent)
  if verify:validate_prior(root,{},'fresh')
  return {'profile':'fresh','writes':0,'checkedFiles':0}
 safe_root(target)
 proof=json.loads(safe_file(target,'.mixel-source.json').read_bytes())
 expected={'commit':PIN,'tree':TREE,'original_inventory_sha256':'6eb1693729ae5132242209f2f24a5be73a2bbf72931e06d3154798c5b98b5236'}
 if proof!=expected:raise RuntimeError('Pinned native dependency provenance differs')
 manager,_=canonical(safe_file(target,'linux/multi_window_manager.cc').read_bytes())
 if digest(manager.encode())!=MANAGER_SHA:raise RuntimeError('Pinned native owner manager changed')
 texts={};hashes={}
 for name in ORIGINAL:
  path=safe_file(target,name);text,newline=canonical(path.read_bytes());texts[name]=(path,text,newline);hashes[name]=digest(text.encode())
 states=[name for name,pins in [('original',ORIGINAL),('final',FINAL)] if hashes==pins]
 if len(states)!=1:raise RuntimeError('Changed or partial native close layer')
 planned=[(path,transform(name,text,restore).replace('\n',newline).encode()) for name,(path,text,newline) in texts.items()]
 if verify:validate_prior(root,texts,states[0])
 writes=0
 for path,raw in planned:
  if not check and path.read_bytes()!=raw:path.write_bytes(raw);writes+=1
 return {'profile':states[0],'writes':writes,'checkedFiles':4}
def has_layer(root):
 target=Path(root)/DEPENDENCY
 return any((target/name).is_file() and b'releasedEmissionHook' in (target/name).read_bytes() for name in ORIGINAL)
@contextlib.contextmanager
def previous(root):
 root=Path(root).absolute()
 if not has_layer(root):yield root;return
 apply(root,check=True,restore=True)
 prior=prior_namespace();compiler=prior['prior_namespace']()['prior']('patch-support-compiler-cleanup.py')
 compiler['validate_projection_sources'](root)
 originals={name:safe_file(root/DEPENDENCY,name).read_bytes() for name in ORIGINAL}
 with tempfile.TemporaryDirectory(prefix='mixel-native-close-inverse-') as temporary:
  stage=Path(temporary).resolve()/'source'
  shutil.copytree(root,stage,symlinks=True,ignore=lambda directory,names:compiler['projection_ignored'](root,directory,names))
  apply(stage,restore=True,verify=False)
  try:yield stage
  finally:
   if any(safe_file(root/DEPENDENCY,name).read_bytes()!=raw for name,raw in originals.items()):raise RuntimeError('Native close projection changed supplied source')

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True,type=Path);parser.add_argument('--restore',action='store_true');parser.add_argument('--check',action='store_true');parser.add_argument('--before-branding',action='store_true');parser.add_argument('--after-branding',action='store_true');args=parser.parse_args()
 if args.before_branding and not(args.restore and args.check):parser.error('--before-branding requires --restore --check')
 if args.after_branding and args.restore:parser.error('--after-branding cannot restore')
 print(apply(args.root,args.check,args.restore,True))
if __name__=='__main__':main()
