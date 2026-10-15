#!/usr/bin/env python3
"""Pinned exact native close/destructor sanitizer regressions; only owned fixture children."""
import argparse,hashlib,importlib.util,json,os,shutil,signal,subprocess,tempfile,unittest
from pathlib import Path
BASE=Path(__file__).absolute().parent
spec=importlib.util.spec_from_file_location('native_close',BASE/'patch-support-linux-child-window-close.py');patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
INPUT=None
PROOF=None
SOURCE=None
OUTPUT=None

def extract(text,signature):
 start=text.index(signature);brace=text.index('{',start);level=1;end=brace+1
 while level:
  level += (text[end]=='{')-(text[end]=='}');end+=1
 return text[start:end]

def cpp(cc,header):
 close=extract(cc,'gboolean onWindowClose(');destroy=extract(cc,'FlutterWindow::~FlutterWindow()')
 hooks=cc[cc.index('  this->pressedEmissionHook = g_signal_add_emission_hook('):cc.index('\n  gtk_widget_grab_focus',cc.index('  this->pressedEmissionHook = g_signal_add_emission_hook('))]
 release='  gulong releasedEmissionHook = 0;' if '  gulong releasedEmissionHook = 0;' in header else ''
 return r'''
#include <cassert>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <vector>
#include <algorithm>
using gboolean=bool;using gpointer=void*;using gulong=unsigned long;
struct GtkWidget{};struct GdkEvent{};struct FlValue{};
constexpr int GTK_TYPE_WIDGET=0;
struct FlutterWindow;
static std::vector<std::string> events;
static std::unique_ptr<FlutterWindow> window;
static std::unique_ptr<FlutterWindow> other;
static std::string scenario;
static GtkWidget widget;
struct Hook{gulong id;int signal;void(*function)(void*);void* owner;};
static std::vector<Hook> hooks;
static gulong nextHook=0;
int g_signal_lookup(const char* name,int){return std::string(name)=="button-press-event"?1:2;}
gulong g_signal_add_emission_hook(int signal,int,void(*function)(void*),void*owner,void*){auto id=++nextHook;hooks.push_back({id,signal,function,owner});return id;}
void g_signal_remove_emission_hook(int signal,gulong id){auto at=std::find_if(hooks.begin(),hooks.end(),[&](const Hook& h){return h.signal==signal&&h.id==id;});assert(at!=hooks.end());hooks.erase(at);}
void gtk_widget_destroy(GtkWidget*){events.push_back("widget-destroy");}
FlValue* fl_value_new_map(){static FlValue value;return &value;}
void onMousePressHook(void*);void onMouseReleaseHook(void*);
struct WindowChannel{void InvokeMethodSelfVoid(const char*,FlValue*);};
struct FlutterWindowCallback{virtual void OnWindowClose(int64_t)=0;virtual void OnWindowDestroy(int64_t)=0;virtual ~FlutterWindowCallback()=default;};
struct FlutterWindow{
 std::weak_ptr<FlutterWindowCallback> callback_;
 int64_t id_=42;
 GtkWidget*window_=&widget;
 std::unique_ptr<WindowChannel> window_channel_=std::make_unique<WindowChannel>();
 bool isPreventClose=false;
 gulong pressedEmissionHook=0;
''' + release + r'''
 ~FlutterWindow();
 WindowChannel*GetWindowChannel(){return window_channel_.get();}
 void InstallHooks(){
''' + hooks + r'''
 }
};
void onMousePressHook(void*p){events.push_back("press:"+std::to_string(static_cast<FlutterWindow*>(p)->id_));}
void onMouseReleaseHook(void*p){events.push_back("release:"+std::to_string(static_cast<FlutterWindow*>(p)->id_));}
void emit(int signal){auto snapshot=hooks;for(const auto&h:snapshot)if(h.signal==signal)h.function(h.owner);}
void WindowChannel::InvokeMethodSelfVoid(const char*name,FlValue*){events.push_back(std::string("channel:")+name);if(scenario=="channel-destroys")window.reset();}
struct Callback:FlutterWindowCallback{
 void OnWindowClose(int64_t id)override{events.push_back("close:"+std::to_string(id));if(scenario=="close-destroys")window.reset();}
 void OnWindowDestroy(int64_t id)override{events.push_back("destroy:"+std::to_string(id));if(scenario!="retained"&&scenario!="weak-expired")window.reset();}
};
void _emitEvent(const char*event,FlutterWindow*self){events.push_back(std::string("event:")+event+":"+std::to_string(self->id_));if(scenario=="prevented-destroys")window.reset();else if(scenario=="prevented-changes-decision")self->isPreventClose=false;}
''' + destroy + '\n' + close + r'''
int main(int argc,char**argv){
 assert(argc==2);scenario=argv[1];auto cb=std::make_shared<Callback>();
 window=std::make_unique<FlutterWindow>();window->callback_=cb;window->InstallHooks();
 other=std::make_unique<FlutterWindow>();other->id_=99;other->InstallHooks();
 const bool prevented=scenario.rfind("prevented",0)==0;window->isPreventClose=prevented;
 if(scenario=="weak-expired")cb.reset();
 auto*original=window.get();bool result=onWindowClose(&widget,nullptr,original);
 assert(result==prevented);
 if(prevented){assert(events[0]=="event:close:42");assert(std::none_of(events.begin(),events.end(),[](const auto&e){return e.rfind("close:",0)==0||e.rfind("destroy:",0)==0;}));}
 else{
  assert(events[0]=="channel:onDestroy");
  if(scenario!="weak-expired"){
   auto closeAt=std::find(events.begin(),events.end(),"close:42"),destroyAt=std::find(events.begin(),events.end(),"destroy:42");
   assert(closeAt!=events.end()&&destroyAt!=events.end()&&closeAt<destroyAt);
  }
 }
 if(window)window.reset();
 // A closed child must unregister only its own press and release hooks.
 assert(hooks.size()==2);
 emit(1);emit(2);assert(events[events.size()-2]=="press:99"&&events.back()=="release:99");
 other.reset();assert(hooks.empty());
 emit(1);emit(2);
 std::cout<<"PASS "<<scenario<<" exact close/destructor, callback order, captured return, owned hook cleanup\n";
}
'''

class NativeCloseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  patch.apply(SOURCE,check=True,restore=True,verify=False)
  cls.cc,_=patch.canonical((INPUT/'flutter_window.cc').read_bytes());cls.h,_=patch.canonical((INPUT/'flutter_window.h').read_bytes())
  cls.cc=patch.transform('linux/flutter_window.cc',cls.cc,restore=True);cls.h=patch.transform('linux/flutter_window.h',cls.h,restore=True)
  assert patch.digest(cls.cc.encode())==patch.ORIGINAL['linux/flutter_window.cc'];assert patch.digest(cls.h.encode())==patch.ORIGINAL['linux/flutter_window.h']
  cls.fixed=patch.transform('linux/flutter_window.cc',cls.cc);cls.fixed_h=patch.transform('linux/flutter_window.h',cls.h)
  cls.build=OUTPUT/'native';cls.build.mkdir()
  cls.exe=cls.compile('fixed',cls.fixed,cls.fixed_h)
 @classmethod
 def compile(cls,name,cc,h):
  source=cls.build/(name+'.cc');source.write_text(cpp(cc,h));exe=cls.build/name
  command=['/usr/bin/clang++','-std=c++17','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(source),'-o',str(exe)]
  run=subprocess.run(command,capture_output=True,text=True,timeout=60);(cls.build/(name+'-compile.json')).write_text(json.dumps({'argv':command,'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr},indent=2)+'\n')
  if run.returncode:raise AssertionError(run.stderr)
  return exe
 def run_case(self,exe,case,name=None):
  env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1:abort_on_error=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
  r=subprocess.run([str(exe),case],capture_output=True,text=True,timeout=10,env=env)
  name=name or case;(self.build/(name+'.json')).write_text(json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2)+'\n');return r
 def test_01_actual_destructive_callbacks(self):
  for case in ['destroy-destroys','close-destroys','channel-destroys']:
   with self.subTest(case=case):
    r=self.run_case(self.exe,case);self.assertEqual(r.returncode,0,r.stderr);self.assertIn('PASS '+case,r.stdout)
 def test_02_prevented_return_survives_callbacks(self):
  for case in ['prevented-retained','prevented-destroys','prevented-changes-decision']:
   with self.subTest(case=case):
    r=self.run_case(self.exe,case);self.assertEqual(r.returncode,0,r.stderr)
 def test_03_missing_callback_and_retained_window(self):
  for case in ['weak-expired','retained']:
   with self.subTest(case=case):
    r=self.run_case(self.exe,case);self.assertEqual(r.returncode,0,r.stderr)
 def test_04_historical_self_read_is_real_sanitizer_failure(self):
  unsafe=self.fixed.replace(extract(self.fixed,'gboolean onWindowClose('),extract(self.cc,'gboolean onWindowClose('),1)
  exe=self.compile('historical-close',unsafe,self.fixed_h);r=self.run_case(exe,'destroy-destroys','historical-close-failure')
  self.assertNotEqual(r.returncode,0);self.assertIn('AddressSanitizer: heap-use-after-free',r.stderr)
 def test_05_historical_release_hook_survives_window(self):
  # Keep the close fix; invert only registration/removal to isolate the second defect.
  unsafe=self.fixed
  for old,new in patch.CHANGES['linux/flutter_window.cc'][1:]:unsafe=unsafe.replace(new,old,1)
  exe=self.compile('historical-release-hook',unsafe,self.fixed_h);r=self.run_case(exe,'destroy-destroys','historical-release-hook-failure')
  self.assertEqual(r.returncode,-signal.SIGABRT,r.stderr)
  self.assertIn('hooks.size()==2',r.stderr)
 def fixture(self,parent,newline='\n'):
  root=parent/'source';target=root/patch.DEPENDENCY;target.joinpath('linux').mkdir(parents=True)
  original={'flutter_window.cc':self.cc,'flutter_window.h':self.h,'multi_window_manager.cc':patch.canonical((INPUT/'multi_window_manager.cc').read_bytes())[0]}
  for name,text in original.items():(target/'linux'/name).write_text(text.replace('\n',newline),newline='')
  (target/'.mixel-source.json').write_bytes(PROOF.read_bytes());return root,target
 def test_06_exact_inverse_and_lf_crlf_idempotence(self):
  for nl in ['\n','\r\n']:
   with tempfile.TemporaryDirectory(dir=OUTPUT,prefix='pure-inverse-') as tmp:
    root,target=self.fixture(Path(tmp),nl);before={p.name:p.read_bytes() for p in (target/'linux').iterdir()}
    self.assertEqual(patch.apply(root,check=True,verify=False)['writes'],0);self.assertEqual(patch.apply(root,verify=False)['writes'],2);self.assertEqual(patch.apply(root,verify=False)['writes'],0)
    self.assertEqual(patch.apply(root,check=True,restore=True,verify=False)['writes'],0);self.assertEqual(patch.apply(root,restore=True,verify=False)['writes'],2)
    self.assertEqual(before,{p.name:p.read_bytes() for p in (target/'linux').iterdir()})
 def test_07_partial_changed_missing_and_owned_path_refusals(self):
  for variant in ['partial','changed','manager','proof','symlink','hardlink','missing','mixed-newline']:
   with self.subTest(variant=variant),tempfile.TemporaryDirectory(dir=OUTPUT,prefix='pure-refusal-') as tmp:
    root,target=self.fixture(Path(tmp));cc=target/'linux/flutter_window.cc';h=target/'linux/flutter_window.h'
    if variant=='partial':cc.write_text(self.fixed)
    elif variant=='changed':cc.write_bytes(cc.read_bytes()+b'// changed\n')
    elif variant=='manager':(target/'linux/multi_window_manager.cc').write_bytes(b'changed')
    elif variant=='proof':(target/'.mixel-source.json').write_text('{}')
    elif variant=='symlink':cc.rename(target/'linux/other');cc.symlink_to('other')
    elif variant=='hardlink':os.link(cc,target/'linux/other')
    elif variant=='missing':h.unlink()
    elif variant=='mixed-newline':cc.write_bytes(cc.read_bytes().replace(b'\n',b'\r\n',1))
    with self.assertRaises((RuntimeError,OSError,ValueError)):patch.apply(root,verify=False)
 def test_08_actual_projection_ownership_before_copy(self):
  prior=patch.prior_namespace();compiler=prior['prior_namespace']()['prior']('patch-support-compiler-cleanup.py')
  for variant in ['hardlink','symlink']:
   with self.subTest(variant=variant),tempfile.TemporaryDirectory(dir=OUTPUT,prefix='pure-owned-') as tmp:
    root,target=self.fixture(Path(tmp));unrelated=root/'untouched-source.txt';unrelated.write_text('owned source')
    if variant=='hardlink':os.link(unrelated,root/'another-source.txt')
    else:unrelated.unlink();unrelated.symlink_to(target/'linux/flutter_window.h')
    with self.assertRaises(RuntimeError):compiler['validate_projection_sources'](root)
    with self.assertRaises(RuntimeError):patch.apply(root,check=True,verify=True)
 def test_09_fresh_restore_only(self):
  with tempfile.TemporaryDirectory(dir=OUTPUT,prefix='pure-fresh-') as tmp:
   root=Path(tmp)/'source';(root/'flutter').mkdir(parents=True)
   self.assertEqual(patch.apply(root,restore=True,verify=False)['profile'],'fresh')
   with self.assertRaises(RuntimeError):patch.apply(root,verify=False)

def main(_projected_source=None):
 global OUTPUT,INPUT,PROOF,SOURCE
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 if _projected_source is not None:a.source=_projected_source
 uri_spec=importlib.util.spec_from_file_location('native_close_uri_fixture',BASE/'patch-support-uri-routing.py');uri=importlib.util.module_from_spec(uri_spec);uri_spec.loader.exec_module(uri)
 if uri.has_layer(a.source):
  with uri.previous(a.source) as previous:return main(previous)
 SOURCE=a.source.absolute();INPUT=SOURCE/patch.DEPENDENCY/'linux';PROOF=SOURCE/patch.DEPENDENCY/'.mixel-source.json';OUTPUT=a.output.absolute();OUTPUT.mkdir(mode=0o700)
 result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCloseTests));raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()
