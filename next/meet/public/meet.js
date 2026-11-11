// Mixel Meet: small meetings in the browser. Every pair of participants is connected directly
// (WebRTC, encrypted between the two); the meeting service only introduces them.
import { t, applyTexts } from './texts.js';

const $ = id => document.getElementById(id);
const query = new URLSearchParams(location.search);
const FAKE = query.get('fake'); // test hook: generated picture and tone instead of camera and microphone
let [roomId, hostKey] = location.hash.slice(1).split('.');
let socket = null, me = null, role = 'guest', iceConfig = { iceServers: [] };
let micTrack = null, camTrack = null, screenTrack = null, screenStream = null;
const localStream = new MediaStream();
const peers = new Map();
let wantMic = false, wantCam = false, sidePanel = null, unread = 0, joined = false;

function status(text) { $('status').textContent = text; }
function toast(text) { const box = $('toast'); box.textContent = text; box.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { box.hidden = true; }, 4000); }
function send(message) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(message)); }

// ---------- camera, microphone, screen ----------
function fakeMedia(name) {
  const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 360;
  const context = canvas.getContext('2d'); let frame = 0;
  const hue = [...name].reduce((sum, letter) => sum + letter.charCodeAt(0), 0) % 360;
  setInterval(() => {
    context.fillStyle = `hsl(${hue} 60% 35%)`; context.fillRect(0, 0, 640, 360);
    context.fillStyle = '#fff'; context.font = '40px sans-serif'; context.fillText(name, 30, 80); context.fillText(String(frame++), 30, 150);
    context.fillRect((frame * 8) % 640, 250, 60, 60);
  }, 100);
  const audio = new (window.AudioContext || window.webkitAudioContext)();
  const tone = audio.createOscillator(), gain = audio.createGain(), out = audio.createMediaStreamDestination();
  tone.frequency.value = 220 + hue; gain.gain.value = 0.02; tone.connect(gain).connect(out); tone.start();
  return { video: () => canvas.captureStream(10).getVideoTracks()[0], audio: () => out.stream.getAudioTracks()[0] };
}
let fake = null;
async function getTrack(kind) {
  if (FAKE) { fake = fake || fakeMedia(FAKE); return kind === 'audio' ? fake.audio() : fake.video(); }
  const stream = await navigator.mediaDevices.getUserMedia(kind === 'audio' ? { audio: { echoCancellation: true, noiseSuppression: true } } : { video: { width: { ideal: 1280 }, height: { ideal: 720 } } });
  return stream.getTracks()[0];
}
async function setMic(on) {
  if (on && !micTrack) {
    try { micTrack = await getTrack('audio'); localStream.addTrack(micTrack); }
    catch { toast(t('The microphone is not available. Check the browser permission.')); on = false; }
  }
  if (micTrack) micTrack.enabled = on;
  wantMic = on && !!micTrack;
  for (const peer of peers.values()) if (peer.audioSender) peer.audioSender.replaceTrack(micTrack).catch(() => {});
  render(); announce();
}
async function setCam(on) {
  if (on && !camTrack) {
    try { camTrack = await getTrack('video'); localStream.addTrack(camTrack); }
    catch { toast(t('The camera is not available. Check the browser permission.')); on = false; }
  }
  if (!on && camTrack) { localStream.removeTrack(camTrack); if (!FAKE) camTrack.stop(); camTrack = null; }
  wantCam = on && !!camTrack;
  for (const peer of peers.values()) if (peer.videoSender) peer.videoSender.replaceTrack(camTrack).catch(() => {});
  render(); announce();
}
async function setScreen(on) {
  if (on && !screenTrack) {
    try {
      screenStream = FAKE ? new MediaStream([fakeMedia(`${FAKE} screen`).video()]) : await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 15 } }, audio: false });
      screenTrack = screenStream.getVideoTracks()[0];
      screenTrack.addEventListener('ended', () => setScreen(false));
      for (const peer of peers.values()) peer.screenSender = peer.pc.addTrack(screenTrack, screenStream);
    } catch { screenTrack = null; screenStream = null; }
  } else if (!on && screenTrack) {
    for (const peer of peers.values()) if (peer.screenSender) { try { peer.pc.removeTrack(peer.screenSender); } catch { /* closed */ } peer.screenSender = null; }
    screenTrack.stop(); screenTrack = null; screenStream = null;
  }
  render(); announce();
}
function announce() { send({ type: 'state', mic: wantMic, cam: wantCam, screen: !!screenTrack }); }

// ---------- one connection per other participant ----------
function connect(info) {
  const pc = new RTCPeerConnection({ iceServers: iceConfig.iceServers, iceTransportPolicy: iceConfig.relayOnly ? 'relay' : 'all' });
  const peer = { id: info.id, name: info.name, role: info.role, state: info.state || {}, pc, polite: me > info.id, makingOffer: false, ignoreOffer: false, media: new MediaStream(), screen: new MediaStream(), audioSender: null, videoSender: null, screenSender: null };
  peers.set(peer.id, peer);
  pc.onicecandidate = event => { if (event.candidate) send({ type: 'signal', to: peer.id, data: { candidate: event.candidate } }); };
  pc.onnegotiationneeded = async () => {
    try { peer.makingOffer = true; await pc.setLocalDescription(); send({ type: 'signal', to: peer.id, data: { description: pc.localDescription } }); }
    catch { /* retried by the next negotiation */ } finally { peer.makingOffer = false; }
  };
  pc.ontrack = event => {
    // The first audio and video line carry microphone and camera; any further video line is a shared screen.
    const index = pc.getTransceivers().indexOf(event.transceiver);
    const target = index <= 1 ? peer.media : peer.screen;
    for (const old of target.getTracks()) if (old.kind === event.track.kind) target.removeTrack(old);
    target.addTrack(event.track);
    render();
  };
  pc.onconnectionstatechange = () => { if (pc.connectionState === 'failed') pc.restartIce(); };
  if (!peer.polite) {
    // The side with the smaller id opens the two standard lines; the other side answers on them.
    peer.audioSender = pc.addTransceiver('audio', { direction: 'sendrecv' }).sender;
    peer.videoSender = pc.addTransceiver('video', { direction: 'sendrecv' }).sender;
    peer.audioSender.replaceTrack(micTrack).catch(() => {}); peer.videoSender.replaceTrack(camTrack).catch(() => {});
    if (screenTrack) peer.screenSender = pc.addTrack(screenTrack, screenStream);
  }
  return peer;
}
async function onSignal(from, data) {
  const peer = peers.get(from); if (!peer) return;
  const pc = peer.pc;
  try {
    if (data.description) {
      const collision = data.description.type === 'offer' && (peer.makingOffer || pc.signalingState !== 'stable');
      peer.ignoreOffer = !peer.polite && collision;
      if (peer.ignoreOffer) return;
      await pc.setRemoteDescription(data.description);
      if (data.description.type === 'offer') {
        if (!peer.audioSender) {
          const lines = pc.getTransceivers();
          if (lines[0] && lines[1]) {
            lines[0].direction = 'sendrecv'; lines[1].direction = 'sendrecv';
            peer.audioSender = lines[0].sender; peer.videoSender = lines[1].sender;
            await peer.audioSender.replaceTrack(micTrack); await peer.videoSender.replaceTrack(camTrack);
            if (screenTrack && !peer.screenSender) peer.screenSender = pc.addTrack(screenTrack, screenStream);
          }
        }
        await pc.setLocalDescription();
        send({ type: 'signal', to: peer.id, data: { description: pc.localDescription } });
      }
    } else if (data.candidate) {
      try { await pc.addIceCandidate(data.candidate); } catch (problem) { if (!peer.ignoreOffer) throw problem; }
    }
  } catch (problem) { console.warn('signal', problem); }
}
function drop(id) { const peer = peers.get(id); if (!peer) return; try { peer.pc.close(); } catch { /* closed */ } peers.delete(id); render(); }

// ---------- screen ----------
function tile(id, name, stream, options) {
  let box = document.querySelector(`[data-tile="${id}"]`);
  if (!box) {
    box = document.createElement('div'); box.className = 'tile'; box.dataset.tile = id;
    const video = document.createElement('video'); video.autoplay = true; video.playsInline = true;
    const avatar = document.createElement('div'); avatar.className = 'avatar';
    const label = document.createElement('div'); label.className = 'label';
    box.append(avatar, video, label); $('tiles').appendChild(box);
  }
  const [avatar, video, label] = box.children;
  if (video.srcObject !== stream) video.srcObject = stream;
  video.muted = !!options.self;
  box.classList.toggle('off', !options.cam);
  avatar.textContent = (name || '?').trim().charAt(0).toUpperCase();
  label.textContent = options.self ? `${name} (${t('you')})` : name;
  label.classList.toggle('muted', !options.mic);
  if (!options.self) video.play().catch(() => {});
  return box;
}
function render() {
  if (!joined) return;
  const wanted = new Set(['self', ...peers.keys()]);
  for (const box of [...$('tiles').children]) if (!wanted.has(box.dataset.tile)) box.remove();
  tile('self', $('name').value.trim(), localStream, { self: true, cam: wantCam, mic: wantMic });
  let shared = screenTrack ? { name: $('name').value.trim(), stream: screenStream } : null;
  for (const peer of peers.values()) {
    tile(peer.id, peer.name, peer.media, { cam: !!peer.state.cam, mic: !!peer.state.mic });
    if (!shared && peer.state.screen && peer.screen.getVideoTracks().length) shared = { name: peer.name, stream: peer.screen };
  }
  $('meeting').classList.toggle('sharing', !!shared);
  $('stage').hidden = !shared;
  if (shared) { if ($('stage-video').srcObject !== shared.stream) $('stage-video').srcObject = shared.stream; $('stage-name').textContent = `${shared.name} · ${t('shared screen')}`; $('stage-video').play().catch(() => {}); }
  else $('stage-video').srcObject = null;
  $('c-mic').setAttribute('aria-pressed', String(wantMic)); $('c-cam').setAttribute('aria-pressed', String(wantCam)); $('c-screen').setAttribute('aria-pressed', String(!!screenTrack));
  $('pre-mic').setAttribute('aria-pressed', String(wantMic)); $('pre-cam').setAttribute('aria-pressed', String(wantCam));
  $('people-count').textContent = `(${peers.size + 1})`;
  renderPeople();
}
const knocks = new Map();
function renderPeople() {
  const list = $('people-list'); list.replaceChildren();
  const rows = [{ id: 'self', name: `${$('name').value.trim()} (${t('you')})`, role }, ...[...peers.values()].map(peer => ({ id: peer.id, name: peer.name, role: peer.role }))];
  for (const row of rows) {
    const line = document.createElement('div'); line.className = 'person';
    const name = document.createElement('span'); name.textContent = row.role === 'host' ? `${row.name} · ${t('host')}` : row.name; line.appendChild(name);
    if (role === 'host' && row.id !== 'self' && row.role !== 'host') { const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = t('Remove'); remove.onclick = () => send({ type: 'remove', id: row.id }); line.appendChild(remove); }
    list.appendChild(line);
  }
  const box = $('knocks'); box.replaceChildren();
  for (const [id, name] of knocks) {
    const card = document.createElement('div'); card.className = 'knock';
    const text = document.createElement('span'); text.textContent = `${name} ${t('wants to join')}`;
    const buttons = document.createElement('div');
    const admit = document.createElement('button'); admit.type = 'button'; admit.className = 'primary'; admit.textContent = t('Admit'); admit.onclick = () => send({ type: 'admit', id });
    const deny = document.createElement('button'); deny.type = 'button'; deny.textContent = t('Decline'); deny.onclick = () => send({ type: 'deny', id });
    buttons.append(admit, deny); card.append(text, buttons); box.appendChild(card);
  }
  $('lock-row').hidden = role !== 'host';
}
function showSide(panel) {
  sidePanel = sidePanel === panel ? null : panel;
  $('side').hidden = !sidePanel; $('meeting').classList.toggle('with-side', !!sidePanel); $('people').hidden = sidePanel !== 'people'; $('chat').hidden = sidePanel !== 'chat';
  $('side-title').textContent = sidePanel === 'chat' ? t('Chat') : t('People');
  if (sidePanel === 'chat') { unread = 0; $('c-chat').textContent = t('Chat'); $('chat-text').focus(); }
}
function chatLine(name, text) {
  const line = document.createElement('p'); const who = document.createElement('strong'); who.textContent = name;
  line.append(who, document.createTextNode(text)); $('chat-log').appendChild(line); $('chat-log').scrollTop = $('chat-log').scrollHeight;
  if (sidePanel !== 'chat') { unread++; $('c-chat').textContent = `${t('Chat')} (${unread})`; }
}

// ---------- joining ----------
function show(card) { for (const id of ['code-card', 'join-card', 'wait-card']) $(id).hidden = id !== card; }
function leave(message) {
  joined = false;
  for (const id of [...peers.keys()]) drop(id);
  for (const track of [micTrack, camTrack, screenTrack]) if (track && !FAKE) track.stop();
  micTrack = camTrack = screenTrack = null;
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  $('meeting').hidden = true; $('start').hidden = false; show(roomId ? 'join-card' : 'code-card'); status(message || '');
}
function join() {
  const name = $('name').value.trim();
  if (!name) { status(t('Please enter your name.')); return; }
  try { localStorage.setItem('mixel.meet.name', name); } catch { /* not kept */ }
  status(t('Connecting…'));
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/meet/ws`);
  socket.onopen = () => send({ type: 'join', room: roomId, name, host_key: hostKey || undefined });
  socket.onclose = () => { if (joined || !$('wait-card').hidden) leave(t('The connection was lost. Join again to continue.')); };
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    switch (message.type) {
      case 'waiting': show('wait-card'); status(''); $('wait-text').textContent = message.host ? t('The host has been asked to let you in.') : t('Waiting for the host to arrive…'); break;
      case 'denied': leave({ unknown: t('This meeting does not exist or has ended.'), locked: t('This meeting is locked.'), full: t('This meeting is full.'), declined: t('The host did not let you in.'), name: t('Please enter your name.') }[message.reason] || t('You cannot join this meeting.')); break;
      case 'welcome':
        me = message.id; role = message.role; iceConfig = { iceServers: message.ice, relayOnly: message.relayOnly }; joined = true;
        $('start').hidden = true; $('meeting').hidden = false; status('');
        $('c-end').hidden = role !== 'host'; $('lock').checked = !!message.locked;
        for (const waiting of message.waiting || []) knocks.set(waiting.id, waiting.name);
        for (const info of message.peers) connect(info);
        document.title = message.title ? `${message.title} · Mixel Meet` : 'Mixel Meet';
        render(); announce();
        break;
      case 'peer-joined': connect(message); toast(`${message.name} ${t('joined')}`); render(); break;
      case 'peer-left': { const peer = peers.get(message.id); if (peer) toast(`${peer.name} ${t('left')}`); drop(message.id); break; }
      case 'signal': onSignal(message.from, message.data); break;
      case 'state': { const peer = peers.get(message.id); if (peer) { peer.state = message.state; render(); } break; }
      case 'chat': chatLine(message.id === me ? `${message.name} (${t('you')})` : message.name, message.text); break;
      case 'knock': knocks.set(message.id, message.name); toast(`${message.name} ${t('wants to join')}`); if (sidePanel !== 'people') showSide('people'); renderPeople(); break;
      case 'knock-done': knocks.delete(message.id); renderPeople(); break;
      case 'locked': $('lock').checked = message.on; break;
      case 'ice': iceConfig.iceServers = message.ice; break;
      case 'ended': leave(message.reason === 'removed' ? t('The host removed you from the meeting.') : t('The meeting has ended.')); break;
      default: break;
    }
  };
}

applyTexts();
try { $('name').value = localStorage.getItem('mixel.meet.name') || ''; } catch { /* no storage */ }
if (FAKE) $('name').value = FAKE;
$('preview').srcObject = localStream;
if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) $('c-screen').hidden = !FAKE;
show(roomId ? 'join-card' : 'code-card');
$('start-title').textContent = roomId ? `${t('Meeting')} ${roomId}` : t('Join a meeting');
$('code-go').onclick = () => { const code = $('code').value.trim().toLowerCase().split('#').pop(); if (code) { location.hash = code; location.reload(); } };
$('pre-mic').onclick = () => setMic(!wantMic); $('pre-cam').onclick = () => setCam(!wantCam);
$('c-mic').onclick = () => setMic(!wantMic); $('c-cam').onclick = () => setCam(!wantCam); $('c-screen').onclick = () => setScreen(!screenTrack);
$('join').onclick = join; $('wait-leave').onclick = () => leave('');
$('c-leave').onclick = () => leave(t('You left the meeting.'));
$('c-end').onclick = () => send({ type: 'end' });
$('c-chat').onclick = () => showSide('chat'); $('c-people').onclick = () => showSide('people'); $('side-close').onclick = () => showSide(sidePanel);
$('c-invite').onclick = () => { const link = `${location.origin}${location.pathname}#${roomId}`; (navigator.clipboard ? navigator.clipboard.writeText(link) : Promise.reject()).then(() => toast(t('Invitation link copied.')), () => toast(link)); };
$('lock').onchange = () => send({ type: 'lock', on: $('lock').checked });
$('chat-form').onsubmit = event => { event.preventDefault(); const text = $('chat-text').value.trim(); if (text) { send({ type: 'chat', text }); $('chat-text').value = ''; } };
window.addEventListener('pagehide', () => { if (socket) socket.close(); });
setInterval(() => { if (joined) send({ type: 'ice' }); }, 3 * 3600 * 1000);
if (FAKE && query.get('auto')) { setMic(true).then(() => setCam(true)).then(join); }
// For tests and support: a summary of what this participant sends and receives.
window.__mixelMeet = async () => {
  const summary = { me, role, peers: [] };
  for (const peer of peers.values()) {
    let audioIn = 0, videoIn = 0;
    (await peer.pc.getStats()).forEach(report => { if (report.type === 'inbound-rtp') { if (report.kind === 'audio') audioIn += report.packetsReceived || 0; else videoIn += report.framesDecoded || 0; } });
    summary.peers.push({ name: peer.name, connection: peer.pc.connectionState, audioIn, videoIn, state: peer.state, screenTracks: peer.screen.getVideoTracks().length });
  }
  return summary;
};
