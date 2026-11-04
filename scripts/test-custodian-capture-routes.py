#!/usr/bin/env python3
"""Compile the actual custody receipt router with inert peers and no OS APIs.

This verifies protocol ordering, not native input, accounts, or hosted QA.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HEADER = r'''
#![allow(dead_code)]
use std::{collections::HashMap,sync::{Arc,atomic::{AtomicUsize,Ordering}}};
type ResultType<T> = Result<T,String>;
#[macro_export] macro_rules! anyhow { ($($t:tt)*) => { format!($($t)*) }; }
macro_rules! bail { ($($t:tt)*) => { return Err(format!($($t)*)) }; }
mod hbb_common { pub mod anyhow { pub use crate::anyhow; } }
#[derive(Clone)] struct Peer {pid:u32,born:u64}
type Reply=Arc<dyn Fn(PortableReply)->bool+Send+Sync>;
enum PortableReply {Refused(String),Block(String,String,bool)}
struct Broker {state:AtomicUsize}
'''

TESTS = r'''
fn broker()->Broker{Broker{state:AtomicUsize::new(1)}}
fn route()->RouteReply{RouteReply{peer:Peer{pid:7,born:11},role:0,reply:Arc::new(|_|true),receipts:CaptureReceipts::default()}}
fn capture(routes:&mut HashMap<String,RouteReply>,sequence:u64,broker:&Broker){
    let route=routes.entry("scope".into()).or_insert_with(route);
    assert!(route.receipts.begin(sequence));
    deliver(Response::Queued(sequence,Some(sequence)),routes,Some((sequence,"scope")),broker).unwrap();
}
#[test] fn cleaned_crossing_recapture_keeps_pending_route(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    deliver(Response::Cleaned("scope".into(),1),&mut routes,Some((2,"scope")),&broker).unwrap();
    assert!(routes.contains_key("scope"),"old receipt must not erase pending recapture");
    deliver(Response::Queued(2,Some(2)),&mut routes,Some((2,"scope")),&broker).unwrap();
    deliver(Response::Cleaned("scope".into(),2),&mut routes,None,&broker).unwrap();
    assert!(routes.is_empty());assert_eq!(broker.state.load(Ordering::SeqCst),1);
}
#[test] fn queued_reuse_keeps_one_actual_capture(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    deliver(Response::Queued(2,Some(1)),&mut routes,Some((2,"scope")),&broker).unwrap();
    deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).unwrap();assert!(routes.is_empty());
}
#[test] fn old_receipt_cannot_clear_new_capture(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).unwrap();capture(&mut routes,2,&broker);
    assert!(deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).is_err());
    assert_eq!(routes["scope"].receipts.captured,Some(2));
}
#[test] fn unknown_or_duplicate_cleanup_fails_closed(){
    let broker=broker();let mut routes=HashMap::new();
    assert!(deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).is_err());
    capture(&mut routes,1,&broker);deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).unwrap();
    assert!(deliver(Response::Cleaned("scope".into(),1),&mut routes,None,&broker).is_err());
}
#[test] fn refused_is_not_cleanup(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    deliver(Response::Refused("scope".into()),&mut routes,None,&broker).unwrap();
    assert_eq!(routes["scope"].receipts.captured,Some(1));
}
#[test] fn invalid_queue_identity_does_not_erase_pending(){
    let broker=broker();let mut routes=HashMap::from([("scope".into(),route())]);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    for (sequence,capture) in [(3,Some(3)),(2,Some(0)),(2,Some(1)),(2,Some(3))]{
        assert!(deliver(Response::Queued(sequence,capture),&mut routes,Some((2,"scope")),&broker).is_err());
        assert_eq!(routes["scope"].receipts.pending,Some(2));assert_eq!(routes["scope"].receipts.captured,None);
    }
    assert!(deliver(Response::Queued(2,Some(2)),&mut routes,None,&broker).is_err());
}
#[test] fn absent_child_capture_is_never_completed_capture(){
    let broker=broker();let mut routes=HashMap::from([("scope".into(),route())]);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    deliver(Response::Queued(2,None),&mut routes,Some((2,"scope")),&broker).unwrap();assert!(routes.is_empty());
    assert!(deliver(Response::Cleaned("scope".into(),2),&mut routes,None,&broker).is_err());
}
#[test] fn completed_old_capture_then_refused_recapture_is_bounded(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    deliver(Response::Cleaned("scope".into(),1),&mut routes,Some((2,"scope")),&broker).unwrap();
    deliver(Response::Refused("scope".into()),&mut routes,Some((2,"scope")),&broker).unwrap();
    deliver(Response::Queued(2,None),&mut routes,Some((2,"scope")),&broker).unwrap();assert!(routes.is_empty());
}
#[test] fn overlapping_request_cannot_overwrite_pending(){
    let mut receipts=CaptureReceipts::default();assert!(!receipts.begin(0));assert!(receipts.begin(1));
    assert!(!receipts.begin(2));assert_eq!(receipts.pending,Some(1));
}
#[test] fn finished_requires_actual_capture_and_pending_drain(){
    let broker=broker();let mut routes=HashMap::new();capture(&mut routes,1,&broker);
    assert!(routes.get_mut("scope").unwrap().receipts.begin(2));
    deliver(Response::Retiring,&mut routes,None,&broker).unwrap();
    assert!(deliver(Response::Finished,&mut routes,None,&broker).is_err());
    deliver(Response::Cleaned("scope".into(),1),&mut routes,Some((2,"scope")),&broker).unwrap();
    assert!(deliver(Response::Finished,&mut routes,None,&broker).is_err());
    deliver(Response::Queued(2,Some(2)),&mut routes,Some((2,"scope")),&broker).unwrap();
    assert!(deliver(Response::Finished,&mut routes,None,&broker).is_err());
    deliver(Response::Cleaned("scope".into(),2),&mut routes,None,&broker).unwrap();
    assert!(deliver(Response::Finished,&mut routes,None,&broker).unwrap());assert_eq!(broker.state.load(Ordering::SeqCst),4);
}
#[test] fn repeated_capture_orderings_leave_no_tombstones(){
    let broker=broker();let mut routes=HashMap::new();
    for first in (1..2001).step_by(2){capture(&mut routes,first,&broker);
        assert!(routes.get_mut("scope").unwrap().receipts.begin(first+1));
        deliver(Response::Cleaned("scope".into(),first),&mut routes,Some((first+1,"scope")),&broker).unwrap();
        deliver(Response::Queued(first+1,Some(first+1)),&mut routes,Some((first+1,"scope")),&broker).unwrap();
        deliver(Response::Cleaned("scope".into(),first+1),&mut routes,None,&broker).unwrap();assert!(routes.is_empty());
    }
}
'''

LEGACY = r'''
#[test] fn cleaned_crossing_recapture_keeps_pending_route(){
    let broker=Broker{state:AtomicUsize::new(1)};
    let mut routes=HashMap::from([("scope".into(),RouteReply{peer:Peer{pid:7,born:11},role:0,reply:Arc::new(|_|true)})]);
    // Production has retained the existing route for a queued Retire request.
    // The old capture completes before the new request's Queued frame arrives.
    deliver(Response::Cleaned("scope".into()),&mut routes,Some(2),&broker).unwrap();
    deliver(Response::Queued(2),&mut routes,Some(2),&broker).unwrap();
    assert!(deliver(Response::Cleaned("scope".into()),&mut routes,None,&broker).is_ok(),"recaptured cleanup must not kill the guardian");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('support-receiver-custodian-windows.rs'))
    parser.add_argument('--legacy-reproduction', action='store_true')
    args = parser.parse_args()
    raw = args.source.read_bytes()
    text = raw.decode().replace('\r\n', '\n')
    enum = next(line for line in text.splitlines() if line.startswith('enum Response {'))
    start = text.index('struct RouteReply {') if args.legacy_reproduction else text.index('// Each authenticated child capture')
    end = text.index('async fn broker_loop(', start)
    harness = HEADER + enum + '\n' + text[start:end] + (LEGACY if args.legacy_reproduction else TESTS)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'actual-router-tests.rs'
    source.write_text(harness)
    compiler = shutil.which('rustc')
    if not compiler:
        raise RuntimeError('rustc is required to run the production router tests')
    binary = output / ('actual-router-tests.exe' if os.name == 'nt' else 'actual-router-tests')
    commands = [[compiler, '--edition=2021', '--test', str(source), '-D', 'warnings', '-o', str(binary)], [str(binary), '--nocapture']]
    result = {'scope': 'actual production routing with inert peers; no Windows or input qualification', 'sourceSHA256': hashlib.sha256(raw).hexdigest(), 'legacyReproduction': args.legacy_reproduction, 'passed': False}
    for number, command in enumerate(commands):
        run = subprocess.run(command, capture_output=True, text=True)
        (output / f'{number}.stdout.log').write_text(run.stdout)
        (output / f'{number}.stderr.log').write_text(run.stderr)
        print(run.stdout, end='')
        print(run.stderr, end='')
        result[f'exit{number}'] = run.returncode
        if run.returncode:
            (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            raise SystemExit(run.returncode)
    result['passed'] = True
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
