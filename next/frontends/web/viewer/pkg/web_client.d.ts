/* tslint:disable */
/* eslint-disable */

export class ControlIdentity {
    free(): void;
    [Symbol.dispose](): void;
    /**
     * peer_key must come from authenticated enrollment or verified pairing.
     */
    begin(session_nonce: Uint8Array | number[], is_controller: boolean, peer_key: Uint8Array | number[], local_dtls_fingerprint: Uint8Array | number[]): PendingControl;
    constructor();
    /**
     * Public enrollment material only. Secret signing keys never cross into JS.
     */
    public_key(): Uint8Array;
}

/**
 * The enrolled identity of this browser: an Ed25519 key that stays in WASM memory
 * (and, as a 32-byte seed, in the browser's own storage) plus its current ticket.
 */
export class Device {
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Checks the ticket the authority returned for `proof` and keeps it. Returns the
     * encoded ticket for `authenticate_device` / `renew`.
     */
    accept_ticket(ticket_json: string, proof_json: string, authority_keys: string, staff: boolean, token_expires: number, now: number): string;
    /**
     * Starts the key agreement for a checked session.
     */
    begin(context: SessionContext, local_dtls_fingerprint: Uint8Array | number[]): PendingControl;
    /**
     * The signed request body for the identity authority (`/v1/device-ticket`).
     */
    delegation_proof(device: string, profile: string, tenant: string | null | undefined, connection_id: number, generation: number, now: number): string;
    /**
     * A new key, or the stored one when `seed` holds 32 bytes.
     */
    constructor(seed?: Uint8Array | number[] | null);
    /**
     * Signs the broker's login challenge after checking it names this enrollment.
     */
    prove_login(challenge_json: string): Uint8Array;
    public_key(): Uint8Array;
    seed(): Uint8Array;
    /**
     * Checks an `accepted` (or `restarted`) message and binds the session to both
     * verified identities. `host_key` is the host's key read from the account
     * service before the request, never from signaling.
     */
    session(session: string, nonce: Uint8Array | number[], controller_ticket_json: string, host_ticket_json: string, host_key: Uint8Array | number[], host_id: number, unattended: boolean, authority_keys: string, now: number): SessionContext;
    ticket_expires(): number;
}

export class EncryptedControl {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    button(button: number, down: boolean): Uint8Array;
    click(button: number, down: boolean, clicks: number): Uint8Array;
    decrypt_input(record: Uint8Array | number[]): string;
    key(usage: number, down: boolean): Uint8Array;
    /**
     * Opens the next record from the host.
     */
    open(record: Uint8Array | number[]): Uint8Array;
    pointer(display: number, x: number, y: number, width: number, height: number): Uint8Array;
    release_all(): Uint8Array;
    /**
     * Seals any control record (keyframe request, display choice, action …).
     */
    seal(plaintext: Uint8Array | number[]): Uint8Array;
    text(text: string): Uint8Array;
    wheel(horizontal: number, vertical: number): Uint8Array;
}

export class PendingControl {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    /**
     * observed fingerprint comes from the browser's completed DTLS certificate,
     * not from the Hello or an unauthenticated signaling field.
     */
    finish(peer_hello: string, observed_dtls_fingerprint: Uint8Array | number[]): EncryptedControl;
    hello(): string;
}

export class SessionContext {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    host_expires(): number;
}

export function create_control_channel(peer: RTCPeerConnection): RTCDataChannel;

/**
 * Browser-owned WebRTC I/O, with DTLS-SRTP managed by the browser. ICE server
 * configuration is supplied by authenticated staging configuration, not defaults
 * to public third-party infrastructure.
 */
export function create_peer(configuration_json: string): RTCPeerConnection;

export function draw_video(canvas: HTMLCanvasElement, video: HTMLVideoElement): void;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_controlidentity_free: (a: number, b: number) => void;
    readonly __wbg_device_free: (a: number, b: number) => void;
    readonly __wbg_encryptedcontrol_free: (a: number, b: number) => void;
    readonly __wbg_pendingcontrol_free: (a: number, b: number) => void;
    readonly __wbg_sessioncontext_free: (a: number, b: number) => void;
    readonly controlidentity_begin: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number) => [number, number, number];
    readonly controlidentity_new: () => number;
    readonly controlidentity_public_key: (a: number) => [number, number];
    readonly create_control_channel: (a: any) => any;
    readonly create_peer: (a: number, b: number) => [number, number, number];
    readonly device_accept_ticket: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number, j: number) => [number, number, number, number];
    readonly device_begin: (a: number, b: number, c: number, d: number) => [number, number, number];
    readonly device_delegation_proof: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number, j: number) => [number, number, number, number];
    readonly device_new: (a: number, b: number) => [number, number, number];
    readonly device_prove_login: (a: number, b: number, c: number) => [number, number, number, number];
    readonly device_public_key: (a: number) => [number, number];
    readonly device_seed: (a: number) => [number, number];
    readonly device_session: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number, j: number, k: number, l: number, m: number, n: number, o: number, p: number) => [number, number, number];
    readonly device_ticket_expires: (a: number) => number;
    readonly draw_video: (a: any, b: any) => [number, number];
    readonly encryptedcontrol_button: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_click: (a: number, b: number, c: number, d: number) => [number, number, number, number];
    readonly encryptedcontrol_decrypt_input: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_key: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_open: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_pointer: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number, number];
    readonly encryptedcontrol_release_all: (a: number) => [number, number, number, number];
    readonly encryptedcontrol_seal: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_text: (a: number, b: number, c: number) => [number, number, number, number];
    readonly encryptedcontrol_wheel: (a: number, b: number, c: number) => [number, number, number, number];
    readonly pendingcontrol_finish: (a: number, b: number, c: number, d: number, e: number) => [number, number, number];
    readonly pendingcontrol_hello: (a: number) => [number, number, number, number];
    readonly sessioncontext_host_expires: (a: number) => number;
    readonly __wbindgen_exn_store: (a: number) => void;
    readonly __externref_table_alloc: () => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
