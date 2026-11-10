(() => {
  const invoke=window.__TAURI__.core.invoke;
  function element<T extends HTMLElement>(id:string):T{const item=document.getElementById(id);if(!item)throw Error(`Missing file control ${id}`);return item as T;}
  type Transfer={id:string;name:string;size:number;offset:number;phase:string;message:string};
  type View={connected:boolean;incoming:Transfer|null;outgoing:Transfer|null;message:string};
  const panel=element<HTMLDivElement>('remote-files'),send=element<HTMLButtonElement>('remote-file-send'),status=element<HTMLParagraphElement>('remote-file-status'),error=element<HTMLParagraphElement>('remote-file-error');
  const incoming=element<HTMLDivElement>('remote-file-incoming'),outgoing=element<HTMLDivElement>('remote-file-outgoing');
  let consent='',phase='',enabled=false,busy=false,polling=false,version=0,view:View|undefined,lastIncoming='',lastOutgoing='';
  const active=(item:Transfer):boolean=>['offered','waiting','transferring','verifying','paused'].includes(item.phase);
  const bytes=(value:number):string=>value<1024?`${value} B`:value<1024**2?`${(value/1024).toFixed(1)} KiB`:value<1024**3?`${(value/1024**2).toFixed(1)} MiB`:`${(value/1024**3).toFixed(2)} GiB`;
  function card(target:HTMLElement,item:Transfer|null,direction:'incoming'|'outgoing'):void{
    const key=JSON.stringify([item,busy,view?.connected]);
    if(direction==='incoming'){if(key===lastIncoming)return;lastIncoming=key;}else{if(key===lastOutgoing)return;lastOutgoing=key;}
    target.replaceChildren();if(!item)return;
    const title=document.createElement('p');title.textContent=`${direction==='incoming'?'Receiving':'Sending'}: ${item.name} · ${bytes(item.size)}`;target.appendChild(title);
    const progress=document.createElement('progress');progress.max=item.size||1;progress.value=item.phase==='complete'?progress.max:item.offset;progress.setAttribute('aria-label',`${direction} file progress`);target.appendChild(progress);
    const info=document.createElement('p');info.textContent=`${item.phase} · ${bytes(item.offset)} / ${bytes(item.size)}${item.message?` · ${item.message}`:''}`;target.appendChild(info);
    if(direction==='incoming'&&item.phase==='offered'){
      const accept=document.createElement('button');accept.type='button';accept.textContent='Accept and save as…';accept.disabled=busy||!view?.connected;accept.addEventListener('click',()=>void action('remote_file_accept',item.id));target.appendChild(accept);
    }
    if(active(item)){
      const cancel=document.createElement('button');cancel.type='button';cancel.className='secondary';cancel.textContent=item.phase==='offered'?'Decline':'Cancel transfer';
      // Cancelling remains available while another local picker is open.
      cancel.addEventListener('click',()=>void cancelTransfer(item.id));target.appendChild(cancel);
    }
  }
  function render():void{
    panel.hidden=!enabled||!consent;
    document.dispatchEvent(new CustomEvent('mixel-file-attention',{detail:{consent,pending:enabled&&view?.incoming?.phase==='offered'}}));
    send.disabled=busy||phase!=='active'||!view?.connected||!!(view.outgoing&&active(view.outgoing));
    status.textContent=view?.message??'Opening the encrypted file channel…';
    card(incoming,view?.incoming??null,'incoming');card(outgoing,view?.outgoing??null,'outgoing');
  }
  async function read():Promise<void>{
    if(polling||!enabled||!consent)return;polling=true;const current=consent,epoch=version;
    try{const next=await invoke<View>('remote_file_status',{consent:current});if(epoch===version&&current===consent){view=next;render();}}
    catch(value){if(epoch===version&&phase==='active')error.textContent=String(value);}
    finally{polling=false;}
  }
  async function action(command:string,id?:string):Promise<void>{
    if(busy||!consent||!enabled)return;const current=consent,epoch=version;busy=true;error.textContent='';render();
    try{await invoke(command,{consent:current,...(id?{id}:{})});}catch(value){if(epoch===version)error.textContent=String(value);}
    finally{if(epoch===version){busy=false;await read();render();}}
  }
  async function cancelTransfer(id:string):Promise<void>{const current=consent,epoch=version;if(!current)return;try{await invoke('remote_file_cancel',{consent:current,id});if(epoch===version)await read();}catch(value){if(epoch===version)error.textContent=String(value);}}
  send.addEventListener('click',()=>void action('remote_file_send'));
  document.addEventListener('mixel-remote-state',((event:CustomEvent<{phase:string;consent:string|null;files:boolean}>)=>{
    const detail=event.detail;phase=detail.phase;
    const next=detail.consent??'';
    if(next!==consent){++version;consent=next;view=undefined;busy=false;lastIncoming=lastOutgoing='';error.textContent='';}
    enabled=detail.files&&['active','negotiating'].includes(phase);render();if(enabled)void read();
  }) as EventListener);
  setInterval(()=>void read(),500);render();
})();
