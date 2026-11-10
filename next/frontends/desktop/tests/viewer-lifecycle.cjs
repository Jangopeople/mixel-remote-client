const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/viewer.js'),'utf8');
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function harness(){
  const html=fs.readFileSync(path.join(__dirname,'../viewer.html'),'utf8');
  const timers=[],inputs=[],stops=[];
  const elements=Object.fromEntries([...html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)].map(match=>[match[3],{
    disabled:/\bdisabled\b/.test(match[2]),textContent:'',value:'Mixel Grüezi 中文 😀',width:640,height:420,listeners:{},
    addEventListener(name,handler){this.listeners[name]=handler;},getContext(){return {clearRect(){},drawImage(){}};},
  }]));
  let state={request:0,phase:'ready',message:'Ready',received:0,control:false,injected:0,input_access:true};
  function invoke(command,args){
    if(command==='local_session_status')return Promise.resolve({...state});
    if(command==='request_local_session'){
      state={...state,request:state.request+1,phase:'pending',control:args.control,injected:0};
      return Promise.resolve({...state});
    }
    if(command==='next_local_video')return new Promise(()=>{});
    if(command==='send_local_input')return new Promise((resolve,reject)=>inputs.push({args,resolve,reject}));
    if(command==='close_local_session'){
      assert.equal(typeof args?.request,'number','viewer close must identify its own session');
      stops.push(args.request);if(args.request===state.request)state={...state,phase:'closed'};
      return Promise.resolve();
    }
    throw Error('Unexpected command '+command);
  }
  class VideoDecoder {
    static isConfigSupported(){return Promise.resolve({supported:true});}
    constructor(){this.state='unconfigured';this.decodeQueueSize=0;}
    configure(){this.state='configured';}
    close(){this.state='closed';}
  }
  vm.runInNewContext(script,{window:{__TAURI__:{core:{invoke}}},document:{querySelector:selector=>elements[selector.slice(1)]},
    VideoDecoder,EncodedVideoChunk:class{},ArrayBuffer,Uint8Array,DataView,setTimeout:fn=>timers.push(fn)});
  return {elements,inputs,stops,ready:settle(),
    async tick(){const queued=timers.splice(0);for(const fn of queued)fn();await settle();},
    async request(control){elements[control?'connect-keyboard':'connect'].listeners.click();await settle();state={...state,phase:'active'};await this.tick();},
    async disconnect(){await elements.disconnect.listeners.click();await settle();},
  };
}

test('view-only reconnect clears the previous keyboard acknowledgement and disables input',async()=>{
  const h=harness();await h.ready;await h.request(true);
  h.elements['send-text'].listeners.click();await settle();h.inputs[0].resolve();await settle();
  assert.match(h.elements['input-status'].textContent,/Host authenticated/);
  await h.disconnect();assert.equal(h.elements['input-status'].textContent,'No active keyboard session');
  await h.request(false);
  assert.equal(h.elements['input-status'].textContent,'View-only session; keyboard control is disabled');
  assert.equal(h.elements['send-text'].disabled,true);assert.equal(h.elements['test-keys'].disabled,true);
  assert.deepEqual(h.stops,[1]);
});

test('old input rejection cannot close the replacement session or overwrite its view-only status',async()=>{
  const h=harness();await h.ready;await h.request(true);
  h.elements['send-text'].listeners.click();await settle();assert.equal(h.inputs.length,1);
  await h.disconnect();await h.request(false);h.inputs[0].reject(Error('retired transport'));await settle();
  assert.deepEqual(h.stops,[1]);assert.equal(h.elements.disconnect.disabled,false);
  assert.equal(h.elements['input-status'].textContent,'View-only session; keyboard control is disabled');
});

test('retired keyboard batch stops after the outstanding acknowledgement without submitting more keys',async()=>{
  const h=harness();await h.ready;await h.request(true);
  h.elements['test-keys'].listeners.click();await settle();assert.equal(h.inputs.length,1);
  await h.disconnect();await h.request(true);h.inputs[0].resolve();await settle();
  assert.equal(h.inputs.length,1);assert.deepEqual(h.stops,[1]);
  assert.equal(h.elements['input-status'].textContent,'Keyboard control is active for this fixture session');
});
