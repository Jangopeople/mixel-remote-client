use core_protocol::{
    crypto::{Context, ControlChannel, Handshake, Hello, Role},
    input::{self, Input},
};
use ed25519_dalek::{SigningKey, VerifyingKey};
use rand_core::OsRng;
use wasm_bindgen::{JsCast, prelude::*};
use web_sys::{
    CanvasRenderingContext2d, HtmlCanvasElement, HtmlVideoElement, RtcConfiguration,
    RtcDataChannel, RtcDataChannelInit, RtcPeerConnection,
};
fn error(message: impl std::fmt::Display) -> JsValue {
    js_sys::Error::new(&message.to_string()).into()
}
fn array32(bytes: &[u8]) -> Result<[u8; 32], JsValue> {
    bytes.try_into().map_err(|_| error("expected 32 bytes"))
}

#[wasm_bindgen]
pub struct ControlIdentity {
    signing: SigningKey,
}
#[wasm_bindgen]
impl ControlIdentity {
    #[wasm_bindgen(constructor)]
    pub fn new() -> Self {
        Self {
            signing: SigningKey::generate(&mut OsRng),
        }
    }
    /// Public enrollment material only. Secret signing keys never cross into JS.
    pub fn public_key(&self) -> Vec<u8> {
        self.signing.verifying_key().to_bytes().to_vec()
    }
    /// peer_key must come from authenticated enrollment or verified pairing.
    pub fn begin(
        &self,
        session_nonce: &[u8],
        is_controller: bool,
        peer_key: &[u8],
        local_dtls_fingerprint: &[u8],
    ) -> Result<PendingControl, JsValue> {
        let peer = VerifyingKey::from_bytes(&array32(peer_key)?).map_err(error)?;
        let role = if is_controller {
            Role::Controller
        } else {
            Role::Host
        };
        let (controller, host) = if is_controller {
            (self.signing.verifying_key(), peer)
        } else {
            (peer, self.signing.verifying_key())
        };
        let handshake = Handshake::new(
            Context {
                session_id: array32(session_nonce)?,
                controller,
                host,
            },
            role,
            &self.signing,
            array32(local_dtls_fingerprint)?,
        )
        .map_err(error)?;
        Ok(PendingControl {
            handshake: Some(handshake),
        })
    }
}
impl Default for ControlIdentity {
    fn default() -> Self {
        Self::new()
    }
}
#[wasm_bindgen]
pub struct PendingControl {
    handshake: Option<Handshake>,
}
#[wasm_bindgen]
impl PendingControl {
    pub fn hello(&self) -> Result<String, JsValue> {
        serde_json::to_string(
            &self
                .handshake
                .as_ref()
                .ok_or_else(|| error("handshake consumed"))?
                .hello(),
        )
        .map_err(error)
    }
    /// observed fingerprint comes from the browser's completed DTLS certificate,
    /// not from the Hello or an unauthenticated signaling field.
    pub fn finish(
        &mut self,
        peer_hello: &str,
        observed_dtls_fingerprint: &[u8],
    ) -> Result<EncryptedControl, JsValue> {
        if peer_hello.len() > 4096 {
            return Err(error("oversized handshake"));
        }
        let hello: Hello = serde_json::from_str(peer_hello).map_err(error)?;
        let observed = array32(observed_dtls_fingerprint)?;
        let handshake = self
            .handshake
            .take()
            .ok_or_else(|| error("handshake consumed"))?;
        Ok(EncryptedControl {
            channel: handshake.finish(&hello, observed).map_err(error)?,
        })
    }
}
#[wasm_bindgen]
pub struct EncryptedControl {
    channel: ControlChannel,
}
impl EncryptedControl {
    fn encode(&mut self, event: Input<'_>) -> Result<Vec<u8>, JsValue> {
        self.channel
            .seal(&input::encode(&event).map_err(error)?)
            .map_err(error)
    }
}
#[wasm_bindgen]
impl EncryptedControl {
    pub fn pointer(
        &mut self,
        display: u16,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
    ) -> Result<Vec<u8>, JsValue> {
        let (x, y) = crate::normalized_pointer(x, y, width, height).map_err(error)?;
        self.encode(Input::Pointer { display, x, y })
    }
    pub fn button(&mut self, button: u8, down: bool) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::Button { button, down })
    }
    pub fn key(&mut self, usage: u16, down: bool) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::Key { usage, down })
    }
    pub fn wheel(&mut self, horizontal: i16, vertical: i16) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::Wheel {
            horizontal,
            vertical,
        })
    }
    pub fn text(&mut self, text: &str) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::Text(text))
    }
    pub fn release_all(&mut self) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::ReleaseAll)
    }
    pub fn decrypt_input(&mut self, record: &[u8]) -> Result<String, JsValue> {
        let bytes = self.channel.open(record).map_err(error)?;
        serde_json::to_string(&input::decode(&bytes).map_err(error)?).map_err(error)
    }
}
/// Browser-owned WebRTC I/O, with DTLS-SRTP managed by the browser. ICE server
/// configuration is supplied by authenticated staging configuration, not defaults
/// to public third-party infrastructure.
#[wasm_bindgen]
pub fn create_peer(configuration_json: &str) -> Result<RtcPeerConnection, JsValue> {
    if configuration_json.len() > 16384 {
        return Err(error("oversized ICE configuration"));
    }
    let configuration = js_sys::JSON::parse(configuration_json)?;
    if !configuration.is_object() || configuration.is_null() {
        return Err(error("invalid ICE configuration"));
    }
    RtcPeerConnection::new_with_configuration(&configuration.unchecked_into::<RtcConfiguration>())
}
#[wasm_bindgen]
pub fn create_control_channel(peer: &RtcPeerConnection) -> RtcDataChannel {
    let init = RtcDataChannelInit::new();
    init.set_ordered(true);
    peer.create_data_channel_with_data_channel_dict("mixel-control-v1", &init)
}
#[wasm_bindgen]
pub fn draw_video(canvas: &HtmlCanvasElement, video: &HtmlVideoElement) -> Result<(), JsValue> {
    let context = canvas
        .get_context("2d")?
        .ok_or_else(|| error("canvas unavailable"))?
        .dyn_into::<CanvasRenderingContext2d>()?;
    let [x, y, width, height] = crate::video_rect(
        canvas.width().into(),
        canvas.height().into(),
        video.video_width().into(),
        video.video_height().into(),
    )
    .map_err(error)?;
    context.clear_rect(0.0, 0.0, canvas.width().into(), canvas.height().into());
    context.draw_image_with_html_video_element_and_dw_and_dh(video, x, y, width, height)
}

// ---- Browser controller: enrolled device key, tickets, login proof, session checks ----
use core_protocol::{
    delegation::{DelegationProof, DeviceTicket, session_binding},
    device_login::DeviceLoginChallenge,
};

fn uuid_bytes(text: &str) -> Result<[u8; 16], JsValue> {
    let hex: Vec<u8> = text.bytes().filter(|byte| *byte != b'-').collect();
    if hex.len() != 32 || text.len() != 36 {
        return Err(error("invalid identifier"));
    }
    let mut out = [0u8; 16];
    for (index, pair) in hex.chunks(2).enumerate() {
        let digit = |byte: u8| (byte as char).to_digit(16).ok_or_else(|| error("invalid identifier"));
        out[index] = (digit(pair[0])? * 16 + digit(pair[1])?) as u8;
    }
    Ok(out)
}
fn roots(hex_list: &str) -> Result<Vec<VerifyingKey>, JsValue> {
    let mut keys = Vec::new();
    for hex in hex_list.split(',') {
        if hex.len() != 64 {
            return Err(error("invalid authority key"));
        }
        let mut bytes = [0u8; 32];
        for (index, pair) in hex.as_bytes().chunks(2).enumerate() {
            let digit = |byte: u8| (byte as char).to_digit(16).ok_or_else(|| error("invalid authority key"));
            bytes[index] = (digit(pair[0])? * 16 + digit(pair[1])?) as u8;
        }
        keys.push(VerifyingKey::from_bytes(&bytes).map_err(error)?);
    }
    if keys.is_empty() || keys.len() > 4 {
        return Err(error("invalid authority keys"));
    }
    Ok(keys)
}
fn base64url(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let value = u32::from(chunk[0]) << 16
            | u32::from(*chunk.get(1).unwrap_or(&0)) << 8
            | u32::from(*chunk.get(2).unwrap_or(&0));
        for index in 0..=chunk.len() {
            out.push(ALPHABET[(value >> (18 - 6 * index) & 63) as usize] as char);
        }
    }
    out
}
fn seconds(value: f64) -> Result<u64, JsValue> {
    if value.is_finite() && (0.0..9.0e15).contains(&value) {
        Ok(value as u64)
    } else {
        Err(error("invalid time"))
    }
}

/// The enrolled identity of this browser: an Ed25519 key that stays in WASM memory
/// (and, as a 32-byte seed, in the browser's own storage) plus its current ticket.
#[wasm_bindgen]
pub struct Device {
    signing: SigningKey,
    ticket: Option<DeviceTicket>,
}
#[wasm_bindgen]
impl Device {
    /// A new key, or the stored one when `seed` holds 32 bytes.
    #[wasm_bindgen(constructor)]
    pub fn new(seed: Option<Vec<u8>>) -> Result<Device, JsValue> {
        let signing = match seed {
            Some(seed) => SigningKey::from_bytes(&array32(&seed)?),
            None => SigningKey::generate(&mut OsRng),
        };
        Ok(Self { signing, ticket: None })
    }
    pub fn seed(&self) -> Vec<u8> {
        self.signing.to_bytes().to_vec()
    }
    pub fn public_key(&self) -> Vec<u8> {
        self.signing.verifying_key().to_bytes().to_vec()
    }
    fn identity(
        device: &str,
        profile: &str,
        tenant: Option<String>,
        connection_id: u32,
        generation: f64,
        nonce: [u8; 32],
    ) -> Result<DeviceLoginChallenge, JsValue> {
        Ok(DeviceLoginChallenge {
            nonce,
            device: uuid_bytes(device)?,
            profile: uuid_bytes(profile)?,
            tenant: tenant.as_deref().map(uuid_bytes).transpose()?,
            connection_id,
            generation: seconds(generation)?,
        })
    }
    /// The signed request body for the identity authority (`/v1/device-ticket`).
    pub fn delegation_proof(
        &self,
        device: &str,
        profile: &str,
        tenant: Option<String>,
        connection_id: u32,
        generation: f64,
        now: f64,
    ) -> Result<String, JsValue> {
        let mut nonce = [0u8; 32];
        rand_core::RngCore::fill_bytes(&mut OsRng, &mut nonce);
        let challenge = Self::identity(device, profile, tenant, connection_id, generation, nonce)?;
        let proof = DelegationProof::new(challenge, seconds(now)?, &self.signing).map_err(error)?;
        serde_json::to_string(&proof).map_err(error)
    }
    /// Checks the ticket the authority returned for `proof` and keeps it. Returns the
    /// encoded ticket for `authenticate_device` / `renew`.
    pub fn accept_ticket(
        &mut self,
        ticket_json: &str,
        proof_json: &str,
        authority_keys: &str,
        staff: bool,
        token_expires: f64,
        now: f64,
    ) -> Result<String, JsValue> {
        if ticket_json.len() > 8192 || proof_json.len() > 8192 {
            return Err(error("oversized ticket"));
        }
        let ticket: DeviceTicket = serde_json::from_str(ticket_json).map_err(error)?;
        let proof: DelegationProof = serde_json::from_str(proof_json).map_err(error)?;
        let verified = ticket.verify(&roots(authority_keys)?, seconds(now)?).map_err(error)?;
        if verified.key() != self.signing.verifying_key()
            || ticket.identity != proof.challenge
            || ticket.unattended_host_only()
            || ticket.staff != staff
            || ticket.expires_at > seconds(token_expires)?
        {
            return Err(error("the signed device identity does not match this account"));
        }
        let bytes = serde_json::to_vec(&ticket).map_err(error)?;
        if bytes.len() > 4096 {
            return Err(error("oversized ticket"));
        }
        self.ticket = Some(ticket);
        Ok(base64url(&bytes))
    }
    pub fn ticket_expires(&self) -> f64 {
        self.ticket.as_ref().map_or(0.0, |ticket| ticket.expires_at as f64)
    }
    /// Signs the broker's login challenge after checking it names this enrollment.
    pub fn prove_login(&self, challenge_json: &str) -> Result<Vec<u8>, JsValue> {
        let challenge: DeviceLoginChallenge = serde_json::from_str(challenge_json).map_err(error)?;
        let own = &self.ticket.as_ref().ok_or_else(|| error("no ticket"))?.identity;
        if challenge.device != own.device
            || challenge.profile != own.profile
            || challenge.tenant != own.tenant
            || challenge.connection_id != own.connection_id
            || challenge.generation != own.generation
        {
            return Err(error("the login challenge is for another device"));
        }
        challenge.sign(&self.signing).map_err(error)
    }
    /// Checks an `accepted` (or `restarted`) message and binds the session to both
    /// verified identities. `host_key` is the host's key read from the account
    /// service before the request, never from signaling.
    #[allow(clippy::too_many_arguments)]
    pub fn session(
        &self,
        session: &str,
        nonce: &[u8],
        controller_ticket_json: &str,
        host_ticket_json: &str,
        host_key: &[u8],
        host_id: u32,
        unattended: bool,
        authority_keys: &str,
        now: f64,
    ) -> Result<SessionContext, JsValue> {
        let own = self.ticket.as_ref().ok_or_else(|| error("no ticket"))?;
        let nonce = array32(nonce)?;
        if nonce == [0; 32] {
            return Err(error("invalid session"));
        }
        let roots = roots(authority_keys)?;
        let now = seconds(now)?;
        let controller: DeviceTicket = serde_json::from_str(controller_ticket_json).map_err(error)?;
        let host: DeviceTicket = serde_json::from_str(host_ticket_json).map_err(error)?;
        controller.verify(&roots, now).map_err(error)?;
        let same = controller.identity.device == own.identity.device
            && controller.version == own.version
            && controller.identity.profile == own.identity.profile
            && controller.identity.tenant == own.identity.tenant
            && controller.identity.connection_id == own.identity.connection_id
            && controller.identity.generation == own.identity.generation
            && controller.public_key == own.public_key
            && controller.staff == own.staff;
        if !same {
            return Err(error("the session names another controller"));
        }
        let verified = host.verify(&roots, now).map_err(error)?;
        let same_tenant = own.identity.tenant.is_some() && own.identity.tenant == host.identity.tenant;
        if host.identity.connection_id != host_id
            || (!own.staff && !same_tenant)
            || verified.key().to_bytes() != array32(host_key)?
        {
            return Err(error("the remote computer's identity could not be confirmed"));
        }
        Ok(SessionContext {
            binding: session_binding(uuid_bytes(session)?, nonce, &controller, &host),
            controller: self.signing.verifying_key(),
            host: verified.key(),
            host_expires: host.expires_at,
        })
    }
    /// Starts the key agreement for a checked session.
    pub fn begin(&self, context: &SessionContext, local_dtls_fingerprint: &[u8]) -> Result<PendingControl, JsValue> {
        let handshake = Handshake::new(
            Context { session_id: context.binding, controller: context.controller, host: context.host },
            Role::Controller,
            &self.signing,
            array32(local_dtls_fingerprint)?,
        )
        .map_err(error)?;
        Ok(PendingControl { handshake: Some(handshake) })
    }
}
#[wasm_bindgen]
pub struct SessionContext {
    binding: [u8; 32],
    controller: VerifyingKey,
    host: VerifyingKey,
    host_expires: u64,
}
#[wasm_bindgen]
impl SessionContext {
    pub fn host_expires(&self) -> f64 {
        self.host_expires as f64
    }
}
#[wasm_bindgen]
impl EncryptedControl {
    /// Seals any control record (keyframe request, display choice, action …).
    pub fn seal(&mut self, plaintext: &[u8]) -> Result<Vec<u8>, JsValue> {
        self.channel.seal(plaintext).map_err(error)
    }
    /// Opens the next record from the host.
    pub fn open(&mut self, record: &[u8]) -> Result<Vec<u8>, JsValue> {
        self.channel.open(record).map_err(error)
    }
    pub fn click(&mut self, button: u8, down: bool, clicks: u8) -> Result<Vec<u8>, JsValue> {
        self.encode(Input::ButtonClick { button, down, clicks })
    }
}
