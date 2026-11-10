const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const accountScript=fs.readFileSync(path.join(__dirname,'../dist/main.js'),'utf8');
const enrollmentScript=fs.readFileSync(path.join(__dirname,'../dist/enrollment.js'),'utf8');
const captureScript=fs.readFileSync(path.join(__dirname,'../dist/capture.js'),'utf8');
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function harness(accountInvoke, fullDisplay=false, enrollment=false){
  const pending=[],stops=[],timers=[];let stopCalls=0,draws=0,clears=0;
  const context={putImageData(){draws++;},clearRect(){clears++;}};
  // Use the actual page's IDs so missing/renamed account/source controls still
  // fail initialization. The capture scenarios below remain independently
  // driven by deferred native responses, not copied implementation decisions.
  const html=fs.readFileSync(path.join(__dirname,'../dist',fullDisplay?'capture.html':'index.html'),'utf8');
  function node(tag,attributes=''){
    return {tagName:tag.toUpperCase(),disabled:/\bdisabled\b/.test(attributes),hidden:/\bhidden\b/.test(attributes),checked:false,textContent:'',value:'',width:640,height:420,listeners:{},children:[],parentElement:{hidden:false},
      get options(){return this.children;},
      appendChild(child){this.children.push(child);if(this.tagName==='SELECT'&&!this.value)this.value=child.value;return child;},
      append(...children){for(const child of children)this.appendChild(child);},
      setAttribute(name,value){this[name]=value;},
      replaceChildren(...children){this.children=[];this.value='';for(const child of children)this.appendChild(child);},
      addEventListener(name,handler){this.listeners[name]=handler;},getContext(){return context;},reportValidity(){return true;},
      select(){this.selected=true;}};
  }
  const elements=Object.fromEntries([...html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)].map(match=>[match[3],node(match[1],match[2])]));
  const documentListeners={};
  function invoke(command,args){
    if(enrollment && command==='remote_status')return Promise.resolve({phase:'offline'});
    if(enrollment && command==='register_personal_device')return accountInvoke(command,args);
    if(command==='remote_signup_available')return Promise.resolve(true);
    if(accountInvoke && (command.startsWith('account_') || command==='open_company_settings'))return accountInvoke(command,args);
    if(command==='account_status')return Promise.resolve({phase:'signed_out',message:'Signed out',account:null,factors:[]});
    if(command==='capture_targets')return Promise.resolve([{id:fullDisplay?'x11:0:1':'own-fixture',name:'Fixture',full_display:fullDisplay}]);
    if(command==='start_capture'){
      assert.equal(args.confirmFullDisplay,fullDisplay);
      return Promise.resolve('Native stream running');
    }
    if(command==='next_frame')return new Promise((resolve,reject)=>pending.push({resolve,reject}));
    if(command==='stop_capture'){stopCalls++;return new Promise((resolve,reject)=>{resolve.reject=reject;stops.push(resolve);});}
    throw Error('Unexpected command');
  }
  vm.runInNewContext(fullDisplay?captureScript:accountScript+'\n'+captureScript+(enrollment?'\n'+enrollmentScript:''),{window:{__TAURI__:{core:{invoke}},setInterval(){return 1;}},document:{hidden:false,getElementById:id=>elements[id],createElement:tag=>node(tag),addEventListener(name,handler){(documentListeners[name]??=[]).push(handler);},dispatchEvent(event){for(const handler of documentListeners[event.type]??[])handler(event);return true;}},CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},ArrayBuffer,Uint8Array,Uint8ClampedArray,DataView,ImageData:class{},performance:{now:()=>100},setTimeout:resolve=>timers.push(resolve)});
  function frame(sequence=1){const buffer=new ArrayBuffer(24);const view=new DataView(buffer);view.setUint32(0,1,true);view.setUint32(4,1,true);view.setUint32(8,4,true);view.setBigUint64(12,BigInt(sequence),true);return buffer;}
  return {elements,pending,stops,timers,frame,get stopCalls(){return stopCalls;},get draws(){return draws;},get clears(){return clears;},ready:settle(),start:()=>elements.start.listeners.click(),stop:()=>elements.stop.listeners.click()};
}
test('verified Mixel ID offers name setup without the ISM authenticator form', async () => {
  let phase = 'profile_setup';
  const calls = [];
  const h = harness(async (command, args) => {
    if (command === 'account_status') return {
      phase, message: 'Mixel ID verified', factors: [],
      account: phase === 'authenticated' ? {profile: 'personal', staff: false, personal: true, display_name: 'Michael Example'} : null
    };
    if (command === 'account_setup') { calls.push(args.name); phase = 'authenticated'; return; }
    throw Error('Unexpected account command');
  });
  await h.ready;
  assert.equal(h.elements['account-mfa'].hidden, true);
  assert.equal(h.elements['account-profile-setup'].hidden, false);
  h.elements['account-display-name'].value = '  Michael Example ';
  h.elements['account-profile-setup'].listeners.submit({preventDefault() {}});
  await settle();
  assert.deepEqual(calls, ['Michael Example']);
  assert.equal(h.elements['account-profile-setup'].hidden, true);
  assert.match(h.elements['account-status'].textContent, /Michael Example/);
});
test('a personal account registers its Mac without accessing ISM inventory', async () => {
  const calls = [];
  const h = harness(async (command, args) => {
    if (command === 'account_status') return {
      phase: 'authenticated', message: 'Mixel ID verified', factors: [],
      account: {profile: 'personal', staff: false, personal: true, display_name: 'Michael Example'}
    };
    if (command === 'register_personal_device') { calls.push(args.name); return 123456789; }
    throw Error('Unexpected customer device command');
  }, false, true);
  await h.ready;
  assert.equal(h.elements['enrollment-controls'].hidden, true);
  assert.equal(h.elements['personal-device-form'].hidden, false);
  h.elements['personal-device-name'].value = 'Studio Mac';
  await h.elements['personal-device-form'].listeners.submit({preventDefault() {}});
  assert.deepEqual(calls, ['Studio Mac']);
  assert.match(h.elements['personal-device-status'].textContent, /123 456 789/);
});
test('alternate browser link is shown only during its sign-in attempt',async()=>{
  const link='https://identity.example/auth/v1/authorize?code_challenge=fixture';
  let phase='signed_out';
  const h=harness(async (command,args)=>{
    if(command==='account_status')return {phase,message:phase,account:null,factors:[]};
    if(command==='account_sign_in'){
      assert.equal(args.showLink,true);
      phase='waiting_browser';
      return link;
    }
    if(command==='account_cancel'){phase='signed_out';return;}
    throw Error('Unexpected account command');
  });
  await h.ready;
  const alternate=h.elements['account-sign-in-link'];
  const signOut=h.elements['account-cancel'];
  const url=h.elements['account-browser-url'];
  const panel=h.elements['account-browser-link'];
  assert.equal(panel.hidden,true);
  alternate.listeners.click();
  await settle();
  assert.equal(url.value,link);
  assert.equal(panel.hidden,false);
  assert.equal(alternate.disabled,true);
  signOut.listeners.click();
  await settle();
  assert.equal(url.value,'');
  assert.equal(panel.hidden,true);
  assert.equal(alternate.disabled,false);
});
test('switching browsers reuses the pending sign-in without starting another transaction',async()=>{
  const link='https://identity.example/auth/v1/authorize?code_challenge=fixture';
  let phase='signed_out';
  let starts=0;
  const h=harness(async (command,args)=>{
    if(command==='account_status')return {phase,message:phase,account:null,factors:[]};
    if(command==='account_sign_in'){
      assert.equal(args,undefined);
      starts++;
      phase='waiting_browser';
      return link;
    }
    if(command==='account_cancel'){phase='signed_out';return;}
    throw Error('Unexpected account command');
  });
  await h.ready;
  const alternate=h.elements['account-sign-in-link'];
  const url=h.elements['account-browser-url'];
  h.elements['account-sign-in'].listeners.click();
  await settle();
  assert.equal(url.value,'');
  assert.equal(alternate.disabled,false);
  alternate.listeners.click();
  assert.equal(starts,1);
  assert.equal(url.value,link);
  assert.equal(url.selected,true);
  h.elements['account-cancel'].listeners.click();
  await settle();
  assert.equal(url.value,'');
});
test('company administration follows the verified account and passes only its company ID to native settings', async () => {
  const company = {company_id:'10000000-0000-0000-0000-000000000002', company_name:'Fixture Company'};
  let phase = 'authenticated';
  const opened = [];
  const h = harness(async (command, args) => {
    if (command === 'account_status') return {phase, message:phase, account:{companies:[company]}, factors:[]};
    if (command === 'open_company_settings') { opened.push(args.companyId); return; }
    if (command === 'account_cancel') { phase = 'signed_out'; return; }
    throw Error('Unexpected account command');
  });
  await h.ready;
  assert.equal(h.elements['account-companies'].hidden,false);
  const [name, manage] = h.elements['account-company-list'].children[0].children;
  assert.equal(name.textContent,company.company_name);
  await manage.listeners.click();
  assert.deepEqual(opened,[company.company_id]);
  h.elements['account-cancel'].listeners.click();
  await settle();
  assert.equal(h.elements['account-companies'].hidden,true);
  assert.equal(h.elements['account-company-list'].children.length,0);
});
test('late old frame cannot end a newly restarted capture',async()=>{
  const h=harness();await h.ready;const first=h.start();await settle();assert.equal(h.pending.length,1);
  const stopping=h.stop();h.stops.shift()('Stopped');await stopping;
  const second=h.start();await settle();assert.equal(h.pending.length,2);
  h.pending[0].resolve(h.frame());await first;
  assert.equal(h.elements.start.disabled,true);assert.equal(h.elements.stop.disabled,false);assert.equal(h.draws,0);
  h.pending[1].resolve(h.frame(9));await settle();assert.equal(h.draws,1);assert.match(h.elements.metrics.textContent,/sequence 9/);
  const final=h.stop();h.stops.shift()('Stopped again');await final;h.timers.shift()();await second;
  assert.equal(h.elements.start.disabled,false);assert.equal(h.elements.metrics.textContent,'No retained preview');assert.ok(h.clears>=2);
});
test('old frame rejection cannot stop a new session',async()=>{
  const h=harness();await h.ready;const first=h.start();await settle();const stopping=h.stop();h.stops.shift()('Stopped');await stopping;
  const second=h.start();await settle();h.pending[0].reject(Error('old stream stopped'));await first;
  assert.equal(h.stopCalls,1);assert.equal(h.elements.status.textContent,'Native stream running');
  const final=h.stop();h.stops.shift()('Final stop');await final;h.pending[1].reject(Error('stopped'));await second;
  assert.equal(h.stopCalls,2);assert.equal(h.elements.start.disabled,false);
});
test('start stays disabled until stop acknowledgement arrives',async()=>{
  const h=harness();await h.ready;const run=h.start();await settle();const stopping=h.stop();h.pending[0].resolve(h.frame());await run;
  assert.equal(h.elements.start.disabled,true);h.stops.shift()('Stopped');await stopping;assert.equal(h.elements.start.disabled,false);
});

test('source failure clears an already successful preview before cleanup completes',async()=>{
  const h=harness();await h.ready;const run=h.start();await settle();
  for(let i=0;i<3;i++){h.pending[i].resolve(h.frame(i+1));await settle();h.timers.shift()();await settle();}
  assert.match(h.elements.checks.textContent,/Live native screen frames/);assert.equal(h.draws,3);
  h.pending[3].reject(Error('source window closed'));await settle();
  assert.doesNotMatch(h.elements.checks.textContent,/Live native screen frames/);assert.equal(h.elements.metrics.textContent,'No retained preview');
  assert.match(h.elements.status.textContent,/source window closed/);assert.ok(h.clears>=2);
  h.stops.shift()('Stopped');await run;assert.equal(h.elements.start.disabled,false);
});
test('failed stop never restores a stale preview or success status',async()=>{
  const h=harness();await h.ready;const run=h.start();await settle();const stopping=h.stop();
  h.stops.shift().reject(Error('native cleanup failed'));await stopping;
  assert.match(h.elements.status.textContent,/Stop failed/);assert.doesNotMatch(h.elements.checks.textContent,/Live native screen frames/);
  assert.equal(h.elements.metrics.textContent,'No retained preview');h.pending[0].reject(Error('old request ended'));await run;
});

test('standalone capture requires fresh confirmation after stopping a full screen preview',async()=>{
  const h=harness(undefined,true);await h.ready;
  const confirmation=h.elements['confirm-full-display'];
  assert.equal(h.elements.start.disabled,true);
  await h.start();assert.equal(h.pending.length,0);
  confirmation.checked=true;confirmation.listeners.change();
  assert.equal(h.elements.start.disabled,false);
  const run=h.start();await settle();assert.equal(h.pending.length,1);
  assert.equal(confirmation.checked,false);
  const stopped=h.stop();h.stops.shift()('Stopped');await stopped;
  h.pending[0].resolve(h.frame());await run;
  assert.equal(h.elements.start.disabled,true);
  await h.start();assert.equal(h.pending.length,1);
});
