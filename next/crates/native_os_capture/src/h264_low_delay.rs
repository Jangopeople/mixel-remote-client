//! Explicit zero-reorder VUI for our no-B-frame baseline encoder. Some decoders
//! otherwise infer a full level-dependent DPB and delay seven frames at 640x420.
//! This never rewrites arbitrary peer video or claims an encoder has no B frames.
use crate::h264::EncodeError;

struct Bits {
    data: Vec<bool>,
    position: usize,
}
impl Bits {
    fn read(&mut self, count: usize) -> Result<u32, EncodeError> {
        if count > 32
            || self
                .position
                .checked_add(count)
                .is_none_or(|end| end > self.data.len())
        {
            return Err(EncodeError::Bitstream);
        }
        let mut value = 0;
        for _ in 0..count {
            value = (value << 1) | u32::from(self.data[self.position]);
            self.position += 1;
        }
        Ok(value)
    }
    fn flag(&mut self) -> Result<bool, EncodeError> {
        Ok(self.read(1)? != 0)
    }
    fn ue(&mut self, maximum: u32) -> Result<u32, EncodeError> {
        let mut zeros = 0;
        while !self.flag()? {
            zeros += 1;
            if zeros > 31 {
                return Err(EncodeError::Bitstream);
            }
        }
        let value = ((1u64 << zeros) - 1) + u64::from(self.read(zeros)?);
        if value > u64::from(maximum) {
            return Err(EncodeError::Bitstream);
        }
        Ok(value as u32)
    }
    fn hrd(&mut self) -> Result<(), EncodeError> {
        let count = self.ue(31)? + 1;
        self.read(8)?;
        for _ in 0..count {
            self.ue(u32::MAX - 1)?;
            self.ue(u32::MAX - 1)?;
            self.flag()?;
        }
        self.read(20)?;
        Ok(())
    }
    fn trailing(&mut self) -> Result<(), EncodeError> {
        if !self.flag()?
            || self.data[self.position..].iter().any(|bit| *bit)
            || self.data.len() - self.position > 7
        {
            return Err(EncodeError::Bitstream);
        }
        Ok(())
    }
}
fn write_ue(bits: &mut Vec<bool>, value: u32) {
    let value = u64::from(value) + 1;
    let width = 64 - value.leading_zeros() as usize;
    bits.extend(std::iter::repeat_n(false, width - 1));
    for shift in (0..width).rev() {
        bits.push((value >> shift) & 1 != 0);
    }
}

/// Only call for a locally configured constrained-baseline encoder with
/// AllowFrameReordering=false. All reference, timing and colour fields survive.
pub fn zero_reorder_baseline_sps(sps: &[u8]) -> Result<Vec<u8>, EncodeError> {
    if !(5..=4096).contains(&sps.len()) || sps[0] & 0x9f != 7 {
        return Err(EncodeError::Bitstream);
    }
    let mut rbsp = Vec::with_capacity(sps.len());
    let mut zeros = 0;
    let mut index = 1;
    while index < sps.len() {
        let byte = sps[index];
        if zeros == 2 {
            if byte == 3 {
                if sps.get(index + 1).is_none_or(|next| *next > 3) {
                    return Err(EncodeError::Bitstream);
                }
                zeros = 0;
                index += 1;
                continue;
            }
            if byte <= 2 {
                return Err(EncodeError::Bitstream);
            }
        }
        rbsp.push(byte);
        zeros = if byte == 0 { zeros + 1 } else { 0 };
        index += 1;
    }
    let mut reader = Bits {
        data: rbsp
            .iter()
            .flat_map(|byte| (0..8).rev().map(move |shift| byte & (1 << shift) != 0))
            .collect(),
        position: 0,
    };
    if reader.read(8)? != 66 {
        return Err(EncodeError::Bitstream);
    }
    let constraints = reader.read(8)?;
    if constraints & 3 != 0 {
        return Err(EncodeError::Bitstream);
    }
    reader.read(8)?;
    reader.ue(31)?;
    reader.ue(12)?;
    match reader.ue(2)? {
        0 => {
            reader.ue(12)?;
        }
        1 => {
            reader.flag()?;
            reader.ue(u32::MAX - 1)?;
            reader.ue(u32::MAX - 1)?;
            let count = reader.ue(255)?;
            for _ in 0..count {
                reader.ue(u32::MAX - 1)?;
            }
        }
        _ => {}
    }
    let references = reader.ue(16)?;
    reader.flag()?;
    reader.ue(511)?;
    reader.ue(511)?;
    if !reader.flag()? {
        reader.flag()?;
    }
    reader.flag()?;
    if reader.flag()? {
        for _ in 0..4 {
            reader.ue(8192)?;
        }
    }
    let vui_position = reader.position;
    let vui = reader.flag()?;
    let restriction_position;
    if vui {
        if reader.flag()? && reader.read(8)? == 255 {
            reader.read(16)?;
            reader.read(16)?;
        }
        if reader.flag()? {
            reader.flag()?;
        }
        if reader.flag()? {
            reader.read(3)?;
            reader.flag()?;
            if reader.flag()? {
                reader.read(24)?;
            }
        }
        if reader.flag()? {
            reader.ue(5)?;
            reader.ue(5)?;
        }
        if reader.flag()? {
            reader.read(32)?;
            reader.read(32)?;
            reader.flag()?;
        }
        let nal_hrd = reader.flag()?;
        if nal_hrd {
            reader.hrd()?;
        }
        let vcl_hrd = reader.flag()?;
        if vcl_hrd {
            reader.hrd()?;
        }
        if nal_hrd || vcl_hrd {
            reader.flag()?;
        }
        reader.flag()?;
        restriction_position = reader.position;
        if reader.flag()? {
            reader.flag()?;
            reader.ue(16)?;
            reader.ue(16)?;
            reader.ue(16)?;
            reader.ue(16)?;
            let reordered = reader.ue(16)?;
            let buffered = reader.ue(16)?;
            reader.trailing()?;
            if reordered != 0 || buffered < references {
                return Err(EncodeError::Bitstream);
            }
            return Ok(sps.to_vec());
        }
    } else {
        restriction_position = reader.position;
    }
    reader.trailing()?;
    let mut output = if vui {
        reader.data[..restriction_position].to_vec()
    } else {
        let mut prefix = reader.data[..vui_position].to_vec();
        prefix.push(true); // VUI present
        prefix.extend([false; 8]); // aspect, overscan, video, chroma, timing, NAL/VCL HRD, pic_struct
        prefix
    };
    output.push(true); // bitstream_restriction_flag
    output.push(true); // motion_vectors_over_pic_boundaries_flag
    for value in [2, 1, 16, 16, 0, references] {
        write_ue(&mut output, value);
    }
    output.push(true); // rbsp_stop_one_bit
    while !output.len().is_multiple_of(8) {
        output.push(false);
    }
    let mut nal = vec![sps[0]];
    zeros = 0;
    for chunk in output.as_chunks::<8>().0 {
        let byte = chunk
            .iter()
            .fold(0u8, |value, bit| (value << 1) | u8::from(*bit));
        if zeros == 2 && byte <= 3 {
            nal.push(3);
            zeros = 0;
        }
        nal.push(byte);
        zeros = if byte == 0 { zeros + 1 } else { 0 };
    }
    Ok(nal)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn minimal_baseline_sps_and_existing_restriction_are_idempotent() {
        let mut bits = Vec::new();
        for byte in [66u8, 0xc0, 30] {
            for shift in (0..8).rev() {
                bits.push(byte & (1 << shift) != 0);
            }
        }
        for value in [0, 0, 0, 0, 1] {
            write_ue(&mut bits, value);
        }
        bits.push(false);
        write_ue(&mut bits, 39);
        write_ue(&mut bits, 26);
        bits.extend([true, true, false, false, true]);
        while !bits.len().is_multiple_of(8) {
            bits.push(false);
        }
        let mut sps = vec![0x67];
        sps.extend(bits.as_chunks::<8>().0.iter().map(|chunk| {
            chunk
                .iter()
                .fold(0, |value, bit| (value << 1) | u8::from(*bit))
        }));
        let result = zero_reorder_baseline_sps(&sps).unwrap();
        assert!(result.len() > sps.len());
        assert_eq!(zero_reorder_baseline_sps(&result).unwrap(), result);
        for index in 0..result.len() {
            for bit in 0..8 {
                let mut mutated = result.clone();
                mutated[index] ^= 1 << bit;
                let _ = zero_reorder_baseline_sps(&mutated);
            }
        }
        for end in 0..sps.len() {
            assert!(zero_reorder_baseline_sps(&sps[..end]).is_err());
        }
    }
    #[test]
    fn arbitrary_sps_never_panics_or_allocates_unboundedly() {
        for size in 0..128 {
            for byte in 0..=255 {
                let _ = zero_reorder_baseline_sps(&vec![byte; size]);
            }
        }
    }
}
