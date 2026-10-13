#!/usr/bin/env python3
"""Prove pinned remote desktop/file-transfer geometry, preserved modes and source ownership."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mixel_file_transfer_geometry', ROOT/'scripts/patch-support-file-transfer-window.py')
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
analysis_spec=importlib.util.spec_from_file_location("mixel_final_source_fixture",ROOT/"scripts/test-support-flutter-analysis.py")
A=importlib.util.module_from_spec(analysis_spec);analysis_spec.loader.exec_module(A)


def sha(data): return hashlib.sha256(data).hexdigest()


def block(text, marker):
    start = text.index(marker); opening = (text.index(' async {', start) + len(' async ')) if 'Future<' in marker else text.index('{', start); depth = 1
    for end in range(opening + 1, len(text)):
        depth += (text[end] == '{') - (text[end] == '}')
        if depth == 0: return text[start:end+1]
    raise RuntimeError('Incomplete pinned geometry source')


def snapshot(repo):
    return {p.relative_to(repo).as_posix(): ('symlink',os.readlink(p)) if p.is_symlink() else ('file',sha(p.read_bytes()),p.stat().st_nlink) if p.is_file() else ('directory',) for p in repo.rglob('*')}


def capture_source(app, out):
    with A.startup_baseline(app) as plain:
        if plain != app.resolve():
            return capture_source(plain, out)
    P.apply(app.resolve(), restore=True, check=True)
    P.verify_previous_layers(app.resolve())
    result = {}
    for name, expected in P.COMPOSED.items():
        data = P.transform(name, (app/name).read_bytes(), restore=True); logical,_ = P.normalize(data)
        assert sha(logical) == expected, name
        path = out/'pinned-composed'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        result[name] = logical
    return result


def ownership_controls(data, out):
    report = {'roundtrips':0,'refusals':0}
    for newline in (b'\n', b'\r\n'):
        with tempfile.TemporaryDirectory(prefix='mixel-file-transfer-') as temporary:
            repo = Path(temporary).resolve()
            for name, value in data.items():
                path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value.replace(b'\n',newline))
            before=snapshot(repo)
            with contextlib.redirect_stdout(io.StringIO()):
                P.apply(repo,check=True);assert snapshot(repo)==before
                P.apply(repo);corrected=snapshot(repo)
                P.apply(repo);P.apply(repo,check=True);assert snapshot(repo)==corrected
                P.apply(repo,restore=True,check=True);assert snapshot(repo)==corrected
                P.apply(repo,restore=True);assert snapshot(repo)==before
                P.apply(repo);P.apply(repo,restore=True);assert snapshot(repo)==before
            report['roundtrips']+=1
            for fault in ('source-drift','mixed','partial','generated-foreign','generated-missing','generated-drift','source-hardlink','generated-hardlink','source-symlink','generated-symlink','parent-symlink'):
                with contextlib.redirect_stdout(io.StringIO()):P.apply(repo)
                target=repo/P.COMMON; generated=repo/P.GENERATED
                external=repo/'external';external.write_bytes(b'owned test sentinel\n')
                if fault=='source-drift':target.write_bytes(target.read_bytes()+b'// drift'+newline)
                elif fault=='mixed':target.write_bytes(target.read_bytes().replace(newline,b'\r',1))
                elif fault=='partial':target.write_bytes(P.transform(P.COMMON,target.read_bytes(),True))
                elif fault=='generated-foreign':generated.write_bytes(b'foreign\n')
                elif fault=='generated-missing':generated.unlink()
                elif fault=='generated-drift':generated.write_bytes(generated.read_bytes()+b'// drift\n')
                elif fault=='source-hardlink':os.link(target,repo/'hardlink')
                elif fault=='generated-hardlink':os.link(generated,repo/'hardlink')
                elif fault=='source-symlink':target.unlink();target.symlink_to(external)
                elif fault=='generated-symlink':generated.unlink();generated.symlink_to(external)
                elif fault=='parent-symlink':
                    target.parent.rename(repo/'saved-parent');target.parent.symlink_to(repo/'saved-parent',target_is_directory=True)
                failed=snapshot(repo)
                for restore,check in ((False,False),(True,False),(True,True),(False,True)):
                    try:P.apply(repo,restore,check)
                    except (RuntimeError,OSError,UnicodeError):report['refusals']+=1
                    else:raise AssertionError('Accepted geometry ownership '+fault)
                    assert snapshot(repo)==failed, fault
                # This is only our disposable fault fixture; remove it wholly.
                for item in list(repo.iterdir()):
                    if item.is_symlink() or item.is_file():item.unlink()
                    else:shutil.rmtree(item)
                for name,value in data.items():
                    path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value.replace(b'\n',newline))
    (out/'ownership-controls.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('PASS: LF/CRLF read-only/apply/idempotence/restore; '+str(report['refusals'])+' zero-write ownership/drift refusals')


DART = r'''import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import '../lib/mixel_file_transfer_geometry.dart';
const channel = MethodChannel('flutter/windowsize');
Map<String,Object> screen(Rect rect,double scale) => {'frame':[rect.left,rect.top,rect.width,rect.height], 'visibleFrame':[rect.left,rect.top,rect.width,rect.height], 'scaleFactor':scale};
void main() {
 TestWidgetsFlutterBinding.ensureInitialized();
 const work = Rect.fromLTWH(0,24,1100,736);
 test('original first transfer width exceeds actual1100 workarea; fixed initial fits',(){
  const original=Rect.fromLTWH(0,0,1300,740);
  expect(original.width>work.width,isTrue);
  const observedNativeWork=Rect.fromLTWH(0,0,1100,800);
  expect(fitMixelFileTransferFrame(original,[observedNativeWork],center:true),const Rect.fromLTWH(0,30,1100,740));
  expect(fitMixelFileTransferFrame(original,[work],center:true),const Rect.fromLTWH(0,24,1100,736));
 });
 test('fitting saved user frame remains exact; stale partial visibility clamps',(){
  const fitting=Rect.fromLTWH(80,80,900,600);
  expect(fitMixelFileTransferFrame(fitting,[work]),fitting);
  const partial=Rect.fromLTWH(1090,750,1280,720);
  expect(partial.intersect(work).width,10);expect(partial.intersect(work).height,10);
  expect(fitMixelFileTransferFrame(partial,[work]),const Rect.fromLTWH(0,40,1100,720));
 });
 test('negative origin, taskbar, monitor change and disconnected monitor',(){
  const negative=Rect.fromLTWH(-1920,-100,1920,1040);
  const desired=Rect.fromLTWH(-1900,-80,1200,800);
  expect(fitMixelFileTransferFrame(desired,[negative,work]),desired);
  expect(fitMixelFileTransferFrame(const Rect.fromLTWH(4000,100,900,500),[negative,work],preferredArea:work),const Rect.fromLTWH(200,100,900,500));
  expect(fitMixelFileTransferFrame(const Rect.fromLTWH(-500,0,900,900),[negative]),const Rect.fromLTWH(-900,0,900,900));
 });
 test('empty/invalid work areas never invent a screen or divide geometry by scale',(){
  const fitting=Rect.fromLTWH(80,80,900,600);
  expect(fitMixelFileTransferFrame(fitting,[]),fitting);
  expect(fitMixelFileTransferFrame(fitting,[Rect.zero,const Rect.fromLTWH(double.nan,0,100,100)]),fitting);
  expect(fitMixelFileTransferFrame(const Rect.fromLTWH(double.infinity,0,100,100),[work],center:true),const Rect.fromLTWH(0,32,1100,720));
 });
 test('actual pinned plugin channel retains reported visibleFrame coordinates at scale2',() async {
  final calls=<String>[];
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call) async {
   calls.add(call.method);
   if(call.method=='getScreenList')return [screen(work,2)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':2.0,'screen':screen(work,2)};
   throw StateError('Unexpected native write');
  });
  expect(await mixelFileTransferWindowFrame(const Rect.fromLTWH(0,0,1300,740),center:true),const Rect.fromLTWH(0,24,1100,736));
  expect(calls,['getScreenList','getWindowInfo']);
 });
 test('missing current screen and unavailable display are explicit fallbacks',() async {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call) async {
   if(call.method=='getScreenList')return [screen(work,1)];
   throw PlatformException(code:'No Screen');
  });
  expect(await mixelFileTransferWindowFrame(const Rect.fromLTWH(80,80,900,600)),const Rect.fromLTWH(80,80,900,600));
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call) async=>throw PlatformException(code:'No Screen'));
  expect(await mixelFileTransferWindowFrame(const Rect.fromLTWH(80,80,900,600)),const Rect.fromLTWH(80,80,900,600));
 });
 /* EXTRACTED_TESTS */
}
'''


STUBS = r'''
bool isWindows=false,isMacOS=false,isDesktop=true,isWebDesktop=false,isLinux=true;
const kMobileDefaultDisplayWidth=720,kMobileDefaultDisplayHeight=1280,kDesktopMaxDisplaySize=6480,kMobileMaxDisplaySize=1280,kNewWindowOffset=20,kMainWindowId=0;
int? kWindowId=1;
const windowFramePrefix='window_';
const kWindowEventSetFullscreen='fullscreen';
bool _ignoreDevicePixelRatio=false;
final bind=Binding();
class Binding {
 String option='',peerOption=''; String mainGetEnv({required String key})=>'';
 String mainGetPeerFlutterOptionSync({required String id,required String k})=>peerOption;
 String getLocalFlutterOption({required String k})=>option;
 bool isIncomingOnly()=>false;bool isOutgoingOnly()=>false;
}
class FullState {final events=<bool>[];void setFullscreen(bool value){events.add(value);}}
final stateGlobal=FullState();
class MainManager {
 Future<void> center()async=>throw StateError('Main window touched');
 Future<void> maximize()async=>throw StateError('Main window touched');
 Future<void> setPosition(Offset p,{required bool ignoreDevicePixelRatio})async=>throw StateError('Main window touched');
 Future<void> setSize(Size s,{required bool ignoreDevicePixelRatio})async=>throw StateError('Main window touched');
}
final windowManager=MainManager();
class Controller {
 Rect frame=const Rect.fromLTWH(0,0,1300,740);
 final events=<String>[];
 void setInitBackgroundColor(Color c){events.add('color');}
 Future<void> setFrame(Rect value)async {frame=value;events.add('frame');}
 Future<void> center()async{events.add('center');}
 void setTitle(String title){events.add('title');}
 Future<void> show()async{events.add('show');}
 Future<void> maximize()async{events.add('maximize');}
 Future<Rect> getFrame()async{frameReads.add(1);return frame;}
 int get windowId=>1;
}
class MainController extends Controller {
 @override Future<Rect> getFrame()async{frameReads.add(0);return frame;}
}
class WindowController { static Controller fromWindowId(int id)=>id==0?mainController:controller; }
final frameReads=<int>[];
final mainController=MainController()..frame=const Rect.fromLTWH(0,0,800,600);
final controller=Controller();
class DesktopMultiWindow {
 static Future<Controller> createWindow(String message)async=>controller;
 static Future<dynamic> invokeMethod(int id,String method,dynamic arguments)async=>null;
}
String getWindowNameWithId(String id,{required WindowType overrideType})=>'QA';
void registerActiveWindow(int id){}
bool mixelApplicationExitStarted=false;
Future<void> mixelRunCleanupSteps(List<Future<void> Function()> steps)async { for(final step in steps){await step();} }
class Creation {
 void _mixelAssertCanOpen(){}
 final _mixelPendingCreations=<Future<void>>[];
 bool _mixelClosingSubWindows=false;
 final _mixelExitPendingWindowIds=<int>{};
 Future<void> _mixelPrepareWindowClose(int id)async{}
 Future<void> _mixelForceCloseWindow(int id)async{}
 /* EXTRACTED_CREATION */
}
/* EXTRACTED_POSITION */
/* EXTRACTED_SIZE */
/* EXTRACTED_OFFSET */
/* EXTRACTED_RESTORE */
'''
EXTRACTED_TESTS = r'''
 test('literal initial creation preserves other window types and fits transfer and remote desktop',() async {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(work,1)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':1.0,'screen':screen(work,1)};
   throw StateError('Unexpected native method');
  });
  final creation=Creation();
  for(final type in [WindowType.ViewCamera,WindowType.PortForward,WindowType.Terminal]) {
   controller.events.clear();
   await creation.newSessionWindow(type,'QA','',[],false);
   expect(controller.frame,const Rect.fromLTWH(0,0,1300,740));expect(controller.events,['frame','center','title']);
  }
  controller.events.clear();await creation.newSessionWindow(WindowType.FileTransfer,'QA','',[],false);
  expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));expect(controller.events,['frame','title']);
  controller.frame=const Rect.fromLTWH(-100,10,1600,900);controller.events.clear();
  await creation.newSessionWindow(WindowType.FileTransfer,'QA','',[],true);
  expect(controller.frame,const Rect.fromLTWH(-100,10,1600,900));expect(controller.events,['title']);
 });
 test('literal restore default/saved/reconnect preserves every fitting choice',()async {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(work,1)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':1.0,'screen':screen(work,1)};
   throw StateError('Unexpected native method');
  });
  bind.option='';controller.frame=const Rect.fromLTWH(0,0,1300,740);controller.events.clear();
  expect(await restoreWindowPosition(WindowType.FileTransfer,windowId:1),isTrue);
  expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));
  bind.option=jsonEncode({'width':900.0,'height':600.0,'offsetWidth':80.0,'offsetHeight':80.0,'isMaximized':false,'isFullscreen':false});
  controller.events.clear();await restoreWindowPosition(WindowType.FileTransfer,windowId:1);
  expect(controller.frame,const Rect.fromLTWH(80,80,900,600));
  await restoreWindowPosition(WindowType.FileTransfer,windowId:1);expect(controller.frame,const Rect.fromLTWH(80,80,900,600));
  bind.option=jsonEncode({'width':1280.0,'height':720.0,'offsetWidth':1090.0,'offsetHeight':750.0,'isMaximized':false,'isFullscreen':false});
  await restoreWindowPosition(WindowType.FileTransfer,windowId:1);expect(controller.frame,const Rect.fromLTWH(0,40,1100,720));
  bind.option=jsonEncode({'width':900.0,'height':600.0,'offsetWidth':null,'offsetHeight':null,'isMaximized':false,'isFullscreen':false});
  await restoreWindowPosition(WindowType.FileTransfer,windowId:1);expect(controller.frame,const Rect.fromLTWH(100,92,900,600));
  bind.option='';controller.frame=const Rect.fromLTWH(0,0,1300,740);controller.events.clear();
  await restoreWindowPosition(WindowType.ViewCamera,windowId:1);expect(controller.frame,const Rect.fromLTWH(0,0,1300,740));expect(controller.events,isEmpty);
 });
 test('literal remote initial creation fits Linux Mac and Windows; explicit screen rect is preserved',()async {
  final calls=<String>[];
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   calls.add(call.method);
   if(call.method=='getScreenList')return [screen(work,2)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':2.0,'screen':screen(work,2)};
   throw StateError('Unexpected native method');
  });
  final creation=Creation();kWindowId=null;
  for(final platform in ['linux','macos','windows']) {
   isMacOS=platform=='macos';isWindows=platform=='windows';isLinux=platform=='linux';
   controller.events.clear();calls.clear();
   await creation.newSessionWindow(WindowType.RemoteDesktop,'QA','',[],false);
   await Future<void>.delayed(Duration.zero);
   expect(controller.frame,const Rect.fromLTWH(0,24,1100,736),reason:platform);
   expect(controller.events,[if(isWindows)'color','frame','title',if(isMacOS)'show']);
   expect(calls,['getScreenList','getWindowInfo']);
   controller.frame=const Rect.fromLTWH(-1920,-100,1920,1080);controller.events.clear();calls.clear();
   await creation.newSessionWindow(WindowType.RemoteDesktop,'QA','',[],true);
   await Future<void>.delayed(Duration.zero);
   expect(controller.frame,const Rect.fromLTWH(-1920,-100,1920,1080));
   expect(controller.events,[if(isWindows)'color','title',if(isMacOS)'show']);expect(calls,isEmpty);
  }
  isMacOS=false;isWindows=false;isLinux=true;kWindowId=1;
 });
 test('literal remote restore keeps peer precedence, local cascade and coordinates across monitors',()async {
  var screens=[work];Rect current=work;
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return screens.map((rect)=>screen(rect,2)).toList();
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':2.0,'screen':screen(current,2)};
   throw StateError('Unexpected native method');
  });
  String saved(double? left,double? top,double width,double height)=>jsonEncode({'width':width,'height':height,'offsetWidth':left,'offsetHeight':top,'isMaximized':false,'isFullscreen':false});
  bind.option='';bind.peerOption='';controller.frame=const Rect.fromLTWH(0,40,1300,740);controller.events.clear();
  expect(await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA'),isTrue);
  expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));
  bind.option=saved(60,60,900,600);
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1);expect(controller.frame,const Rect.fromLTWH(80,80,900,600));
  bind.peerOption=saved(80,80,900,600);
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(80,80,900,600));
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(80,80,900,600));
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA',display:1);expect(controller.frame,const Rect.fromLTWH(100,100,900,600));
  bind.peerOption=saved(1090,750,1280,720);
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(0,40,1100,720));
  bind.peerOption=saved(null,null,900,600);
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(100,92,900,600));
  const negative=Rect.fromLTWH(-1920,-100,1920,1040);screens=[negative,work];current=negative;
  bind.peerOption=saved(-1900,-80,1200,800);
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(-1900,-80,1200,800));
  screens=[work];current=work;
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));
  bind.option='';bind.peerOption='';
 });
 test('literal remote restore preserves saved maximize and fullscreen modes on Linux and Mac',()async {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(work,1)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,0.0,800.0,600.0],'scaleFactor':1.0,'screen':screen(work,1)};
   throw StateError('Unexpected native method');
  });
  bind.peerOption=jsonEncode({'width':1300.0,'height':740.0,'offsetWidth':0.0,'offsetHeight':40.0,'isMaximized':true,'isFullscreen':false});
  controller.events.clear();stateGlobal.events.clear();
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');
  await Future<void>.delayed(const Duration(milliseconds:350));
  expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));expect(controller.events,['frame','maximize']);expect(stateGlobal.events,isEmpty);
  bind.peerOption=jsonEncode({'width':1300.0,'height':740.0,'offsetWidth':0.0,'offsetHeight':40.0,'isMaximized':false,'isFullscreen':true});
  for(final platform in ['linux','macos']) {
   isMacOS=platform=='macos';isLinux=platform=='linux';
   controller.frame=const Rect.fromLTWH(-1920,-100,1920,1080);controller.events.clear();stateGlobal.events.clear();
   await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');
   await Future<void>.delayed(const Duration(milliseconds:350));
   expect(stateGlobal.events,[true]);
   expect(controller.events,isMacOS?isEmpty:['frame']);
   expect(controller.frame,isMacOS?const Rect.fromLTWH(-1920,-100,1920,1080):const Rect.fromLTWH(0,24,1100,736));
  }
  isMacOS=false;isLinux=true;bind.option='';bind.peerOption='';
 });

 test('Mac boundary calibrates same native frame without changing saved coordinates or scale',()async {
  const globalWork=Rect.fromLTWH(0,824,1100,736);
  const globalFrame=Rect.fromLTWH(0,800,800,600);
  var reads=0;
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(globalWork,2)];
   if(call.method=='getWindowInfo')return {'frame':[globalFrame.left,globalFrame.top,globalFrame.width,globalFrame.height],'scaleFactor':2.0,'screen':screen(globalWork,2)};
   throw StateError('Unexpected native write');
  });
  Future<Rect> nativeFrame()async{reads++;return const Rect.fromLTWH(0,0,800,600);}
  expect(await mixelFileTransferWindowFrame(const Rect.fromLTWH(0,0,1300,740),center:true,nativeWindowFrame:nativeFrame),const Rect.fromLTWH(0,24,1100,736));
  const fitting=Rect.fromLTWH(80,80,900,600);
  expect(await mixelFileTransferWindowFrame(fitting,nativeWindowFrame:nativeFrame),fitting);expect(reads,2);
 });
 test('Mac missing, changed-size, wrong-window and invalid observations preserve valid requested frame',()async {
  const globalWork=Rect.fromLTWH(0,824,1100,736);
  const desired=Rect.fromLTWH(0,0,1300,740);
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(globalWork,1)];
   if(call.method=='getWindowInfo')return {'frame':[0.0,800.0,800.0,600.0],'scaleFactor':1.0,'screen':screen(globalWork,1)};
   throw StateError('Unexpected native write');
  });
  for(final observed in [const Rect.fromLTWH(0,0,700,600),const Rect.fromLTWH(50,0,800,600),const Rect.fromLTWH(0,0,800,500),Rect.zero,const Rect.fromLTWH(0,double.nan,800,600)]) {
   expect(await mixelFileTransferWindowFrame(desired,center:true,nativeWindowFrame:()async=>observed),desired);
  }
  expect(await mixelFileTransferWindowFrame(desired,center:true,nativeWindowFrame:()async=>throw StateError('No native frame')),desired);
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(globalWork,1)];
   throw PlatformException(code:'No current frame');
  });
  expect(await mixelFileTransferWindowFrame(desired,center:true,nativeWindowFrame:()async=>const Rect.fromLTWH(0,0,800,600)),desired);
 });
 test('literal Mac creation and inactive reuse calibrate current engine; explicit-screen path stays exact',()async {
  const globalWork=Rect.fromLTWH(0,824,1100,736);
  var reported=const Rect.fromLTWH(0,800,800,600);
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,(call)async{
   if(call.method=='getScreenList')return [screen(globalWork,2)];
   if(call.method=='getWindowInfo')return {'frame':[reported.left,reported.top,reported.width,reported.height],'scaleFactor':2.0,'screen':screen(globalWork,2)};
   throw StateError('Unexpected native write');
  });
  isMacOS=true;isLinux=false;isWindows=false;kWindowId=null;bind.option='';bind.peerOption='';
  mainController.frame=const Rect.fromLTWH(0,0,800,600);frameReads.clear();controller.events.clear();
  await Creation().newSessionWindow(WindowType.RemoteDesktop,'QA','',[],false);
  await Future<void>.delayed(Duration.zero);
  expect(controller.frame,const Rect.fromLTWH(0,24,1100,736));expect(frameReads,[0]);
  controller.frame=const Rect.fromLTWH(-1920,-100,1920,1080);frameReads.clear();controller.events.clear();
  await Creation().newSessionWindow(WindowType.RemoteDesktop,'QA','',[],true);
  await Future<void>.delayed(Duration.zero);
  expect(controller.frame,const Rect.fromLTWH(-1920,-100,1920,1080));expect(frameReads,isEmpty);expect(controller.events,['title','show']);
  controller.frame=const Rect.fromLTWH(50,-70,900,600);frameReads.clear();
  bind.peerOption=jsonEncode({'width':900.0,'height':600.0,'offsetWidth':80.0,'offsetHeight':80.0,'isMaximized':false,'isFullscreen':false});
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');
  expect(controller.frame,const Rect.fromLTWH(80,80,900,600));expect(frameReads,[0]);
  kWindowId=1;reported=const Rect.fromLTWH(80,880,900,600);frameReads.clear();
  await restoreWindowPosition(WindowType.RemoteDesktop,windowId:1,peerId:'QA');
  expect(controller.frame,const Rect.fromLTWH(80,80,900,600));expect(frameReads,[1]);
  isMacOS=false;isLinux=true;kWindowId=1;bind.option='';bind.peerOption='';
 });

'''


def flutter_controls(data, out, flutter):
    app=out/'actual-dart-contract';(app/'lib').mkdir(parents=True);(app/'test').mkdir()
    (app/'pubspec.yaml').write_text('name: mixel_file_transfer_workarea_controls\nenvironment:\n  sdk: ">=3.6.0 <4.0.0"\ndependencies:\n  flutter:\n    sdk: flutter\n  window_size:\n    git:\n      url: https://github.com/google/flutter-desktop-embedding\n      path: plugins/window_size\n      ref: eb3964990cf19629c89ff8cb4a37640c7b3d5601\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n',encoding='utf-8')
    (app/'lib/mixel_file_transfer_geometry.dart').write_text(P.DART,encoding='utf-8')
    fixed={name:P.transform(name,value).decode() for name,value in data.items()}
    common=fixed[P.COMMON];manager=fixed[P.MANAGER]
    stub=STUBS.replace('/* EXTRACTED_CREATION */',block(manager,'  Future<int> newSessionWindow(')).replace('/* EXTRACTED_POSITION */',block(common,'class LastWindowPosition {')).replace('/* EXTRACTED_SIZE */',block(common,'Future<Size> _adjustRestoreMainWindowSize(')).replace('/* EXTRACTED_OFFSET */',block(common,'Future<Offset?> _adjustRestoreMainWindowOffset(')).replace('/* EXTRACTED_RESTORE */',block(common,'Future<bool> restoreWindowPosition('))
    enum=block(manager,'enum WindowType {')
    test=DART.replace('/* EXTRACTED_TESTS */',EXTRACTED_TESTS)
    test=test.replace("import '../lib/mixel_file_transfer_geometry.dart';","import '../lib/mixel_file_transfer_geometry.dart';\nimport 'package:window_size/window_size.dart' as window_size;")+'\n'+enum+'\n'+stub
    (app/'test/geometry_test.dart').write_text(test,encoding='utf-8')
    env=dict(os.environ,CI='true',FLUTTER_SUPPRESS_ANALYTICS='true',DART_SUPPRESS_ANALYTICS='true',PYTHONIOENCODING='utf-8')
    for label,args in [('pub-get',[flutter,'pub','get']),('analyze',[flutter,'analyze','--no-pub','--no-fatal-infos']),('test',[flutter,'test','--no-pub','--reporter','expanded'])]:
        r=subprocess.run(list(map(str,args)),cwd=app,env=env,capture_output=True,text=True,encoding='utf-8',timeout=240)
        (out/('actual-dart-'+label+'.log')).write_text(r.stdout+r.stderr,encoding='utf-8')
        assert r.returncode==0,label+': '+r.stdout+r.stderr
    print('PASS: actual Flutter compiled literal initial/restored branches, remote initial Linux/Mac/Windows branches, saved peer/local cascade, fullscreen/maximize, all preserved window types and fourteen geometry tests with same-engine Mac boundary calibration')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--flutter',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    data=capture_source(args.app,args.output);ownership_controls(data,args.output)
    if args.flutter:flutter_controls(data,args.output,args.flutter.resolve())


if __name__=='__main__':main()
