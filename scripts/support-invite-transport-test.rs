//! Mock only the low-level transport; the generated FFI request body is inserted below.
use std::cell::RefCell;
use std::future::Future;
use std::sync::Arc;
use std::task::{Context, Poll, Wake, Waker};

type ResultType<T> = Result<T, &'static str>;
#[derive(Clone, Default)]
struct Trace {
    tls_type: Option<u8>,
    accept_invalid: Option<bool>,
    original_accept_invalid: Option<bool>,
    tls_url: String,
    timeouts: Vec<u64>,
    body: Option<String>,
    header: String,
    response_error: bool,
    body_error: bool,
    deadline_error: bool,
}
thread_local! {
    static TRACE: RefCell<Trace> = RefCell::new(Trace::default());
}
struct Config;
impl Config {
    fn get_socks() {}
}
fn get_url_for_tls<'a>(_url: &'a str, _proxy: &()) -> &'a str {
    "https://proxy.example.invalid"
}
fn get_cached_tls_type(_url: &str) -> Option<u8> {
    Some(7)
}
fn get_cached_tls_accept_invalid_cert(_url: &str) -> Option<bool> {
    Some(true)
}
struct Response;
impl Response {
    async fn text(self) -> ResultType<String> {
        TRACE.with(|trace| {
            if trace.borrow().body_error {
                Err("synthetic body error")
            } else {
                Ok("response-body".to_owned())
            }
        })
    }
}
async fn get_http_response_async(
    _url: &str,
    tls_url: &str,
    _method: &str,
    body: Option<String>,
    header: &str,
    tls_type: Option<u8>,
    accept_invalid: Option<bool>,
    original_accept_invalid: Option<bool>,
) -> ResultType<Response> {
    TRACE.with(|trace| {
        let mut trace = trace.borrow_mut();
        trace.tls_type = tls_type;
        trace.accept_invalid = accept_invalid;
        trace.original_accept_invalid = original_accept_invalid;
        trace.tls_url = tls_url.to_owned();
        trace.body = body;
        trace.header = header.to_owned();
    });
    TRACE.with(|trace| {
        if trace.borrow().response_error {
            Err("synthetic response error")
        } else {
            Ok(Response)
        }
    })
}
async fn timeout<F: Future>(millis: u64, future: F) -> ResultType<F::Output> {
    TRACE.with(|trace| trace.borrow_mut().timeouts.push(millis));
    if TRACE.with(|trace| trace.borrow().deadline_error) {
        return Err("synthetic deadline error");
    }
    Ok(future.await)
}
struct NoopWake;
impl Wake for NoopWake {
    fn wake(self: Arc<Self>) {}
}
fn run<F: Future>(future: F) -> F::Output {
    let waker = Waker::from(Arc::new(NoopWake));
    let mut context = Context::from_waker(&waker);
    let mut future = Box::pin(future);
    loop {
        match future.as_mut().poll(&mut context) {
            Poll::Ready(result) => return result,
            Poll::Pending => std::thread::yield_now(),
        }
    }
}

// GENERATED_HTTP_REQUEST_SYNC

#[test]
fn generated_support_transport_requires_valid_tls_and_bounded_header_and_body() {
    let body = Some("synthetic-body".to_owned());
    let result = run(http_request_sync(
        "https://rs.mixel.ch/api/presence/client?request=nonce1".to_owned(),
        "POST".to_owned(),
        body.clone(),
        "synthetic-header".to_owned(),
    ));
    assert_eq!(result, Ok("response-body".to_owned()));
    TRACE.with(|trace| {
        let trace = trace.borrow();
        assert_eq!(trace.accept_invalid, Some(false));
        assert_eq!(trace.original_accept_invalid, Some(false));
        assert_eq!(trace.tls_type, Some(7));
        assert_eq!(trace.tls_url, "https://proxy.example.invalid");
        assert_eq!(trace.timeouts, [6_000]);
        assert_eq!(trace.body, body);
        assert_eq!(trace.header, "synthetic-header");
    });
}

#[test]
fn generated_support_transport_propagates_header_body_and_total_deadline_errors() {
    for error in ["response", "body", "deadline"] {
        TRACE.with(|trace| {
            *trace.borrow_mut() = Trace {
                response_error: error == "response",
                body_error: error == "body",
                deadline_error: error == "deadline",
                ..Trace::default()
            };
        });
        let result = run(http_request_sync(
            "https://rs.mixel.ch/api/presence/client?request=nonce2".to_owned(),
            "POST".to_owned(),
            Some("synthetic-body".to_owned()),
            "synthetic-header".to_owned(),
        ));
        assert_eq!(
            result,
            Err(match error {
                "response" => "synthetic response error",
                "body" => "synthetic body error",
                _ => "synthetic deadline error",
            })
        );
        TRACE.with(|trace| assert_eq!(trace.borrow().timeouts, [6_000]));
    }
}
#[test]
fn unrelated_generated_transport_keeps_cached_tls_behavior() {
    assert!(run(http_request_sync(
        "https://example.invalid/generic".to_owned(),
        "GET".to_owned(),
        None,
        String::new(),
    ))
    .is_ok());
    TRACE.with(|trace| {
        let trace = trace.borrow();
        assert_eq!(trace.accept_invalid, Some(true));
        assert_eq!(trace.original_accept_invalid, Some(true));
        assert_eq!(trace.tls_type, Some(7));
        assert!(trace.timeouts.is_empty());
    });
}
