import init, {ControlIdentity, create_peer, create_control_channel, draw_video} from './pkg/web_client.js';
const button = document.querySelector('#run'), status = document.querySelector('#status'), checks = document.querySelector('#checks');
const source = document.querySelector('#source'), canvas = document.querySelector('#received'), video = document.querySelector('#video');
const log = (text) => { const li = document.createElement('li'); li.textContent = `PASS · ${text}`; li.className = 'pass'; checks.append(li); };
const assert = (condition, text) => { if (!condition) throw Error(text); };
function bounded(promise, text, ms = 15000) {
  let timer; return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(text)), ms); })]).finally(() => clearTimeout(timer));
}
function gather(peer) {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();
  return bounded(new Promise(resolve => { const listener = () => { if (peer.iceGatheringState === 'complete') { peer.removeEventListener('icegatheringstatechange', listener); resolve(); } }; peer.addEventListener('icegatheringstatechange', listener); }), 'ICE gathering timeout');
}
function opened(channel) {
  if (channel.readyState === 'open') return Promise.resolve();
  return bounded(new Promise((resolve, reject) => { channel.addEventListener('open', resolve, {once:true}); channel.addEventListener('error', reject, {once:true}); }), 'DataChannel open timeout');
}
function message(channel) {
  return bounded(new Promise(resolve => channel.addEventListener('message', event => resolve(new Uint8Array(event.data)), {once:true})), 'DataChannel message timeout');
}
function fingerprint(sdp) {
  const lines = sdp.split(/\r?\n/).filter(line => line.startsWith('a=fingerprint:'));
  assert(lines.length > 0 && lines.every(line => line === lines[0]), 'conflicting/missing local fingerprints');
  const bytes = lines[0].replace('a=fingerprint:sha-256 ', '').split(':').map(text => parseInt(text, 16));
  assert(bytes.length === 32 && bytes.every(Number.isFinite), 'invalid SHA256 fingerprint'); return Uint8Array.from(bytes);
}
async function observed(peer) {
  const certificates = peer.sctp.transport.getRemoteCertificates();
  assert(certificates.length > 0, 'actual DTLS certificate missing');
  return new Uint8Array(await crypto.subtle.digest('SHA-256', certificates[0]));
}
function same(a, b) { return a.length === b.length && a.every((value, i) => value === b[i]); }
try { await init(); button.disabled = false; status.textContent = 'Ready for local test'; }
catch (error) { status.textContent = `FAIL: ${error.message}`; status.className = 'fail'; }
button.addEventListener('click', async () => {
  button.disabled = true; checks.replaceChildren(); status.className = ''; status.textContent = 'Testing real browser peers…';
  let a, b, stream, drawing; const owned = [];
  try {
    a = create_peer('{"iceServers":[]}'); b = create_peer('{"iceServers":[]}');
    const context = source.getContext('2d'); let counter = 0;
    function frame() { context.fillStyle = '#10d060'; context.fillRect(0, 0, 320, 360); context.fillStyle = '#2050df'; context.fillRect(320, 0, 320, 360); context.fillStyle = 'white'; context.font = '24px system-ui'; context.fillText(`Mixel WebRTC frame ${++counter}`, 45, 190); }
    frame(); drawing = setInterval(frame, 100); stream = source.captureStream(10);
    a.addTrack(stream.getVideoTracks()[0], stream);
    const track = bounded(new Promise(resolve => b.addEventListener('track', event => { video.srcObject = event.streams[0]; resolve(); }, {once:true})), 'video track timeout');
    const outbound = create_control_channel(a); outbound.binaryType = 'arraybuffer';
    const remote = bounded(new Promise(resolve => b.addEventListener('datachannel', event => { event.channel.binaryType = 'arraybuffer'; resolve(event.channel); }, {once:true})), 'incoming DataChannel timeout');
    await a.setLocalDescription(await a.createOffer()); await gather(a);
    await b.setRemoteDescription(a.localDescription); await b.setLocalDescription(await b.createAnswer()); await gather(b); await a.setRemoteDescription(b.localDescription);
    const inbound = await remote; await Promise.all([opened(outbound), opened(inbound), track]);
    log('ICE + DTLS + SCTP opened between real browser peers');
    const af = fingerprint(a.localDescription.sdp), bf = fingerprint(b.localDescription.sdp);
    const observedA = await observed(a), observedB = await observed(b);
    assert(same(observedA, bf) && same(observedB, af), 'actual certificate fingerprint mismatch');
    log('Actual remote DTLS certificates match both local fingerprints');
    const ai = new ControlIdentity(), bi = new ControlIdentity(); owned.push(ai, bi);
    const nonce = crypto.getRandomValues(new Uint8Array(32));
    const ah = ai.begin(nonce, true, bi.public_key(), af), bh = bi.begin(nonce, false, ai.public_key(), bf); owned.push(ah, bh);
    const helloA = ah.hello(), helloB = bh.hello();
    const ac = ah.finish(helloB, observedA), bc = bh.finish(helloA, observedB); owned.push(ac, bc);
    const incoming = message(inbound), record = ac.text('Browser WASM Grüezi 中文 😀'); outbound.send(record);
    const wire = await incoming, plain = JSON.parse(bc.decrypt_input(wire)); assert(plain.Text === 'Browser WASM Grüezi 中文 😀', 'Unicode input mismatch');
    const reply = message(outbound); inbound.send(bc.release_all()); assert(JSON.parse(ac.decrypt_input(await reply)) === 'ReleaseAll', 'reverse input mismatch');
    let replayRejected = false; try { bc.decrypt_input(wire); } catch { replayRejected = true; } assert(replayRejected, 'replay accepted');
    log('Signed X25519 + AES-256-GCM Unicode controls round-trip; replay rejected');
    await video.play();
    await bounded(new Promise(resolve => video.requestVideoFrameCallback(resolve)), 'decoded video frame timeout');
    draw_video(canvas, video);
    const pixel = canvas.getContext('2d').getImageData(50, 50, 1, 1).data;
    assert(pixel[1] > 140 && pixel[1] > pixel[0] * 2, `decoded test-pattern pixel mismatch: ${pixel}`);
    log(`Video decoded and rendered by Rust canvas binding (${video.videoWidth} × ${video.videoHeight})`);
    status.textContent = 'PASS · browser transport verified'; status.className = 'pass';
  } catch (error) { status.textContent = `FAIL · ${error.message || error}`; status.className = 'fail'; console.error(error); }
  finally { clearInterval(drawing); stream?.getTracks().forEach(track => track.stop()); a?.close(); b?.close(); owned.reverse().forEach(value => value.free()); button.disabled = false; }
});
