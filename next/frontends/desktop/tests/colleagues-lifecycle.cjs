const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/colleagues.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
function node(tag='div'){
  const item={tag,hidden:false,disabled:false,textContent:'',value:'',checked:false,title:'',className:'',type:'',children:[],attributes:{},listeners:{},
    addEventListener(name,handler){(this.listeners[name]??=[]).push(handler);},
    click(){for(const handler of this.listeners.click??[])handler();},
    replaceChildren(...children){this.children=children;},append(...children){this.children.push(...children);},
    setAttribute(name,value){this.attributes[name]=value;},focus(){}};
  return item;
}
function harness(){
  const ids=[...html.matchAll(/id="([^"]+)"/g)].map(match=>match[1]);
  const elements=Object.fromEntries(ids.map(id=>[id,node()]));
  const calls=[],handlers={};
  const document={getElementById:id=>elements[id]??null,createElement:tag=>node(tag),
    addEventListener(name,handler){(handlers[name]??=[]).push(handler);},
    dispatchEvent(event){for(const handler of handlers[event.type]??[])handler(event);}};
  class CustomEvent{constructor(type,init){this.type=type;this.detail=init?.detail;}}
  const invoke=async(command,args)=>{calls.push([command,args]);return null;};
  vm.runInNewContext(script,{window:{__TAURI__:{core:{invoke}}},document,CustomEvent,setInterval:()=>0,Date,Map,Number,String,Error});
  const state=detail=>document.dispatchEvent(new CustomEvent('mixel-remote-state',{detail}));
  const texts=item=>[item.textContent,...item.children.flatMap(texts)].join(' ');
  return {elements,calls,state,document,CustomEvent,texts};
}
const host={phase:'active',session:'s1',sharing:true,control:true,peer:111111111,peer_features:['join','switch']};
const technician={phase:'active',session:'s2',sharing:false,control:true,peer:222222222,peer_features:['join','switch']};

test('the person at the computer decides about a colleague and sees who is connected',async()=>{
  const h=harness();
  h.state({...host,join_request:{session:'j1',connection:333444555,inviter_connection:111111111,control:true,unattended:false},colleagues:[{session:'j1',connection:333444555,control:false,state:'asking'}]});
  assert.equal(h.elements['colleague-request'].hidden,false);
  assert.match(h.elements['colleague-request-text'].textContent,/333 444 555.*keyboard and mouse/);
  assert.equal(h.elements['colleague-list'].hidden,true,'a colleague still asking is not listed as connected');
  h.elements['colleague-view'].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_decide_join',{session:'j1',accept:true,control:false}]);
  h.state({...host,join_request:null,colleagues:[{session:'j1',connection:333444555,control:false,state:'active'}]});
  assert.equal(h.elements['colleague-request'].hidden,true);
  assert.equal(h.elements['colleague-list'].hidden,false);
  assert.match(h.texts(h.elements['colleague-items']),/Technician 333 444 555 · viewing/);
  h.elements['colleague-items'].children[0].children[1].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_remove_colleague',{session:'j1'}]);
  // With a colleague connected, sides cannot be switched.
  assert.equal(h.elements['host-switch'].hidden,true);
});

test('a colleague asking only to view gets no control button, and declining is sent',async()=>{
  const h=harness();
  h.state({...host,join_request:{session:'j2',connection:333444555,inviter_connection:111111111,control:false,unattended:false}});
  assert.equal(h.elements['colleague-control'].hidden,true);
  h.elements['colleague-decline'].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_decide_join',{session:'j2',accept:false,control:false}]);
});

test('the technician invites a colleague or falls back to a meeting link',async()=>{
  const h=harness();let meeting=0;
  h.document.addEventListener('mixel-invite-meeting',()=>meeting++);
  h.state({...technician,peer_features:[]});
  h.document.dispatchEvent(new h.CustomEvent('mixel-invite-colleague'));
  assert.equal(meeting,1,'an older computer: Mixel Meet link');
  h.state(technician);
  h.document.dispatchEvent(new h.CustomEvent('mixel-company-members',{detail:{members:[{user_id:'me',name:'Me',role:'technician'},{user_id:'u2',name:'Tom Tech',role:'technician'},{user_id:'u3',name:'Mia Member',role:'member'}],me:'me'}}));
  h.document.dispatchEvent(new h.CustomEvent('mixel-invite-colleague'));
  assert.equal(h.elements['invite-panel'].hidden,false);
  assert.deepEqual(h.elements['invite-member'].children.map(option=>option.textContent),['Choose a colleague…','Tom Tech'],'only other technicians are offered');
  h.elements['invite-send'].click();await settle();
  assert.match(h.elements['invite-status'].textContent,/Choose a colleague/);
  h.elements['invite-device'].value='444 555 666';h.elements['invite-control'].checked=true;
  h.elements['invite-send'].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_invite',{session:'s2',user:null,device:444555666,control:true}]);
  h.elements['invite-member'].value='u2';h.elements['invite-control'].checked=false;
  h.elements['invite-send'].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_invite',{session:'s2',user:'u2',device:null,control:false}]);
  h.state({...technician,invite_message:'Your colleague is not online in Mixel Remote right now'});
  assert.match(h.elements['invite-status'].textContent,/not online/);
  // A second invitation with the same answer shows that answer again, not a stuck "Sending".
  h.elements['invite-send'].click();
  assert.match(h.elements['invite-status'].textContent,/Sending/);
  await settle();
  h.state({...technician,invite_message:'Your colleague is not online in Mixel Remote right now'});
  assert.match(h.elements['invite-status'].textContent,/not online/);
  h.state({...technician,phase:'ready',session:null});
  assert.equal(h.elements['invite-panel'].hidden,true,'the panel closes with the session');
});

test('invitations show the inviter and join only when this app is free',async()=>{
  const h=harness();
  h.document.dispatchEvent(new h.CustomEvent('mixel-company-members',{detail:{members:[{user_id:'u1',name:'Ann Admin',role:'admin'}],me:'me'}}));
  const offer={invitation:'i1',host:555666777,inviter:'u1',inviter_connection:111111111,control:true,unattended:false,expires_at:Date.now()/1000+60};
  const expired={...offer,invitation:'i0',expires_at:Date.now()/1000-1};
  h.state({phase:'active',session:'x',sharing:false,peer:1,invitations:[offer,expired]});
  assert.equal(h.elements['colleague-invitations'].children.length,1);
  assert.match(h.texts(h.elements['colleague-invitations']),/Ann Admin invites you to join their session on computer 555 666 777 with keyboard and mouse/);
  assert.equal(h.elements['colleague-invitations'].children[0].children[1].disabled,true,'busy in another session');
  h.state({phase:'ready',session:null,sharing:false,peer:null,invitations:[offer]});
  h.elements['colleague-invitations'].children[0].children[1].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_join',{invitation:'i1'}]);
});

test('both sides can switch sides in a one-to-one session, and the Wayland limits are explained',async()=>{
  const h=harness();
  h.state(technician);
  assert.equal(h.elements['remote-switch'].hidden,false);
  assert.equal(h.elements['host-switch'].hidden,true);
  h.elements['remote-switch'].click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_switch_sides',{session:'s2'}]);
  h.state({...host,switched:true});
  assert.equal(h.elements['host-switch'].hidden,false);
  assert.equal(h.elements['host-switch-button'].textContent,'Switch back');
  h.state({...technician,peer_features:['join']});
  assert.equal(h.elements['remote-switch'].hidden,true,'an app without switching keeps the meeting fallback');
  h.state({...technician,joined_session:true});
  assert.equal(h.elements['remote-switch'].hidden,true,'a joined colleague does not switch');
  h.state({...technician,host_actions:['lock','wayland']});
  assert.equal(h.elements['remote-host-limits'].hidden,false);
  assert.match(h.elements['remote-host-limits'].textContent,/Wayland/);
});

test('rows survive the four state reads a second, so a click between two reads is not lost',async()=>{
  const h=harness();let scrolled=0;
  h.elements['colleague-invitations'].scrollIntoView=()=>scrolled++;
  const offer={invitation:'i1',host:555666777,inviter:'u1',inviter_connection:111111111,control:false,unattended:false,expires_at:Date.now()/1000+60};
  h.state({phase:'ready',session:null,sharing:false,peer:null,invitations:[offer]});
  assert.equal(scrolled,1,'a new invitation is brought into view');
  const join=h.elements['colleague-invitations'].children[0].children[1];
  h.state({phase:'ready',session:null,sharing:false,peer:null,invitations:[offer]});
  assert.equal(h.elements['colleague-invitations'].children[0].children[1],join,'the same Join button after an unchanged read');
  join.click();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls.at(-1))),['remote_join',{invitation:'i1'}]);
  h.state({phase:'ready',session:null,sharing:false,peer:null,invitations:[offer,{...offer,invitation:'i2'}]});
  assert.equal(h.elements['colleague-invitations'].children.length,2,'a new invitation still appears');
  assert.equal(scrolled,2,'only new invitations scroll, not every read');
  const joined={...host,join_request:null,colleagues:[{session:'j1',connection:333444555,control:true,state:'active'}]};
  h.state(joined);
  const remove=h.elements['colleague-items'].children[0].children[1];
  h.state(joined);
  assert.equal(h.elements['colleague-items'].children[0].children[1],remove,'the same Remove button after an unchanged read');
  h.state({...joined,colleagues:[{session:'j1',connection:333444555,control:true,state:'active'},{session:'j2',connection:444555666,control:false,state:'connecting'}]});
  assert.equal(h.elements['colleague-items'].children.length,2);
  assert.match(h.texts(h.elements['colleague-items']),/444 555 666 · connecting/);
});

test('switch labels are set only when they change, so a click between two reads is not lost',async()=>{
  const h=harness();let writes=0,text='';
  Object.defineProperty(h.elements['remote-switch'],'textContent',{get:()=>text,set:value=>{writes++;text=value;}});
  // The script labelled the button once at start; identical reads leave it alone.
  h.state(technician);h.state(technician);h.state(technician);
  assert.equal(writes,0,'no write for identical reads');
  h.state({...technician,switched:true});h.state({...technician,switched:true});
  assert.equal(text,'Switch back');
  assert.equal(writes,1,'one write when the label changes');
});
