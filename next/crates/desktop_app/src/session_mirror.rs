//! A second technician sees the same picture as the first one. The first technician's media
//! task captures and encodes the screen once; it hands every encoded picture to this mirror, and
//! the media task of each colleague who joined sends those pictures on its own connection.
//! Nothing is captured twice, and a joined colleague never changes what is captured.
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;
use tokio::sync::broadcast;

/// One encoded picture (H.264 Annex B) as the first technician received it.
pub struct Picture {
    pub annex_b: Vec<u8>,
    pub keyframe: bool,
    pub duration: Duration,
}

struct Hub {
    pictures: broadcast::Sender<Arc<Picture>>,
    keyframe: AtomicBool,
    closed: AtomicBool,
}

static CURRENT: Mutex<Option<Arc<Hub>>> = Mutex::new(None);

/// Held by the first technician's media task while it shares the screen.
pub struct Publisher(Arc<Hub>);

/// Starts mirroring the pictures of the session that is shared now.
pub fn publish() -> Publisher {
    let (pictures, _) = broadcast::channel(8);
    let hub = Arc::new(Hub { pictures, keyframe: AtomicBool::new(false), closed: AtomicBool::new(false) });
    if let Ok(mut current) = CURRENT.lock()
        && let Some(previous) = current.replace(hub.clone())
    {
        previous.closed.store(true, Ordering::Release);
    }
    Publisher(hub)
}

impl Publisher {
    /// Hands a sent picture to every joined colleague (copied only when someone watches).
    pub fn send(&self, annex_b: &[u8], duration: Duration) {
        if self.0.pictures.receiver_count() == 0 {
            return;
        }
        let picture = Picture { annex_b: annex_b.to_vec(), keyframe: is_keyframe(annex_b), duration };
        let _ = self.0.pictures.send(Arc::new(picture));
    }
    /// A colleague joined or lost pictures and needs a complete one.
    pub fn take_keyframe_request(&self) -> bool {
        self.0.keyframe.swap(false, Ordering::AcqRel)
    }
}

impl Drop for Publisher {
    fn drop(&mut self) {
        self.0.closed.store(true, Ordering::Release);
        if let Ok(mut current) = CURRENT.lock()
            && current.as_ref().is_some_and(|hub| Arc::ptr_eq(hub, &self.0))
        {
            *current = None;
        }
    }
}

/// Held by a joined colleague's media task.
pub struct Viewer {
    hub: Arc<Hub>,
    pictures: broadcast::Receiver<Arc<Picture>>,
    waiting_for_key: bool,
}

/// Attaches to the pictures of the session shared now; `None` when nothing is shared.
pub fn watch() -> Option<Viewer> {
    let hub = CURRENT.lock().ok()?.clone()?;
    if hub.closed.load(Ordering::Acquire) {
        return None;
    }
    let pictures = hub.pictures.subscribe();
    hub.keyframe.store(true, Ordering::Release);
    Some(Viewer { hub, pictures, waiting_for_key: true })
}

pub enum Next {
    Picture(Arc<Picture>),
    /// Nothing new within the wait.
    Idle,
    /// The first technician's session ended.
    Ended,
}

impl Viewer {
    /// The next picture to send; pictures before the first complete one are skipped.
    pub async fn next(&mut self, wait: Duration) -> Next {
        loop {
            if self.hub.closed.load(Ordering::Acquire) {
                return Next::Ended;
            }
            match tokio::time::timeout(wait, self.pictures.recv()).await {
                Err(_) => return Next::Idle,
                Ok(Err(broadcast::error::RecvError::Closed)) => return Next::Ended,
                Ok(Err(broadcast::error::RecvError::Lagged(_))) => {
                    // Pictures were dropped: the next one cannot be decoded without a key picture.
                    self.request_keyframe();
                }
                Ok(Ok(picture)) => {
                    if self.waiting_for_key && !picture.keyframe {
                        self.hub.keyframe.store(true, Ordering::Release);
                        continue;
                    }
                    self.waiting_for_key = false;
                    return Next::Picture(picture);
                }
            }
        }
    }
    /// The colleague's app asked for a complete picture.
    pub fn request_keyframe(&mut self) {
        self.waiting_for_key = true;
        self.hub.keyframe.store(true, Ordering::Release);
    }
}

/// Whether an Annex B access unit starts a decodable picture (IDR slice or parameter sets).
pub fn is_keyframe(annex_b: &[u8]) -> bool {
    let mut index = 0;
    while index + 3 < annex_b.len() {
        let start = if annex_b[index..].starts_with(&[0, 0, 1]) {
            3
        } else if annex_b[index..].starts_with(&[0, 0, 0, 1]) {
            4
        } else {
            index += 1;
            continue;
        };
        if let Some(header) = annex_b.get(index + start) {
            let kind = header & 0x1f;
            if kind == 5 || kind == 7 {
                return true;
            }
        }
        index += start;
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    const IDR: &[u8] = &[0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x65, 9];
    const DELTA: &[u8] = &[0, 0, 0, 1, 0x41, 7, 7];

    #[test]
    fn key_pictures_are_recognised() {
        assert!(is_keyframe(IDR));
        assert!(is_keyframe(&[0, 0, 1, 0x65, 1]));
        assert!(!is_keyframe(DELTA));
        assert!(!is_keyframe(&[]));
        assert!(!is_keyframe(&[0, 0, 1]));
    }

    #[tokio::test]
    async fn a_joined_viewer_starts_at_a_key_picture_and_ends_with_the_first_session() {
        let publisher = publish();
        // Nobody watches: nothing is copied and no picture is asked for.
        publisher.send(DELTA, Duration::from_millis(30));
        assert!(!publisher.take_keyframe_request());
        let mut viewer = watch().expect("a shared session can be watched");
        // Joining asks the first session for a complete picture.
        assert!(publisher.take_keyframe_request());
        publisher.send(DELTA, Duration::from_millis(30));
        publisher.send(IDR, Duration::from_millis(30));
        publisher.send(DELTA, Duration::from_millis(30));
        let Next::Picture(first) = viewer.next(Duration::from_millis(50)).await else { panic!("key picture expected") };
        assert!(first.keyframe);
        // The skipped delta picture asked again for a key picture.
        assert!(publisher.take_keyframe_request());
        let Next::Picture(second) = viewer.next(Duration::from_millis(50)).await else { panic!("delta picture expected") };
        assert!(!second.keyframe);
        assert!(matches!(viewer.next(Duration::from_millis(10)).await, Next::Idle));
        viewer.request_keyframe();
        assert!(publisher.take_keyframe_request());
        drop(publisher);
        assert!(matches!(viewer.next(Duration::from_millis(10)).await, Next::Ended));
        assert!(watch().is_none());
    }
}
