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
  let personal = false;
  let key='', staff=false, busy=false, version=0, accountVersion=0, remoteVersion=0, offline=true, cursor:string|null=null, currentSearch='', devices:Device[]=[];
  function render():void{
    controls.hidden=!staff;
    personalForm.hidden = !personal;
    personalName.disabled = personalRegister.disabled = !personal || busy || !offline;
    find.disabled=search.disabled=choice.disabled=confirm.disabled=!staff || busy || !offline;
    more.hidden=cursor===null || devices.length>=200;more.disabled=find.disabled;
    enroll.disabled=find.disabled || !confirm.checked || !devices.some(device=>device.id===choice.value);
    help.textContent=personal ? (offline ? 'Register this computer under your Remote account to get a permanent connection ID.' : 'Go offline before changing this computer’s registration.') : !offline?'Go offline before changing this computer’s enrollment.':staff?'Choose the existing Mixel ISM record for this computer. Its protected identity will be created or reused; existing enrollment keys are never replaced.':'Sign in with your Mixel ID to register a device, or ask your company administrator for access to managed devices.';
  }
  function clear():void{devices=[];cursor=null;choice.replaceChildren();confirm.checked=false;status.textContent='';}
  function account(value:Account):void{
    const next=value.phase==='authenticated' && (value.account?.staff || value.account?.personal) ? JSON.stringify(value.account) : '';
    if(key===next)return;
    ++version;key=next;staff=!!next && !!value.account?.staff;personal=!!next && !!value.account?.personal;busy=false;clear();search.value='';personalName.value='';personalStatus.textContent='';render();
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
      confirm.checked=false;document.dispatchEvent(new CustomEvent('mixel-device-enrolled'));
    }catch(error){if(version===request)status.textContent=String(error);}
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
  render();const av=accountVersion,rv=remoteVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===av)account(value);}).catch(()=>{});
  void invoke<{phase:string}>('remote_status').then(value=>{if(remoteVersion===rv){offline=['offline','error'].includes(value.phase);render();}}).catch(()=>{});
})();
