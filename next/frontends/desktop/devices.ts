(() => {
  function element<T extends HTMLElement>(id: string): T {
    const value=document.getElementById(id); if (!value) throw Error(`Missing device directory control ${id}`); return value as T;
  }
  const invoke=window.__TAURI__.core.invoke;
  type Device={device:string; connection_id:number; name:string; os:string};
  type Page={devices:Device[]; next:number|null};
  type Account={phase:string; account:{profile:string;organization:string|null;staff:boolean}|null};
  type Remote={phase:string;host:number|null;online?:number[]};
  const form=element<HTMLFormElement>('devices-search-form'), search=element<HTMLInputElement>('devices-search');
  const refresh=element<HTMLButtonElement>('devices-refresh'), previous=element<HTMLButtonElement>('devices-previous'), next=element<HTMLButtonElement>('devices-next');
  const submit=element<HTMLButtonElement>('devices-submit');
  const list=element<HTMLUListElement>('devices-list'), status=element<HTMLParagraphElement>('devices-status');
  let accountKey='', authenticated=false, busy=false, version=0, accountVersion=0, remoteVersion=0, currentSearch='', remote:Remote={phase:'offline',host:null};
  let entries:Device[]=[], nextCursor:number|null=null, cursors:(number|null)[]=[null], index=0;
  const formatId=(value:number):string=>String(value).replace(/(\d{3})(\d{3})(\d{3})/,'$1 $2 $3');
  function render():void {
    refresh.disabled=search.disabled=!authenticated || busy;
    submit.disabled=refresh.disabled;
    previous.disabled=!authenticated || busy || index===0;
    next.disabled=!authenticated || busy || nextCursor===null;
    element<HTMLElement>('devices-empty').hidden=entries.length>0;
    element<HTMLElement>('devices-empty-title').textContent=busy?'Loading your devices…':!authenticated?'Your devices belong here':currentSearch?'No matching devices':'No devices to display';
    element<HTMLElement>('devices-empty-detail').textContent=!authenticated?'Sign in to see the devices available to your account.':currentSearch?'Try another device name or clear the search.':'Devices appear here after a Mixel team member enrolls them.';
    element<HTMLElement>('devices-empty-action').hidden=authenticated;
    list.replaceChildren();
    for (const device of entries) {
      const row=document.createElement('li'), identity=document.createElement('div'), name=document.createElement('strong'), detail=document.createElement('span'), id=document.createElement('code'), connect=document.createElement('button');
      const live=(remote.online??[]).includes(device.connection_id);
      const checked=['ready','active','incoming','outgoing'].includes(remote.phase);
      const presence=document.createElement('span');presence.className='presence';presence.dataset.online=String(live);
      presence.textContent=!checked?'':live?'Online':'Offline';
      name.textContent=device.name;detail.textContent=device.os;identity.append(name,detail,presence);
      id.textContent=formatId(device.connection_id);connect.type='button';connect.textContent='Connect';
      connect.disabled=busy || !authenticated || remote.phase!=='ready' || remote.host===device.connection_id;
      connect.addEventListener('click',async()=>{
        const request=++version;busy=true;render();status.textContent=`Requesting ${device.name}…`;
        try { await invoke('remote_request',{host:device.connection_id}); if (version===request) {const state=await invoke<Remote>('remote_status');if(version===request && authenticated){remote=state;status.textContent='Connection requested. The host must Accept before sharing starts.';}} }
        catch (error) { if (version===request) status.textContent=String(error); }
        finally { if (version===request) {busy=false;render();} }
      });
      row.append(identity,id,connect);list.append(row);
    }
  }
  async function load(cursor:number|null, wantedIndex:number, reset=false):Promise<void>{
    if (!authenticated || busy) return;
    const request=++version;busy=true;
    if (reset){entries=[];cursors=[null];index=0;nextCursor=null;currentSearch=search.value.trim();}
    render();status.textContent='Loading enrolled devices…';
    try {
      const page=await invoke<Page>('remote_devices',{after:cursor,search:currentSearch});
      if (version!==request || !authenticated) return;
      if (!Array.isArray(page.devices) || page.devices.length>50 || page.devices.some(device=>typeof device.name!=='string' || typeof device.os!=='string' || !Number.isInteger(device.connection_id) || device.connection_id<100000000 || device.connection_id>999999999) || (page.next!==null && (!Number.isInteger(page.next) || page.next<100000000 || page.next>999999999))) throw Error('Invalid device directory response');
      entries=page.devices;nextCursor=page.next;index=wantedIndex;cursors[index]=cursor;
      status.textContent=entries.length?`Page ${index+1} · ${entries.length} enrolled devices. Make this computer available before connecting.`:'No enrolled devices match this search.';
      checkPresence();
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  }
  function checkPresence():void{
    if(!authenticated || !entries.length || !['ready','active','incoming','outgoing'].includes(remote.phase))return;
    void invoke('remote_presence',{hosts:entries.map(device=>device.connection_id)}).catch(()=>{});
  }
  window.setInterval(()=>{if(!document.hidden)checkPresence();},15000);
  function accountChanged(value:Account):void{
    const key=value.phase==='authenticated' && value.account?JSON.stringify(value.account):'';
    if (key===accountKey) return;
    ++version;accountKey=key;authenticated=!!key;busy=false;entries=[];nextCursor=null;cursors=[null];index=0;search.value='';render();
    if(authenticated)void load(null,0,true);else status.textContent='Sign in to view the devices available to your account.';
  }
  document.addEventListener('mixel-account-state',event=>{++accountVersion;accountChanged((event as CustomEvent<Account>).detail);});
  document.addEventListener('mixel-remote-state',event=>{const state=(event as CustomEvent<Remote>).detail;++remoteVersion;const becameReady=state.phase==='ready'&&remote.phase!=='ready';if(state.phase!==remote.phase || state.host!==remote.host || JSON.stringify(state.online??[])!==JSON.stringify(remote.online??[])){remote=state;render();}if(becameReady)checkPresence();});
  form.addEventListener('submit',event=>{event.preventDefault();void load(null,0,true);});
  document.addEventListener('mixel-device-enrolled',()=>{if(!busy)void load(null,0,true);});
  refresh.addEventListener('click',()=>{void load(null,0,true);});
  next.addEventListener('click',()=>{if(nextCursor!==null)void load(nextCursor,index+1);});
  previous.addEventListener('click',()=>{if(index>0)void load(cursors[index-1],index-1);});
  render();
  const initialAccountVersion=accountVersion;
  void invoke<Account>('account_status').then(value=>{if(accountVersion===initialAccountVersion)accountChanged(value);}).catch(()=>{});
  const initialRemoteVersion=remoteVersion;
  void invoke<Remote>('remote_status').then(value=>{if(remoteVersion===initialRemoteVersion){remote=value;render();}}).catch(()=>{});
})();
