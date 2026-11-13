//! "Mixel Remote Printer" on macOS and Linux. The print system (CUPS) has a printer whose
//! address is this app on the local computer (`ipp://127.0.0.1:61631/ipp/print`): when someone
//! prints on it, CUPS sends the PDF here by IPP, and a running session forwards it to the
//! technician, as the Windows printer does with its PDF file. Nothing listens beyond this
//! computer, nothing is kept outside a session, and only PDF documents are taken.
//!
//! The IPP subset is what the CUPS `ipp` backend uses: Get-Printer-Attributes, Validate-Job,
//! Print-Job, Get-Job-Attributes, Get-Jobs and Cancel-Job (RFC 8011).
use std::{
    collections::VecDeque,
    sync::{Mutex, OnceLock},
    time::Duration,
};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

pub const PORT: u16 = 61631;
pub const PATH: &str = "/ipp/print";
/// The CUPS queue name (no spaces) and the name people see.
pub const QUEUE: &str = "Mixel_Remote_Printer";
pub const DESCRIPTION: &str = "Mixel Remote Printer";
const MAX_JOB: usize = core_protocol::clipboard::MAX_RICH - 4096;
const MAX_HEADER: usize = 16 * 1024;

/// Received print jobs, kept only while a session takes them.
static JOBS: Mutex<VecDeque<Vec<u8>>> = Mutex::new(VecDeque::new());
static STARTED: OnceLock<bool> = OnceLock::new();

/// Starts the local printer endpoint once per app (on its own thread); `false` when the port is taken.
pub fn ensure_running() -> bool {
    *STARTED.get_or_init(|| {
        let Ok(listener) = std::net::TcpListener::bind(("127.0.0.1", PORT)) else {
            return false;
        };
        if listener.set_nonblocking(true).is_err() {
            return false;
        }
        std::thread::Builder::new()
            .name("mixel-remote-printer".into())
            .spawn(move || {
                let Ok(runtime) = tokio::runtime::Builder::new_current_thread().enable_all().build() else { return };
                runtime.block_on(async move {
                    let Ok(listener) = tokio::net::TcpListener::from_std(listener) else { return };
                    serve(listener).await;
                });
            })
            .is_ok()
    })
}

async fn serve(listener: tokio::net::TcpListener) {
    let slots = std::sync::Arc::new(tokio::sync::Semaphore::new(4));
    loop {
        let Ok((stream, peer)) = listener.accept().await else { continue };
        if !peer.ip().is_loopback() {
            continue;
        }
        let Ok(slot) = slots.clone().try_acquire_owned() else { continue };
        tokio::spawn(async move {
            let _slot = slot;
            let _ = tokio::time::timeout(Duration::from_secs(120), connection(stream, accept_job)).await;
        });
    }
}

fn accept_job(job: Vec<u8>) {
    let mut jobs = JOBS.lock().unwrap_or_else(|error| error.into_inner());
    // Only the latest few wait: a session takes them within a second.
    while jobs.len() >= 4 {
        jobs.pop_front();
    }
    jobs.push_back(job);
}

/// A session starts: jobs printed before it are not sent.
pub fn clear() {
    JOBS.lock().unwrap_or_else(|error| error.into_inner()).clear();
}

/// The next job printed during the session.
pub fn take() -> Option<Vec<u8>> {
    JOBS.lock().unwrap_or_else(|error| error.into_inner()).pop_front()
}

/// One HTTP/1.1 connection: IPP requests until the client closes it.
async fn connection<S, F>(stream: S, deliver: F) -> std::io::Result<()>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    F: Fn(Vec<u8>),
{
    let mut stream = BufReader::new(stream);
    loop {
        let mut head = Vec::new();
        loop {
            let mut line = Vec::new();
            if stream.read_until(b'\n', &mut line).await? == 0 {
                return Ok(());
            }
            head.extend_from_slice(&line);
            if head.len() > MAX_HEADER {
                return Ok(());
            }
            if line == b"\r\n" || line == b"\n" {
                break;
            }
        }
        let text = String::from_utf8_lossy(&head).into_owned();
        let mut lines = text.lines();
        let request_line = lines.next().unwrap_or_default().to_owned();
        let headers: Vec<(String, String)> = lines
            .filter_map(|line| line.split_once(':'))
            .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
            .collect();
        let header = |name: &str| headers.iter().find(|(key, _)| key == name).map(|(_, value)| value.as_str());
        let mut parts = request_line.split_whitespace();
        let (method, path) = (parts.next().unwrap_or_default(), parts.next().unwrap_or_default());
        let keep_alive = !header("connection").is_some_and(|value| value.eq_ignore_ascii_case("close"));
        if method != "POST" || !header("content-type").is_some_and(|value| value.starts_with("application/ipp")) {
            let body = if method == "GET" { "Mixel Remote Printer" } else { "" };
            let status = if method == "GET" { "200 OK" } else { "405 Method Not Allowed" };
            let reply = format!("HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            stream.get_mut().write_all(reply.as_bytes()).await?;
            return Ok(());
        }
        if header("expect").is_some_and(|value| value.eq_ignore_ascii_case("100-continue")) {
            stream.get_mut().write_all(b"HTTP/1.1 100 Continue\r\n\r\n").await?;
        }
        let chunked = header("transfer-encoding").is_some_and(|value| value.to_ascii_lowercase().contains("chunked"));
        let length = header("content-length").and_then(|value| value.parse::<usize>().ok());
        let limit = MAX_JOB + MAX_HEADER;
        let body = if chunked {
            read_chunked(&mut stream, limit).await?
        } else {
            let length = length.unwrap_or(0);
            if length > limit {
                None
            } else {
                let mut body = vec![0; length];
                stream.read_exact(&mut body).await?;
                Some(body)
            }
        };
        let response = match body {
            Some(body) => respond(&body, path, &deliver),
            None => error_response(1, 0x0409),
        };
        let reply = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\nContent-Length: {}\r\n{}\r\n",
            response.len(),
            if keep_alive { "" } else { "Connection: close\r\n" }
        );
        stream.get_mut().write_all(reply.as_bytes()).await?;
        stream.get_mut().write_all(&response).await?;
        stream.get_mut().flush().await?;
        if !keep_alive {
            return Ok(());
        }
    }
}

/// A chunked body up to `limit` bytes; `None` when it is larger (the rest is not read).
async fn read_chunked<S: tokio::io::AsyncRead + Unpin>(stream: &mut BufReader<S>, limit: usize) -> std::io::Result<Option<Vec<u8>>> {
    let mut body = Vec::new();
    loop {
        let mut line = Vec::new();
        stream.read_until(b'\n', &mut line).await?;
        let size_text = String::from_utf8_lossy(&line);
        let size = usize::from_str_radix(size_text.trim().split(';').next().unwrap_or("0"), 16)
            .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "bad chunk size"))?;
        if size == 0 {
            // Trailer lines until the empty line.
            loop {
                let mut trailer = Vec::new();
                if stream.read_until(b'\n', &mut trailer).await? == 0 || trailer == b"\r\n" || trailer == b"\n" {
                    break;
                }
            }
            return Ok(Some(body));
        }
        if body.len() + size > limit {
            return Ok(None);
        }
        let start = body.len();
        body.resize(start + size, 0);
        stream.read_exact(&mut body[start..]).await?;
        let mut end = [0u8; 2];
        stream.read_exact(&mut end).await?;
    }
}

const TAG_OPERATION: u8 = 0x01;
const TAG_JOB: u8 = 0x02;
const TAG_END: u8 = 0x03;
const TAG_PRINTER: u8 = 0x04;
const INTEGER: u8 = 0x21;
const BOOLEAN: u8 = 0x22;
const ENUM: u8 = 0x23;
const KEYWORD: u8 = 0x44;
const URI: u8 = 0x45;
const CHARSET: u8 = 0x47;
const LANGUAGE: u8 = 0x48;
const MIME: u8 = 0x49;
const NAME: u8 = 0x42;
const TEXT: u8 = 0x41;

/// A parsed IPP request: version, operation, request id, and the document after the attributes.
struct Request<'a> {
    version: (u8, u8),
    operation: u16,
    id: u32,
    document: &'a [u8],
}

fn parse(body: &[u8]) -> Option<Request<'_>> {
    if body.len() < 9 {
        return None;
    }
    let version = (body[0], body[1]);
    let operation = u16::from_be_bytes([body[2], body[3]]);
    let id = u32::from_be_bytes([body[4], body[5], body[6], body[7]]);
    let mut at = 8;
    loop {
        let tag = *body.get(at)?;
        at += 1;
        if tag == TAG_END {
            return Some(Request { version, operation, id, document: &body[at..] });
        }
        if tag <= 0x0f {
            // A new attribute group.
            continue;
        }
        let name_length = usize::from(u16::from_be_bytes([*body.get(at)?, *body.get(at + 1)?]));
        at += 2 + name_length;
        let value_length = usize::from(u16::from_be_bytes([*body.get(at)?, *body.get(at + 1)?]));
        at += 2 + value_length;
        if at > body.len() {
            return None;
        }
    }
}

struct Writer(Vec<u8>);
impl Writer {
    fn new(version: (u8, u8), status: u16, id: u32) -> Self {
        let mut bytes = vec![version.0, version.1];
        bytes.extend_from_slice(&status.to_be_bytes());
        bytes.extend_from_slice(&id.to_be_bytes());
        let mut writer = Self(bytes);
        writer.group(TAG_OPERATION);
        writer.value(CHARSET, "attributes-charset", b"utf-8");
        writer.value(LANGUAGE, "attributes-natural-language", b"en");
        writer
    }
    fn group(&mut self, tag: u8) {
        self.0.push(tag);
    }
    fn value(&mut self, tag: u8, name: &str, value: &[u8]) {
        self.0.push(tag);
        self.0.extend_from_slice(&(name.len() as u16).to_be_bytes());
        self.0.extend_from_slice(name.as_bytes());
        self.0.extend_from_slice(&(value.len() as u16).to_be_bytes());
        self.0.extend_from_slice(value);
    }
    /// Further values of the attribute written just before.
    fn more(&mut self, tag: u8, value: &[u8]) {
        self.value(tag, "", value);
    }
    fn text(&mut self, tag: u8, name: &str, values: &[&str]) {
        for (index, value) in values.iter().enumerate() {
            if index == 0 { self.value(tag, name, value.as_bytes()) } else { self.more(tag, value.as_bytes()) }
        }
    }
    fn integers(&mut self, tag: u8, name: &str, values: &[i32]) {
        for (index, value) in values.iter().enumerate() {
            if index == 0 { self.value(tag, name, &value.to_be_bytes()) } else { self.more(tag, &value.to_be_bytes()) }
        }
    }
    fn finish(mut self) -> Vec<u8> {
        self.0.push(TAG_END);
        self.0
    }
}

fn error_response(id: u32, status: u16) -> Vec<u8> {
    Writer::new((1, 1), status, id).finish()
}

fn job_attributes(writer: &mut Writer, job: i32) {
    writer.group(TAG_JOB);
    writer.integers(INTEGER, "job-id", &[job]);
    writer.text(URI, "job-uri", &[&format!("ipp://127.0.0.1:{PORT}{PATH}/{job}")]);
    // Forwarded at once: done as far as the print system is concerned.
    writer.integers(ENUM, "job-state", &[9]);
    writer.text(KEYWORD, "job-state-reasons", &["job-completed-successfully"]);
}

fn respond(body: &[u8], path: &str, deliver: &dyn Fn(Vec<u8>)) -> Vec<u8> {
    static NEXT_JOB: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(1);
    let Some(request) = parse(body) else {
        return error_response(0, 0x0400);
    };
    if !path.starts_with(PATH) && path != "/" {
        return error_response(request.id, 0x0406);
    }
    if !matches!(request.version.0, 1 | 2) {
        return error_response(request.id, 0x0503);
    }
    let version = request.version;
    match request.operation {
        // Get-Printer-Attributes
        0x000B => {
            let mut writer = Writer::new(version, 0, request.id);
            writer.group(TAG_PRINTER);
            writer.text(URI, "printer-uri-supported", &[&format!("ipp://127.0.0.1:{PORT}{PATH}")]);
            writer.text(KEYWORD, "uri-security-supported", &["none"]);
            writer.text(KEYWORD, "uri-authentication-supported", &["none"]);
            writer.text(NAME, "printer-name", &[DESCRIPTION]);
            writer.text(TEXT, "printer-info", &[DESCRIPTION]);
            writer.text(TEXT, "printer-make-and-model", &["Mixel Remote Printer"]);
            writer.integers(ENUM, "printer-state", &[3]);
            writer.text(KEYWORD, "printer-state-reasons", &["none"]);
            writer.text(KEYWORD, "ipp-versions-supported", &["1.1", "2.0"]);
            writer.integers(ENUM, "operations-supported", &[0x0002, 0x0004, 0x0008, 0x0009, 0x000A, 0x000B]);
            writer.text(CHARSET, "charset-configured", &["utf-8"]);
            writer.text(CHARSET, "charset-supported", &["utf-8"]);
            writer.text(LANGUAGE, "natural-language-configured", &["en"]);
            writer.text(LANGUAGE, "generated-natural-language-supported", &["en"]);
            writer.text(MIME, "document-format-default", &["application/pdf"]);
            writer.text(MIME, "document-format-supported", &["application/pdf", "application/octet-stream"]);
            writer.value(BOOLEAN, "printer-is-accepting-jobs", &[1]);
            writer.integers(INTEGER, "queued-job-count", &[0]);
            writer.text(KEYWORD, "pdl-override-supported", &["not-attempted"]);
            writer.integers(INTEGER, "printer-up-time", &[1]);
            writer.text(KEYWORD, "compression-supported", &["none"]);
            writer.value(BOOLEAN, "color-supported", &[1]);
            writer.finish()
        }
        // Validate-Job, Cancel-Job, Get-Jobs
        0x0004 | 0x0008 | 0x000A => Writer::new(version, 0, request.id).finish(),
        // Print-Job: only PDF documents are forwarded.
        0x0002 => {
            if !request.document.starts_with(b"%PDF") {
                return error_response(request.id, 0x040A);
            }
            if request.document.len() > MAX_JOB {
                return error_response(request.id, 0x0409);
            }
            deliver(request.document.to_vec());
            let job = NEXT_JOB.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let mut writer = Writer::new(version, 0, request.id);
            job_attributes(&mut writer, job);
            writer.finish()
        }
        // Get-Job-Attributes: every job was forwarded when it arrived.
        0x0009 => {
            let mut writer = Writer::new(version, 0, request.id);
            job_attributes(&mut writer, 1);
            writer.finish()
        }
        _ => error_response(request.id, 0x0501),
    }
}

/// The printer description CUPS uses for the queue: PDF in, PDF to this app.
pub fn ppd() -> String {
    format!(
        "*PPD-Adobe: \"4.3\"\n*FormatVersion: \"4.3\"\n*FileVersion: \"1.0\"\n*LanguageVersion: English\n*LanguageEncoding: ISOLatin1\n\
         *PCFileName: \"MIXELRMT.PPD\"\n*Manufacturer: \"Mixel\"\n*Product: \"({DESCRIPTION})\"\n*ModelName: \"{DESCRIPTION}\"\n\
         *ShortNickName: \"{DESCRIPTION}\"\n*NickName: \"{DESCRIPTION}\"\n*PSVersion: \"(3010.000) 0\"\n*LanguageLevel: \"3\"\n\
         *ColorDevice: True\n*DefaultColorSpace: RGB\n*FileSystem: False\n*Throughput: \"10\"\n*LandscapeOrientation: Plus90\n\
         *TTRasterizer: Type42\n*cupsVersion: 2.2\n*cupsFilter2: \"application/pdf application/pdf 0 -\"\n\
         *OpenUI *PageSize/Media Size: PickOne\n*OrderDependency: 10 AnySetup *PageSize\n*DefaultPageSize: A4\n\
         *PageSize A4/A4: \"<</PageSize[595 842]>>setpagedevice\"\n*PageSize Letter/US Letter: \"<</PageSize[612 792]>>setpagedevice\"\n\
         *CloseUI: *PageSize\n*OpenUI *PageRegion: PickOne\n*OrderDependency: 10 AnySetup *PageRegion\n*DefaultPageRegion: A4\n\
         *PageRegion A4/A4: \"<</PageSize[595 842]>>setpagedevice\"\n*PageRegion Letter/US Letter: \"<</PageSize[612 792]>>setpagedevice\"\n\
         *CloseUI: *PageRegion\n*DefaultImageableArea: A4\n*ImageableArea A4/A4: \"0 0 595 842\"\n*ImageableArea Letter/US Letter: \"0 0 612 792\"\n\
         *DefaultPaperDimension: A4\n*PaperDimension A4/A4: \"595 842\"\n*PaperDimension Letter/US Letter: \"612 792\"\n"
    )
}

/// `lpadmin` arguments that add the queue (the PPD file path is passed in).
pub fn install_arguments(ppd: &str) -> Vec<String> {
    [
        "-p", QUEUE, "-D", DESCRIPTION, "-L", "This computer", "-E",
        "-v", &format!("ipp://127.0.0.1:{PORT}{PATH}"),
        "-P", ppd,
        // Never shared on the network; a job printed outside a session is dropped, not retried.
        "-o", "printer-is-shared=false", "-o", "printer-error-policy=abort-job",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect()
}

/// Whether CUPS has the queue (`lpstat -v` lists its device).
pub fn installed() -> bool {
    std::process::Command::new("lpstat")
        .args(["-v", QUEUE])
        .output()
        .is_ok_and(|output| output.status.success() && String::from_utf8_lossy(&output.stdout).contains(&format!("127.0.0.1:{PORT}")))
}

/// Adds or removes the queue. macOS: an administrator may do this directly, otherwise macOS asks
/// for an administrator's approval. Linux: through the system's administrator prompt (pkexec).
pub fn set_installed(enabled: bool) -> Result<(), String> {
    let ppd_file = tempfile::Builder::new().prefix("mixel-remote-printer").suffix(".ppd").tempfile().map_err(|_| "Cannot prepare the printer description")?;
    std::fs::write(ppd_file.path(), ppd()).map_err(|_| "Cannot prepare the printer description")?;
    let ppd_path = ppd_file.path().to_str().ok_or("Cannot prepare the printer description")?.to_owned();
    let arguments = if enabled { install_arguments(&ppd_path) } else { vec!["-x".into(), QUEUE.into()] };
    let direct = std::process::Command::new("lpadmin").args(&arguments).output();
    if direct.as_ref().is_ok_and(|output| output.status.success()) && installed() == enabled {
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    let elevated = {
        let quoted: Vec<String> = arguments.iter().map(|argument| format!("'{}'", argument.replace('\'', "'\\''"))).collect();
        let script = format!("do shell script \"/usr/sbin/lpadmin {}\" with administrator privileges", quoted.join(" ").replace('\\', "\\\\").replace('"', "\\\""));
        std::process::Command::new("/usr/bin/osascript").args(["-e", &script]).output()
    };
    #[cfg(not(target_os = "macos"))]
    let elevated = std::process::Command::new("pkexec").arg("/usr/sbin/lpadmin").args(&arguments).output();
    match elevated {
        Ok(output) if output.status.success() && installed() == enabled => Ok(()),
        Ok(_) => Err(if enabled { "The printer could not be added (administrator approval is needed)" } else { "The printer could not be removed (administrator approval is needed)" }.into()),
        Err(_) => Err("The print system (CUPS) is not available on this computer".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn request(operation: u16, id: u32, document: &[u8]) -> Vec<u8> {
        let mut writer = Writer::new((2, 0), operation, id);
        writer.text(URI, "printer-uri", &[&format!("ipp://127.0.0.1:{PORT}{PATH}")]);
        writer.text(NAME, "requesting-user-name", &["lab"]);
        writer.text(MIME, "document-format", &["application/pdf"]);
        let mut bytes = writer.finish();
        bytes.extend_from_slice(document);
        bytes
    }
    fn status(response: &[u8]) -> u16 {
        u16::from_be_bytes([response[2], response[3]])
    }
    fn has(response: &[u8], name: &str) -> bool {
        response.windows(name.len()).any(|window| window == name.as_bytes())
    }

    #[test]
    fn printer_attributes_and_jobs_follow_ipp() {
        let delivered = std::cell::RefCell::new(Vec::new());
        let deliver = |job: Vec<u8>| delivered.borrow_mut().push(job);
        let attributes = respond(&request(0x000B, 7, b""), PATH, &deliver);
        assert_eq!(status(&attributes), 0);
        assert_eq!(u32::from_be_bytes(attributes[4..8].try_into().unwrap()), 7);
        for name in ["printer-uri-supported", "document-format-supported", "printer-is-accepting-jobs", "operations-supported"] {
            assert!(has(&attributes, name), "{name}");
        }
        assert_eq!(status(&respond(&request(0x0004, 8, b""), PATH, &deliver)), 0);
        let printed = respond(&request(0x0002, 9, b"%PDF-1.7\n1 0 obj\n%%EOF\n"), PATH, &deliver);
        assert_eq!(status(&printed), 0);
        assert!(has(&printed, "job-state"));
        assert_eq!(delivered.borrow().as_slice(), [b"%PDF-1.7\n1 0 obj\n%%EOF\n".to_vec()]);
        // Not a PDF: refused (document-format-not-supported), nothing forwarded.
        assert_eq!(status(&respond(&request(0x0002, 10, b"%!PS-Adobe"), PATH, &deliver)), 0x040A);
        assert_eq!(delivered.borrow().len(), 1);
        // Create-Job is not offered; malformed requests and other paths are refused.
        assert_eq!(status(&respond(&request(0x0005, 11, b""), PATH, &deliver)), 0x0501);
        assert_eq!(status(&respond(&[1, 1, 0], PATH, &deliver)), 0x0400);
        assert_eq!(status(&respond(&request(0x000B, 12, b""), "/admin", &deliver)), 0x0406);
        assert_eq!(status(&respond(&[1, 1, 0, 0x0B, 0, 0, 0, 1, 0x01, 0x47, 0, 200], PATH, &deliver)), 0x0400);
    }

    #[test]
    fn the_queue_is_private_and_drops_jobs_outside_a_session() {
        let arguments = install_arguments("/tmp/x.ppd").join(" ");
        assert!(arguments.contains("printer-is-shared=false"));
        assert!(arguments.contains("printer-error-policy=abort-job"));
        assert!(arguments.contains(&format!("ipp://127.0.0.1:{PORT}{PATH}")));
        assert!(ppd().contains("*cupsFilter2: \"application/pdf application/pdf 0 -\""));
    }

    async fn exchange(chunked: bool, expect: bool, document: &[u8]) -> (String, Vec<u8>, Vec<Vec<u8>>) {
        let (client, server) = tokio::io::duplex(1 << 20);
        let jobs = std::sync::Arc::new(Mutex::new(Vec::new()));
        let sink = jobs.clone();
        let task = tokio::spawn(async move { connection(server, move |job| sink.lock().unwrap().push(job)).await });
        let (mut reader, mut writer) = tokio::io::split(client);
        let body = request(0x0002, 3, document);
        let mut head = format!("POST {PATH} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nContent-Type: application/ipp\r\nConnection: close\r\n");
        if expect {
            head.push_str("Expect: 100-continue\r\n");
        }
        if chunked {
            head.push_str("Transfer-Encoding: chunked\r\n\r\n");
            writer.write_all(head.as_bytes()).await.unwrap();
            for part in body.chunks(7) {
                writer.write_all(format!("{:x}\r\n", part.len()).as_bytes()).await.unwrap();
                writer.write_all(part).await.unwrap();
                writer.write_all(b"\r\n").await.unwrap();
            }
            writer.write_all(b"0\r\n\r\n").await.unwrap();
        } else {
            head.push_str(&format!("Content-Length: {}\r\n\r\n", body.len()));
            writer.write_all(head.as_bytes()).await.unwrap();
            writer.write_all(&body).await.unwrap();
        }
        let mut reply = Vec::new();
        reader.read_to_end(&mut reply).await.unwrap();
        task.await.unwrap().unwrap();
        let split = reply.windows(4).rposition(|window| window == b"\r\n\r\n").unwrap() + 4;
        let jobs = jobs.lock().unwrap().clone();
        (String::from_utf8_lossy(&reply[..split]).into_owned(), reply[split..].to_vec(), jobs)
    }

    #[tokio::test]
    async fn cups_style_http_requests_deliver_the_pdf() {
        let pdf = b"%PDF-1.4\nchunked job\n%%EOF\n";
        let (head, body, jobs) = exchange(true, true, pdf).await;
        assert!(head.starts_with("HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK"), "{head}");
        assert_eq!(status(&body), 0);
        assert_eq!(jobs, vec![pdf.to_vec()]);
        let (head, body, jobs) = exchange(false, false, pdf).await;
        assert!(head.starts_with("HTTP/1.1 200 OK"));
        assert_eq!(status(&body), 0);
        assert_eq!(jobs.len(), 1);
    }

    #[test]
    fn the_job_queue_is_bounded_and_cleared_when_a_session_starts() {
        clear();
        for index in 0..6u8 {
            accept_job(vec![index]);
        }
        assert_eq!(take(), Some(vec![2]));
        clear();
        assert_eq!(take(), None);
    }

    /// Lab check against the real print system: adds a private test queue that points at this
    /// endpoint, prints a PDF with `lp`, expects it here, and removes the queue again.
    /// Run with `cargo test -- --ignored real_cups_round_trip` on a computer where the user may
    /// administer printers without a prompt (macOS administrators, Linux `lpadmin` group or root).
    #[test]
    #[ignore = "changes the computer's printer list for a moment"]
    fn real_cups_round_trip() {
        assert!(ensure_running(), "port {PORT} is free");
        clear();
        let queue = "Mixel_Remote_Lab_Check";
        let ppd_file = tempfile::Builder::new().suffix(".ppd").tempfile().unwrap();
        std::fs::write(ppd_file.path(), ppd()).unwrap();
        let mut arguments = install_arguments(ppd_file.path().to_str().unwrap());
        arguments[1] = queue.into();
        let added = std::process::Command::new("lpadmin").args(&arguments).output().unwrap();
        assert!(added.status.success(), "lpadmin: {}", String::from_utf8_lossy(&added.stderr));
        let pdf = tempfile::Builder::new().suffix(".pdf").tempfile().unwrap();
        let document = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n";
        std::fs::write(pdf.path(), document).unwrap();
        let printed = std::process::Command::new("lp").args(["-d", queue]).arg(pdf.path()).output().unwrap();
        let mut job = None;
        for _ in 0..300 {
            job = take();
            if job.is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = std::process::Command::new("lpadmin").args(["-x", queue]).output();
        assert!(printed.status.success(), "lp: {}", String::from_utf8_lossy(&printed.stderr));
        let job = job.expect("the print system delivered the job");
        assert!(job.starts_with(b"%PDF"), "a PDF arrived ({} bytes)", job.len());
        println!("CUPS delivered a PDF of {} bytes", job.len());
    }
}
