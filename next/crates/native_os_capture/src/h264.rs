//! Bounded AVCC -> Annex-B framing for H.264 RTP packetizers.
use zeroize::Zeroizing;
pub const MAX_ACCESS_UNIT: usize = core_protocol::MAX_VIDEO_ACCESS_UNIT;
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum EncodeError {
    #[error("invalid encoder dimensions, rate, or timestamp")]
    Configuration,
    #[error("VideoToolbox {operation} failed ({status})")]
    Native {
        operation: &'static str,
        status: i32,
    },
    #[error("VideoToolbox property {key} failed ({status})")]
    Property { key: String, status: i32 },
    #[error("hardware H264 is unavailable")]
    HardwareUnavailable,
    #[error("encoder timed out and must be recreated")]
    Timeout,
    #[error("encoder failed and must be recreated")]
    Failed,
    #[error("encoder dropped the frame")]
    Dropped,
    #[error("encoder could not recover a keyframe within three seconds")]
    Stalled,
    #[error("malformed or oversized H264 access unit")]
    Bitstream,
}
pub struct EncodedH264 {
    pub annex_b: Zeroizing<Vec<u8>>,
    pub keyframe: bool,
}
pub struct AvccAccessUnit<'a> {
    pub bytes: Zeroizing<Vec<u8>>,
    pub sps: Option<&'a [u8]>,
    pub pps: Option<&'a [u8]>,
    pub keyframe: bool,
}
/// Restore length-prefixed samples for native decoding, retaining parameter
/// sets separately. Bounds and NAL headers are validated before any native API.
pub fn annex_b_to_avcc(data: &[u8]) -> Result<AvccAccessUnit<'_>, EncodeError> {
    if data.is_empty() || data.len() > MAX_ACCESS_UNIT {
        return Err(EncodeError::Bitstream);
    }
    let mut starts = Vec::new();
    let mut cursor = 0;
    while cursor + 3 <= data.len() {
        let length = if data.get(cursor..cursor + 4) == Some(&[0, 0, 0, 1]) {
            4
        } else if data.get(cursor..cursor + 3) == Some(&[0, 0, 1]) {
            3
        } else {
            cursor += 1;
            continue;
        };
        if starts.len() >= 1024 {
            return Err(EncodeError::Bitstream);
        }
        starts.push((cursor, length));
        cursor += length;
    }
    if starts.first().is_none_or(|(position, _)| *position != 0) {
        return Err(EncodeError::Bitstream);
    }
    let mut output = AvccAccessUnit {
        bytes: Zeroizing::new(Vec::with_capacity(data.len())),
        sps: None,
        pps: None,
        keyframe: false,
    };
    let mut picture = false;
    for (index, (position, prefix)) in starts.iter().enumerate() {
        let end = starts
            .get(index + 1)
            .map_or(data.len(), |(position, _)| *position);
        let nal = data
            .get(position + prefix..end)
            .filter(|nal| !nal.is_empty())
            .ok_or(EncodeError::Bitstream)?;
        let kind = nal[0] & 0x1f;
        if nal[0] & 0x80 != 0 || !(1..=23).contains(&kind) {
            return Err(EncodeError::Bitstream);
        }
        if matches!(kind, 7 | 8) {
            if nal.len() > 65535 {
                return Err(EncodeError::Bitstream);
            }
            let parameter = if kind == 7 {
                &mut output.sps
            } else {
                &mut output.pps
            };
            if parameter.is_some_and(|previous| previous != nal) {
                return Err(EncodeError::Bitstream);
            }
            *parameter = Some(nal);
            continue;
        }
        if output
            .bytes
            .len()
            .checked_add(nal.len() + 4)
            .is_none_or(|length| length > MAX_ACCESS_UNIT)
        {
            return Err(EncodeError::Bitstream);
        }
        output.bytes.extend((nal.len() as u32).to_be_bytes());
        output.bytes.extend_from_slice(nal);
        picture |= (1..=5).contains(&kind);
        output.keyframe |= kind == 5;
    }
    if !picture {
        return Err(EncodeError::Bitstream);
    }
    Ok(output)
}
pub fn avcc_to_annex_b(
    avcc: &[u8],
    length_size: usize,
    parameters: &[&[u8]],
) -> Result<EncodedH264, EncodeError> {
    if !matches!(length_size, 1 | 2 | 4)
        || avcc.is_empty()
        || avcc.len() > MAX_ACCESS_UNIT
        || parameters.len() > 16
    {
        return Err(EncodeError::Bitstream);
    }
    let mut output = Zeroizing::new(Vec::with_capacity(avcc.len().min(MAX_ACCESS_UNIT)));
    for parameter in parameters {
        if parameter.is_empty()
            || parameter.len() > 65535
            || !matches!(parameter[0] & 0x1f, 7 | 8)
            || parameter[0] & 0x80 != 0
        {
            return Err(EncodeError::Bitstream);
        }
        append(&mut output, parameter)?;
    }
    let mut position = 0;
    let mut count = 0;
    let mut keyframe = false;
    while position < avcc.len() {
        count += 1;
        if count > 1024 {
            return Err(EncodeError::Bitstream);
        }
        let header = avcc
            .get(position..position + length_size)
            .ok_or(EncodeError::Bitstream)?;
        let length = header
            .iter()
            .fold(0usize, |value, byte| (value << 8) | usize::from(*byte));
        position += length_size;
        let end = position.checked_add(length).ok_or(EncodeError::Bitstream)?;
        let nal = avcc
            .get(position..end)
            .filter(|nal| !nal.is_empty())
            .ok_or(EncodeError::Bitstream)?;
        if nal[0] & 0x80 != 0 || !(1..=23).contains(&(nal[0] & 0x1f)) {
            return Err(EncodeError::Bitstream);
        }
        keyframe |= nal[0] & 0x1f == 5;
        append(&mut output, nal)?;
        position = end;
    }
    Ok(EncodedH264 {
        annex_b: output,
        keyframe,
    })
}
fn append(output: &mut Vec<u8>, nal: &[u8]) -> Result<(), EncodeError> {
    if output
        .len()
        .checked_add(4)
        .and_then(|size| size.checked_add(nal.len()))
        .is_none_or(|size| size > MAX_ACCESS_UNIT)
    {
        return Err(EncodeError::Bitstream);
    }
    output.extend([0, 0, 0, 1]);
    output.extend_from_slice(nal);
    Ok(())
}
