(() => {
  function element<T extends HTMLElement>(id: string): T {
    const value=document.getElementById(id); if (!value) throw Error(`Missing device directory control ${id}`); return value as T;
  }
  const invoke=window.__TAURI__.core.invoke;
  type Device={device:string; connection_id:number; name:string; os:string; group_id?:string|null; group_name?:string|null; generation?:number|null};
  type Page={devices:Device[]; next:number|null};
  type Account={phase:string; account:{profile:string;organization:string|null;staff:boolean;personal?:boolean}|null};
  type Remote={phase:string;host:number|null;online?:number[];message?:string};
  // Mixel Remote companies (SaaS tenants): see company.ts. Staff, ISM companies and personal spaces have none.
  type Tenant={tenant_id:string;name:string;role:string;plan:string;status:string};
  type Overview=Tenant&{features:Record<string,boolean>;limits:{devices:number|null;technicians:number|null};usage:{devices:number;technicians:number}};
  type Company={tenant:Tenant|null;overview:Overview|null};
  type SaasDevice={device_id:string;name:string;os:string;group_id:string|null;group_name:string|null;connection_id:number|null;generation:number|null;revoked:boolean|null};
  type Group={group_id:string;name:string;devices:number};
  type Field={id:string;kind:'text'|'number'|'select';label:string;value?:string;options?:{value:string;label:string}[];min?:number;max?:number;maxLength?:number;required?:boolean;hint?:string};
  type Dialog={open(options:{title:string;text?:string;fields?:Field[];confirm:string;danger?:boolean;run:(values:Record<string,string>)=>Promise<HTMLElement|void>}):void};
  const dialog=():Dialog=>(window as unknown as {mixelDialog:Dialog}).mixelDialog;
  const tr=(text:string):string=>(window as unknown as {mixelText?:(value:string)=>string}).mixelText?.(text)??text;
  const THIS_COMPUTER='mixel.remote.this-computer.v1';
  const form=element<HTMLFormElement>('devices-search-form'), search=element<HTMLInputElement>('devices-search');
  const refresh=element<HTMLButtonElement>('devices-refresh'), previous=element<HTMLButtonElement>('devices-previous'), next=element<HTMLButtonElement>('devices-next');
  const submit=element<HTMLButtonElement>('devices-submit');
  const list=element<HTMLUListElement>('devices-list'), status=element<HTMLParagraphElement>('devices-status');
  const banner=element<HTMLDivElement>('devices-connection'), bannerTitle=element<HTMLElement>('devices-connection-title'), bannerDetail=element<HTMLElement>('devices-connection-detail');
  const addThis=element<HTMLButtonElement>('devices-add-this'), addOthers=element<HTMLButtonElement>('devices-add-others'), groupFilter=element<HTMLSelectElement>('devices-group'), plan=element<HTMLParagraphElement>('devices-plan');
  element<HTMLButtonElement>('devices-reconnect').addEventListener('click',()=>{status.textContent='Reconnecting this computer…';document.dispatchEvent(new CustomEvent('mixel-reconnect-now'));});
  // Devices whose presence has been asked at least once while connected: only those can be called Offline.
  const checkedOnce=new Set<number>();
  // Companies: what the connection service last recorded per device (online, or offline since when).
  type ServerPresence={device_id:string;status:string;last_seen:string|null};
  let serverPresence=new Map<number,ServerPresence&{connection_id:number}>();
  function seenAgo(iso:string|null):string{
    const at=iso?Date.parse(iso):NaN;
    if(!Number.isFinite(at))return '';
    const minutes=Math.max(0,Math.round((Date.now()-at)/60000));
    if(minutes<2)return 'Last seen just now';
    if(minutes<60)return `Last seen ${minutes} minutes ago`;
    const hours=Math.round(minutes/60);
    if(hours<24)return hours===1?'Last seen 1 hour ago':`Last seen ${hours} hours ago`;
    const days=Math.round(hours/24);
    return days===1?'Last seen yesterday':`Last seen ${days} days ago`;
  }
  async function loadServerPresence():Promise<void>{
    const tenant=company.tenant?.tenant_id;
    if(!tenant || !authenticated)return;
    try{
      const rows=await invoke<(ServerPresence&{connection_id:number|null})[]>('saas_device_presence',{tenant});
      if(company.tenant?.tenant_id!==tenant || !Array.isArray(rows))return;
      serverPresence=new Map(rows.filter(row=>Number.isInteger(row.connection_id)).map(row=>[row.connection_id!,{...row,connection_id:row.connection_id!}]));
      render();
    }catch{/* the live state from the connection still shows */}
  }
  let accountKey='', authenticated=false, busy=false, version=0, accountVersion=0, remoteVersion=0, currentSearch='', remote:Remote={phase:'offline',host:null};
  let entries:Device[]=[], nextCursor:number|null=null, cursors:(number|null)[]=[null], index=0;
  // Company mode: the whole (filtered) list is loaded at once and shown 50 per page.
  let company:Company={tenant:null,overview:null}, companyKey='', waitingForCompany=false, saasAll:Device[]|null=null, groups:Group[]=[], openMenu:HTMLElement|null=null;
  const formatId=(value:number):string=>String(value).replace(/(\d{3})(\d{3})(\d{3})/,'$1 $2 $3');
  const role=():string=>company.overview?.role??company.tenant?.role??'';
  const tenantStatus=():string=>company.overview?.status??company.tenant?.status??'';
  const writable=():boolean=>['active','trial','past_due'].includes(tenantStatus());
  const manages=():boolean=>!!company.tenant && ['owner','admin','technician'].includes(role());
  const deploys=():boolean=>!!company.tenant && ['owner','admin'].includes(role());
  const groupsOn=():boolean=>!!company.overview?.features?.groups;
  function room():number{
    const limit=company.overview?.limits.devices;
    if(!company.overview || limit===null || limit===undefined)return Infinity;
    return limit-company.overview.usage.devices;
  }
  function thisComputer():{device:string;connection_id:number;generation:number;name:string;tenant?:string}|null{
    try{const saved=JSON.parse(localStorage.getItem(THIS_COMPUTER)??'null');return saved && typeof saved.device==='string' && Number.isInteger(saved.connection_id)?saved:null;}catch{return null;}
  }
  function remember(device:Device):void{
    if(!company.tenant || !Number.isInteger(device.generation))return;
    const value={device:device.device,connection_id:device.connection_id,generation:device.generation,name:device.name,tenant:company.tenant.tenant_id};
    const before=thisComputer();
    if(JSON.stringify(before)===JSON.stringify(value))return;
    try{localStorage.setItem(THIS_COMPUTER,JSON.stringify(value));}catch{/* not kept */}
    document.dispatchEvent(new CustomEvent('mixel-this-computer'));
  }
  function forget():void{try{localStorage.removeItem(THIS_COMPUTER);}catch{/* none */}document.dispatchEvent(new CustomEvent('mixel-this-computer'));}
  // Is this computer already one of the company's devices? Then "Add this computer" is not offered.
  function registeredHere():boolean{
    if(remote.host!==null && remote.host!==undefined)return true;
    const saved=thisComputer();
    return !!saved && saved.tenant===company.tenant?.tenant_id && !!saasAll?.some(device=>device.connection_id===saved.connection_id);
  }
  function renderTools():void{
    const saas=!!company.tenant && authenticated;
    addThis.hidden=!saas || !manages() || registeredHere();
    addOthers.hidden=!saas || !deploys();
    const blocked=!writable()?'This company is read-only':room()<=0?'Device limit of your plan reached':'';
    addThis.disabled=addOthers.disabled=busy || !!blocked;
    addThis.title=addOthers.title=blocked;
    groupFilter.hidden=!saas || !manages() || !groupsOn() || !groups.length;
    groupFilter.disabled=busy;
    plan.hidden=!saas || !manages() || !blocked;
    if(!writable())plan.textContent='This company is read-only. You can still remove devices; adding devices and other changes need an active plan.';
    else if(room()<=0)plan.textContent=`Device limit of your plan reached (${company.overview!.usage.devices} of ${company.overview!.limits.devices}). Remove a device or upgrade the plan to add more.`;
  }
  function closeMenu():void{if(openMenu){openMenu.hidden=true;(openMenu.previousElementSibling as HTMLElement|null)?.setAttribute?.('aria-expanded','false');openMenu=null;}}
  function menuFor(device:Device):HTMLElement{
    const box=document.createElement('div'), toggle=document.createElement('button'), menu=document.createElement('div');
    box.className='device-menu';toggle.type='button';toggle.className='button-quiet device-manage';toggle.textContent='Manage';
    toggle.setAttribute('aria-haspopup','menu');toggle.setAttribute('aria-label',`Manage ${device.name}`);toggle.disabled=busy;
    menu.className='device-menu-list';menu.setAttribute('role','menu');menu.hidden=true;
    const item=(label:string, blocked:string, action:()=>void, danger=false):void=>{
      const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitem');button.textContent=label;
      button.className=danger?'menu-danger':'';button.disabled=!!blocked;button.title=blocked;
      button.addEventListener('click',()=>{closeMenu();action();});menu.append(button);
    };
    const readOnly=writable()?'':'This company is read-only';
    item('Rename…',readOnly,()=>rename(device));
    if(groupsOn())item('Move to group…',readOnly || (!groups.length && !deploys()?'No groups yet. An owner or administrator creates groups.':''),()=>move(device));
    item('Remove…','',()=>remove(device),true);
    toggle.setAttribute('aria-expanded','false');
    toggle.addEventListener('click',()=>{const wasOpen=!menu.hidden;closeMenu();if(!wasOpen){menu.hidden=false;openMenu=menu;toggle.setAttribute('aria-expanded','true');}});
    box.append(toggle,menu);
    return box;
  }
  function render():void {
    refresh.disabled=search.disabled=!authenticated || busy;
    submit.disabled=refresh.disabled;
    previous.disabled=!authenticated || busy || index===0;
    next.disabled=!authenticated || busy || nextCursor===null;
    const saas=!!company.tenant;
    element<HTMLElement>('devices-empty').hidden=entries.length>0;
    element<HTMLElement>('devices-empty-title').textContent=busy?'Loading your devices…':!authenticated?'Your devices belong here':currentSearch?'No matching devices':saas&&groupFilter.value?'No devices in this group':'No devices to display';
    element<HTMLElement>('devices-empty-detail').textContent=!authenticated?'Sign in to see the devices available to your account.':currentSearch?'Try another device name or clear the search.':saas?(manages()?'Add this computer, or add other computers with a deploy link.':'Devices you add appear here. Ask an administrator of your company for access to other devices.'):'Devices appear here after a Mixel team member enrolls them.';
    element<HTMLElement>('devices-empty-action').hidden=authenticated;
    renderTools();
    list.replaceChildren();openMenu=null;
    const connected=['ready','active','incoming','outgoing','deciding','negotiating'].includes(remote.phase);
    const inSession=['active','incoming','outgoing','deciding','negotiating','closing'].includes(remote.phase);
    const reconnecting=authenticated && !connected && remote.phase!=='closing' && !(saas && !registeredHere());
    // This computer's own connection: say what is wrong and offer to try again, instead of greying everything out.
    banner.hidden=!reconnecting;
    if(reconnecting){
      bannerTitle.textContent=remote.phase==='error'?'This computer lost its connection':'This computer is not connected yet';
      bannerDetail.textContent=(remote.phase==='error' && remote.message ? `${remote.message.replace(/[.\s]*$/,'')}. ` : '')+'Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.';
    }
    for (const device of entries) {
      const row=document.createElement('li'), identity=document.createElement('div'), name=document.createElement('strong'), detail=document.createElement('span'), id=document.createElement('code'), connect=document.createElement('button');
      const state=document.createElement('div'), presence=document.createElement('span'), reason=document.createElement('small');
      state.className='device-state';presence.className='presence';reason.className='device-reason';
      const self=remote.host===device.connection_id;
      const live=self?connected:(remote.online??[]).includes(device.connection_id);
      // online | offline | unknown (this computer is not connected, so nobody can tell yet) | self
      const known=connected && checkedOnce.has(device.connection_id);
      // Live presence from this computer's connection first; for companies, the connection service's record when this computer cannot ask.
      const recorded=saas?serverPresence.get(device.connection_id):undefined;
      const fallback=!connected && recorded && ['online','offline','never'].includes(recorded.status);
      const kind=self?'self':!connected?(fallback?(recorded!.status==='online'?'online':'offline'):'unknown'):live?'online':known?'offline':'checking';
      presence.dataset.state=kind==='self'&&!connected?'unknown':kind;
      presence.textContent=kind==='self'?(connected?'This computer':'This computer · not connected'):kind==='online'?'Online':kind==='offline'?'Offline':kind==='checking'?'Checking…':'Status unknown';
      let why='';
      const seen=kind==='offline'&&recorded&&recorded.status!=='online'?seenAgo(recorded.last_seen):'';
      if(kind==='self')why=connected?'You are using this device.':'Reconnecting automatically.';
      else if(kind==='offline')why=(seen?`${seen}. `:'')+'Turned off, asleep or without internet. It must be online to connect.';
      else if(kind==='unknown')why='Shown once this computer is connected.';
      else if(kind==='online')why='Ready. The person there accepts your request, unless unattended access is set up.';
      reason.textContent=why;
      state.append(presence,reason);
      name.textContent=device.name;detail.textContent=device.os==='other'?'phone or tablet':device.os;identity.append(name,detail);
      if(saas && device.group_name){const group=document.createElement('small');group.className='device-group';group.dataset.noTranslate='';group.textContent=device.group_name;identity.append(group);}
      id.textContent=formatId(device.connection_id);connect.type='button';connect.textContent='Connect';
      const blocked=self?'This is the computer you are using.':inSession?'End the current session first.':kind==='offline'?'This device is offline.':busy?'Please wait…':!authenticated?'Sign in first.':'';
      connect.disabled=!!blocked;
      connect.title=blocked||`Connect to ${device.name}`;
      connect.addEventListener('click',()=>{
        // The same path as typing the ID: brings this computer online if needed, prepares phones, then requests.
        status.textContent=`Connecting to ${device.name}…`;
        document.dispatchEvent(new CustomEvent('mixel-connect-to',{detail:{id:device.connection_id}}));
      });
      let macs:string[]=[];
      try{macs=(JSON.parse(localStorage.getItem('mixel.remote.macs.v1')??'{}') as Record<string,string[]>)[String(device.connection_id)]??[];}catch{/* none */}
      row.append(identity,state,id);
      if(macs.length && kind==='offline'){
        const wake=document.createElement('button');wake.type='button';wake.className='button-quiet device-wake';wake.textContent='Wake';
        wake.addEventListener('click',async()=>{
          status.textContent=`Waking ${device.name}…`;
          document.dispatchEvent(new CustomEvent('mixel-wake',{detail:macs}));
          try{await invoke('wake_on_lan',{macs});status.textContent=`Wake signal sent to ${device.name}. It shows Online when it has started.`;}
          catch(error){status.textContent=String(error);}
        });
        state.append(wake);
      }
      // Connect stays the last control of a row.
      if(saas && manages())row.append(menuFor(device));
      row.append(connect);list.append(row);
    }
  }
  function showPage(wantedIndex:number):void{
    const all=saasAll??[];
    index=Math.max(0,Math.min(wantedIndex,Math.max(0,Math.ceil(all.length/50)-1)));
    entries=all.slice(index*50,index*50+50);
    nextCursor=(index+1)*50<all.length?index+1:null;cursors[index]=index;
    status.textContent=entries.length?`Page ${index+1} · ${entries.length} devices.`:'No devices match this search.';
  }
  async function loadCompanyDevices(wantedIndex:number, reset:boolean):Promise<void>{
    if(!reset && saasAll){showPage(wantedIndex);render();checkPresence();return;}
    const request=++version;busy=true;
    if(reset){entries=[];cursors=[null];index=0;nextCursor=null;currentSearch=search.value.trim();saasAll=null;}
    render();status.textContent='Loading your company’s devices…';
    const tenant=company.tenant!.tenant_id, filter=groupFilter.value;
    try{
      const rows=await invoke<SaasDevice[]>('saas_devices',{tenant,group:filter && filter!=='none'?filter:null,search:currentSearch||null});
      if(version!==request || !authenticated || company.tenant?.tenant_id!==tenant)return;
      if(!Array.isArray(rows) || rows.length>2000 || rows.some(row=>typeof row.device_id!=='string' || typeof row.name!=='string' || typeof row.os!=='string'))throw Error('Invalid device directory response');
      saasAll=rows.filter(row=>!row.revoked && Number.isInteger(row.connection_id) && row.connection_id!>=100000000 && row.connection_id!<=999999999 && (filter!=='none' || !row.group_id))
        .map(row=>({device:row.device_id,connection_id:row.connection_id!,name:row.name,os:row.os,group_id:row.group_id,group_name:row.group_name,generation:row.generation}));
      const mine=remote.host?saasAll.find(device=>device.connection_id===remote.host):undefined;
      if(mine)remember(mine);
      showPage(0);
      checkPresence();
      void loadServerPresence();
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  }
  async function load(cursor:number|null, wantedIndex:number, reset=false):Promise<void>{
    if (!authenticated || busy || waitingForCompany) return;
    if (company.tenant) return loadCompanyDevices(wantedIndex,reset);
    const request=++version;busy=true;
    if (reset){entries=[];cursors=[null];index=0;nextCursor=null;currentSearch=search.value.trim();}
    render();status.textContent='Loading enrolled devices…';
    try {
      const page=await invoke<Page>('remote_devices',{after:cursor,search:currentSearch});
      if (version!==request || !authenticated) return;
      if (!Array.isArray(page.devices) || page.devices.length>50 || page.devices.some(device=>typeof device.name!=='string' || typeof device.os!=='string' || !Number.isInteger(device.connection_id) || device.connection_id<100000000 || device.connection_id>999999999) || (page.next!==null && (!Number.isInteger(page.next) || page.next<100000000 || page.next>999999999))) throw Error('Invalid device directory response');
      entries=page.devices;nextCursor=page.next;index=wantedIndex;cursors[index]=cursor;
      status.textContent=entries.length?`Page ${index+1} · ${entries.length} devices.`:'No devices match this search.';
      checkPresence();
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  }
  async function loadGroups():Promise<void>{
    groups=[];
    if(!company.tenant || !manages() || !groupsOn()){renderGroups();return;}
    const tenant=company.tenant.tenant_id;
    try{const value=await invoke<Group[]>('saas_groups',{tenant});if(company.tenant?.tenant_id===tenant && Array.isArray(value))groups=value.filter(group=>typeof group.group_id==='string' && typeof group.name==='string');}
    catch{/* the filter stays hidden */}
    renderGroups();render();
  }
  function renderGroups():void{
    const chosen=groupFilter.value;
    groupFilter.replaceChildren();
    for(const [value,label] of [['',tr('All groups')],['none',tr('No group')],...groups.map(group=>[group.group_id,group.name])]){
      const option=document.createElement('option');option.value=value;option.textContent=label;groupFilter.appendChild(option);
    }
    groupFilter.value=[...groups.map(group=>group.group_id),'none',''].includes(chosen)?chosen:'';
  }
  function afterChange():void{document.dispatchEvent(new CustomEvent('mixel-company-refresh'));busy=false;void load(null,0,true);}
  function rename(device:Device):void{
    const tenant=company.tenant!;
    dialog().open({title:'Rename device',fields:[{id:'name',kind:'text',label:'Device name',value:device.name,maxLength:60}],confirm:'Save name',
      run:async values=>{
        if(values.name===device.name)return;
        const final=await invoke<string>('saas_rename_device',{tenant:tenant.tenant_id,device:device.device,name:values.name});
        status.textContent=`Renamed to ${typeof final==='string'?final:values.name}.`;afterChange();
      }});
  }
  function move(device:Device):void{
    const tenant=company.tenant!;
    const fields:Field[]=[{id:'group',kind:'select',label:'Group',value:device.group_id??'',options:[{value:'',label:tr('No group')},...groups.map(group=>({value:group.group_id,label:group.name}))]}];
    if(deploys())fields.push({id:'create',kind:'text',label:'Or a new group',maxLength:60,required:false,hint:'Leave empty to use the group chosen above.'});
    dialog().open({title:'Move to a group',text:`Choose the group of ${device.name}. Technicians reach the devices of the groups they are granted, and devices in no group.`,fields,confirm:'Move device',
      run:async values=>{
        let target:string|null=values.group||null;
        if(values.create){target=await invoke<string>('saas_create_group',{tenant:tenant.tenant_id,name:values.create});await loadGroups();}
        if(target===(device.group_id??null))return;
        await invoke('saas_move_device',{tenant:tenant.tenant_id,device:device.device,group:target});
        const group=groups.find(item=>item.group_id===target);
        status.textContent=group?`${device.name} moved to ${group.name}.`:`${device.name} is in no group now.`;afterChange();
      }});
  }
  function remove(device:Device):void{
    const tenant=company.tenant!, self=remote.host===device.connection_id || thisComputer()?.connection_id===device.connection_id;
    dialog().open({title:`Remove ${device.name}?`,
      text:`${device.name} is removed from ${tenant.name} at once: its registration is revoked, running sessions with it end, and nobody can connect to it any more. To use it again, it must be added again.${self?' This is the computer you are using.':''}`,
      confirm:'Remove device',danger:true,
      run:async()=>{
        await invoke('saas_remove_device',{tenant:tenant.tenant_id,device:device.device});
        if(self){forget();document.dispatchEvent(new CustomEvent('mixel-device-removed'));}
        status.textContent=`${device.name} was removed.`;afterChange();
      }});
  }
  addThis.addEventListener('click',async()=>{
    if(!company.tenant || busy)return;
    const tenant=company.tenant, suggested=await invoke<string>('this_computer_name').catch(()=>'');
    const fields:Field[]=[{id:'name',kind:'text',label:'Computer name',value:typeof suggested==='string'?suggested:'',maxLength:60}];
    if(groupsOn() && groups.length)fields.push({id:'group',kind:'select',label:'Group',value:'',options:[{value:'',label:tr('No group')},...groups.map(group=>({value:group.group_id,label:group.name}))]});
    dialog().open({title:'Add this computer',text:`This computer gets its own connection ID in ${tenant.name}, protected by a device key that stays on it.`,fields,confirm:'Add this computer',
      run:async values=>{
        const added=await invoke<{device_id:string;connection_id:number;generation:number}>('saas_add_this_device',{tenant:tenant.tenant_id,name:values.name,group:values.group||null});
        if(!added || typeof added.device_id!=='string' || !Number.isInteger(added.connection_id) || added.connection_id<100000000 || added.connection_id>999999999)throw Error('Invalid registration response');
        remember({device:added.device_id,connection_id:added.connection_id,generation:added.generation,name:values.name,os:''});
        status.textContent=`This computer was added as ${values.name}. Connection ID ${formatId(added.connection_id)}.`;
        document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));afterChange();
      }});
  });
  addOthers.addEventListener('click',()=>{
    if(!company.tenant || busy)return;
    const tenant=company.tenant, space=room(), most=Math.max(1,Math.min(10000,Number.isFinite(space)?space:10000));
    const fields:Field[]=[
      {id:'label',kind:'text',label:'Name of the link',value:`Computers ${new Date().toLocaleDateString()}`,maxLength:80},
      {id:'computers',kind:'number',label:'Computers',value:String(Math.min(50,most)),min:1,max:most},
      {id:'days',kind:'number',label:'Valid for days',value:'30',min:1,max:365}];
    if(groupsOn() && groups.length)fields.push({id:'group',kind:'select',label:'Group',value:'',options:[{value:'',label:tr('No group')},...groups.map(group=>({value:group.group_id,label:group.name}))]});
    dialog().open({title:'Add other computers',text:'Create a deploy link: one command adds many Windows, Mac or Linux computers to your company, without anyone signing in on them.',fields,confirm:'Create deploy link',
      run:async values=>{
        const computers=Number(values.computers), days=Number(values.days);
        if(!Number.isInteger(computers) || computers<1 || computers>most)throw Error(`Enter 1 to ${most} computers.`);
        if(!Number.isInteger(days) || days<1 || days>365)throw Error('Enter 1 to 365 days.');
        const created=await invoke<{token:string;windows_command:string;macos_command:string;linux_command:string}>('saas_create_deploy_link',{tenant:tenant.tenant_id,label:values.label,days,computers,group:values.group||null});
        if(!created || ![created.windows_command,created.macos_command,created.linux_command].every(line=>typeof line==='string' && line.includes(created.token)))throw Error('Invalid deploy link response');
        // One command per operating system; Windows first (most rollouts), the others one choice away.
        const systems:{value:string;label:string;command:string;steps:string[]}[]=[
          {value:'windows',label:'Windows',command:created.windows_command,steps:['On each Windows computer, open PowerShell as administrator, or use your deployment tool.','Paste and run the command below. Mixel Remote is installed and the computer appears in Devices, with always-on access.']},
          {value:'macos',label:'macOS',command:created.macos_command,steps:['On each Mac, open Terminal while the person who uses the Mac is signed in, or run it with your MDM.','Paste and run the command below and enter the administrator password. The Mac appears in Devices, with always-on access.','Once per Mac, allow Mixel Remote in System Settings > Privacy & Security > Screen Recording and Accessibility.']},
          {value:'linux',label:'Linux',command:created.linux_command,steps:['On each Ubuntu or Debian computer, open a terminal, or use your deployment tool.','Paste and run the command below and enter the administrator password. The computer appears in Devices, with access at the sign-in screen and in X11 sessions.']}];
        const result=document.createElement('div'), osLabel=document.createElement('label'), os=document.createElement('select'), steps=document.createElement('ol'), command=document.createElement('textarea'), copy=document.createElement('button'), copied=document.createElement('p'), secret=document.createElement('p');
        osLabel.textContent='Operating system';os.dataset.field='os';os.setAttribute('aria-label','Operating system');
        for(const system of systems){const option=document.createElement('option');option.value=system.value;option.textContent=system.label;os.append(option);}
        os.value='windows';
        command.readOnly=true;command.rows=4;command.dataset.noTranslate='';command.dataset.field='command';
        const current=()=>systems.find(system=>system.value===os.value)??systems[0];
        const show=()=>{
          const system=current();
          steps.replaceChildren(...system.steps.map(text=>{const step=document.createElement('li');step.textContent=tr(text);return step;}));
          command.value=system.command;copied.textContent='';
        };
        os.addEventListener('change',show);
        copy.type='button';copy.className='button-secondary';copy.textContent='Copy command';
        copied.className='field-hint';copied.setAttribute('role','status');
        copy.addEventListener('click',()=>{
          command.select?.();
          const line=current().command;
          const clip=(globalThis as {navigator?:{clipboard?:{writeText(text:string):Promise<void>}}}).navigator?.clipboard;
          if(!clip){copied.textContent=tr('Select the command and copy it.');return;}
          void clip.writeText(line).then(()=>{copied.textContent=tr('Command copied.');},()=>{copied.textContent=tr('Select the command and copy it.');});
        });
        secret.className='field-hint';secret.textContent=`It works for ${computers} computers within ${days} days. The command contains a secret: share it only with the people who deploy the computers. It is shown only now.`;
        show();
        result.append(osLabel,os,steps,command,copy,copied,secret);
        document.dispatchEvent(new CustomEvent('mixel-company-refresh'));
        return result;
      }});
  });
  groupFilter.addEventListener('change',()=>{void load(null,0,true);});
  if(typeof document.addEventListener==='function')document.addEventListener('click',event=>{const target=(event as Event).target as HTMLElement|null;if(openMenu && !(target && typeof target.closest==='function' && target.closest('.device-menu')))closeMenu();});
  function checkPresence():void{
    if(!authenticated || !entries.length || !['ready','active','incoming','outgoing','deciding','negotiating'].includes(remote.phase))return;
    const hosts=entries.map(device=>device.connection_id);
    void invoke('remote_presence',{hosts}).then(()=>{
      // The answer arrives through the next status update; mark these as asked a moment later.
      window.setTimeout(()=>{for(const host of hosts)checkedOnce.add(host);render();},2500);
    }).catch(()=>{});
  }
  window.setInterval(()=>{if(!document.hidden)checkPresence();},15000);
  window.setInterval(()=>{if(!document.hidden && company.tenant)void loadServerPresence();},60000);
  function accountChanged(value:Account):void{
    const key=value.phase==='authenticated' && value.account?JSON.stringify(value.account):'';
    if (key===accountKey) return;
    ++version;accountKey=key;authenticated=!!key;busy=false;entries=[];nextCursor=null;cursors=[null];index=0;search.value='';
    // Company members (not Mixel staff, not a personal space) wait for company.ts to say which company.
    company={tenant:null,overview:null};companyKey='';saasAll=null;groups=[];
    waitingForCompany=authenticated && !value.account?.staff && !value.account?.personal && (window as unknown as {mixelCompany?:unknown}).mixelCompany!==undefined;
    render();
    if(authenticated)void load(null,0,true);else status.textContent='Sign in to view the devices available to your account.';
  }
  function companyChanged(value:Company):void{
    const key=value.tenant?`${value.tenant.tenant_id}|${value.overview?.role??value.tenant.role}|${value.overview?.status??value.tenant.status}|${!!value.overview?.features?.groups}`:'';
    company={tenant:value.tenant??null,overview:value.overview??null};
    const wasWaiting=waitingForCompany;waitingForCompany=false;
    if(key===companyKey && !wasWaiting){render();return;}
    companyKey=key;++version;busy=false;saasAll=null;entries=[];
    render();
    if(authenticated){void loadGroups();void load(null,0,true);}
  }
  document.addEventListener('mixel-connect-progress',event=>{if(!element<HTMLElement>('devices').hidden)status.textContent=(event as CustomEvent<string>).detail;});
  document.addEventListener('mixel-account-state',event=>{++accountVersion;accountChanged((event as CustomEvent<Account>).detail);});
  document.addEventListener('mixel-company-state',event=>companyChanged((event as CustomEvent<Company>).detail));
  document.addEventListener('mixel-remote-state',event=>{const state=(event as CustomEvent<Remote>).detail;++remoteVersion;const becameReady=state.phase==='ready'&&remote.phase!=='ready';if(state.phase!==remote.phase || state.host!==remote.host || JSON.stringify(state.online??[])!==JSON.stringify(remote.online??[])){remote=state;if(saasAll && remote.host){const mine=saasAll.find(device=>device.connection_id===remote.host);if(mine)remember(mine);}render();}if(becameReady)checkPresence();if(!['ready','active','incoming','outgoing','deciding','negotiating'].includes(state.phase))checkedOnce.clear();});
  form.addEventListener('submit',event=>{event.preventDefault();void load(null,0,true);});
  document.addEventListener('mixel-device-enrolled',()=>{if(!busy)void load(null,0,true);});
  document.addEventListener('mixel-device-removed',()=>{if(!busy)void load(null,0,true);});
  refresh.addEventListener('click',()=>{if(company.tenant)void loadGroups();void load(null,0,true);});
  next.addEventListener('click',()=>{if(nextCursor!==null)void load(nextCursor,index+1);});
  previous.addEventListener('click',()=>{if(index>0)void load(cursors[index-1],index-1);});
  render();
  const initialAccountVersion=accountVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===initialAccountVersion)accountChanged(value);}).catch(()=>{});
  const initialRemoteVersion=remoteVersion;
  void invoke<Remote>('remote_status').then(value=>{if(remoteVersion===initialRemoteVersion){remote=value;render();}}).catch(()=>{});
})();
