//! Drawing on the remote screen: the technician's pen strokes, arrows and highlights travel
//! from the controller to the host inside the authenticated control channel. The host shows
//! them in a click-through overlay for the person at the computer. Nothing is clicked or typed,
//! so drawing is allowed whenever the session can view the screen.
//!
//! Wire format after [`RECORD`] (big endian):
//! - stroke: `1`, id `u16`, tool `u8`, colour `u8`, width `u8`, flags `u8` (bit 0: done),
//!   point count `u8`, then `count` times x `u16`, y `u16` (0..65535 of the shared picture),
//! - undo: `2`,
//! - clear: `3`.
use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Announced by an app that shows the technician's drawings as host.
pub const FEATURE: &str = "draw";
/// Announced in addition by a host whose overlay is part of the captured picture (Linux X11):
/// the controller then stops drawing a finished stroke itself after [`IN_PICTURE_AFTER_MS`].
pub const FEATURE_IN_PICTURE: &str = "drawpic";
/// Prefix of a control record that carries one drawing message (controller to host).
pub const RECORD: &[u8] = b"mixel-draw-v1:";

pub const MAX_POINTS_PER_MESSAGE: usize = 64;
pub const MAX_POINTS_PER_STROKE: usize = 4096;
pub const MAX_STROKES: usize = 100;
/// The board starts to fade this long after the last change.
pub const FADE_AFTER_MS: u64 = 5000;
/// How long the fade takes; the board is cleared afterwards.
pub const FADE_MS: u64 = 600;
/// Controller with `drawpic` hosts: a finished stroke is left to the picture after this time.
pub const IN_PICTURE_AFTER_MS: u64 = 1500;
/// Drawing records a host accepts from one technician per second; more are ignored.
pub const MAX_RECORDS_PER_SECOND: u32 = 200;
/// Opacity of the highlighter where the platform can blend.
pub const HIGHLIGHT_ALPHA: f64 = 0.35;

/// Red, yellow, green, blue, white (RGB).
pub const PALETTE: [[u8; 3]; 5] = [
    [0xE5, 0x32, 0x2D],
    [0xFF, 0xD4, 0x00],
    [0x22, 0xB1, 0x4C],
    [0x1E, 0x7B, 0xFF],
    [0xFF, 0xFF, 0xFF],
];

/// Line widths of the four size steps on a picture 1080 high (scaled with the picture).
const PEN_WIDTHS: [f64; 4] = [3.0, 5.0, 8.0, 12.0];
const HIGHLIGHT_WIDTHS: [f64; 4] = [14.0, 20.0, 28.0, 36.0];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Tool {
    Pen,
    Highlighter,
    Arrow,
}
impl Tool {
    fn code(self) -> u8 {
        match self {
            Self::Pen => 0,
            Self::Highlighter => 1,
            Self::Arrow => 2,
        }
    }
    fn from_code(code: u8) -> Option<Self> {
        match code {
            0 => Some(Self::Pen),
            1 => Some(Self::Highlighter),
            2 => Some(Self::Arrow),
            _ => None,
        }
    }
}

/// Line width in pixels of a size step (1..=4) on a picture `height` pixels high.
pub fn line_width(tool: Tool, width: u8, height: f64) -> f64 {
    let step = usize::from(width.clamp(1, 4) - 1);
    let base = if tool == Tool::Highlighter {
        HIGHLIGHT_WIDTHS[step]
    } else {
        PEN_WIDTHS[step]
    };
    (base * height.max(1.0) / 1080.0).max(1.0)
}

/// One drawing message. JSON from the apps:
/// `{"kind":"stroke","id":3,"tool":"pen","color":0,"width":2,"points":[[x,y]],"done":false}`,
/// `{"kind":"undo"}`, `{"kind":"clear"}`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Message {
    /// Successive messages with the same id add points to the same stroke.
    Stroke {
        id: u16,
        tool: Tool,
        #[serde(rename = "color")]
        colour: u8,
        width: u8,
        points: Vec<(u16, u16)>,
        done: bool,
    },
    /// Removes this technician's last stroke.
    Undo,
    /// Removes this technician's strokes.
    Clear,
}

#[derive(Debug, Error, PartialEq, Eq)]
#[error("invalid drawing message")]
pub struct DrawError;

impl Message {
    pub fn validate(&self) -> Result<(), DrawError> {
        if let Self::Stroke {
            colour,
            width,
            points,
            done,
            ..
        } = self
            && (usize::from(*colour) >= PALETTE.len()
                || !(1..=4).contains(width)
                || points.len() > MAX_POINTS_PER_MESSAGE
                || (points.is_empty() && !done))
        {
            return Err(DrawError);
        }
        Ok(())
    }

    /// The message without the record prefix.
    pub fn encode(&self) -> Result<Vec<u8>, DrawError> {
        self.validate()?;
        match self {
            Self::Stroke {
                id,
                tool,
                colour,
                width,
                points,
                done,
            } => {
                let mut bytes = Vec::with_capacity(8 + points.len() * 4);
                bytes.push(1);
                bytes.extend(id.to_be_bytes());
                bytes.extend([
                    tool.code(),
                    *colour,
                    *width,
                    u8::from(*done),
                    points.len() as u8,
                ]);
                for (x, y) in points {
                    bytes.extend(x.to_be_bytes());
                    bytes.extend(y.to_be_bytes());
                }
                Ok(bytes)
            }
            Self::Undo => Ok(vec![2]),
            Self::Clear => Ok(vec![3]),
        }
    }

    /// The control record: [`RECORD`] and the encoded message.
    pub fn record(&self) -> Result<Vec<u8>, DrawError> {
        let mut record = RECORD.to_vec();
        record.extend(self.encode()?);
        Ok(record)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, DrawError> {
        let message = match bytes {
            [2] => Self::Undo,
            [3] => Self::Clear,
            [
                1,
                id_high,
                id_low,
                tool,
                colour,
                width,
                flags,
                count,
                rest @ ..,
            ] => {
                let count = usize::from(*count);
                if *flags > 1 || rest.len() != count * 4 {
                    return Err(DrawError);
                }
                Self::Stroke {
                    id: u16::from_be_bytes([*id_high, *id_low]),
                    tool: Tool::from_code(*tool).ok_or(DrawError)?,
                    colour: *colour,
                    width: *width,
                    points: rest
                        .chunks_exact(4)
                        .map(|point| {
                            (
                                u16::from_be_bytes([point[0], point[1]]),
                                u16::from_be_bytes([point[2], point[3]]),
                            )
                        })
                        .collect(),
                    done: *flags == 1,
                }
            }
            _ => return Err(DrawError),
        };
        message.validate()?;
        Ok(message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stroke(points: Vec<(u16, u16)>, done: bool) -> Message {
        Message::Stroke {
            id: 513,
            tool: Tool::Arrow,
            colour: 4,
            width: 4,
            points,
            done,
        }
    }

    #[test]
    fn messages_round_trip() {
        for message in [
            stroke(vec![(0, 0), (65535, 65535), (1, 2)], false),
            stroke(vec![], true),
            stroke(vec![(7, 9); MAX_POINTS_PER_MESSAGE], true),
            Message::Stroke {
                id: 0,
                tool: Tool::Highlighter,
                colour: 0,
                width: 1,
                points: vec![(3, 4)],
                done: false,
            },
            Message::Undo,
            Message::Clear,
        ] {
            let record = message.record().unwrap();
            let rest = record.strip_prefix(RECORD).unwrap();
            assert_eq!(Message::decode(rest).unwrap(), message);
        }
        assert_eq!(
            stroke(vec![(1, 2)], true).encode().unwrap(),
            vec![1, 2, 1, 2, 4, 4, 1, 1, 0, 1, 0, 2]
        );
    }

    #[test]
    fn invalid_messages_are_rejected() {
        let good = stroke(vec![(1, 2), (3, 4)], false).encode().unwrap();
        let mut bad: Vec<Vec<u8>> = vec![
            vec![],
            vec![0],
            vec![4],
            vec![2, 0],
            vec![3, 3],
            good[..good.len() - 1].to_vec(),
            [good.as_slice(), &[0]].concat(),
        ];
        for (index, value) in [
            (3, 3),
            (4, 5),
            (4, 255),
            (5, 0),
            (5, 5),
            (6, 2),
            (7, 3),
            (7, 0),
        ] {
            let mut changed = good.clone();
            changed[index] = value;
            bad.push(changed);
        }
        // No points and not done; more than 64 points.
        bad.push(vec![1, 0, 1, 0, 0, 1, 0, 0]);
        let mut long = vec![1, 0, 1, 0, 0, 1, 0, 65];
        long.extend(vec![0; 65 * 4]);
        bad.push(long);
        for bytes in &bad {
            assert_eq!(Message::decode(bytes), Err(DrawError), "{bytes:?}");
        }
        // Every truncation and every single-byte change either fails or decodes to a valid message.
        for cut in 0..good.len() {
            assert!(Message::decode(&good[..cut]).is_err());
        }
        for index in 0..good.len() {
            for value in [0u8, 1, 2, 3, 4, 5, 64, 65, 128, 255] {
                let mut changed = good.clone();
                changed[index] = value;
                if let Ok(message) = Message::decode(&changed) {
                    assert!(message.validate().is_ok());
                    assert_eq!(message.encode().unwrap(), changed);
                }
            }
        }
        assert!(stroke(vec![(0, 0); 65], false).encode().is_err());
        assert!(
            Message::Stroke {
                id: 1,
                tool: Tool::Pen,
                colour: 5,
                width: 1,
                points: vec![(0, 0)],
                done: false
            }
            .encode()
            .is_err()
        );
    }

    #[test]
    fn the_json_shape_of_the_apps_is_accepted_and_checked() {
        let message: Message = serde_json::from_str(
            r#"{"kind":"stroke","id":3,"tool":"highlighter","color":1,"width":2,"points":[[10,20],[30,40]],"done":false}"#,
        )
        .unwrap();
        assert_eq!(
            message,
            Message::Stroke {
                id: 3,
                tool: Tool::Highlighter,
                colour: 1,
                width: 2,
                points: vec![(10, 20), (30, 40)],
                done: false
            }
        );
        assert_eq!(
            serde_json::from_str::<Message>(r#"{"kind":"undo"}"#).unwrap(),
            Message::Undo
        );
        assert_eq!(
            serde_json::from_str::<Message>(r#"{"kind":"clear"}"#).unwrap(),
            Message::Clear
        );
        for bad in [
            r#"{"kind":"stroke","id":3,"tool":"brush","color":1,"width":2,"points":[],"done":true}"#,
            r#"{"kind":"stroke","id":3,"tool":"pen","color":1,"width":2,"points":[[1,2,3]],"done":true}"#,
            r#"{"kind":"stroke","id":3,"tool":"pen","color":1,"width":2,"points":[[70000,2]],"done":true}"#,
            r#"{"kind":"stroke","id":3,"tool":"pen","color":1,"width":2,"points":[],"done":true,"extra":1}"#,
            r#"{"kind":"erase"}"#,
        ] {
            assert!(serde_json::from_str::<Message>(bad).is_err(), "{bad}");
        }
        let unchecked: Message =
            serde_json::from_str(r#"{"kind":"stroke","id":3,"tool":"pen","color":9,"width":2,"points":[[1,1]],"done":true}"#).unwrap();
        assert!(unchecked.validate().is_err());
    }

    #[test]
    fn widths_follow_the_picture_size() {
        assert_eq!(line_width(Tool::Pen, 1, 1080.0), 3.0);
        assert_eq!(line_width(Tool::Arrow, 4, 2160.0), 24.0);
        assert_eq!(line_width(Tool::Highlighter, 2, 540.0), 10.0);
        assert_eq!(line_width(Tool::Pen, 1, 10.0), 1.0);
        assert_eq!(line_width(Tool::Pen, 9, 1080.0), 12.0);
    }
}
