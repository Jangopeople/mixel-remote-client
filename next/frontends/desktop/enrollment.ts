(() => {
  function element<T extends HTMLElement>(id:string):T{const value=document.getElementById(id);if(!value)throw Error(`Missing enrollment control ${id}`);return value as T;}
  const invoke=window.__TAURI__.core.invoke;
  type Account={phase:string;account:{profile:string;staff:boolean;personal?:boolean;organization:string|null}|null};
  type Device={id:string;name:string;os:string};type Page={devices:Device[];next:string|null};
  const controls=element<HTMLDivElement>('enrollment-controls'), help=element<HTMLParagraphElement>('enrollment-help'), form=element<HTMLFormElement>('enrollment-search-form');
  const search=element<HTMLInputElement>('enrollment-search'), find=element<HTMLButtonElement>('enrollment-find'), more=element<HTMLButtonElement>('enrollment-more');
  const choice=element<HTMLSelectElement>('enrollment-device'), confirm=element<HTMLInputElement>('enrollment-confirm'), enroll=element<HTMLButtonElement>('enrollment-submit'), status=element<HTMLParagraphElement>('enrollment-status');
  const personalForm = element<HTMLFormElement>('personal-device-form');
  const personalName = element<HTMLInputElement>('personal-device-name');
  const personalRegister = element<HTMLButtonElement>('personal-device-register');
  const personalStatus = element<HTMLParagraphElement>('personal-device-status');
  const current=element<HTMLParagraphElement>('enrollment-current'), addBox=element<HTMLDetailsElement>('enrollment-add'), company=element<HTMLSelectElement>('enrollment-company'), newName=element<HTMLInputElement>('enrollment-new-name'), addSubmit=element<HTMLButtonElement>('enrollment-add-submit'), addStatus=element<HTMLParagraphElement>('enrollment-add-status');
  type Company={client_id:string;client_name:string};type Current={device:string;connection_id:number;name:string}|null;
  let companiesLoaded=false;
  const formatId=(id:number)=>String(id).replace(/(\d{3})(\d{3})(\d{3})/,'$1 $2 $3');
  async function refreshCurrent():Promise<void>{
    if(!staff){current.hidden=true;return;}
    const request=version;
    try{
      const value=await invoke<Current>('enrollment_current');
      if(version!==request || !staff)return;
      current.hidden=!value;
      if(value)current.textContent=`This computer is enrolled as ${value.name} · Connection ID ${formatId(value.connection_id)}. Open Remote control and choose “Make this computer available”.`;
    }catch{current.hidden=true;}
  }
  async function loadCompanies():Promise<void>{
    if(!staff || companiesLoaded)return;
    const request=version;
    try{
      const list=await invoke<Company[]>('enrollment_companies');
      if(version!==request || !staff)return;
      company.replaceChildren();
      for(const item of list){const option=document.createElement('option');option.value=item.client_id;option.textContent=item.client_name;company.appendChild(option);}
      companiesLoaded=true;addStatus.textContent=list.length?'':'No company is linked for Remote devices yet. Link the company in Mixel admin first.';
    }catch(error){if(version===request)addStatus.textContent=String(error);}
    finally{render();}
  }
  let personal = false;
  let key='', staff=false, busy=false, version=0, accountVersion=0, remoteVersion=0, offline=true, cursor:string|null=null, currentSearch='', devices:Device[]=[];
  function render():void{
    controls.hidden=!staff;
    personalForm.hidden = !personal;
    personalName.disabled = personalRegister.disabled = !personal || busy || !offline;
    find.disabled=search.disabled=choice.disabled=confirm.disabled=!staff || busy || !offline;
    more.hidden=cursor===null || devices.length>=200;more.disabled=find.disabled;
    enroll.disabled=find.disabled || !confirm.checked || !devices.some(device=>device.id===choice.value);
    company.disabled=newName.disabled=find.disabled;
    addSubmit.disabled=find.disabled || !company.value || !newName.value.trim();
    help.textContent=personal ? (offline ? 'Register this computer under your Remote account to get a permanent connection ID.' : 'Go offline before changing this computer’s registration.') : !offline?'Go offline before changing this computer’s enrollment.':staff?'Find this computer by name and enroll it, or add it below if it is not listed yet. Its protected identity is created or reused; an existing enrollment key is never replaced.':'Sign in with your Mixel ID to register a device, or ask your company administrator for access to managed devices.';
  }
  function clear():void{devices=[];cursor=null;choice.replaceChildren();confirm.checked=false;status.textContent='';}
  function account(value:Account):void{
    const next=value.phase==='authenticated' && (value.account?.staff || value.account?.personal) ? JSON.stringify(value.account) : '';
    if(key===next)return;
    ++version;key=next;staff=!!next && !!value.account?.staff;personal=!!next && !!value.account?.personal;busy=false;clear();search.value='';personalName.value='';personalStatus.textContent='';companiesLoaded=false;company.replaceChildren();newName.value='';addStatus.textContent='';current.hidden=true;render();void refreshCurrent();if(staff&&addBox.open)void loadCompanies();
  }
  async function load(reset:boolean):Promise<void>{
    if(!staff || busy || !offline || (!reset && (cursor===null || devices.length>=200)))return;
    const request=++version;busy=true;if(reset){clear();currentSearch=search.value.trim();}confirm.checked=false;render();status.textContent='Loading Mixel device records…';
    try{
      const page=await invoke<Page>('enrollment_devices',{after:reset?null:cursor,search:currentSearch});
      if(version!==request || !staff)return;
      if(!Array.isArray(page.devices) || page.devices.length>50 || page.devices.some(device=>typeof device.id!=='string' || typeof device.name!=='string' || typeof device.os!=='string'))throw Error('Invalid enrollment directory response');
      devices.push(...page.devices);cursor=page.next;
      for(const device of page.devices){const option=document.createElement('option');option.value=device.id;option.textContent=`${device.name} · ${device.os} · ${device.id.slice(-6)}`;choice.appendChild(option);}
      status.textContent=devices.length?`${devices.length} records loaded. Confirm the selected record belongs to this computer.${devices.length>=200?' Narrow the name search to find other records.':''}`:'No device records match. Use an existing device record in Mixel ISM.';
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  }
  form.addEventListener('submit',event=>{event.preventDefault();void load(true);});
  more.addEventListener('click',()=>{void load(false);});choice.addEventListener('change',()=>{confirm.checked=false;render();});confirm.addEventListener('change',render);
  enroll.addEventListener('click',async()=>{
    const device=devices.find(device=>device.id===choice.value);
    if(!staff || busy || !offline || !confirm.checked || !device)return;
    const request=++version;busy=true;render();status.textContent='Saving the protected identity and verifying enrollment…';
    try{
      const id=await invoke<number>('enroll_this_device',{device:device.id,confirmed:true});
      if(version!==request || !staff)return;
      if(!Number.isInteger(id) || id<100000000 || id>999999999)throw Error('Invalid enrolled connection ID');
      status.textContent=`This computer is enrolled as ${device.name}. Connection ID ${String(id).replace(/(\d{3})(\d{3})(\d{3})/,'$1 $2 $3')}. Return to Remote control to connect.`;
      confirm.checked=false;document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));void refreshCurrent();
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  });
  addBox.addEventListener('toggle',()=>{if(addBox.open)void loadCompanies();});
  newName.addEventListener('input',render);company.addEventListener('change',render);
  addSubmit.addEventListener('click',async()=>{
    const name=newName.value.trim();
    if(!staff || busy || !offline || !company.value || !name)return;
    const request=++version;busy=true;render();addStatus.textContent='Adding this computer and saving its protected identity…';
    try{
      const device=await invoke<string>('add_this_device',{company:company.value,name});
      if(version!==request || !staff)return;
      const id=await invoke<number>('enroll_this_device',{device,confirmed:true});
      if(version!==request || !staff)return;
      if(!Number.isInteger(id) || id<100000000 || id>999999999)throw Error('Invalid enrolled connection ID');
      addStatus.textContent=`This computer is enrolled as ${name}. Connection ID ${formatId(id)}. Open Remote control to connect.`;
      newName.value='';document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));void refreshCurrent();
    }catch(error){if(version===request)addStatus.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  });
  personalForm.addEventListener('submit', async event => {
    event.preventDefault();
    const name = personalName.value.trim();
    if (!personal || busy || !offline || !name) return;
    const request = ++version;
    busy = true;
    render();
    personalStatus.textContent = 'Registering this computer…';
    try {
      const id = await invoke<number>('register_personal_device', {name});
      if (version !== request || !personal) return;
      if (!Number.isInteger(id) || id < 100000000 || id > 999999999) throw Error('Invalid connection ID');
      personalStatus.textContent = `This computer is registered. Connection ID ${String(id).replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3')}. Open Remote control to connect.`;
      document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));
    } catch (error) {
      if (version === request) personalStatus.textContent = String(error);
    } finally {
      if (version === request) { busy = false; render(); }
    }
  });
  document.addEventListener('mixel-account-state',event=>{++accountVersion;account((event as CustomEvent<Account>).detail);});
  document.addEventListener('mixel-remote-state',event=>{++remoteVersion;const phase=(event as CustomEvent<{phase:string}>).detail.phase;const next=phase==='offline'||phase==='error';if(next!==offline){offline=next;confirm.checked=false;render();}});
  const startupPanel=element<HTMLDivElement>('startup-panel'), rememberBox=element<HTMLInputElement>('startup-remember'), autostartBox=element<HTMLInputElement>('startup-autostart'), startupStatus=element<HTMLParagraphElement>('startup-status');
  type Startup={remembered:boolean;autostart:boolean;launched_at_startup:boolean;restored:boolean};
  let signedIn=false, autoTried=false;
  async function refreshStartup():Promise<void>{
    startupPanel.hidden=!signedIn;
    if(!signedIn)return;
    try{
      const value=await invoke<Startup>('startup_settings');
      rememberBox.checked=value.remembered;autostartBox.checked=value.autostart;
      if(!autoTried && offline && (value.restored || value.launched_at_startup)){
        autoTried=true;
        try{await invoke('remote_register');}catch{/* not enrolled yet: stay offline */}
      }
    }catch{/* settings unavailable */}
  }
  rememberBox.addEventListener('change',async()=>{
    const enabled=rememberBox.checked;startupStatus.textContent='';
    try{
      await invoke('set_stay_signed_in',{enabled});
      if(!enabled && autostartBox.checked){await invoke('set_autostart',{enabled:false});autostartBox.checked=false;}
      startupStatus.textContent=enabled?'You stay signed in on this computer until you sign out.':'This computer no longer keeps your sign-in.';
    }catch(error){rememberBox.checked=!enabled;startupStatus.textContent=String(error);}
  });
  autostartBox.addEventListener('change',async()=>{
    const enabled=autostartBox.checked;startupStatus.textContent='';
    try{
      if(enabled && !rememberBox.checked){await invoke('set_stay_signed_in',{enabled:true});rememberBox.checked=true;}
      await invoke('set_autostart',{enabled});
      startupStatus.textContent=enabled?'Mixel Remote starts with this computer and makes it available.':'Mixel Remote no longer starts with this computer.';
    }catch(error){autostartBox.checked=!enabled;startupStatus.textContent=String(error);}
  });
  document.addEventListener('mixel-account-state',event=>{const phase=(event as CustomEvent<Account>).detail.phase;const next=phase==='authenticated';if(next!==signedIn){signedIn=next;void refreshStartup();}});
  render();const av=accountVersion,rv=remoteVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===av)account(value);}).catch(()=>{});
  void invoke<{phase:string}>('remote_status').then(value=>{if(remoteVersion===rv){offline=['offline','error'].includes(value.phase);render();}}).catch(()=>{});
})();
