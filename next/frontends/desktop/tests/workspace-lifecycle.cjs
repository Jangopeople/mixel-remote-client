const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const script=fs.readFileSync(path.join(__dirname,'../dist/workspace.js'),'utf8');
function harness(hash='',fullscreenCommand=async()=>{}){
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 const elements=Object.fromEntries([...html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)].map(match=>[match[3],{id:match[3],hidden:/\bhidden\b/.test(match[2]),disabled:/\bdisabled\b/.test(match[2]),textContent:'',dataset:{fit:match[3]==='workspace-screen'?'true':undefined},attrs:{},listeners:{},value:'',addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(name,value){this.attrs[name]=value;},removeAttribute(name){delete this.attrs[name];},focus(){},click(){return this.listeners.click?.();}}]));
 const pages=Object.values(elements).filter(node=>['remote','devices','account','setup','diagnostics'].includes(node.id));
 const links=['remote','devices','account','setup'].map(name=>({hash:'#'+name,attrs:{},setAttribute(key,value){this.attrs[key]=value;},removeAttribute(key){delete this.attrs[key];}}));
 const events={},windowEvents={},calls=[];
 const window={location:{hash},addEventListener(name,fn){windowEvents[name]=fn;},__TAURI__:{core:{async invoke(command,args){calls.push(command);if(command==='remote_view_fullscreen')return fullscreenCommand(args.session);return false;}}}};
 const document={body:{dataset:{}},dispatchEvent(event){for(const fn of events[event.type]??[])fn(event);},getElementById:id=>elements[id],querySelectorAll:selector=>selector==='[data-workspace-page]'?pages:links,querySelector:()=>({scrollTo(){}}),addEventListener(name,fn){(events[name]??=[]).push(fn);}};
 vm.runInNewContext(script,{window,document,CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}}});
 return{elements,links,calls,get fullscreen(){return document.body.dataset.remoteFullscreen;},get hash(){return window.location.hash;},event(name,detail){for(const fn of events[name]??[])fn({detail});},route(name){window.location.hash='#'+name;windowEvents.hashchange();},visible:()=>pages.filter(page=>!page.hidden).map(page=>page.id)};
}
test('sidebar switches real workspace pages without discarding partner input or selecting multiple pages',()=>{
 const h=harness();assert.deepEqual(h.visible(),['remote']);h.elements['remote-host'].value='123 456 789';
 h.route('devices');assert.deepEqual(h.visible(),['devices']);assert.equal(h.links[1].attrs['aria-current'],'page');assert.equal(h.links[0].attrs['aria-current'],undefined);
 h.route('remote');assert.equal(h.elements['remote-host'].value,'123 456 789');
 h.route('untrusted-page');assert.deepEqual(h.visible(),['remote']);
});
test('account sign-in alone never advertises a ready device or enables session fitting',()=>{
 const h=harness();assert.equal(h.elements['workspace-signin-notice'].hidden,false);assert.equal(h.elements['shell-connection'].textContent,'Not connected');
 h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});assert.equal(h.elements['workspace-signin-notice'].hidden,false);assert.equal(h.elements['workspace-notice-action'].href,'#setup');assert.equal(h.elements['shell-connection'].textContent,'Not connected');
 h.event('mixel-remote-state',{phase:'ready'});assert.equal(h.elements['shell-connection'].textContent,'Ready to connect');assert.equal(h.elements['workspace-fit'].disabled,true);
 h.event('mixel-account-state',{phase:'signed_out',account:null});assert.equal(h.elements['workspace-signin-notice'].hidden,false);assert.equal(h.elements['shell-connection'].textContent,'Not connected');
});
test('a TestFlight deep link cannot open host diagnostics',()=>{
 const h=harness('#diagnostics');assert.deepEqual(h.visible(),['diagnostics']);
 h.event('mixel-remote-state',{phase:'offline',controllerOnly:true});
 assert.deepEqual(h.visible(),['remote']);
 h.route('diagnostics');assert.deepEqual(h.visible(),['remote']);
 h.route('setup');assert.deepEqual(h.visible(),['setup']);
});
test('incoming requests surface consent while video and toolbar state follow actual native events',()=>{
 const h=harness('#devices');h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});h.event('mixel-remote-state',{phase:'incoming'});assert.equal(h.hash,'#remote');assert.deepEqual(h.visible(),['remote']);assert.equal(h.elements['workspace-session-empty'].hidden,false);
 h.event('mixel-remote-state',{phase:'active',sharing:false,control:false});assert.equal(h.elements['workspace-session-mode'].textContent,'View only');assert.equal(h.elements['workspace-fit'].disabled,true);
 h.event('mixel-video-visibility',{visible:true});assert.equal(h.elements['workspace-session-empty'].hidden,true);assert.equal(h.elements['workspace-fit'].disabled,false);
 h.elements['workspace-fit'].listeners.click();assert.equal(h.elements['workspace-screen'].dataset.fit,'false');assert.equal(h.elements['workspace-fit'].textContent,'Actual size');
 h.event('mixel-video-visibility',{visible:false});h.event('mixel-remote-state',{phase:'offline'});assert.equal(h.elements['workspace-session-empty'].hidden,false);assert.equal(h.elements['workspace-session-actions'].hidden,true);assert.equal(h.elements['workspace-security-label'].textContent,'No screen is being shared');
});
test('OS permissions are never requested on page navigation and a declined permission stays declined',async()=>{
 const h=harness();h.route('setup');assert.deepEqual(h.calls,[]);
 await h.elements['workspace-screen-permission'].listeners.click();assert.deepEqual(h.calls,['request_screen_access']);assert.match(h.elements['workspace-permission-result'].textContent,/Complete the permission choice/);assert.equal(h.elements['workspace-screen-permission'].disabled,false);
});

test('session tabs expose only granted tools, reset for fresh consent, and surface file attention without stealing the screen',()=>{
 const h=harness();h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});
 h.event('mixel-remote-state',{phase:'active',consent:'scope-one',files:true,clipboard:false});assert.equal(h.elements.remote.dataset.session,'true');assert.equal(h.elements['workspace-files-tab'].disabled,false);assert.equal(h.elements['workspace-clipboard-tab'].disabled,true);
 h.event('mixel-file-attention',{consent:'scope-one',pending:true});assert.equal(h.elements['workspace-files-tab'].textContent,'Files (1)');assert.equal(h.elements.remote.dataset.tool,'screen');
 h.elements['workspace-files-tab'].listeners.click();assert.equal(h.elements.remote.dataset.tool,'files');
 h.event('mixel-file-attention',{consent:'retired-scope',pending:false});assert.equal(h.elements['workspace-files-tab'].textContent,'Files (1)');
 h.event('mixel-remote-state',{phase:'active',consent:'scope-two',files:false,clipboard:true});assert.equal(h.elements.remote.dataset.tool,'screen');assert.equal(h.elements['workspace-files-tab'].disabled,true);assert.equal(h.elements['workspace-clipboard-tab'].disabled,false);
 h.elements['workspace-clipboard-tab'].listeners.click();assert.equal(h.elements.remote.dataset.tool,'clipboard');
});

test('session tool keyboard navigation skips denied tools and keeps the selected tab focusable',()=>{
 const h=harness();h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});
 h.event('mixel-remote-state',{phase:'active',consent:'scope-one',files:false,clipboard:true});
 h.elements['workspace-screen-tab'].listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(h.elements.remote.dataset.tool,'clipboard');assert.equal(h.elements['workspace-clipboard-tab'].tabIndex,0);assert.equal(h.elements['workspace-screen-tab'].tabIndex,-1);
 h.elements['workspace-clipboard-tab'].listeners.keydown({key:'Home',preventDefault(){}});assert.equal(h.elements.remote.dataset.tool,'screen');
});

test('full screen requires a frame from the current session and exits after revocation during native entry',async()=>{
 const requested=[];let finishEntry;
 const h=harness('',session=>{requested.push(session);if(session)return new Promise(resolve=>{finishEntry=resolve;});});
 const button=h.elements['workspace-fullscreen'];
 h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});
 h.event('mixel-remote-state',{phase:'active',session:'room-one',consent:'scope-one'});
 assert.equal(button.hidden,true);
 h.event('mixel-video-visibility',{visible:true});
 assert.equal(button.disabled,false);
 button.click();
 assert.equal(button.disabled,true);
 h.event('mixel-remote-state',{phase:'closing',session:null,consent:'scope-one',stopping:true});
 finishEntry();
 await new Promise(setImmediate);
 assert.deepEqual(requested,['room-one',null]);
 assert.equal(h.fullscreen,'false');
 h.event('mixel-remote-state',{phase:'active',session:'room-two',consent:'scope-two'});
 assert.equal(button.hidden,true);
});

test('full screen keeps End Session available, restores sizing, and leaves on navigation',async()=>{
 const requested=[];
 const h=harness('',async session=>{requested.push(session);});
 h.event('mixel-account-state',{phase:'authenticated',account:{profile:'qa'}});
 h.event('mixel-remote-state',{phase:'active',session:'room-one',consent:'scope-one'});
 h.event('mixel-video-visibility',{visible:true});
 const button=h.elements['workspace-fullscreen'];
 h.elements['workspace-fit'].click();
 button.click();
 await new Promise(setImmediate);
 assert.equal(button.textContent,'Exit full screen');
 assert.equal(h.elements['workspace-session-actions'].hidden,false);
 h.route('devices');
 await new Promise(setImmediate);
 assert.equal(h.fullscreen,'false');
 assert.equal(h.elements['workspace-screen'].dataset.fit,'false');
 assert.deepEqual(requested,['room-one',null]);
});
