// Favorites, recent connections, session reports and the session-end note (SQL 029) in the desktop
// app: the shared rules and CSV/PDF builders (report-export.js), the Devices page with stars and
// sections (devices.js), the Reports page and the note prompt (reports.js). Runs the built scripts
// in a vm with a fake Tauri.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const dist=name=>fs.readFileSync(path.join(__dirname,'../dist',name),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const settle=async()=>{for(let i=0;i<80;i++)await Promise.resolve();};
const plain=value=>JSON.parse(JSON.stringify(value));
const TENANT='11111111-1111-4111-8111-111111111111', OWNER='aaaaaaaa-0000-4000-8000-000000000001', TECH='aaaaaaaa-0000-4000-8000-000000000002';

function exportTools(){
  const window={};
  vm.runInNewContext(dist('report-export.js'),{window});
  return window.mixelReports;
}
const labels={technician:'Technician',email:'Email',device:'Device',remoteId:'Connection ID',start:'Start',end:'End',duration:'Duration',durationSeconds:'Duration (seconds)',
  kind:'Type',colleague:'Joined as colleague',note:'Note',sessionId:'Session ID',attended:'Attended',unattended:'Unattended',running:'Running',yes:'Yes',no:'No'};
function reportRow(over={}){
  return {session_id:'s1',technician_id:OWNER,technician_name:'Olivia Owner',technician_email:'olivia@acme.test',device_id:'d-office',device_name:'Office PC',connection_id:196516669,
    started_at:'2026-11-10T08:00:00Z',ended_at:'2026-11-10T09:02:05Z',duration_seconds:3725,kind:'attended',colleague:false,note:null,...over};
}
function checkPdf(pdf){
  assert.ok(pdf.startsWith('%PDF-1.4\n'));assert.ok(pdf.endsWith('%%EOF\n'));
  assert.ok(/^[\x09\x0a\x0d\x20-\x7e]*$/.test(pdf),'pure ASCII');
  const startxref=Number(/startxref\n(\d+)\n%%EOF\n$/.exec(pdf)[1]);
  assert.equal(pdf.slice(startxref,startxref+5),'xref\n');
  const header=/^xref\n0 (\d+)\n/.exec(pdf.slice(startxref));const size=Number(header[1]);
  const entries=pdf.slice(startxref+header[0].length).split('\n').slice(0,size);
  for(let id=1;id<size;id++){const offset=Number(entries[id].slice(0,10));assert.equal((entries[id]+'\n').length,20);assert.equal(pdf.slice(offset,offset+`${id} 0 obj`.length),`${id} 0 obj`);}
  for(const match of pdf.matchAll(/<< \/Length (\d+) >>\nstream\n/g)){const start=match.index+match[0].length;assert.equal(pdf.slice(start+Number(match[1]),start+Number(match[1])+10),'\nendstream');}
  return Number(/\/Count (\d+) >>/.exec(pdf)[1]);
}

test('export rules: CSV with BOM and quoting, a valid paginated PDF, durations and the filter range',()=>{
  const r=exportTools();
  const csv=r.reportCsv([reportRow({note:'Printer "HP", fixed',technician_name:'=cmd'}),reportRow({session_id:'s2',kind:'unattended',colleague:true,ended_at:null,duration_seconds:60,device_name:'Zürich Büro'})],labels);
  assert.ok(csv.startsWith('﻿Technician,Email,Device,Connection ID,Start,End,Duration,Duration (seconds),Type,Joined as colleague,Note,Session ID\r\n'));
  assert.match(csv,/"'=cmd"|'=cmd/);
  assert.ok(csv.includes('"Printer ""HP"", fixed"'));
  assert.ok(csv.includes(',Running,0:01:00,60,Unattended,Yes,,s2\r\n'));
  assert.ok(csv.includes('Zürich Büro'));
  const rows=Array.from({length:90},(_,i)=>reportRow({session_id:`s${i}`,device_name:`Device ${i+1}`,note:i%5?null:'Drucker in Zürich neu gestartet – alles OK, Kundin informiert, nächster Termin nächste Woche.'}));
  const pdf=r.reportPdf(rows,{company:'Acme AG',title:'Session report',range:'r',generated:'g',totals:'90 sessions · total 93:05:30',page:(p,n)=>`Page ${p} of ${n}`,labels,formatTime:iso=>iso.slice(0,16)});
  const pages=checkPdf(pdf);
  assert.ok(pages>=3);assert.ok(pdf.includes(`(Page ${pages} of ${pages})`));
  assert.ok(pdf.includes('(Device 90)'));assert.ok(pdf.includes('Z\\374rich'));assert.ok(pdf.includes('(90 sessions \\267 total 93:05:30)'));
  assert.equal(r.formatDuration(3725),'1:02:05');assert.equal(r.reportTotals([reportRow(),reportRow({duration_seconds:75})]).seconds,3800);
  assert.equal(r.reportFileName('Acme AG','2026-10-14','2026-11-13','pdf'),'mixel-remote-acmeag-sessions-2026-10-14-2026-11-13.pdf');
  const range=r.reportParams({from:'2026-11-01',to:'2026-11-13',technician:'',device:'d1'});
  assert.equal(new Date(range.params.from_at).getTime(),new Date(2026,10,1).getTime());
  assert.equal(new Date(range.params.to_at).getTime(),new Date(2026,10,14).getTime());
  assert.equal(range.params.technician,null);assert.equal(range.params.device,'d1');
  assert.match(r.reportParams({from:'2026-11-14',to:'2026-11-13',technician:'',device:''}).problem,/start date/);
  assert.match(r.reportParams({from:'2025-01-01',to:'2026-11-13',technician:'',device:''}).problem,/366/);
  assert.deepEqual(plain(r.defaultDays(new Date(2026,10,13,15))),{from:'2026-10-14',to:'2026-11-13'});
  assert.deepEqual(plain(r.ARGS.sessionReport(TENANT,range.params)),{tenant:TENANT,from:range.params.from_at,to:range.params.to_at,technician:null,device:'d1'});
  assert.deepEqual(plain(r.ARGS.recentDevices(TENANT)),{tenant:TENANT,maxCount:10});
  assert.equal(r.isMissing('Command saas_favorites not found'),true);
  assert.equal(r.isMissing('Could not find the function public.remote_saas_favorites(tenant) in the schema cache'),true);
  assert.equal(r.isMissing('Session reports need a paid plan'),false);assert.equal(r.needsPlan('Session reports need a paid plan'),true);
  assert.equal(r.canEditNote('admin',{technician_id:TECH},OWNER),true);
  assert.equal(r.canEditNote('technician',{technician_id:TECH},TECH),true);
  assert.equal(r.canEditNote('technician',{technician_id:OWNER},TECH),false);
});

test('favorites first by name, then recent connections newest first, then the rest, each once',()=>{
  const r=exportTools();
  const devices=[{device:'a',name:'Zeta'},{device:'b',name:'alpha'},{device:'c',name:'Beta'},{device:'d',name:'Delta'},{device:'e',name:'Echo'}];
  const parts=r.deviceSections(devices,new Set(['a','b']),[{device_id:'c',last_connected_at:'2026-11-01T08:00:00Z'},{device_id:'a',last_connected_at:'2026-11-12T08:00:00Z'},{device_id:'e',last_connected_at:'2026-11-11T08:00:00Z'},{device_id:'x',last_connected_at:'2026-11-13T08:00:00Z'}]);
  assert.deepEqual(plain(parts.favorites.map(d=>d.name)),['alpha','Zeta']);
  assert.deepEqual(plain(parts.recent.map(d=>d.name)),['Echo','Beta']);
  assert.deepEqual(plain(parts.others.map(d=>d.name)),['Delta']);
  assert.equal(r.deviceSections(devices,new Set(),devices.map((d,i)=>({device_id:d.device,last_connected_at:new Date(2026,0,i+1).toISOString()})),2).recent.length,2);
});

test('the note watcher reports only an ended session this computer controlled',()=>{
  const r=exportTools();
  const w=r.noteWatcher();
  assert.equal(w.update({phase:'outgoing',session:null,peer:196516669}),null);
  assert.equal(w.update({phase:'negotiating',session:'s1',peer:196516669}),null);
  assert.equal(w.update({phase:'ready',session:null,peer:null}),null,'never active: nothing to note');
  assert.equal(w.update({phase:'active',session:'s1',peer:196516669,sharing:false}),null);
  assert.equal(w.update({phase:'active',session:'s1',peer:196516669,sharing:false}),null);
  assert.equal(w.update({phase:'closing',session:'s1',peer:196516669}),null);
  assert.equal(w.update({phase:'ready',session:null,peer:null}),196516669);
  assert.equal(w.update({phase:'ready',session:null,peer:null}),null,'only once');
  assert.equal(w.update({phase:'active',session:'s2',peer:440207203,sharing:true}),null,'the host side (sharing) is never noted');
  assert.equal(w.update({phase:'ready',session:null,peer:null}),null);
  assert.equal(w.update({phase:'active',session:'s3',peer:196516669}),null);
  assert.equal(w.update({phase:'active',session:'s4',peer:440207203}),196516669,'a new session means the previous one ended');
  const base={tenant:TENANT,reports:true,companyHosts:new Set([196516669]),host:196516669,dialogOpen:false};
  assert.equal(r.shouldAskNote(base),true);
  assert.equal(r.shouldAskNote({...base,tenant:null}),false,'staff and personal spaces');
  assert.equal(r.shouldAskNote({...base,reports:false}),false,'plan without reports');
  assert.equal(r.shouldAskNote({...base,host:123456789}),false,'not a company device');
  assert.equal(r.shouldAskNote({...base,dialogOpen:true}),false);
});

// ---------------------------------------------------------------------------------------------
// The pages in a fake DOM

function overview(over={}){
  return {tenant_id:TENANT,organization_id:'o',name:'Acme AG',slug:'acme',role:'owner',plan:'business',status:'active',trial_ends_at:null,
    features:{groups:true,reports:true},limits:{technicians:15,devices:2000,sessions:10},usage:{technicians:2,devices:3,members:2},...over};
}
const saasDevices=[
  {device_id:'d-office',name:'Office PC',os:'windows',group_id:null,group_name:null,connection_id:196516669,generation:1,enrolled_at:'x',revoked:false,added_via:'this_device'},
  {device_id:'d-mac',name:'MacBook',os:'macos',group_id:null,group_name:null,connection_id:440207203,generation:1,enrolled_at:'x',revoked:false,added_via:'this_device'},
  {device_id:'d-server',name:'Server',os:'linux',group_id:null,group_name:null,connection_id:648493658,generation:1,enrolled_at:'x',revoked:false,added_via:'this_device'},
];
function harness({role='owner',info=overview({role}),account,answers={},hash='',scripts=['dialog.js','report-export.js','company.js','devices.js','reports.js'],me=OWNER}={}){
  const listeners={},windowListeners={},calls=[],timers=[],dispatched=[];
  function node(tag){return {tagName:String(tag).toUpperCase(),hidden:false,disabled:false,value:'',title:'',dataset:{},children:[],listeners:{},attributes:{},textContent:'',className:'',parentElement:null,style:{setProperty(){}},classList:{add(){}},
    addEventListener(n,c){this.listeners[n]=c;},append(...c){for(const x of c){const child=typeof x==='string'?{textContent:x,children:[]}:x;child.parentElement=this;this.children.push(child);}},
    prepend(...c){this.children.unshift(...c);},after(){},appendChild(c){this.children.push(c);c.parentElement=this;},replaceChildren(...c){this.children=[...c];},
    setAttribute(n,v){this.attributes[n]=String(v);if(n==='id')elements[v]=this;},getAttribute(n){return this.attributes[n]??null;},focus(){},select(){}};}
  const elements=Object.fromEntries([...html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)].map(m=>[m[2],node(m[1])]));
  for(const id of ['manage-dialog','company-panel','company-signup','devices-add-this','devices-add-others','devices-group','devices-plan','company-invite','company-invites','company-invites-title'])elements[id].hidden=true;
  elements.devices.hidden=false;
  const content=node('main'), nav=node('nav'), devicesLink=node('a');devicesLink.after=(x)=>{nav.children.push(x);};
  let accountValue=account??{phase:'authenticated',account:{profile:me,organization:'o',staff:false,personal:false,has_company:true,has_personal:false}};
  function dispatch(type,detail){for(const c of listeners[type]??[])c({type,detail});}
  function invoke(command,args){
    calls.push({command,args:args===undefined?undefined:JSON.parse(JSON.stringify(args))});
    if(command in answers){const answer=answers[command];const value=typeof answer==='function'?answer(args,calls):answer;return value instanceof Error?Promise.reject(value.message):Promise.resolve(value);}
    switch(command){
      case 'account_status':return Promise.resolve(accountValue);
      case 'remote_status':return Promise.resolve({phase:'offline',host:null});
      case 'saas_my_tenants':return Promise.resolve([{tenant_id:TENANT,organization_id:'o',name:'Acme AG',slug:'acme',role:info.role,plan:info.plan,status:info.status,trial_ends_at:null}]);
      case 'saas_tenant_overview':return Promise.resolve(info);
      case 'saas_members':return Promise.resolve([{user_id:OWNER,name:'Olivia Owner',email:'olivia@acme.test',role:'owner',created_at:'x'},{user_id:TECH,name:'Tom Tech',email:'tom@acme.test',role:'technician',created_at:'x'}]);
      case 'saas_invites':return Promise.resolve([]);
      case 'saas_devices':return Promise.resolve(saasDevices);
      case 'saas_groups':return Promise.resolve([]);
      case 'remote_devices':return Promise.resolve({devices:[{device:'x',connection_id:123456789,name:'Staff PC',os:'linux'}],next:null});
      default:return Promise.resolve();
    }
  }
  const window={__TAURI__:{core:{invoke}},location:{hash},setInterval(){},setTimeout(c){timers.push(c);},addEventListener(n,c){(windowListeners[n]??=[]).push(c);},dispatchEvent(e){for(const c of windowListeners[e.type]??[])c(e);}};
  const document={hidden:false,documentElement:{lang:'en'},getElementById:id=>elements[id],createElement:node,
    querySelector:selector=>selector==='.app-content'?content:selector==='.app-navigation a[href="#devices"]'?devicesLink:null,
    addEventListener(n,c){(listeners[n]??=[]).push(c);},dispatchEvent(e){dispatched.push(e);dispatch(e.type,e.detail);}};
  const context={window,document,localStorage:{getItem:()=>null,setItem(){},removeItem(){}},navigator:{},Event:class{constructor(t){this.type=t;}},
    CustomEvent:class{constructor(t,o){this.type=t;this.detail=o?.detail;}}};
  vm.createContext(context);
  for(const name of scripts)vm.runInContext(dist(name),context,{filename:name});
  const all=(root,found=[])=>{for(const child of root.children??[]){found.push(child);all(child,found);}return found;};
  const signedIn=async()=>{dispatch('mixel-account-state',accountValue);await settle();};
  const confirmDialog=async()=>{await elements['manage-dialog-form'].listeners.submit({preventDefault(){}});await settle();};
  const field=id=>all(elements['manage-dialog-fields']).find(n=>n.dataset?.field===id);
  const page=()=>content.children.find(n=>n.attributes?.id==='reports');
  const byId=id=>all(page()).find(n=>n.attributes?.id===id);
  return {elements,calls,timers,dispatched,window,content,nav,all,signedIn,confirmDialog,field,page,byId,emit:dispatch,called:command=>calls.filter(c=>c.command===command)};
}
const deviceItems=h=>h.elements['devices-list'].children.map(li=>li.className==='device-section'?`# ${li.textContent}`:li.children[0].children[0].textContent);
const star=(h,name)=>h.elements['devices-list'].children.find(li=>li.className!=='device-section'&&li.children[0].children[0].textContent===name).children.find(c=>c.className==='button-quiet device-star');

test('company devices: stars, Favorites and Recent connections on top, and a star toggle that is saved',async()=>{
  const h=harness({answers:{saas_favorites:[{device_id:'d-server',created_at:'x'}],saas_recent_devices:[{device_id:'d-mac',last_connected_at:'2026-11-13T08:00:00Z'},{device_id:'d-server',last_connected_at:'2026-11-12T08:00:00Z'}]}});
  await h.signedIn();
  assert.deepEqual(h.called('saas_favorites').at(-1).args,{tenant:TENANT});
  assert.deepEqual(h.called('saas_recent_devices').at(-1).args,{tenant:TENANT,maxCount:10});
  assert.deepEqual(deviceItems(h),['# Favorites','Server','# Recent connections','MacBook','# All other devices','Office PC']);
  const office=star(h,'Office PC');
  assert.equal(office.getAttribute('aria-pressed'),'false');assert.equal(office.getAttribute('aria-label'),'Add to favorites: Office PC');assert.equal(office.textContent,'☆');
  office.listeners.click();await settle();
  assert.deepEqual(h.called('saas_set_favorite').map(c=>c.args),[{tenant:TENANT,device:'d-office',favorite:true}]);
  assert.deepEqual(deviceItems(h),['# Favorites','Office PC','Server','# Recent connections','MacBook']);
  assert.equal(star(h,'Office PC').getAttribute('aria-pressed'),'true');assert.equal(star(h,'Office PC').textContent,'★');
  assert.equal(h.elements['devices-status'].textContent,'Office PC: added to favorites');
  star(h,'Server').listeners.click();await settle();
  assert.deepEqual(h.called('saas_set_favorite').at(-1).args,{tenant:TENANT,device:'d-server',favorite:false});
  assert.deepEqual(deviceItems(h),['# Favorites','Office PC','# Recent connections','MacBook','Server']);
});

test('a refused star goes back, and without the new commands the list is unchanged and has no stars',async()=>{
  const h=harness({answers:{saas_favorites:[],saas_recent_devices:[],saas_set_favorite:new Error('Device unavailable')}});
  await h.signedIn();
  assert.deepEqual(deviceItems(h),['Office PC','MacBook','Server'],'no sections while nothing is starred or recent');
  star(h,'MacBook').listeners.click();await settle();
  assert.deepEqual(deviceItems(h),['Office PC','MacBook','Server']);
  assert.equal(star(h,'MacBook').getAttribute('aria-pressed'),'false');
  assert.match(h.elements['devices-status'].textContent,/^The favorite could not be saved\. Device unavailable$/);
  const old=harness({answers:{saas_favorites:new Error('Command saas_favorites not found'),saas_recent_devices:new Error('Command saas_recent_devices not found')}});
  await old.signedIn();
  assert.deepEqual(deviceItems(old),['Office PC','MacBook','Server']);
  assert.equal(star(old,'Office PC'),undefined,'no stars');
});

test('staff and personal device lists have no stars and never ask for favorites',async()=>{
  const h=harness({account:{phase:'authenticated',account:{profile:'p',organization:null,staff:true}}});
  await h.signedIn();
  assert.deepEqual(deviceItems(h),['Staff PC']);
  assert.equal(h.called('saas_favorites').length,0);
  assert.equal(star(h,'Staff PC'),undefined);
  assert.equal(h.nav.children[0].hidden,true,'no Session reports entry without a company');
});

const reportRows=[
  reportRow({session_id:'r1',device_id:'d-mac',device_name:'MacBook',connection_id:440207203,started_at:'2026-11-12T08:00:00Z',ended_at:null,duration_seconds:null,kind:'unattended'}),
  reportRow({session_id:'r2',technician_id:TECH,technician_name:'Tom Tech',technician_email:'tom@acme.test',note:'Printer fixed'}),
];

test('Reports: filters, table, editing a note and exporting CSV and PDF through the save command',async()=>{
  const h=harness({hash:'#reports',answers:{saas_session_report:reportRows,save_report_file:args=>`/Users/q/Documents/${args.name}`}});
  await h.signedIn();
  assert.equal(h.nav.children[0].hidden,false,'Session reports in the navigation for a company');
  const report=h.called('saas_session_report').at(-1).args;
  assert.equal(report.tenant,TENANT);assert.equal(report.technician,null);assert.equal(report.device,null);
  assert.ok(Date.parse(report.to)-Date.parse(report.from)>=30*86400000);
  const rows=h.byId('reports-rows').children;
  assert.equal(rows.length,2);
  assert.deepEqual(rows[0].children.map(td=>td.children[0]?.textContent??td.textContent).slice(0,2),['Olivia Owner','MacBook']);
  assert.equal(rows[0].children[3].children[0].textContent,'Running');
  assert.equal(rows[0].children[5].children[0].textContent,'Unattended');
  assert.equal(rows[1].children[4].textContent,'1:02:05');
  assert.equal(h.byId('reports-technician').parentElement.hidden,false,'owners filter by technician');
  assert.equal(h.byId('reports-summary').hidden,false);
  // Device and technician filters send their ids.
  const device=h.byId('reports-device');device.value='d-mac';device.listeners.change();await settle();
  assert.equal(h.called('saas_session_report').at(-1).args.device,'d-mac');
  const tech=h.byId('reports-technician');tech.value=TECH;tech.listeners.change();await settle();
  assert.equal(h.called('saas_session_report').at(-1).args.technician,TECH);
  // Edit the note of the second row.
  const edit=h.byId('reports-rows').children[1].children[6].children.find(c=>c.className==='button-quiet report-note-edit');
  assert.equal(edit.textContent,'Edit note');
  edit.listeners.click();
  assert.equal(h.elements['manage-dialog'].hidden,false);assert.equal(h.elements['manage-dialog-title'].textContent,'Session note');
  assert.equal(h.field('note').value,'Printer fixed');
  h.field('note').value='Printer fixed, toner replaced';await h.confirmDialog();
  assert.deepEqual(h.called('saas_session_note').at(-1).args,{tenant:TENANT,note:'Printer fixed, toner replaced',session:'r2'});
  assert.equal(h.byId('reports-status').textContent,'Note saved.');
  // Exports.
  h.byId('reports-csv').listeners.click();await settle();
  const csv=h.called('save_report_file').at(-1).args;
  assert.match(csv.name,/^mixel-remote-acme-sessions-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.csv$/);
  assert.ok(csv.content.startsWith('﻿Technician,Email,Device,Connection ID,'));
  assert.ok(csv.content.includes('Printer fixed, toner replaced'));
  assert.match(h.byId('reports-status').textContent,/^Saved as \/Users\/q\/Documents\/mixel-remote-acme-sessions-.*\.csv$/);
  h.byId('reports-pdf').listeners.click();await settle();
  const pdf=h.called('save_report_file').at(-1).args;
  assert.match(pdf.name,/\.pdf$/);assert.equal(checkPdf(pdf.content),1);assert.ok(pdf.content.includes('(Acme AG)'));
});

test('Reports: a technician sees no technician filter and edits only own notes; a cancelled save says so',async()=>{
  const h=harness({role:'technician',me:TECH,hash:'#reports',answers:{saas_session_report:reportRows,save_report_file:null}});
  await h.signedIn();
  assert.equal(h.byId('reports-technician').parentElement.hidden,true);
  const editable=h.byId('reports-rows').children.map(row=>row.children[6].children.some(c=>c.className==='button-quiet report-note-edit'));
  assert.deepEqual(editable,[false,true]);
  h.byId('reports-csv').listeners.click();await settle();
  assert.equal(h.byId('reports-status').textContent,'Export cancelled.');
});

test('Reports: a plan without reports shows the upgrade, an older server says not available yet',async()=>{
  const free=harness({hash:'#reports',info:overview({plan:'free',features:{reports:false}})});
  await free.signedIn();
  assert.equal(free.called('saas_session_report').length,0);
  assert.equal(free.byId('reports-notice').hidden,false);assert.equal(free.byId('reports-upgrade').hidden,false);
  assert.match(free.byId('reports-notice-text').textContent,/need a paid plan/);
  assert.equal(free.byId('reports-filters').hidden,true);
  free.byId('reports-upgrade').listeners.click();
  assert.deepEqual(free.called('open_billing_page').at(-1).args,{lang:'en'});
  const refused=harness({hash:'#reports',answers:{saas_session_report:new Error('Session reports need a paid plan')}});
  await refused.signedIn();
  assert.equal(refused.byId('reports-upgrade').hidden,false);
  const old=harness({hash:'#reports',info:overview({features:{}}),answers:{saas_session_report:new Error('Command saas_session_report not found')}});
  await old.signedIn();
  assert.equal(old.byId('reports-notice').hidden,false);assert.equal(old.byId('reports-upgrade').hidden,true);
  assert.equal(old.byId('reports-notice-text').textContent,'Session reports are not available yet. Please try again later.');
});

test('after a controlled session with a company device ends, an optional note is offered; Skip closes it',async()=>{
  const h=harness({answers:{saas_session_note:'s1'}});
  await h.signedIn();
  const end=async(states)=>{for(const state of states)h.emit('mixel-remote-state',state);for(const t of h.timers.splice(0))t();await settle();};
  await end([{phase:'active',session:'s1',peer:196516669,sharing:false},{phase:'closing',session:'s1',peer:196516669},{phase:'ready',session:null,peer:null}]);
  assert.equal(h.elements['manage-dialog'].hidden,false);
  assert.equal(h.elements['manage-dialog-title'].textContent,'Session note');
  assert.match(h.elements['manage-dialog-text'].textContent,/^Session with Office PC\. Add a note\?/);
  assert.equal(h.elements['manage-dialog-cancel'].textContent,'Skip');
  h.field('note').value='Reset the password';await h.confirmDialog();
  assert.deepEqual(h.called('saas_session_note').at(-1).args,{tenant:TENANT,note:'Reset the password',connection:196516669});
  assert.equal(h.elements['manage-dialog'].hidden,true);
  // Skip saves nothing.
  await end([{phase:'active',session:'s2',peer:440207203},{phase:'ready',session:null,peer:null}]);
  assert.equal(h.elements['manage-dialog'].hidden,false);
  h.elements['manage-dialog-cancel'].listeners.click();
  assert.equal(h.elements['manage-dialog'].hidden,true);
  assert.equal(h.called('saas_session_note').length,1);
  // No prompt: host side, never active, another computer, a reconnect to the same computer.
  await end([{phase:'active',session:'s3',peer:196516669,sharing:true},{phase:'ready',session:null,peer:null}]);
  await end([{phase:'outgoing',session:null,peer:196516669},{phase:'ready',session:null,peer:null}]);
  await end([{phase:'active',session:'s4',peer:123456789},{phase:'ready',session:null,peer:null}]);
  h.emit('mixel-remote-state',{phase:'active',session:'s5',peer:196516669});h.emit('mixel-remote-state',{phase:'ready',session:null,peer:null});
  h.emit('mixel-remote-state',{phase:'active',session:'s6',peer:196516669});for(const t of h.timers.splice(0))t();await settle();
  assert.equal(h.elements['manage-dialog'].hidden,true);
});

test('no session note without reports in the plan, and none for staff',async()=>{
  const h=harness({info:overview({features:{reports:false}})});
  await h.signedIn();
  h.emit('mixel-remote-state',{phase:'active',session:'s1',peer:196516669});h.emit('mixel-remote-state',{phase:'ready',session:null,peer:null});
  for(const t of h.timers.splice(0))t();await settle();
  assert.equal(h.elements['manage-dialog'].hidden,true);
  const staff=harness({account:{phase:'authenticated',account:{profile:'p',organization:null,staff:true}}});
  await staff.signedIn();
  staff.emit('mixel-remote-state',{phase:'active',session:'s1',peer:196516669});staff.emit('mixel-remote-state',{phase:'ready',session:null,peer:null});
  for(const t of staff.timers.splice(0))t();await settle();
  assert.equal(staff.elements['manage-dialog'].hidden,true);
  assert.equal(staff.called('saas_session_note').length,0);
});

test('German, French and Italian texts for every new English text, without replacing existing ones',()=>{
  const window={};
  const context={window};vm.createContext(context);
  vm.runInContext(dist('i18n-strings.js'),context);
  const before=JSON.parse(JSON.stringify(window.MIXEL_I18N));
  vm.runInContext(dist('i18n-reports.js'),context);
  const tables=window.MIXEL_I18N;
  for(const language of ['de','fr','it'])for(const [text,value] of Object.entries(before[language].texts))assert.equal(tables[language].texts[text],value,'existing texts are kept');
  const used=[...new Set([...dist('reports.js').matchAll(/(?:text: |tr\()'([^']+)'/g),...dist('devices.js').matchAll(/tr\('([^']+)'\)/g)].map(match=>match[1]))]
    .concat(['Favorites','Recent connections','All other devices','Session reports','Technician','Start','End','Duration','Note','Running','Attended','Unattended','Skip','Save note','Session note']);
  for(const language of ['de','fr','it'])for(const text of used)assert.ok(tables[language].texts[text],`${language}: ${text}`);
  assert.equal(tables.de.texts['Session reports'],'Sitzungsberichte');
  assert.equal(tables.fr.texts['Recent connections'],'Connexions récentes');
  assert.equal(tables.it.texts.Skip,'Salta');
  for(const value of Object.values(tables.de.texts))assert.ok(!value.includes('ß'),'Swiss German spelling');
});
