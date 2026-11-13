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
  const signinPanel=element<HTMLDivElement>('signin-panel'), signinCurrent=element<HTMLParagraphElement>('signin-current'), signinSetup=element<HTMLDivElement>('signin-setup'), signinCompany=element<HTMLSelectElement>('signin-company'), signinName=element<HTMLInputElement>('signin-name'), signinEnable=element<HTMLButtonElement>('signin-enable'), signinDisable=element<HTMLButtonElement>('signin-disable'), signinStatus=element<HTMLParagraphElement>('signin-status'), signinUpdate=element<HTMLButtonElement>('signin-update'), signinOutdatedText=element<HTMLParagraphElement>('signin-outdated');
  type SignIn={wayland_greeter?:boolean;supported:boolean;outdated:boolean;connection_id:number|null;name:string|null};
  let signinBusy=false, signinLoaded=false, signinInstalled=false, signinOutdated=false;
  function renderSignin():void{
    signinSetup.hidden=signinInstalled;signinDisable.hidden=!signinInstalled;
    signinCompany.disabled=signinName.disabled=signinBusy;
    signinEnable.disabled=signinBusy || !signinCompany.value || !signinName.value.trim();
    signinDisable.disabled=signinUpdate.disabled=signinBusy;
    signinUpdate.hidden=signinOutdatedText.hidden=!(signinInstalled && signinOutdated);
  }
  async function refreshSignin():Promise<void>{
    if(!staff){signinPanel.hidden=true;return;}
    try{
      const value=await invoke<SignIn>('signin_screen_status');
      signinPanel.hidden=!value.supported || !staff;
      if(signinPanel.hidden)return;
      signinInstalled=value.connection_id!==null;signinOutdated=value.outdated;
      signinCurrent.hidden=!signinInstalled;
      if(signinInstalled)signinCurrent.textContent=`Always-on access is on for ${value.name ?? 'this computer'} · Remote ID ${formatId(value.connection_id ?? 0)}. A technician with unattended access can pair and connect to this ID.`;
      // Linux: a Wayland sign-in screen lets no program see or control it.
      if(signinInstalled && value.wayland_greeter)signinCurrent.textContent=`Always-on access is installed (Remote ID ${formatId(value.connection_id ?? 0)}), but this computer’s sign-in screen uses Wayland, which cannot be shared. Switch the sign-in screen to Xorg (GDM: WaylandEnable=false) to use it.`;
      if(!signinLoaded && !signinInstalled){
        const list=await invoke<Company[]>('enrollment_companies');
        signinCompany.replaceChildren();
        for(const item of list){const option=document.createElement('option');option.value=item.client_id;option.textContent=item.client_name;signinCompany.appendChild(option);}
        signinLoaded=true;
        if(!list.length)signinStatus.textContent='No company is linked for Remote devices yet. Link the company in Mixel admin first.';
      }
    }catch{signinPanel.hidden=true;}
    finally{renderSignin();}
  }
  signinName.addEventListener('input',renderSignin);signinCompany.addEventListener('change',renderSignin);
  signinEnable.addEventListener('click',async()=>{
    if(signinBusy)return;signinBusy=true;renderSignin();
    signinStatus.textContent='Waiting for administrator approval on this computer…';
    try{
      const id=await invoke<number>('signin_screen_enable',{company:signinCompany.value,name:signinName.value.trim()});
      signinStatus.textContent=`Always-on access is on. Remote ID ${formatId(id)}.`;
    }catch(error){signinStatus.textContent=String(error);}
    finally{signinBusy=false;await refreshSignin();}
  });
  signinUpdate.addEventListener('click',async()=>{
    if(signinBusy)return;signinBusy=true;renderSignin();
    signinStatus.textContent='Waiting for administrator approval on this computer…';
    try{await invoke('signin_screen_update');signinStatus.textContent='Always-on access is up to date.';}
    catch(error){signinStatus.textContent=String(error);}
    finally{signinBusy=false;await refreshSignin();}
  });
  signinDisable.addEventListener('click',async()=>{
    if(signinBusy)return;signinBusy=true;renderSignin();
    signinStatus.textContent='Waiting for administrator approval on this computer…';
    try{await invoke('signin_screen_disable');signinStatus.textContent='Always-on access is off.';}
    catch(error){signinStatus.textContent=String(error);}
    finally{signinBusy=false;signinLoaded=false;await refreshSignin();}
  });
  document.addEventListener('mixel-account-state',()=>{void Promise.resolve().then(refreshSignin);});
  // Rollout: company token + command for many computers (team members).
  const rolloutPanel=element<HTMLDivElement>('rollout-panel'), rolloutCompany=element<HTMLSelectElement>('rollout-company'), rolloutLabel=element<HTMLInputElement>('rollout-label'), rolloutCount=element<HTMLInputElement>('rollout-count'), rolloutDays=element<HTMLInputElement>('rollout-days'), rolloutCreate=element<HTMLButtonElement>('rollout-create'), rolloutResult=element<HTMLDivElement>('rollout-result'), rolloutCommand=element<HTMLTextAreaElement>('rollout-command'), rolloutCopy=element<HTMLButtonElement>('rollout-copy'), rolloutList=element<HTMLUListElement>('rollout-list'), rolloutStatus=element<HTMLParagraphElement>('rollout-status');
  type Rollout={id:string;label:string;expires_at:string;max_uses:number;uses:number;revoked_at:string|null};
  let rolloutBusy=false, rolloutLoaded=false;
  function renderRollout():void{
    rolloutCreate.disabled=rolloutBusy || !rolloutCompany.value || !rolloutLabel.value.trim();
  }
  async function refreshRolloutList():Promise<void>{
    if(!rolloutCompany.value)return;
    try{
      const list=await invoke<Rollout[]>('rollout_list',{company:rolloutCompany.value});
      rolloutList.replaceChildren();
      for(const item of list){
        const row=document.createElement('li');
        const text=document.createElement('span');
        const state=item.revoked_at?'revoked':new Date(item.expires_at).getTime()<Date.now()?'expired':`valid until ${new Date(item.expires_at).toLocaleDateString()}`;
        text.textContent=`${item.label} · ${item.uses}/${item.max_uses} computers · ${state}`;
        row.appendChild(text);
        if(!item.revoked_at){
          const revoke=document.createElement('button');revoke.type='button';revoke.className='button-quiet';revoke.textContent='Revoke';
          revoke.addEventListener('click',async()=>{try{await invoke('rollout_revoke',{id:item.id});await refreshRolloutList();}catch(error){rolloutStatus.textContent=String(error);}});
          row.appendChild(revoke);
        }
        rolloutList.appendChild(row);
      }
    }catch(error){rolloutStatus.textContent=String(error);}
  }
  async function refreshRollout():Promise<void>{
    rolloutPanel.hidden=!staff;
    if(!staff || rolloutLoaded)return;
    try{
      const list=await invoke<Company[]>('enrollment_companies');
      rolloutCompany.replaceChildren();
      for(const item of list){const option=document.createElement('option');option.value=item.client_id;option.textContent=item.client_name;rolloutCompany.appendChild(option);}
      rolloutLoaded=true;
      await refreshRolloutList();
    }catch{rolloutPanel.hidden=true;}
    finally{renderRollout();}
  }
  rolloutLabel.addEventListener('input',renderRollout);
  rolloutCompany.addEventListener('change',()=>{renderRollout();void refreshRolloutList();});
  rolloutCreate.addEventListener('click',async()=>{
    if(rolloutBusy)return;rolloutBusy=true;renderRollout();rolloutStatus.textContent='Creating…';
    try{
      const created=await invoke<{token:string;windows_command:string}>('rollout_create',{company:rolloutCompany.value,label:rolloutLabel.value.trim(),days:Number(rolloutDays.value)||30,computers:Number(rolloutCount.value)||50});
      rolloutCommand.value=created.windows_command;rolloutResult.hidden=false;rolloutStatus.textContent='Rollout command created.';
      await refreshRolloutList();
    }catch(error){rolloutStatus.textContent=String(error);}
    finally{rolloutBusy=false;renderRollout();}
  });
  rolloutCopy.addEventListener('click',()=>{rolloutCommand.select();void navigator.clipboard?.writeText(rolloutCommand.value).then(()=>{rolloutStatus.textContent='Command copied.';},()=>{});});
  document.addEventListener('mixel-account-state',()=>{rolloutLoaded=false;void Promise.resolve().then(refreshRollout);});
  render();const av=accountVersion,rv=remoteVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===av)account(value);}).catch(()=>{});
  void invoke<{phase:string}>('remote_status').then(value=>{if(remoteVersion===rv){offline=['offline','error'].includes(value.phase);render();}}).catch(()=>{});
})();
