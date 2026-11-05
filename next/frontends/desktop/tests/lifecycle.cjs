const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/main.js'),'utf8');
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function harness(){
  const pending=[],stops=[],timers=[];let stopCalls=0,draws=0,clears=0;
  const context={putImageData(){draws++;},clearRect(){clears++;}};
  const elements=Object.fromEntries(['start','stop','status','metrics','checks','preview'].map(id=>[id,{disabled:id==='stop',textContent:'',width:640,height:420,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;},getContext(){return context;}}]));
  function invoke(command){
    if(command==='start_capture')return Promise.resolve('Native stream running');
    if(command==='next_frame')return new Promise((resolve,reject)=>pending.push({resolve,reject}));
    if(command==='stop_capture'){stopCalls++;return new Promise((resolve,reject)=>{resolve.reject=reject;stops.push(resolve);});}
    throw Error('Unexpected command');
  }
  vm.runInNewContext(script,{window:{__TAURI__:{core:{invoke}}},document:{getElementById:id=>elements[id]},ArrayBuffer,Uint8Array,Uint8ClampedArray,DataView,ImageData:class{},performance:{now:()=>100},setTimeout:resolve=>timers.push(resolve)});
  function frame(sequence=1){const buffer=new ArrayBuffer(24);const view=new DataView(buffer);view.setUint32(0,1,true);view.setUint32(4,1,true);view.setUint32(8,4,true);view.setBigUint64(12,BigInt(sequence),true);return buffer;}
  return {elements,pending,stops,timers,frame,get stopCalls(){return stopCalls;},get draws(){return draws;},get clears(){return clears;},start:()=>elements.start.listeners.click(),stop:()=>elements.stop.listeners.click()};
}
test('late old frame cannot end a newly restarted capture',async()=>{
  const h=harness();const first=h.start();await settle();assert.equal(h.pending.length,1);
  const stopping=h.stop();h.stops.shift()('Stopped');await stopping;
  const second=h.start();await settle();assert.equal(h.pending.length,2);
  h.pending[0].resolve(h.frame());await first;
  assert.equal(h.elements.start.disabled,true);assert.equal(h.elements.stop.disabled,false);assert.equal(h.draws,0);
  h.pending[1].resolve(h.frame(9));await settle();assert.equal(h.draws,1);assert.match(h.elements.metrics.textContent,/sequence 9/);
  const final=h.stop();h.stops.shift()('Stopped again');await final;h.timers.shift()();await second;
  assert.equal(h.elements.start.disabled,false);assert.equal(h.elements.metrics.textContent,'No retained preview');assert.ok(h.clears>=2);
});
test('old frame rejection cannot stop a new session',async()=>{
  const h=harness();const first=h.start();await settle();const stopping=h.stop();h.stops.shift()('Stopped');await stopping;
  const second=h.start();await settle();h.pending[0].reject(Error('old stream stopped'));await first;
  assert.equal(h.stopCalls,1);assert.equal(h.elements.status.textContent,'Native stream running');
  const final=h.stop();h.stops.shift()('Final stop');await final;h.pending[1].reject(Error('stopped'));await second;
  assert.equal(h.stopCalls,2);assert.equal(h.elements.start.disabled,false);
});
test('start stays disabled until stop acknowledgement arrives',async()=>{
  const h=harness();const run=h.start();await settle();const stopping=h.stop();h.pending[0].resolve(h.frame());await run;
  assert.equal(h.elements.start.disabled,true);h.stops.shift()('Stopped');await stopping;assert.equal(h.elements.start.disabled,false);
});

test('source failure clears an already successful preview before cleanup completes',async()=>{
  const h=harness();const run=h.start();await settle();
  for(let i=0;i<3;i++){h.pending[i].resolve(h.frame(i+1));await settle();h.timers.shift()();await settle();}
  assert.match(h.elements.checks.textContent,/PASS/);assert.equal(h.draws,3);
  h.pending[3].reject(Error('source window closed'));await settle();
  assert.doesNotMatch(h.elements.checks.textContent,/PASS/);assert.equal(h.elements.metrics.textContent,'No retained preview');
  assert.match(h.elements.status.textContent,/source window closed/);assert.ok(h.clears>=2);
  h.stops.shift()('Stopped');await run;assert.equal(h.elements.start.disabled,false);
});
test('failed stop never restores a stale preview or PASS status',async()=>{
  const h=harness();const run=h.start();await settle();const stopping=h.stop();
  h.stops.shift().reject(Error('native cleanup failed'));await stopping;
  assert.match(h.elements.status.textContent,/Stop failed/);assert.doesNotMatch(h.elements.checks.textContent,/PASS/);
  assert.equal(h.elements.metrics.textContent,'No retained preview');h.pending[0].reject(Error('old request ended'));await run;
});
