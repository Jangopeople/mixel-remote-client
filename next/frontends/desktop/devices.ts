(() => {
  function element<T extends HTMLElement>(id: string): T {
    const value=document.getElementById(id); if (!value) throw Error(`Missing device directory control ${id}`); return value as T;
  }
  const invoke=window.__TAURI__.core.invoke;
  type Device={device:string; connection_id:number; name:string; os:string};
  type Page={devices:Device[]; next:number|null};
  type Account={phase:string; account:{profile:string;organization:string|null;staff:boolean}|null};
  type Remote={phase:string;host:number|null;online?:number[];message?:string};
  const form=element<HTMLFormElement>('devices-search-form'), search=element<HTMLInputElement>('devices-search');
  const refresh=element<HTMLButtonElement>('devices-refresh'), previous=element<HTMLButtonElement>('devices-previous'), next=element<HTMLButtonElement>('devices-next');
  const submit=element<HTMLButtonElement>('devices-submit');
  const list=element<HTMLUListElement>('devices-list'), status=element<HTMLParagraphElement>('devices-status');
  const banner=element<HTMLDivElement>('devices-connection'), bannerTitle=element<HTMLElement>('devices-connection-title'), bannerDetail=element<HTMLElement>('devices-connection-detail');
  element<HTMLButtonElement>('devices-reconnect').addEventListener('click',()=>{status.textContent='Reconnecting this computer…';document.dispatchEvent(new CustomEvent('mixel-reconnect-now'));});
  // Devices whose presence has been asked at least once while connected: only those can be called Offline.
  const checkedOnce=new Set<number>();
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
    const connected=['ready','active','incoming','outgoing','deciding','negotiating'].includes(remote.phase);
    const inSession=['active','incoming','outgoing','deciding','negotiating','closing'].includes(remote.phase);
    const reconnecting=authenticated && !connected && remote.phase!=='closing';
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
      const kind=self?'self':!connected?'unknown':live?'online':known?'offline':'checking';
      presence.dataset.state=kind==='self'&&!connected?'unknown':kind;
      presence.textContent=kind==='self'?(connected?'This computer':'This computer · not connected'):kind==='online'?'Online':kind==='offline'?'Offline':kind==='checking'?'Checking…':'Status unknown';
      let why='';
      if(kind==='self')why=connected?'You are using this device.':'Reconnecting automatically.';
      else if(kind==='offline')why='Turned off, asleep or without internet. It must be online to connect.';
      else if(kind==='unknown')why='Shown once this computer is connected.';
      else if(kind==='online')why='Ready. The person there accepts your request, unless unattended access is set up.';
      reason.textContent=why;
      state.append(presence,reason);
      name.textContent=device.name;detail.textContent=device.os==='other'?'phone or tablet':device.os;identity.append(name,detail);
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
      row.append(connect);list.append(row);
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
      status.textContent=entries.length?`Page ${index+1} · ${entries.length} devices.`:'No devices match this search.';
      checkPresence();
    }catch(error){if(version===request)status.textContent=String(error);}
    finally{if(version===request){busy=false;render();}}
  }
  function checkPresence():void{
    if(!authenticated || !entries.length || !['ready','active','incoming','outgoing','deciding','negotiating'].includes(remote.phase))return;
    const hosts=entries.map(device=>device.connection_id);
    void invoke('remote_presence',{hosts}).then(()=>{
      // The answer arrives through the next status update; mark these as asked a moment later.
      window.setTimeout(()=>{for(const host of hosts)checkedOnce.add(host);render();},2500);
    }).catch(()=>{});
  }
  window.setInterval(()=>{if(!document.hidden)checkPresence();},15000);
  function accountChanged(value:Account):void{
    const key=value.phase==='authenticated' && value.account?JSON.stringify(value.account):'';
    if (key===accountKey) return;
    ++version;accountKey=key;authenticated=!!key;busy=false;entries=[];nextCursor=null;cursors=[null];index=0;search.value='';render();
    if(authenticated)void load(null,0,true);else status.textContent='Sign in to view the devices available to your account.';
  }
  document.addEventListener('mixel-connect-progress',event=>{if(!element<HTMLElement>('devices').hidden)status.textContent=(event as CustomEvent<string>).detail;});
  document.addEventListener('mixel-account-state',event=>{++accountVersion;accountChanged((event as CustomEvent<Account>).detail);});
  document.addEventListener('mixel-remote-state',event=>{const state=(event as CustomEvent<Remote>).detail;++remoteVersion;const becameReady=state.phase==='ready'&&remote.phase!=='ready';if(state.phase!==remote.phase || state.host!==remote.host || JSON.stringify(state.online??[])!==JSON.stringify(remote.online??[])){remote=state;render();}if(becameReady)checkPresence();if(!['ready','active','incoming','outgoing','deciding','negotiating'].includes(state.phase))checkedOnce.clear();});
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
