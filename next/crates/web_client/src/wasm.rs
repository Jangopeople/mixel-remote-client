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
