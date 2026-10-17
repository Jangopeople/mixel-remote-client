//! Actual production send observer, real Tokio timers, counted slow cleanup sink.
#![allow(dead_code)]
use std::time::{Duration,Instant};
const IPC_REQUEST_TIMEOUT:u64=1000;
struct Data;
struct Connection {delay:Duration}
impl Connection {async fn send(&mut self,_:&Data)->Result<(),()>{tokio::time::sleep(self.delay).await;Ok(())}}
struct Sink {delay:Duration,slices:usize}
impl Sink {
    fn sweep(&mut self,_:u64,mut emit:impl FnMut(u16,bool)->Result<(),String>,mut flush:impl FnMut()->Result<(),String>)->bool {
        self.slices+=1;assert!(self.slices<=100,"cleanup catch-up starved transport/deadline");
        std::thread::sleep(self.delay);emit(29,false).unwrap();flush().unwrap();true
    }
}
#[cfg(not(feature="original-timer"))]
include!("send-observed.rs");
#[cfg(feature="original-timer")]
include!("send-observed-original.rs");
#[tokio::main(flavor="current_thread")]
async fn main(){
    let mut state=Sink{delay:Duration::from_millis(40),slices:0};let mut stream=Connection{delay:Duration::from_secs(60)};
    let mut timer=tokio::time::interval(Duration::from_millis(10));timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let started=Instant::now();assert!(!send_observed(&mut stream,&Data,&mut state,&mut timer,1,|_,_|Ok(()),||Ok(())).await);
    assert!(started.elapsed()<Duration::from_millis(1500));assert!(state.slices<40);println!("PASS real-Tokio expired-deadline wins over slow cleanup slices");
    let mut state=Sink{delay:Duration::from_millis(30),slices:0};let mut stream=Connection{delay:Duration::from_millis(45)};
    let mut timer=tokio::time::interval(Duration::from_millis(10));timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let started=Instant::now();assert!(send_observed(&mut stream,&Data,&mut state,&mut timer,1,|_,_|Ok(()),||Ok(())).await);
    assert!(started.elapsed()<Duration::from_millis(500));assert!(state.slices<10);println!("PASS real-Tokio ready transport progresses after missed ticks");
}
