//! Remote tools (terminal, task manager, scripts, quality): typed errors, record framing and the
//! messages of the `mixel-tools-v1` channel. See `docs/mixel-remote-2.2-engine-contract.md`.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub const LABEL: &str = "mixel-tools-v1";
/// Announced in `a=x-mixel-features:`.
pub const FEATURE: &str = "tools";
pub const FEATURE_TERM: &str = "term";
pub const FEATURE_TERM_ELEVATE: &str = "term-elevate";
pub const FEATURE_TASKS: &str = "tasks";
pub const FEATURE_QUALITY: &str = "quality";
pub const FEATURE_THUMBS: &str = "thumbs";
pub const FEATURE_SCRIPTS: &str = "scriptrun";

/// One sealed record holds at most this many plain bytes (the crypto limit is 4096).
pub const RECORD: usize = 4000;
/// A reassembled message may not exceed this.
pub const MAX_MESSAGE: usize = 256 * 1024;

/// A typed failure: apps switch on `code` (contract section 0).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolError {
    pub code: String,
    pub message: String,
    pub feature: String,
}
impl ToolError {
    pub fn new(feature: &str, code: &str, message: &str) -> Self {
        Self { code: code.into(), message: message.into(), feature: feature.into() }
    }
    pub fn json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "{}".into())
    }
}
impl From<ToolError> for String {
    fn from(error: ToolError) -> String {
        error.json()
    }
}
pub type ToolResult<T> = Result<T, ToolError>;

pub fn err(feature: &str, code: &str, message: &str) -> ToolError {
    ToolError::new(feature, code, message)
}

/// Record kinds (first byte).
pub const KIND_JSON: u8 = 1;
pub const KIND_OUT: u8 = 2;
pub const KIND_IN: u8 = 3;

/// Splits a JSON message into records: `[1][0 single | 1 more | 2 last][bytes]`.
pub fn frame_json(value: &Value) -> Vec<Vec<u8>> {
    let bytes = serde_json::to_vec(value).unwrap_or_default();
    let chunk = RECORD - 2;
    if bytes.len() <= chunk {
        let mut record = vec![KIND_JSON, 0];
        record.extend(bytes);
        return vec![record];
    }
    let parts: Vec<&[u8]> = bytes.chunks(chunk).collect();
    let last = parts.len() - 1;
    parts
        .into_iter()
        .enumerate()
        .map(|(index, part)| {
            let mut record = vec![KIND_JSON, if index == last { 2 } else { 1 }];
            record.extend_from_slice(part);
            record
        })
        .collect()
}

pub fn frame_raw(kind: u8, data: &[u8]) -> Vec<Vec<u8>> {
    data.chunks(RECORD - 1)
        .map(|part| {
            let mut record = vec![kind];
            record.extend_from_slice(part);
            record
        })
        .collect()
}

/// What a received record is.
#[derive(Debug, PartialEq)]
pub enum Incoming {
    Json(Value),
    Out(Vec<u8>),
    In(Vec<u8>),
}

/// Reassembles fragmented JSON messages.
#[derive(Default)]
pub struct Reader {
    partial: Vec<u8>,
}
impl Reader {
    pub fn push(&mut self, record: &[u8]) -> Result<Option<Incoming>, String> {
        let (&kind, body) = record.split_first().ok_or("Empty tools record")?;
        match kind {
            KIND_OUT => Ok(Some(Incoming::Out(body.to_vec()))),
            KIND_IN => Ok(Some(Incoming::In(body.to_vec()))),
            KIND_JSON => {
                let (&flag, body) = body.split_first().ok_or("Empty tools message")?;
                if flag > 2 {
                    return Err("Invalid tools fragment".into());
                }
                if self.partial.len() + body.len() > MAX_MESSAGE {
                    self.partial.clear();
                    return Err("Oversized tools message".into());
                }
                self.partial.extend_from_slice(body);
                if flag == 1 {
                    return Ok(None);
                }
                let bytes = std::mem::take(&mut self.partial);
                let value = serde_json::from_slice(&bytes).map_err(|_| "Invalid tools message")?;
                Ok(Some(Incoming::Json(value)))
            }
            _ => Err("Unknown tools record".into()),
        }
    }
}

/// A request from the technician.
pub fn request(id: u64, op: &str, args: Value) -> Value {
    json!({"t": "req", "id": id, "op": op, "args": args})
}
pub fn response_ok(id: u64, data: Value) -> Value {
    json!({"t": "resp", "id": id, "ok": true, "data": data})
}
pub fn response_err(id: u64, error: &ToolError) -> Value {
    json!({"t": "resp", "id": id, "ok": false, "error": error})
}

/// `reason` of at least eight characters (after trimming).
pub fn valid_reason(reason: Option<&str>) -> bool {
    reason.is_some_and(|text| text.trim().chars().count() >= 8 && text.len() <= 256 && !text.chars().any(char::is_control))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_and_large_messages_survive_framing() {
        let small = json!({"a": 1});
        let big = json!({"x": "y".repeat(20_000)});
        for value in [small, big] {
            let mut reader = Reader::default();
            let mut got = None;
            for record in frame_json(&value) {
                assert!(record.len() <= RECORD);
                got = reader.push(&record).unwrap();
            }
            assert_eq!(got, Some(Incoming::Json(value)));
        }
    }
    #[test]
    fn raw_data_is_split_and_bad_records_are_refused() {
        let data = vec![7u8; 9000];
        let parts = frame_raw(KIND_OUT, &data);
        assert!(parts.len() >= 3 && parts.iter().all(|part| part.len() <= RECORD));
        let mut reader = Reader::default();
        assert!(reader.push(&[]).is_err());
        assert!(reader.push(&[9, 1]).is_err());
        assert!(reader.push(&[KIND_JSON, 7, b'{']).is_err());
        assert!(reader.push(&[KIND_JSON, 0, b'{']).is_err());
    }
    #[test]
    fn errors_are_typed_json_and_reasons_are_checked() {
        let text: String = err("terminal", "policy_off", "off").into();
        assert!(text.contains("\"code\":\"policy_off\"") && text.contains("\"feature\":\"terminal\""));
        assert!(!valid_reason(None) && !valid_reason(Some("short")) && !valid_reason(Some("bad\u{7}reason ok")));
        assert!(valid_reason(Some("fix printer queue")));
    }
}
