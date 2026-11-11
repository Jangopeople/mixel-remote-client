const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/remote.js'),'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
const signedIn={phase:'authenticated',account:{profile:'fixture-profile',organization:'fixture-org',staff:false}};
function harness({deferAccount=false,authenticated=false,deferInput=false,stopFails=false,supportAvailable=true,controllerOnly=false,claim=null}={}){
  const requests=[];
  const captureRequests=[];
  const meetings=[];const documentListeners={},intervals=[],pendingVideo=[],input=[],pendingInput=[],pendingClipboard=[],stops=[],closed=[],support=[],recordingCommands=[];let draws=0,accountReply;
  let state={controller_only:controllerOnly,phase:'offline',message:'Ready',host:123456789,session:null,consent:null,peer:null,requester:null,received:0,sharing:false,control:false,clipboard:false,files:false,input_access:false};
  const context={clearRect(){},putImageData(){draws++;}};
  function node(tag){return {tagName:tag.toUpperCase(),value:'',checked:false,hidden:false,disabled:false,dataset:{},children:[],listeners:{},textContent:'',width:640,height:420,
    addEventListener(name,callback){this.listeners[name]=callback;},appendChild(child){this.children.push(child);if(this.tagName==='SELECT'&&!this.value)this.value=child.value;},replaceChildren(){this.children=[];this.value='';},
    append(...children){this.children.push(...children);},getContext(){return context;},focus(){},setPointerCapture(){},hasPointerCapture(){return false;},releasePointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:640,height:420};}};}
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const elements=Object.fromEntries([...html.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/gi)].map(match=>[match[2],node(match[1])]));
  elements['support-connection'].hidden=/<details[^>]*id="support-connection"[^>]*\bhidden\b/.test(html);
  const body={dataset:{}};
  function dispatch(type,detail){for(const callback of documentListeners[type]??[])callback({type,detail});}
  function invoke(command,args){
    if(command==='account_status')return deferAccount?new Promise((resolve,reject)=>{accountReply={resolve,reject};}):Promise.resolve(authenticated?signedIn:{phase:'signed_out',account:null});
    if(command==='quick_phone_claim')return claim instanceof Error?Promise.reject(claim):Promise.resolve(!!claim);
    if(command==='remote_request'){requests.push(args);return Promise.resolve();}
    if(command==='connect_support_id'){support.push(args);return Promise.resolve();}
    if(command==='remote_status')return supportAvailable?Promise.resolve({...state}):Promise.reject(Error('Command unavailable'));
    if(command==='capture_targets'){captureRequests.push(command);return Promise.resolve([]);}
    if(command==='remote_next_video')return new Promise((resolve,reject)=>pendingVideo.push({resolve,reject}));
    if(command==='remote_input'){input.push(args);return deferInput?new Promise((resolve,reject)=>pendingInput.push({resolve,reject})):Promise.resolve(true);}
    if(command==='remote_clipboard')return new Promise((resolve,reject)=>pendingClipboard.push({resolve,reject}));
    if(command==='remote_stop_session'){stops.push(args);if(stopFails)return Promise.reject(Error('IPC unavailable'));state={...state,phase:'closed',session:null,consent:null};return Promise.resolve();}
    if(command==='remote_close'){closed.push(args);return Promise.resolve();}
    if(command==='meeting_open'){meetings.push(args);return Promise.resolve();}
    if(command==='remote_record_start'||command==='remote_record_stop'){recordingCommands.push({command,args});return Promise.resolve();}
    throw Error(`Unexpected native command ${command}`);
  }
  vm.runInNewContext(script,{window:{addEventListener(){},__TAURI__:{core:{invoke}},setInterval(callback){intervals.push(callback);},setTimeout(){}},document:{body,getElementById:id=>elements[id],createElement:node,addEventListener(name,callback){(documentListeners[name]??=[]).push(callback);},dispatchEvent(event){dispatch(event.type,event.detail);}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},ArrayBuffer,Uint8Array,Uint8ClampedArray,DataView,ImageData:class{},performance:{now:()=>100},requestAnimationFrame(){},setTimeout(){}});
  return {requests,meetings,body,elements,captureRequests,pendingVideo,input,pendingInput,pendingClipboard,stops,closed,support,recordingCommands,emit:dispatch,get draws(){return draws;},get accountReply(){return accountReply;},account(value){dispatch('mixel-account-state',value);},async tick(){intervals[0]();await settle();},ready(message='Ready'){state={...state,phase:'ready',message};},active(extra={}){state={...state,phase:'active',session:'fixture-room',consent:'fixture-consent',control:true,...extra};}};
}
function frame(){const bytes=new Uint8Array(48);bytes.set([77,82,70,49]);const view=new DataView(bytes.buffer);view.setUint32(4,2,true);view.setUint32(8,2,true);view.setUint32(12,8,true);view.setBigUint64(16,1n,true);view.setBigUint64(24,33333n,true);return bytes.buffer;}
test('registration and connect require authenticated account and stale initial failures cannot undo newer sign-in',async()=>{
  const h=harness({deferAccount:true});await settle();await h.tick();assert.equal(h.elements['remote-register'].disabled,true);assert.equal(h.elements['remote-connect'].disabled,true);
  h.account(signedIn);await h.tick();assert.equal(h.elements['remote-register'].disabled,false);assert.equal(h.elements['remote-connect'].disabled,false,'Connect brings this computer online itself');h.ready();await h.tick();assert.equal(h.elements['remote-connect'].disabled,false);
  h.accountReply.reject(Error('old account read failed'));await settle();assert.equal(h.elements['remote-connect'].disabled,false);
  h.account({phase:'mfa_required',account:null});assert.equal(h.elements['remote-register'].disabled,true);assert.equal(h.elements['remote-connect'].disabled,true);
});
test('the TestFlight controller can connect without displaying host or legacy support controls',async()=>{
  const h=harness({authenticated:true,controllerOnly:true});await settle();await h.tick();
  assert.equal(h.body.dataset.controllerOnly,'true');
  assert.deepEqual(h.captureRequests,[]);
  assert.equal(h.elements['support-connection'].hidden,true);
  const actions=h.elements['controller-connection-actions'];
  assert.equal(actions.hidden,false);
  assert.deepEqual(actions.children,[h.elements['remote-register'],h.elements['remote-disconnect']]);
  assert.equal(h.elements['remote-register'].textContent,'Connect to Mixel Remote');
  assert.equal(h.elements['remote-register'].disabled,false);
  h.ready();await h.tick();assert.equal(h.elements['remote-connect'].disabled,false);
});
test('a refused connection stays visible and the user can try another ID',async()=>{
  const h=harness({authenticated:true});await settle();
  h.ready('This account could not verify that device ID. Check that your company has access.');await h.tick();
  assert.match(h.elements['remote-status'].textContent,/could not verify that device ID/);
  assert.equal(h.elements['remote-connect'].disabled,false);
  h.ready('Ready for an attended connection');await h.tick();
  assert.equal(h.elements['remote-status'].textContent,'Ready for an attended connection');
});
test('sign-out clears native video immediately and a late old frame cannot restore it or enable input',async()=>{
  const h=harness({authenticated:true});await settle();h.active();await h.tick();assert.equal(h.pendingVideo.length,1);
  h.account({phase:'signed_out',account:null});h.pendingVideo[0].resolve(frame());await settle();
  assert.equal(h.draws,0);assert.equal(h.elements['remote-canvas'].hidden,true);
  h.elements['remote-canvas'].listeners.mousedown({button:0,clientX:10,clientY:10,detail:2,preventDefault(){}});await settle();assert.equal(h.input.length,0);
});
test('local double-click count survives the native input command queue and releases on pointer-up',async()=>{
  const h=harness({authenticated:true});await settle();h.active();await h.tick();h.pendingVideo[0].resolve(frame());await settle();assert.equal(h.draws,1);
  const canvas=h.elements['remote-canvas'];canvas.listeners.mousedown({button:0,clientX:320,clientY:210,detail:2,preventDefault(){}});
  canvas.listeners.pointerup({button:0,pointerId:1,clientX:320,clientY:210});await settle();
  const buttons=h.input.filter(command=>command.event.kind==='button_click').map(command=>JSON.parse(JSON.stringify(command.event)));
  assert.deepEqual(buttons,[{kind:'button_click',button:1,down:true,clicks:2},{kind:'button_click',button:1,down:false,clicks:2}]);
});

test('queued input rejected after native revocation does not replace the native close reason', async () => {
  const h = harness({ authenticated: true, deferInput: true });
  await settle();
  h.active();
  await h.tick();
  h.pendingVideo[0].resolve(frame());
  await settle();
  const canvas = h.elements['remote-canvas'];
  const key = { code: 'KeyA', preventDefault() {} };
  canvas.listeners.keydown(key);
  canvas.listeners.keyup(key);
  h.pendingInput[0].resolve(false);
  await settle();
  assert.equal(h.input.length, 1);
  assert.deepEqual(h.closed, []);
  assert.equal(h.elements['remote-error'].textContent, '');
});

test('letterbox clicks and wheel are ignored, while an active drag releases at the remote edge',async()=>{
  const h=harness({authenticated:true});await settle();h.active();await h.tick();h.pendingVideo[0].resolve(frame());await settle();
  const canvas=h.elements['remote-canvas'];
  canvas.getBoundingClientRect=()=>({left:50,top:100,width:800,height:400});
  const event=(x,y)=>({button:0,clientX:x,clientY:y,pointerId:1,detail:1,preventDefault(){}});
  canvas.listeners.mousedown(event(249,300));
  canvas.listeners.wheel(event(249,300));
  canvas.listeners.pointermove(event(249,300));
  await settle();
  assert.equal(h.input.length,0);
  canvas.listeners.mousedown(event(450,300));
  await settle();
  canvas.listeners.pointerup(event(900,300));
  await settle();
  assert.deepEqual(h.input.map(({event})=>JSON.parse(JSON.stringify(event))),[
    {kind:'pointer',x:32768,y:32768},
    {kind:'button_click',button:1,down:true,clicks:1},
    {kind:'pointer',x:65535,y:32768},
    {kind:'button_click',button:1,down:false,clicks:1},
  ]);
});

test('unattended sharing can be disabled before enable is confirmed and waits before re-enabling', async () => {
  const h = harness({authenticated: true});
  await settle();
  const enable = h.elements['remote-enable-unattended'];
  const disable = h.elements['remote-disable-unattended'];
  const status = h.elements['remote-unattended-state'];
  h.elements['remote-unattended-display'].value = 'display:1';
  h.elements['remote-unattended-confirm'].checked = true;

  h.active({phase: 'ready', unattended_enabled: false, unattended_pending: 'enabling'});
  await h.tick();
  assert.match(status.textContent, /Turning on/);
  assert.equal(enable.disabled, true);
  assert.equal(disable.hidden, false);
  assert.equal(disable.disabled, false);

  for (const pending of ['disabling_after_enable', 'disabling']) {
    h.active({phase: 'ready', unattended_pending: pending});
    await h.tick();
    assert.match(status.textContent, /Sharing is off/);
    assert.equal(enable.disabled, true);
    assert.equal(disable.hidden, true);
    assert.equal(h.elements['remote-generate-code'].disabled, true);
  }

  h.active({phase: 'ready', unattended_pending: null});
  await h.tick();
  assert.match(status.textContent, /^Off/);
  assert.equal(enable.disabled, false);
});

test('recording requires both the native host grant and a received frame in the current session',async()=>{
  const h=harness({authenticated:true});await settle();h.active();await h.tick();
  const button=h.elements['remote-record'];
  assert.equal(button.hidden,true);
  h.active({recording:{phase:'ready'}});await h.tick();
  assert.equal(button.disabled,true);
  await button.listeners.click();
  assert.equal(h.recordingCommands.length,0);
  h.pendingVideo[0].resolve(frame());await settle();await h.tick();
  assert.equal(button.disabled,false);
  await button.listeners.click();
  h.active({recording:{phase:'recording'}});await h.tick();
  assert.equal(button.textContent,'Stop recording');
  await button.listeners.click();
  assert.deepEqual(h.recordingCommands.map(call=>call.command),['remote_record_start','remote_record_stop']);
  assert.ok(h.recordingCommands.every(call=>call.args.session==='fixture-room'));
  h.active({session:'fresh-room',consent:'fresh-consent',recording:undefined});await h.tick();
  assert.equal(button.hidden,true);
});

test('leaving the remote workspace releases held native keys in the same live session',async()=>{
  const h=harness({authenticated:true});await settle();h.active();await h.tick();h.pendingVideo[0].resolve(frame());await settle();
  h.elements['remote-canvas'].listeners.keydown({code:'ShiftLeft',isComposing:false,preventDefault(){}});await settle();
  assert.equal(h.input.at(-1).event.kind,'key');assert.equal(h.input.at(-1).event.down,true);
  h.emit('mixel-workspace-leaving');await settle();assert.equal(h.input.at(-1).event.kind,'release_all');assert.equal(h.input.at(-1).session,'fixture-room');
});

test('End session preempts a pending clipboard operation and never restarts receiving old media',async()=>{
 const h=harness({authenticated:true});await settle();h.active({clipboard:true});await h.tick();
 const clipboard=h.elements['remote-push-clipboard'].listeners.click();await settle();assert.equal(h.pendingClipboard.length,1);
 assert.equal(h.elements['remote-close'].disabled,false,'End session must remain available during clipboard I/O');
 h.elements['remote-close'].listeners.click();await settle();assert.equal(h.stops.length,1);assert.equal(h.stops[0].consent,'fixture-consent');assert.equal(h.pendingVideo.length,1);
 h.pendingVideo[0].resolve(frame());await settle();assert.equal(h.draws,0);assert.equal(h.elements['remote-canvas'].hidden,true);
 h.pendingClipboard[0].resolve();await clipboard;assert.equal(h.elements['remote-clipboard-result'].textContent,'');assert.equal(h.elements['remote-error'].textContent,'');
});
test('failed Stop stays locally latched and cannot resume video or keyboard until fresh consent',async()=>{
 const h=harness({authenticated:true,stopFails:true});await settle();h.active();await h.tick();
 h.elements['remote-close'].listeners.click();await settle();assert.match(h.elements['remote-error'].textContent,/Could not confirm/);assert.equal(h.elements['remote-close'].disabled,false);
 await h.tick();assert.equal(h.pendingVideo.length,1,'old active status must not start a new reader after Stop failed');
 h.pendingVideo[0].resolve(frame());await settle();assert.equal(h.draws,0);assert.equal(h.elements['remote-canvas'].hidden,true);
 h.elements['remote-canvas'].listeners.keydown({code:'KeyA',isComposing:false,preventDefault(){}});await settle();assert.equal(h.input.length,0);
 h.active({session:'replacement',consent:'fresh-consent'});await h.tick();assert.equal(h.pendingVideo.length,2);
 h.pendingVideo[1].resolve(frame());await settle();assert.equal(h.draws,1);
});
test('late input failure from a retired session cannot clear a replacement screen',async()=>{
 const h=harness({authenticated:true,deferInput:true});await settle();h.active();await h.tick();h.pendingVideo[0].resolve(frame());await settle();
 h.elements['remote-canvas'].listeners.keydown({code:'KeyA',isComposing:false,preventDefault(){}});await settle();assert.equal(h.pendingInput.length,1);
 h.active({session:'replacement',consent:'fresh-consent'});await h.tick();h.pendingVideo[1].resolve(frame());await settle();assert.equal(h.draws,2);
 h.pendingInput[0].reject(Error('retired channel'));await settle();assert.equal(h.elements['remote-canvas'].hidden,false);assert.equal(h.closed.length,0);assert.equal(h.elements['remote-error'].textContent,'');
});
test('leaving the screen preserves queued key release behind an outstanding native key-down',async()=>{
 const h=harness({authenticated:true,deferInput:true});await settle();h.active();await h.tick();h.pendingVideo[0].resolve(frame());await settle();
 h.elements['remote-canvas'].listeners.keydown({code:'ShiftLeft',isComposing:false,preventDefault(){}});await settle();
 h.emit('mixel-workspace-leaving');h.emit('mixel-workspace-focus',{enabled:false});h.pendingInput[0].resolve(true);await settle();assert.equal(h.input.length,2);assert.equal(h.input[1].event.kind,'release_all');
 h.pendingInput[1].resolve(true);await settle();h.elements['remote-canvas'].listeners.keydown({code:'KeyA',isComposing:false,preventDefault(){}});await settle();assert.equal(h.input.length,2);
});

test('Support is explicit and forwards only the Support ID to its native command',async()=>{
  const h=harness();await settle();
  assert.equal(h.elements['remote-connect'].disabled,true);
  assert.equal(h.elements['support-connection'].hidden,false);
  const form=h.elements['support-connect-form'];
  h.elements['support-host'].value='304619778/r';
  await form.listeners.submit({preventDefault(){}});
  assert.equal(h.support.length,0);
  h.elements['support-host'].value='304 619 778';
  await form.listeners.submit({preventDefault(){}});
  assert.equal(JSON.stringify(h.support),JSON.stringify([{id:'304619778'}]));
  assert.match(h.elements['support-connect-status'].textContent,/prompts to connect/);
});

test('the compatibility option stays hidden when the Mac session commands are unavailable',async()=>{
  const h=harness({supportAvailable:false});await settle();
  assert.equal(h.elements['support-connection'].hidden,true);
  h.elements['support-host'].value='304619778';
  await h.elements['support-connect-form'].listeners.submit({preventDefault(){}});
  assert.equal(h.support.length,0);
});

test('the person is asked before the technician\'s screen opens, and only that room for this session opens',async()=>{
  const h=harness({authenticated:true});await settle();await h.tick();
  h.active({sharing:true,meeting_offer:['other-session','pu5f-9qwq-m767']});await h.tick();
  assert.equal(h.elements['meeting-offer'].hidden,true,'an offer from another session is never shown');
  h.active({sharing:true,meeting_offer:['fixture-room','pu5f-9qwq-m767']});await h.tick();
  assert.equal(h.elements['meeting-offer'].hidden,false,'the person sees the question');
  assert.equal(h.meetings.length,0,'nothing opens without the person');
  h.elements['meeting-offer-open'].listeners.click();await settle();
  assert.equal(JSON.stringify(h.meetings),JSON.stringify([{link:'https://rs.mixel.ch/meet/#pu5f-9qwq-m767'}]));
  assert.equal(h.elements['meeting-offer'].hidden,true);
  h.active({sharing:true,meeting_offer:['fixture-room','abcd-efgh-jkmn']});await h.tick();
  h.elements['meeting-offer-dismiss'].listeners.click();await settle();
  assert.equal(h.elements['meeting-offer'].hidden,true,'"Not now" puts it away');
  assert.equal(h.meetings.length,1);
});
test('a phone ID that cannot be prepared shows why and sends no request',async()=>{
  const h=harness({authenticated:true,claim:Error('duplicate key value violates unique constraint')});await settle();
  h.ready();await h.tick();
  h.emit('mixel-connect-to',{id:648493658});await settle();await settle();
  assert.match(h.elements['remote-error'].textContent,/could not be prepared for the session: .*duplicate key/);
  assert.equal(h.requests.length,0);
});
test('a phone lookup refused for missing second confirmation step asks to sign in again',async()=>{
  const h=harness({authenticated:true,claim:Error('MFA-authenticated staff required')});await settle();
  h.ready();await h.tick();
  h.emit('mixel-connect-to',{id:648493658});await settle();await settle();
  assert.match(h.elements['remote-error'].textContent,/second confirmation step/);
  assert.equal(h.requests.length,0);
});
test('an ID that is not a phone goes on to the ordinary request',async()=>{
  const h=harness({authenticated:true,claim:false});await settle();
  h.ready();await h.tick();
  h.emit('mixel-connect-to',{id:440207203});await settle();await settle();
  assert.equal(h.requests.length,1);
  assert.equal(h.requests[0].host,440207203);
});
