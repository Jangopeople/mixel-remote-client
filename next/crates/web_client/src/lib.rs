//! Browser geometry and the same authenticated control cryptography as native.
//! Enrollment and user consent are external prerequisites, never inferred from
//! possession of a connection ID or an unverified signaling message.
#[cfg(target_arch = "wasm32")]
mod wasm;
#[derive(Debug, thiserror::Error, PartialEq)]
#[error("invalid video viewport")]
pub struct GeometryError;

/// Map a pointer within the *rendered video rectangle* to absolute normalized
/// desktop coordinates. The caller excludes canvas letterboxing first.
pub fn normalized_pointer(
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<(u16, u16), GeometryError> {
    if ![x, y, width, height].iter().all(|v| v.is_finite()) || width <= 0.0 || height <= 0.0 {
        return Err(GeometryError);
    }
    Ok((
        ((x / width).clamp(0.0, 1.0) * 65535.0).round() as u16,
        ((y / height).clamp(0.0, 1.0) * 65535.0).round() as u16,
    ))
}
/// Centered aspect-fit rectangle (x, y, width, height) for canvas drawing and
/// inverse pointer scaling. CSS and physical pixels must use one coordinate unit.
pub fn video_rect(
    view_width: f64,
    view_height: f64,
    stream_width: f64,
    stream_height: f64,
) -> Result<[f64; 4], GeometryError> {
    if ![view_width, view_height, stream_width, stream_height]
        .iter()
        .all(|v| v.is_finite() && *v > 0.0)
    {
        return Err(GeometryError);
    }
    let scale = (view_width / stream_width).min(view_height / stream_height);
    if !scale.is_finite() || scale <= 0.0 {
        return Err(GeometryError);
    }
    let width = (stream_width * scale).min(view_width);
    let height = (stream_height * scale).min(view_height);
    if !width.is_finite() || !height.is_finite() || width <= 0.0 || height <= 0.0 {
        return Err(GeometryError);
    }
    Ok([
        (view_width - width) / 2.0,
        (view_height - height) / 2.0,
        width,
        height,
    ])
}
