const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/devices.js'),'utf8');
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const devices=[{device:'d1',connection_id:196516669,name:'Office PC',os:'windows'},{device:'d2',connection_id:440207203,name:'MacBook',os:'macos'},{device:'d3',connection_id:648493658,name:'iPhone',os:'other'}];
function harness(remote){
  const listeners={},calls=[],timers=[];
  function node(tag){return {tagName:tag.toUpperCase(),hidden:false,disabled:false,value:'',title:'',dataset:{},children:[],listeners:{},textContent:'',className:'',classList:{add(){}},
    addEventListener(n,c){this.listeners[n]=c;},append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);},replaceChildren(){this.children=[];}};}
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const elements=Object.fromEntries([...html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)].map(m=>[m[2],node(m[1])]));
  elements.devices.hidden=false;
  const dispatched=[];
  function dispatch(type,detail){for(const c of listeners[type]??[])c({type,detail});}
  function invoke(command,args){
    calls.push({command,args});
    if(command==='account_status')return Promise.resolve({phase:'authenticated',account:{profile:'p',organization:null,staff:true}});
    if(command==='remote_status')return Promise.resolve({...remote});
    if(command==='remote_devices')return Promise.resolve({devices,next:null});
    if(command==='remote_presence')return Promise.resolve();
    return Promise.resolve();
  }
  vm.runInNewContext(script,{window:{__TAURI__:{core:{invoke}},setInterval(){},setTimeout(c){timers.push(c);}},localStorage:{getItem(){return null;}},
    document:{hidden:false,getElementById:id=>elements[id],createElement:node,addEventListener(n,c){(listeners[n]??=[]).push(c);},dispatchEvent(e){dispatched.push(e);dispatch(e.type,e.detail);}},
    CustomEvent:class{constructor(t,o){this.type=t;this.detail=o?.detail;}}});
  const rows=()=>elements['devices-list'].children.map(li=>{const [identity,state,,]=li.children;const connect=li.children[li.children.length-1];return {name:identity.children[0].textContent,status:state.children[0].textContent,reason:state.children[1].textContent,connect};});
  return {elements,calls,timers,dispatched,rows,emit:dispatch};
}
test('while this computer lost its connection, devices say why, Connect still works and a reconnect is offered',async()=>{
  const h=harness({phase:'error',host:440207203,online:[],message:'Connection authorization expired; sharing and input have stopped'});await settle();
  const r=h.rows();
  assert.equal(r.length,3);
  assert.equal(r[0].status,'Status unknown');
  assert.match(r[0].reason,/once this computer is connected/);
  assert.equal(r[0].connect.disabled,false,'Connect is not greyed out just because this computer is reconnecting');
  assert.equal(r[1].status,'This computer · not connected');assert.equal(r[1].connect.disabled,true);
  assert.equal(h.elements['devices-connection'].hidden,false);
  assert.match(h.elements['devices-connection-detail'].textContent,/authorization expired/);
  r[0].connect.listeners.click();
  assert.deepEqual(h.dispatched.filter(e=>e.type==='mixel-connect-to').map(e=>e.detail.id),[196516669]);
  h.elements['devices-reconnect'].listeners.click();
  assert.ok(h.dispatched.some(e=>e.type==='mixel-reconnect-now'));
});
test('when connected, devices show Online or Offline with a reason, and Offline cannot be connected',async()=>{
  const h=harness({phase:'ready',host:440207203,online:[]});await settle();
  assert.ok(h.calls.some(c=>c.command==='remote_presence'));
  for(const t of h.timers.splice(0))t();
  h.emit('mixel-remote-state',{phase:'ready',host:440207203,online:[648493658]});
  const r=h.rows();
  assert.equal(h.elements['devices-connection'].hidden,true);
  assert.equal(r[0].status,'Offline');assert.match(r[0].reason,/Turned off, asleep or without internet/);
  assert.equal(r[0].connect.disabled,true);assert.match(r[0].connect.title,/offline/);
  assert.equal(r[2].status,'Online');assert.equal(r[2].connect.disabled,false);
});
test('during a session other devices explain that the session must end first',async()=>{
  const h=harness({phase:'active',host:440207203,online:[196516669]});await settle();
  for(const t of h.timers.splice(0))t();
  const r=h.rows();
  assert.equal(r[0].status,'Online');
  assert.equal(r[0].connect.disabled,true);assert.match(r[0].connect.title,/End the current session first/);
});
