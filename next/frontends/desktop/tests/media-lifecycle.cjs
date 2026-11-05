const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/viewer.js'),'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
async function harness(){
  let phase='ready',draws=0,closes=0;const pending=[],timers=[],decoders=[];
  const elements=Object.fromEntries(['connect','connect-keyboard','send-text','test-keys','remote-text','input-status','disconnect','viewer-status','viewer-metrics','remote-video'].map(id=>[id,{disabled:false,textContent:'',width:640,height:420,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;},getContext(){return {drawImage(){draws++;},clearRect(){}};}}]));
  class Decoder{
    static async isConfigSupported(){return {supported:true};}
    constructor(callbacks){this.callbacks=callbacks;this.state='unconfigured';this.decodeQueueSize=0;decoders.push(this);}
    configure(){this.state='configured';} close(){this.state='closed';}
    decode(chunk){this.last=chunk;}
  }
  const invoke=async(command)=>{
    if(command==='local_session_status')return {request:1,phase,message:'fixture status',received:0};
    if(command==='next_local_video')return new Promise((resolve,reject)=>pending.push({resolve,reject}));
    if(command==='request_local_session'){phase='pending';return {};}
    if(command==='close_local_session'){closes++;phase='closed';return {};}
    throw Error(command);
  };
  vm.runInNewContext(script,{window:{__TAURI__:{core:{invoke}}},document:{querySelector:id=>elements[id.slice(1)]},VideoDecoder:Decoder,EncodedVideoChunk:class{constructor(value){Object.assign(this,value);}},ArrayBuffer,Uint8Array,DataView,setTimeout:(callback,ms)=>timers.push({callback,ms})});
  await settle();
  function packet(sequence,key=1){const bytes=new Uint8Array(23);const view=new DataView(bytes.buffer);view.setBigUint64(0,BigInt(sequence),true);bytes[8]=key;view.setBigUint64(9,33333n,true);bytes.set([0,0,0,1,0x65,1],17);return bytes;}
  async function active(){phase='active';timers.splice(timers.findIndex(t=>t.ms===200),1)[0].callback();await settle();}
  return {elements,pending,decoders,packet,active,get draws(){return draws;},get closes(){return closes;}};
}
test('disconnect rejects late media and closes late decoded frames without drawing',async()=>{
  const h=await harness();await h.active();assert.equal(h.pending.length,1);
  const old=h.decoders[0];await h.elements.disconnect.listeners.click();
  h.pending[0].resolve(h.packet(1));await settle();assert.equal(old.last,undefined);
  let released=false;old.callbacks.output({close(){released=true;}});
  assert.equal(released,true);assert.equal(h.draws,0);assert.equal(old.state,'closed');
});
test('missing predictive frame terminates and clears the viewer',async()=>{
  const h=await harness();await h.active();h.pending[0].resolve(h.packet(2));await settle();
  assert.equal(h.closes,1);assert.equal(h.draws,0);assert.match(h.elements['viewer-status'].textContent,/Invalid video sequence/);
  assert.equal(h.decoders[0].state,'closed');
});
test('decoder backpressure terminates instead of silently dropping video',async()=>{
  const h=await harness();await h.active();h.decoders[0].decodeQueueSize=5;
  h.pending[0].resolve(h.packet(1));await settle();assert.equal(h.closes,1);
  assert.match(h.elements['viewer-status'].textContent,/cannot keep up/);assert.equal(h.draws,0);
});
