(() => {
  const pages = Array.from(document.querySelectorAll<HTMLElement>('[data-workspace-page]'));
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('.app-navigation a'));
  const titles: Record<string, string> = {remote:'Remote control',devices:'Devices',account:'Account',setup:'Device settings',diagnostics:'Diagnostics'};
  const title = document.getElementById('workspace-title')!;
  const notice = document.getElementById('workspace-signin-notice')!;
  const accountLabel = document.getElementById('shell-account-label')!;
  const connection = document.getElementById('shell-connection')!;
  const connectionDetail = document.getElementById('shell-connection-detail')!;
  const dot = document.getElementById('shell-status-dot')!;
  const mode = document.getElementById('workspace-session-mode')!;
  const empty = document.getElementById('workspace-session-empty')!;
  const screen = document.getElementById('workspace-screen')!;
  const fit = document.getElementById('workspace-fit') as HTMLButtonElement;
  const fullscreen = document.getElementById('workspace-fullscreen') as HTMLButtonElement;
  const fullscreenError = document.getElementById('workspace-fullscreen-error')!;
  const canvas = document.getElementById('remote-canvas') as HTMLCanvasElement;
  const remotePage = document.getElementById('remote')!;
  const screenTab = document.getElementById('workspace-screen-tab') as HTMLButtonElement;
  const filesTab = document.getElementById('workspace-files-tab') as HTMLButtonElement;
  const clipboardTab = document.getElementById('workspace-clipboard-tab') as HTMLButtonElement;
  const chatTab = document.getElementById('workspace-chat-tab') as HTMLButtonElement;
  const sessionTabs = [[screenTab,'screen'],[filesTab,'files'],[clipboardTab,'clipboard'],[chatTab,'chat']] as const;
  const globalStop = document.getElementById('workspace-global-stop') as HTMLButtonElement;
  let authenticated = false, phase = 'offline', sharing = false, video = false, control = false, lastPhase = '', lastRoute = '';
  let files = false, clipboard = false, chat = false, stopping = false, consent = '', tool = 'screen', fileAttention = false, unreadChat = 0;
  let session = '', fullscreenSession = '', switching = false;
  function viewable(): boolean {
    return authenticated && phase === 'active' && !sharing && !stopping &&
      video && lastRoute === 'remote' && tool === 'screen' && !!session;
  }
  async function changeFullscreen(target: string): Promise<void> {
    if (switching || (target && (!viewable() || target !== session))) return;
    switching = true;
    fullscreen.disabled = true;
    fullscreenError.textContent = '';
    document.dispatchEvent(new CustomEvent('mixel-workspace-leaving'));
    document.dispatchEvent(new CustomEvent('mixel-workspace-focus',{detail:{enabled:false}}));
    let success = false;
    try {
      await window.__TAURI__.core.invoke('remote_view_fullscreen', {session: target || null});
      fullscreenSession = target;
      success = true;
    } catch (error) {
      fullscreenError.textContent = String(error);
    } finally {
      switching = false;
      render(success);
      if (success && fullscreenSession === target && viewable() &&
          (!target || target === session)) canvas.focus({preventScroll:true});
    }
  }
  let controllerOnly = false;
  function route(): void {
    const name = window.location.hash.slice(1);
    const current = Object.hasOwn(titles, name) && !(controllerOnly && name === 'diagnostics') ? name : 'remote';
    if (lastRoute === 'remote' && current !== 'remote') document.dispatchEvent(new CustomEvent('mixel-workspace-leaving'));
    lastRoute = current;
    for (const page of pages) page.hidden = page.id !== current;
    for (const link of links) {
      if (link.hash === '#' + current) link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    }
    title.textContent = titles[current];
    document.querySelector('.app-content')?.scrollTo(0,0);
    render();
  }
  function render(autoExit = true): void {
    const live = authenticated ? phase : 'offline';
    notice.hidden = authenticated && !['offline','error'].includes(live);
    document.getElementById('workspace-notice-message')!.textContent = authenticated ? (controllerOnly ? 'Connect to the Mixel network. First time here? Enroll this controller in device settings.' : 'Make this computer available to connect. First time here? Open device settings to finish setup.') : (controllerOnly ? 'Sign in to access your managed devices.' : 'Sign in to get your connection ID and access your devices.');
    const noticeAction = document.getElementById('workspace-notice-action') as HTMLAnchorElement;
    noticeAction.href = authenticated ? '#setup' : '#account'; noticeAction.textContent = authenticated ? 'Device settings →' : 'Sign in →';
    dot.dataset.state = live;
    const busy = ['incoming','outgoing','deciding','negotiating','active','closing'].includes(live);
    const hasFiles = live === 'active' && files, hasClipboard = live === 'active' && clipboard && !sharing;
    const hasChat = live === 'active' && chat;
    if ((tool === 'files' && !hasFiles) || (tool === 'clipboard' && !hasClipboard) || (tool === 'chat' && !hasChat)) tool = 'screen';
    const presenting = !!fullscreenSession;
    document.body.dataset.remoteFullscreen = String(presenting);
    if (autoExit && presenting && !switching && (!viewable() || fullscreenSession !== session)) {
      void changeFullscreen('');
    }
    remotePage.dataset.session = String(busy); remotePage.dataset.phase = live; remotePage.dataset.tool = tool;
    document.dispatchEvent(new CustomEvent('mixel-workspace-focus',{detail:{enabled:!switching&&lastRoute==='remote'&&tool==='screen'&&live==='active'&&!sharing}}));
    globalStop.hidden = !busy; globalStop.disabled = stopping || !consent;
    document.getElementById('workspace-session-tabs')!.hidden = live !== 'active';
    screenTab.setAttribute('aria-selected',String(tool === 'screen'));screenTab.tabIndex=tool==='screen'?0:-1;
    filesTab.setAttribute('aria-selected',String(tool === 'files'));filesTab.tabIndex=tool==='files'?0:-1; filesTab.disabled = !hasFiles;
    filesTab.textContent = fileAttention && hasFiles ? 'Files (1)' : 'Files';
    clipboardTab.setAttribute('aria-selected',String(tool === 'clipboard'));clipboardTab.tabIndex=tool==='clipboard'?0:-1; clipboardTab.disabled = !hasClipboard;
    chatTab.setAttribute('aria-selected',String(tool === 'chat'));chatTab.tabIndex=tool==='chat'?0:-1; chatTab.disabled=!hasChat;
    chatTab.textContent = unreadChat && hasChat ? `Chat (${unreadChat})` : 'Chat';
    fit.hidden = presenting || tool !== 'screen' || sharing;
    fit.disabled = live !== 'active' || sharing || !video;
    fullscreen.hidden = !presenting && !viewable();
    fullscreen.disabled = switching || (!presenting && !viewable());
    fullscreen.textContent = presenting ? 'Exit full screen' : 'Full screen';
    fullscreen.setAttribute('aria-pressed', String(presenting));
    mode.textContent = live === 'closing' ? 'Ending…' : live === 'active' ? (sharing ? 'Sharing this computer' : control ? 'Remote control' : 'View only') : busy ? 'Connecting' : 'No active session';
    empty.hidden = video && live === 'active' && !sharing;
    connection.textContent = live === 'closing' ? 'Ending session…' : live === 'active' ? 'Session active' : live === 'ready' ? 'Ready to connect' : live === 'incoming' ? 'Incoming request' : live === 'error' ? 'Connection error' : busy ? 'Connecting…' : 'Not connected';
    connectionDetail.textContent = !authenticated ? 'Sign in to get started' : live === 'ready' ? (controllerOnly ? 'Connected to the Mixel network' : 'This computer is available') : live === 'active' ? (sharing ? 'Sharing this computer' : 'Viewing a remote device') : live === 'error' ? 'Reconnecting automatically…' : 'Your account is signed in';
    document.getElementById('workspace-security-label')!.textContent = live === 'closing' ? 'Ending the session…' : live === 'active' ? 'End-to-end encrypted' : 'No screen is being shared';
    document.getElementById('workspace-session-actions')!.hidden = !busy && !presenting;
    document.getElementById('workspace-empty-title')!.textContent = live === 'closing' ? 'Ending the session' : live === 'active' && sharing ? 'You’re sharing your screen' : busy ? 'Waiting for a secure connection' : 'Your next connection starts here';
    document.getElementById('workspace-empty-detail')!.textContent = live === 'closing' ? 'Waiting for confirmation that the connection has closed.' : live === 'active' && sharing ? 'The other person can see the display you approved. End the session at any time.' : busy ? 'Screen sharing starts only after the host accepts the request.' : 'Enter a partner ID above. Their screen appears here once they accept.';
  }
  document.addEventListener('mixel-account-state', ((event: CustomEvent<{phase:string;account:unknown}>) => {
    authenticated = event.detail.phase === 'authenticated' && !!event.detail.account;
    accountLabel.textContent = authenticated ? 'My account' : event.detail.phase === 'mfa_required' ? 'Verify account' : 'Sign in';
    render();
  }) as EventListener);
  document.addEventListener('mixel-remote-state', ((event: CustomEvent<{phase:string;controllerOnly?:boolean;session?:string|null;consent?:string|null;files?:boolean;clipboard?:boolean;sharing?:boolean;control?:boolean;stopping?:boolean}>) => {
    if (event.detail.controllerOnly && !controllerOnly) {
      controllerOnly = true;
      if (lastRoute === 'diagnostics') route();
    }
    phase = event.detail.phase; sharing = !!event.detail.sharing; control = !!event.detail.control;
    const nextConsent = event.detail.consent ?? '';
    if (consent !== nextConsent) { tool = 'screen'; fileAttention = false; }
    consent = nextConsent; files = !!event.detail.files; clipboard = !!event.detail.clipboard; stopping = !!event.detail.stopping;
    const nextSession = event.detail.session ?? '';
    if (phase !== 'active' || nextSession !== session) video = false;
    session = nextSession;
    if (['incoming','outgoing'].includes(phase) && lastPhase !== phase) { window.location.hash = '#remote'; route(); }
    lastPhase = phase; render();
  }) as EventListener);
  document.addEventListener('mixel-video-visibility', ((event: CustomEvent<{visible:boolean}>) => {video=event.detail.visible;render();}) as EventListener);
  document.addEventListener('mixel-file-attention', ((event: CustomEvent<{consent:string;pending:boolean}>) => {
    if(event.detail.consent===consent){fileAttention=event.detail.pending;render();}
  }) as EventListener);
  document.addEventListener('mixel-chat-availability', ((event: CustomEvent<{available:boolean}>) => {
    chat=event.detail.available;
    if (!chat) unreadChat=0;
    render();
  }) as EventListener);
  document.addEventListener('mixel-chat-unread', ((event: CustomEvent<{count:number}>) => {
    if (lastRoute!=='remote' || tool!=='chat') {
      unreadChat=Math.min(80,unreadChat+event.detail.count);
      render();
    }
  }) as EventListener);
  for (const [button,name] of sessionTabs) {
    button.addEventListener('click',()=>{
      if(button.disabled)return;
      if(tool==='screen'&&name!=='screen')document.dispatchEvent(new CustomEvent('mixel-workspace-leaving'));
      tool=name;
      if (tool==='chat') unreadChat=0;
      render();
    });
    button.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const available=sessionTabs.map(([tab])=>tab).filter(item=>!item.disabled);
      const index=available.indexOf(button);
      const next=event.key==='Home'?0:event.key==='End'?available.length-1:(index+(event.key==='ArrowRight'?1:-1)+available.length)%available.length;
      available[next].click();available[next].focus();
    });
  }
  globalStop.addEventListener('click',()=>{const end=document.getElementById('remote-close') as HTMLButtonElement;if(!globalStop.disabled&&!end.disabled)end.click();});
  fullscreen.addEventListener('click', () => {
    if (!fullscreen.disabled) void changeFullscreen(fullscreenSession ? '' : session);
  });
  fit.addEventListener('click', () => {
    const fitted = screen.dataset.fit !== 'true'; screen.dataset.fit = String(fitted);
    fit.textContent = fitted ? 'Fit to window' : 'Actual size'; fit.setAttribute('aria-pressed', String(fitted));
    canvas.focus({preventScroll:true});
  });
  for (const [id,command] of [['workspace-screen-permission','request_screen_access'],['workspace-input-permission','request_input_access']] as const) {
    const button = document.getElementById(id) as HTMLButtonElement;
    button.addEventListener('click',async()=>{
      button.disabled=true;const result=document.getElementById('workspace-permission-result')!;
      try{const allowed=await window.__TAURI__.core.invoke<boolean>(command);result.textContent=allowed?'Permission is enabled.':'Complete the permission choice in macOS System Settings, then return to Mixel Remote.';}
      catch(error){result.textContent=String(error);}finally{button.disabled=false;}
    });
  }
  window.addEventListener('hashchange', route);
  route();
})();
