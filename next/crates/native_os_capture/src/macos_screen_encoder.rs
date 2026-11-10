//! Damage suppression and bounded recovery around the owner-thread encoder.
//! A dropped output must not make an unchanged screen permanently disappear.
use crate::{
    damage::DamageTracker,
    h264::{EncodeError, EncodedH264},
    macos_encoder::HardwareH264Encoder,
    stream::CapturedFrame,
};
use std::time::{Duration, Instant};

const RECOVERY_LIMIT: Duration = Duration::from_secs(3);

pub struct ScreenH264Encoder {
    hardware: HardwareH264Encoder,
    progress: Progress,
}
impl ScreenH264Encoder {
    pub fn new(width: usize, height: usize, fps: u32, bitrate: u32) -> Result<Self, EncodeError> {
        Ok(Self {
            hardware: HardwareH264Encoder::new(width, height, fps, bitrate)?,
            progress: Progress::new(),
        })
    }

    /// Retry the retained latest capture even when ScreenCaptureKit has no new
    /// dirty image. Callers still poll input and check authorization per retry.
    pub fn needs_retry(&self) -> bool {
        self.progress.recovery_since.is_some()
    }

    pub fn encode(
        &mut self,
        frame: &CapturedFrame,
        timestamp_us: u64,
        force_keyframe: bool,
    ) -> Result<Option<EncodedH264>, EncodeError> {
        self.progress
            .encode(frame, force_keyframe, Instant::now(), |force| {
                match self.hardware.encode(frame, timestamp_us, force) {
                    Err(EncodeError::Timeout) => {
                        self.hardware = self.hardware.recreate()?;
                        Err(EncodeError::Timeout)
                    }
                    result => result,
                }
            })
    }
}

struct Progress {
    damage: DamageTracker,
    recovery_since: Option<Instant>,
    has_video: bool,
}
impl Progress {
    fn new() -> Self {
        Self {
            damage: DamageTracker::new(32).expect("fixed valid damage tile size"),
            recovery_since: None,
            has_video: false,
        }
    }

    fn encode(
        &mut self,
        frame: &CapturedFrame,
        requested_keyframe: bool,
        now: Instant,
        encode: impl FnOnce(bool) -> Result<EncodedH264, EncodeError>,
    ) -> Result<Option<EncodedH264>, EncodeError> {
        if self
            .recovery_since
            .is_some_and(|start| now.saturating_duration_since(start) >= RECOVERY_LIMIT)
        {
            return Err(EncodeError::Stalled);
        }
        // VideoToolbox predictive units always encode the complete image.
        // Dirty tiles suppress redundant work, never crop an inter-frame unit.
        let force = requested_keyframe || !self.has_video || self.recovery_since.is_some();
        if self.damage.update(&frame.frame()).is_empty() && !force {
            return Ok(None);
        }
        match encode(force) {
            Ok(encoded) if !force || encoded.keyframe => {
                self.recovery_since = None;
                self.has_video = true;
                Ok(Some(encoded))
            }
            // Do not publish a predictive frame if the requested recovery IDR
            // did not arrive. A decoder may have missed its reference picture.
            Ok(_) | Err(EncodeError::Dropped | EncodeError::Timeout) => {
                self.recovery_since.get_or_insert(now);
                Ok(None)
            }
            Err(error) => Err(error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use zeroize::Zeroizing;

    fn image(value: u8) -> CapturedFrame {
        CapturedFrame::bgra(32, 32, 128, vec![value; 32 * 32 * 4]).unwrap()
    }
    fn encoded(keyframe: bool) -> EncodedH264 {
        EncodedH264 {
            annex_b: Zeroizing::new(vec![0, 0, 0, 1, if keyframe { 0x65 } else { 0x41 }, 0x80]),
            keyframe,
        }
    }

    #[test]
    fn dropped_changed_image_is_retried_even_when_the_screen_becomes_static() {
        let mut progress = Progress::new();
        let now = Instant::now();
        assert!(
            progress
                .encode(&image(0), false, now, |force| {
                    assert!(force);
                    Ok(encoded(true))
                })
                .unwrap()
                .is_some()
        );
        assert!(
            progress
                .encode(&image(1), false, now, |force| {
                    assert!(!force);
                    Err(EncodeError::Dropped)
                })
                .unwrap()
                .is_none()
        );
        assert!(progress.recovery_since.is_some());
        assert!(
            progress
                .encode(&image(1), false, now + Duration::from_millis(33), |force| {
                    assert!(force, "unchanged pixels still need a recovery IDR");
                    Ok(encoded(true))
                })
                .unwrap()
                .is_some()
        );
        assert!(progress.recovery_since.is_none());
        assert!(
            progress
                .encode(&image(1), false, now + Duration::from_millis(66), |_| {
                    panic!("identical acknowledged image should suppress redundant encoding")
                })
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn persistent_drops_and_missing_recovery_idr_are_bounded_without_publishing_bad_references() {
        let mut progress = Progress::new();
        let frame = image(0);
        let now = Instant::now();
        assert!(
            progress
                .encode(&frame, false, now, |_| Err(EncodeError::Dropped))
                .unwrap()
                .is_none()
        );
        assert!(
            progress
                .encode(&frame, false, now + Duration::from_secs(2), |force| {
                    assert!(force);
                    Ok(encoded(false))
                })
                .unwrap()
                .is_none()
        );
        assert!(matches!(
            progress.encode(&frame, false, now + RECOVERY_LIMIT, |_| panic!(
                "deadline must precede native encode"
            )),
            Err(EncodeError::Stalled)
        ));
    }

    #[test]
    fn encoder_timeout_retries_static_screen_with_an_independent_keyframe() {
        let mut progress = Progress::new();
        let frame = image(0);
        let now = Instant::now();
        progress
            .encode(&frame, false, now, |_| Ok(encoded(true)))
            .unwrap();
        assert!(
            progress
                .encode(&frame, true, now, |_| Err(EncodeError::Timeout))
                .unwrap()
                .is_none()
        );
        assert!(
            progress
                .encode(&frame, false, now + Duration::from_millis(33), |force| {
                    assert!(force);
                    Ok(encoded(true))
                })
                .unwrap()
                .is_some()
        );
    }

    #[test]
    fn explicit_decoder_recovery_overrides_damage_suppression_and_real_errors_propagate() {
        let mut progress = Progress::new();
        let frame = image(0);
        let now = Instant::now();
        progress
            .encode(&frame, false, now, |_| Ok(encoded(true)))
            .unwrap();
        assert!(matches!(
            progress.encode(&frame, true, now, |force| {
                assert!(force);
                Err(EncodeError::Failed)
            }),
            Err(EncodeError::Failed)
        ));
    }
}
