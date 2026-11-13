// Device and company management for Mixel Remote companies (SaaS tenants): the Devices page menus
// and add flows, the company panel (plan, trial, usage, members, invitations), "Create a company",
// and "Remove this computer" (offline-first). Runs the built scripts in a vm with a fake Tauri.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const dist=name=>fs.readFileSync(path.join(__dirname,'../dist',name),'utf8');
const scripts=['dialog.js','company.js','devices.js','this-computer.js'].map(name=>[name,dist(name)]);
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const settle=async()=>{for(let i=0;i<60;i++)await Promise.resolve();};
const TENANT='11111111-1111-4111-8111-111111111111', OWNER='aaaaaaaa-0000-4000-8000-000000000001', TECH='aaaaaaaa-0000-4000-8000-000000000002', MEMBER='aaaaaaaa-0000-4000-8000-000000000003';
const DAY=86400000;
function overview(over={}){
  return {tenant_id:TENANT,organization_id:'o',name:'Acme AG',slug:'acme',role:'owner',plan:'business',status:'trial',trial_ends_at:new Date(Date.now()+13.5*DAY).toISOString(),
    features:{groups:true},limits:{technicians:15,devices:2000,sessions:10},usage:{technicians:2,devices:2,members:3},...over};
}
const saasDevices=[
  {device_id:'d-office',name:'Office PC',os:'windows',group_id:null,group_name:null,connection_id:196516669,generation:1,enrolled_at:'x',revoked:false,added_via:'this_device'},
  {device_id:'d-mac',name:'MacBook',os:'macos',group_id:'g-sales',group_name:'Sales',connection_id:440207203,generation:3,enrolled_at:'x',revoked:false,added_via:'this_device'},
];
const groups=[{group_id:'g-sales',name:'Sales',devices:1,technicians:[]}];
const people=[
  {user_id:OWNER,name:'Olivia Owner',email:'olivia@acme.test',role:'owner',created_at:'x'},
  {user_id:TECH,name:'Tom Tech',email:'tom@acme.test',role:'technician',created_at:'x'},
  {user_id:MEMBER,name:'Mia Member',email:'mia@acme.test',role:'member',created_at:'x'},
];
function harness({me=OWNER,role='owner',info=overview({role}),account,remote={phase:'offline',host:null},answers={},saved=null,pending=false}={}){
  const listeners={},windowListeners={},calls=[],timers=[],intervals=[],dispatched=[],store=new Map(),clipboard=[];
  if(saved)store.set('mixel.remote.this-computer.v1',JSON.stringify(saved));
  if(pending)store.set('mixel.remote.self-removal.v1','true');
  function node(tag){return {tagName:tag.toUpperCase(),hidden:false,disabled:false,value:'',title:'',dataset:{},children:[],listeners:{},attributes:{},textContent:'',className:'',style:{setProperty(){}},classList:{add(){}},
    addEventListener(n,c){this.listeners[n]=c;},append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);},replaceChildren(...c){this.children=[...c];},
    setAttribute(n,v){this.attributes[n]=String(v);},getAttribute(n){return this.attributes[n]??null;},focus(){},select(){}};}
  const elements=Object.fromEntries([...html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)].map(m=>[m[2],node(m[1])]));
  for(const id of ['manage-dialog','company-panel','company-signup','this-computer-panel','this-computer-pending','devices-add-this','devices-add-others','devices-group','devices-plan','company-invite','company-invites','company-invites-title'])elements[id].hidden=true;
  elements.devices.hidden=false;
  let accountValue=account??{phase:'authenticated',account:{profile:me,organization:'o',staff:false,personal:false,has_company:true,has_personal:false}};
  let remoteValue={...remote};
  function dispatch(type,detail){for(const c of listeners[type]??[])c({type,detail});}
  function invoke(command,args){
    calls.push({command,args:args===undefined?undefined:JSON.parse(JSON.stringify(args))});
    if(command in answers){const answer=answers[command];const value=typeof answer==='function'?answer(args,calls):answer;return value instanceof Error?Promise.reject(value.message):Promise.resolve(value);}
    switch(command){
      case 'account_status':return Promise.resolve(accountValue);
      case 'remote_status':return Promise.resolve({...remoteValue});
      case 'saas_my_tenants':return Promise.resolve([{tenant_id:TENANT,organization_id:'o',name:'Acme AG',slug:'acme',role:info.role,plan:info.plan,status:info.status,trial_ends_at:info.trial_ends_at}]);
      case 'saas_tenant_overview':return Promise.resolve(info);
      case 'saas_members':return Promise.resolve(people);
      case 'saas_invites':return Promise.resolve([]);
      case 'saas_devices':return Promise.resolve(saasDevices.filter(d=>!args.group||d.group_id===args.group));
      case 'saas_groups':return Promise.resolve(groups);
      case 'remote_devices':return Promise.resolve({devices:[{device:'x',connection_id:123456789,name:'Staff PC',os:'linux'}],next:null});
      case 'this_computer_name':return Promise.resolve('Front desk');
      case 'remote_disconnect':remoteValue={phase:'offline',host:null};return Promise.resolve();
      case 'device_self_remove_flush':return Promise.resolve(null);
      default:return Promise.resolve();
    }
  }
  const window={__TAURI__:{core:{invoke}},setInterval(c){intervals.push(c);},setTimeout(c){timers.push(c);},addEventListener(n,c){(windowListeners[n]??=[]).push(c);}};
  const context={window,localStorage:{getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)},
    navigator:{clipboard:{writeText(text){clipboard.push(text);return Promise.resolve();}}},
    document:{hidden:false,getElementById:id=>elements[id],createElement:node,addEventListener(n,c){(listeners[n]??=[]).push(c);},dispatchEvent(e){dispatched.push(e);dispatch(e.type,e.detail);}},
    CustomEvent:class{constructor(t,o){this.type=t;this.detail=o?.detail;}}};
  vm.createContext(context);
  for(const [name,code] of scripts)vm.runInContext(code,context,{filename:name});
  const all=(root,found=[])=>{for(const child of root.children??[]){found.push(child);all(child,found);}return found;};
  const byText=(root,text)=>all(root).find(n=>n.textContent===text);
  const field=id=>all(elements['manage-dialog-fields']).find(n=>n.dataset?.field===id);
  const rows=()=>elements['devices-list'].children.map(li=>{const [identity,state]=li.children;const connect=li.children[li.children.length-1];const menu=li.children.find(c=>c.className==='device-menu');
    return {name:identity.children[0].textContent,group:identity.children[2]?.textContent??null,status:state.children[0].textContent,connect,menu,
      items:menu?Object.fromEntries(menu.children[1].children.map(b=>[b.textContent,b])):null};});
  const confirmDialog=async()=>{await elements['manage-dialog-form'].listeners.submit({preventDefault(){}});await settle();};
  const signedIn=async(value=accountValue)=>{accountValue=value;dispatch('mixel-account-state',value);await settle();};
  return {elements,calls,timers,intervals,dispatched,store,clipboard,windowListeners,rows,field,byText,all,confirmDialog,signedIn,emit:dispatch,
    called:command=>calls.filter(c=>c.command===command),setRemote(value){remoteValue={...value};dispatch('mixel-remote-state',value);}};
}

test('an owner sees the company: plan, trial days, usage, and the devices with group, menus and add actions',async()=>{
  const h=harness();await h.signedIn();
  const e=h.elements;
  assert.equal(e['company-panel'].hidden,false);
  assert.equal(e['company-name'].textContent,'Acme AG');
  assert.equal(e['company-plan'].textContent,'Business plan');
  assert.equal(e['company-role'].textContent,'Owner');
  assert.equal(e['company-status'].textContent,'Free trial · 14 days left');
  assert.deepEqual(e['company-usage'].children.map(r=>[r.children[0].textContent,r.children[1].textContent]),[['Devices','2 / 2000'],['Technicians','2 / 15'],['Members','3']]);
  assert.equal(h.called('remote_devices').length,0,'a company member never uses the staff directory');
  assert.deepEqual(h.called('saas_devices').at(-1).args,{tenant:TENANT,group:null,search:null});
  const r=h.rows();
  assert.deepEqual(r.map(x=>[x.name,x.group]),[['Office PC',null],['MacBook','Sales']]);
  assert.equal(r[0].connect.textContent,'Connect','Connect stays the last control of a row');
  assert.deepEqual(Object.keys(r[0].items),['Rename…','Move to group…','Remove…']);
  assert.equal(e['devices-add-this'].hidden,false);assert.equal(e['devices-add-others'].hidden,false);
  assert.equal(e['devices-add-this'].disabled,false);
  assert.equal(e['devices-group'].hidden,false);
  assert.deepEqual(e['devices-group'].children.map(o=>o.textContent),['All groups','No group','Sales']);
  e['devices-group'].value='g-sales';e['devices-group'].listeners.change();await h.signedIn();await settle();
  assert.deepEqual(h.called('saas_devices').at(-1).args,{tenant:TENANT,group:'g-sales',search:null});
  assert.deepEqual(h.rows().map(x=>x.name),['MacBook']);
});

test('Remove asks first, explains the device is revoked at once and its sessions end, and only then removes',async()=>{
  const h=harness();await h.signedIn();
  const office=h.rows()[0];
  office.menu.children[0].listeners.click();
  assert.equal(office.menu.children[1].hidden,false,'the menu opens');
  office.items['Remove…'].listeners.click();
  const e=h.elements;
  assert.equal(e['manage-dialog'].hidden,false);
  assert.equal(e['manage-dialog-title'].textContent,'Remove Office PC?');
  assert.match(e['manage-dialog-text'].textContent,/removed from Acme AG at once: its registration is revoked, running sessions with it end/);
  assert.equal(e['manage-dialog-confirm'].className,'button-danger');
  e['manage-dialog-cancel'].listeners.click();
  assert.equal(e['manage-dialog'].hidden,true);
  assert.equal(h.called('saas_remove_device').length,0,'cancel removes nothing');
  h.rows()[0].items['Remove…'].listeners.click();
  await h.confirmDialog();
  assert.deepEqual(h.called('saas_remove_device').map(c=>c.args),[{tenant:TENANT,device:'d-office'}]);
  assert.equal(e['manage-dialog'].hidden,true);
  assert.equal(e['devices-status'].textContent.includes('Page 1')||e['devices-status'].textContent==='Office PC was removed.',true);
  assert.ok(h.dispatched.some(x=>x.type==='mixel-company-refresh'),'usage is refreshed');
  assert.ok(h.called('saas_devices').length>=2,'the list reloads');
});

test('a refused removal keeps the dialog open with the database message',async()=>{
  const h=harness({answers:{saas_remove_device:new Error('Device unavailable')}});await h.signedIn();
  h.rows()[1].items['Remove…'].listeners.click();
  await h.confirmDialog();
  assert.equal(h.elements['manage-dialog'].hidden,false);
  assert.equal(h.elements['manage-dialog-error'].textContent,'Device unavailable');
  assert.equal(h.elements['manage-dialog-confirm'].disabled,false);
});

test('Rename and Move to group send the device, the new name and the chosen or new group',async()=>{
  const h=harness({answers:{saas_rename_device:'Reception PC',saas_create_group:'g-new'}});await h.signedIn();
  h.rows()[0].items['Rename…'].listeners.click();
  assert.equal(h.field('name').value,'Office PC');
  h.field('name').value='Reception PC';await h.confirmDialog();
  assert.deepEqual(h.called('saas_rename_device').map(c=>c.args),[{tenant:TENANT,device:'d-office',name:'Reception PC'}]);
  h.rows()[0].items['Move to group…'].listeners.click();
  assert.deepEqual(h.field('group').children.map(o=>o.textContent),['No group','Sales']);
  h.field('group').value='g-sales';await h.confirmDialog();
  assert.deepEqual(h.called('saas_move_device').at(-1).args,{tenant:TENANT,device:'d-office',group:'g-sales'});
  h.rows()[0].items['Move to group…'].listeners.click();
  h.field('create').value='Warehouse';await h.confirmDialog();
  assert.deepEqual(h.called('saas_create_group').at(-1).args,{tenant:TENANT,name:'Warehouse'});
  assert.deepEqual(h.called('saas_move_device').at(-1).args,{tenant:TENANT,device:'d-office',group:'g-new'});
});

test('roles: a member sees devices but no management; a technician manages devices but cannot deploy or invite',async()=>{
  const m=harness({me:MEMBER,role:'member'});await m.signedIn();
  assert.equal(m.rows().length,2);
  assert.equal(m.rows()[0].menu,undefined,'members get no device menu');
  assert.equal(m.rows()[0].connect.textContent,'Connect');
  assert.equal(m.elements['devices-add-this'].hidden,true);assert.equal(m.elements['devices-add-others'].hidden,true);
  assert.equal(m.elements['devices-group'].hidden,true);
  assert.equal(m.called('saas_members').length,0,'members cannot list members');
  assert.equal(m.called('saas_groups').length,0);
  assert.equal(m.elements['company-invite'].hidden,true);
  assert.equal(m.byText(m.elements['company-members'],'Leave company')!==undefined,true,'anyone may leave');
  const t=harness({me:TECH,role:'technician'});await t.signedIn();
  assert.ok(t.rows()[0].menu);
  assert.equal(t.elements['devices-add-this'].hidden,false);assert.equal(t.elements['devices-add-others'].hidden,true,'deploy links are for owners and administrators');
  assert.equal(t.elements['company-invite'].hidden,true);
  const memberRows=t.elements['company-members'].children;
  assert.equal(memberRows.length,3,'technicians see the members');
  assert.equal(t.all(memberRows[0]).some(n=>n.textContent==='Change role'||n.textContent==='Remove'),false,'technicians change no roles');
  assert.equal(t.all(memberRows[1]).some(n=>n.textContent==='Leave company'),true);
});

test('read-only company: adding and changes are blocked with a reason; removing devices still works',async()=>{
  const h=harness({info:overview({status:'read_only',trial_ends_at:new Date(Date.now()-DAY).toISOString()})});await h.signedIn();
  const e=h.elements;
  assert.match(e['company-status'].textContent,/^Read-only: the trial has ended/);
  assert.equal(e['devices-add-this'].disabled,true);assert.equal(e['devices-add-this'].title,'This company is read-only');
  assert.equal(e['devices-add-others'].disabled,true);
  assert.equal(e['devices-plan'].hidden,false);assert.match(e['devices-plan'].textContent,/read-only\. You can still remove devices/);
  const r=h.rows()[0];
  assert.equal(r.items['Rename…'].disabled,true);assert.equal(r.items['Move to group…'].disabled,true);
  assert.equal(r.items['Remove…'].disabled,false);
  assert.equal(e['company-invite'].disabled,true);
  assert.equal(h.all(e['company-members']).some(n=>n.textContent==='Change role'),false);
  assert.equal(h.all(e['company-members']).some(n=>n.textContent==='Remove'),true,'members can still be removed');
});

test('plan limits: at the device limit nothing can be added, and at the technician limit only members can be invited',async()=>{
  const h=harness({info:overview({plan:'solo',features:{groups:false},limits:{technicians:1,devices:2,sessions:1},usage:{technicians:1,devices:2,members:3}})});await h.signedIn();
  const e=h.elements;
  assert.equal(e['devices-add-this'].disabled,true);assert.equal(e['devices-add-this'].title,'Device limit of your plan reached');
  assert.equal(e['devices-plan'].textContent,'Device limit of your plan reached (2 of 2). Remove a device or upgrade the plan to add more.');
  assert.deepEqual(Object.keys(h.rows()[0].items),['Rename…','Remove…'],'no groups on the Solo plan');
  assert.equal(e['devices-group'].hidden,true);
  e['company-invite'].listeners.click();
  const choice=h.field('role');
  assert.deepEqual(choice.children.map(o=>[o.value,o.disabled]),[['technician',true],['admin',true],['member',false]]);
  assert.equal(choice.value,'member');
  assert.match(choice.children[0].textContent,/plan limit reached/);
});

test('Add other computers creates a deploy link and shows the command to copy with short instructions',async()=>{
  const command="$t='mrt_secret'; $f=Join-Path $env:TEMP 'Mixel-Remote-Setup.exe'";
  const h=harness({answers:{saas_create_deploy_link:{token:'mrt_secret',windows_command:command}}});await h.signedIn();
  h.elements['devices-add-others'].listeners.click();
  assert.equal(h.elements['manage-dialog-title'].textContent,'Add other computers');
  assert.equal(h.field('computers').value,'50');assert.equal(h.field('days').value,'30');
  h.field('label').value='Office rollout';h.field('computers').value='12';h.field('group').value='g-sales';
  await h.confirmDialog();
  assert.deepEqual(h.called('saas_create_deploy_link').map(c=>c.args),[{tenant:TENANT,label:'Office rollout',days:30,computers:12,group:'g-sales'}]);
  assert.equal(h.elements['manage-dialog'].hidden,false,'the result stays to be copied');
  const box=h.field('command');assert.equal(box.value,command);
  assert.ok(h.all(h.elements['manage-dialog-fields']).some(n=>/PowerShell as administrator/.test(n.textContent)));
  assert.ok(h.all(h.elements['manage-dialog-fields']).some(n=>/shown only now/.test(n.textContent)));
  h.byText(h.elements['manage-dialog-fields'],'Copy command').listeners.click();await h.signedIn();
  assert.deepEqual(h.clipboard,[command]);
  assert.equal(h.elements['manage-dialog-confirm'].textContent,'Done');
  await h.confirmDialog();assert.equal(h.elements['manage-dialog'].hidden,true);
});

test('Add this computer registers it, remembers its registration and brings it online',async()=>{
  const h=harness({answers:{saas_add_this_device:{device_id:'d-new',connection_id:555666777,generation:1,profile:'p',tenant_org:'o'}}});await h.signedIn();
  h.elements['devices-add-this'].listeners.click();await h.signedIn();
  assert.equal(h.field('name').value,'Front desk');
  await h.confirmDialog();
  assert.deepEqual(h.called('saas_add_this_device').map(c=>c.args),[{tenant:TENANT,name:'Front desk',group:null}]);
  assert.deepEqual(JSON.parse(h.store.get('mixel.remote.this-computer.v1')),{device:'d-new',connection_id:555666777,generation:1,name:'Front desk',tenant:TENANT});
  assert.ok(h.dispatched.some(e=>e.type==='mixel-device-enrolled'));
  assert.equal(h.elements['this-computer-panel'].hidden,false,'Settings now offers to remove this computer');
  assert.equal(h.elements['this-computer-detail'].textContent,'Registered as Front desk · Connection ID 555 666 777');
});

test('this computer removes itself offline: it goes offline, the removal is queued, then sent when online',async()=>{
  let flushes=0;
  const saved={device:'d-office',connection_id:196516669,generation:1,name:'Office PC',tenant:TENANT};
  const h=harness({saved,remote:{phase:'ready',host:196516669},answers:{device_self_remove:'queued',device_self_remove_flush:()=>++flushes===1?null:'removed'}});await h.signedIn();
  const e=h.elements;
  assert.equal(e['this-computer-panel'].hidden,false);
  e['this-computer-remove'].listeners.click();
  assert.equal(e['manage-dialog-title'].textContent,'Remove this computer from Mixel Remote?');
  assert.match(e['manage-dialog-text'].textContent,/removed from Acme AG at once: its registration is revoked, running sessions end/);
  assert.match(e['manage-dialog-text'].textContent,/Without internet, the removal is saved and sent as soon as this computer is online/);
  await h.confirmDialog();
  h.setRemote({phase:'offline',host:null});
  const order=h.calls.map(c=>c.command).filter(c=>['remote_disconnect','device_self_remove'].includes(c));
  assert.deepEqual(order,['remote_disconnect','device_self_remove'],'goes offline first');
  assert.ok(h.dispatched.some(x=>x.type==='mixel-self-remove-start'));
  assert.deepEqual(h.called('device_self_remove')[0].args,{registration:{device:'d-office',connection_id:196516669,generation:1},confirmed:true});
  assert.equal(e['this-computer-pending'].hidden,false,'shows "will be removed when online"');
  assert.equal(e['this-computer-remove'].hidden,true);
  assert.match(e['this-computer-status'].textContent,/will be removed as soon as it is online/);
  assert.equal(h.store.get('mixel.remote.self-removal.v1'),'true');
  // Back online: the saved request is sent.
  h.windowListeners.online[0]();await settle();
  assert.equal(h.store.has('mixel.remote.self-removal.v1'),false);
  assert.equal(h.store.has('mixel.remote.this-computer.v1'),false,'back to not set up');
  assert.equal(e['this-computer-pending'].hidden,true);
  assert.match(e['this-computer-status'].textContent,/It is no longer set up/);
  assert.ok(h.dispatched.some(x=>x.type==='mixel-self-remove-done'));
  assert.ok(h.dispatched.some(x=>x.type==='mixel-device-removed'));
});

test('a removal still waiting from an earlier start is shown and sent, without a sign-in',async()=>{
  const h=harness({pending:true,account:{phase:'signed_out',account:null},answers:{device_self_remove_flush:'queued'}});await settle();
  assert.equal(h.elements['this-computer-panel'].hidden,false);
  assert.equal(h.elements['this-computer-pending'].hidden,false);
  assert.equal(h.called('device_self_remove_flush').length,1);
  assert.equal(h.called('saas_my_tenants').length,0);
  for(const tick of h.intervals)tick();await settle();
  assert.ok(h.called('device_self_remove_flush').length>=2,'retried every 30 seconds');
});

test('a stale registration removes nothing and says so',async()=>{
  const saved={device:'d-office',connection_id:196516669,generation:1,name:'Office PC',tenant:TENANT};
  const h=harness({saved,answers:{device_self_remove:'stale'}});await h.signedIn();
  h.elements['this-computer-remove'].listeners.click();await h.confirmDialog();
  assert.match(h.elements['this-computer-status'].textContent,/nothing was removed/);
  assert.equal(h.store.has('mixel.remote.this-computer.v1'),false);
});

test('members: owners change any role, admins not owners, the last owner stays, and invitations give a link to copy',async()=>{
  const h=harness({answers:{saas_invite_member:'https://remote.mixel.ch/invite#mri_abc'}});await h.signedIn();
  const rows=h.elements['company-members'].children;
  assert.equal(h.all(rows[0]).some(n=>n.textContent==='Remove'||n.textContent==='Leave company'),false,'the only owner cannot leave');
  assert.ok(h.all(rows[0]).some(n=>n.textContent==='A company needs at least one owner.'));
  h.byText(rows[1],'Change role').listeners.click();
  assert.deepEqual(h.field('role').children.map(o=>o.value),['owner','admin','technician','member']);
  h.field('role').value='admin';await h.confirmDialog();
  assert.deepEqual(h.called('saas_set_member_role').map(c=>c.args),[{tenant:TENANT,member:TECH,role:'admin'}]);
  h.byText(h.elements['company-members'].children[2],'Remove').listeners.click();
  assert.equal(h.elements['manage-dialog-title'].textContent,'Remove Mia Member?');
  await h.confirmDialog();
  assert.deepEqual(h.called('saas_remove_member').map(c=>c.args),[{tenant:TENANT,member:MEMBER}]);
  h.elements['company-invite'].listeners.click();
  h.field('email').value='new@acme.test';h.field('role').value='technician';await h.confirmDialog();
  assert.deepEqual(h.called('saas_invite_member').map(c=>c.args),[{tenant:TENANT,email:'new@acme.test',role:'technician'}]);
  assert.equal(h.field('link').value,'https://remote.mixel.ch/invite#mri_abc');
  h.byText(h.elements['manage-dialog-fields'],'Copy invitation link').listeners.click();await settle();
  assert.deepEqual(h.clipboard,['https://remote.mixel.ch/invite#mri_abc']);
  const a=harness({me:TECH,role:'admin'});await a.signedIn();
  const owner=a.elements['company-members'].children[0];
  assert.equal(a.all(owner).some(n=>n.textContent==='Change role'||n.textContent==='Remove'),false,'admins cannot change or remove owners');
  a.byText(a.elements['company-members'].children[2],'Change role').listeners.click();
  assert.deepEqual(a.field('role').children.map(o=>o.value),['admin','technician','member']);
});

test('Create a company: offered to a Mixel ID without one, not to staff or company members',async()=>{
  const personal={phase:'authenticated',account:{profile:'p',organization:'p',staff:false,personal:true,has_company:false,has_personal:true}};
  const h=harness({account:personal});await h.signedIn(personal);
  assert.equal(h.elements['company-signup'].hidden,false);
  assert.equal(h.elements['company-panel'].hidden,true);
  assert.equal(h.called('saas_my_tenants').length,0,'a personal space has no company');
  h.elements['company-signup-name'].value='  Acme AG ';
  await h.elements['company-signup'].listeners.submit({preventDefault(){}});await settle();
  assert.deepEqual(h.called('saas_signup').map(c=>c.args),[{company:'Acme AG'}]);
  assert.match(h.elements['company-signup-status'].textContent,/14-day Business trial has started/);
  await h.signedIn({phase:'profile_setup',account:null});
  assert.equal(h.elements['company-signup'].hidden,false,'also before the Remote account is set up');
  await h.signedIn({phase:'authenticated',account:{profile:'s',organization:null,staff:true,personal:false,has_company:true}});
  assert.equal(h.elements['company-signup'].hidden,true);
  await h.signedIn({phase:'authenticated',account:{profile:OWNER,organization:'o',staff:false,personal:false,has_company:true}});
  assert.equal(h.elements['company-signup'].hidden,true);
  const short=harness({account:personal});await short.signedIn(personal);
  short.elements['company-signup-name'].value='A';await short.elements['company-signup'].listeners.submit({preventDefault(){}});
  assert.equal(short.called('saas_signup').length,0);
});

test('Mixel staff and ISM company members keep the existing device directory, without company tools',async()=>{
  const staff={phase:'authenticated',account:{profile:'s',organization:null,staff:true,personal:false,has_company:true}};
  const h=harness({account:staff});await h.signedIn(staff);
  assert.equal(h.called('saas_my_tenants').length,0);
  assert.equal(h.called('remote_devices').length>=1,true);
  assert.deepEqual(h.rows().map(r=>[r.name,r.menu]),[['Staff PC',undefined]]);
  assert.equal(h.elements['company-panel'].hidden,true);
  assert.equal(h.elements['devices-add-this'].hidden,true);
  const ism=harness({answers:{saas_my_tenants:[]}});await ism.signedIn();
  assert.equal(ism.called('remote_devices').length>=1,true,'no tenant: the existing directory');
  assert.equal(ism.called('saas_devices').length,0);
  assert.equal(ism.elements['company-panel'].hidden,true);
  assert.equal(ism.elements['this-computer-panel'].hidden,true);
});

test('every new text has German, French and Italian translations',()=>{
  const context={window:{}};vm.runInNewContext(dist('i18n-strings.js'),context);
  const tables=context.window.MIXEL_I18N;
  const translated=(language,text)=>text in tables[language].texts || tables[language].patterns.some(([source])=>new RegExp(source).test(text));
  // Static texts of the new markup.
  const parts=[/<div class="panel company-panel"[\s\S]*?<div class="panel" id="account-companies"/,/<div class="panel" id="this-computer-panel"[\s\S]*?<div class="panel mac-only"/,/<div id="manage-dialog"[\s\S]*?<\/form><\/div>/,/<div class="heading-actions">[\s\S]*?<\/div>/];
  const texts=new Set();
  for(const part of parts){const block=html.match(part)[0];for(const m of block.matchAll(/>([^<>]+)</g)){const text=m[1].trim();if(text)texts.add(text);}for(const m of block.matchAll(/aria-label="([^"]+)"/g))texts.add(m[1]);}
  // Texts and messages set by the scripts (a sample of each kind, with values filled in).
  for(const text of ['Business plan','Owner','Administrator','Technician','Member','Free trial · 9 days left','Free trial · ends today','Active','Change role','Leave company','Remove','Rename…','Move to group…','Remove…','All groups','No group','Manage',
    'Remove Office PC?','Choose the group of Office PC. Technicians reach the devices of the groups they are granted, and devices in no group.','Office PC moved to Sales.','Office PC was removed.','Renamed to Front desk.',
    'Device limit of your plan reached (2 of 2). Remove a device or upgrade the plan to add more.','This company is read-only. You can still remove devices; adding devices and other changes need an active plan.',
    'This computer was added as Front desk. Connection ID 555 666 777.','It works for 12 computers within 30 days. The command contains a secret: share it only with the people who deploy the computers. It is shown only now.',
    'Registered as Front desk · Connection ID 555 666 777','No connection to Mixel Remote right now. This computer will be removed as soon as it is online.','This computer was removed from Mixel Remote. It is no longer set up; add it again in Devices to use it.',
    'Remove this computer from Mixel Remote?','This computer is removed from Acme AG at once: its registration is revoked, running sessions end and nobody can connect to it any more. Without internet, the removal is saved and sent as soon as this computer is online. Afterwards this computer is no longer set up; you can add it again.',
    'Technician · valid until 20.11.2026','Role of Tom Tech changed to Administrator.','Invitation created for new@acme.test.','Your company is ready. Your free 14-day Business trial has started: add this computer in Devices.',
    'Device limit of your plan reached','Technician limit of your plan reached','This Mixel Remote account is read-only','Not allowed for this company','This computer is being removed from Mixel Remote. It is removed as soon as it is online.'])texts.add(text);
  const missing=[...texts].filter(text=>!['de','fr','it'].every(language=>translated(language,text)));
  assert.deepEqual(missing,[]);
});
