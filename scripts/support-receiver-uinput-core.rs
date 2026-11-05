//! Successful actual-device claims. The caller supplies the final sink and deny check.
use std::{collections::{HashMap,HashSet,VecDeque}, time::{Duration, Instant}};

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct Claim { pub owner: String, pub temporary: u64 }
struct Held { stamp:u64, claims: HashMap<Claim, Instant> }
#[derive(Default)]
pub(super) struct Ledger {
    keys: HashMap<u16, Held>,
    aliases: HashMap<(Claim, String), u16>,
    pending:VecDeque<(u16,u64)>,
    next_stamp:u64,
}
impl Ledger {
    pub fn down(&mut self, claim: Claim, alias: &str, key: u16, repeat: bool,
        mut admitted: impl FnMut() -> bool, mut emit: impl FnMut(u16, bool) -> Result<(), String>) -> Result<(), String> {
        if alias.len() > 160 || !admitted() { return Err("uinput generation is denied".into()); }
        if self.keys.len() >= 4096 && !self.keys.contains_key(&key) { return Err("uinput native key bound exceeded".into()); }
        let alias_key = (claim.clone(), alias.to_owned());
        if self.aliases.len() >= 16384 && !self.aliases.contains_key(&alias_key) { return Err("uinput alias bound exceeded".into()); }
        if self.aliases.get(&alias_key).is_some_and(|previous| *previous != key) { return Err("uinput held mapping changed".into()); }
        if let Some(held) = self.keys.get_mut(&key) {
            if repeat && held.claims.contains_key(&claim) {
                if !admitted() { return Err("uinput generation is denied".into()); }
                emit(key, true)?;
            }
            held.claims.insert(claim, Instant::now());
        } else {
            if self.pending.len()>=8192 {return Err("uinput receipt scan bound exceeded".into());}
            let stamp=self.next_stamp.checked_add(1).ok_or("uinput receipt identity exhausted")?;
            if !admitted() { return Err("uinput generation is denied".into()); }
            emit(key, true)?;
            self.keys.insert(key, Held { stamp, claims: HashMap::from([(claim, Instant::now())]) });
            self.next_stamp=stamp;self.pending.push_back((key,stamp));
        }
        self.aliases.insert(alias_key, key);
        Ok(())
    }
    pub fn up(&mut self, claim: &Claim, alias: &str, fallback: Option<u16>,
        mut emit: impl FnMut(u16, bool) -> Result<(), String>) -> Result<(), String> {
        let Some(key) = self.aliases.get(&(claim.clone(), alias.to_owned())).copied().or(fallback) else { return Ok(()); };
        let Some(held) = self.keys.get_mut(&key) else { return Ok(()); };
        if !held.claims.contains_key(claim) { return Ok(()); }
        if held.claims.len() == 1 { emit(key, false)?; self.keys.remove(&key); }
        else { held.claims.remove(claim); }
        self.aliases.retain(|(owner, _), physical| owner != claim || *physical != key);
        Ok(())
    }
    pub fn retire(&mut self, mut selected: impl FnMut(&Claim, Instant) -> bool,
        mut emit: impl FnMut(u16, bool) -> Result<(), String>) -> bool {
        let mut complete = true;
        let keys: Vec<_> = self.keys.keys().copied().collect();
        for key in keys {
            let held = self.keys.get_mut(&key).unwrap();
            let retiring: Vec<_> = held.claims.iter().filter(|(owner, time)| selected(owner, **time)).map(|(owner, _)| owner.clone()).collect();
            if retiring.is_empty() { continue; }
            if retiring.len() == held.claims.len() {
                if emit(key, false).is_err() { complete = false; continue; }
                self.keys.remove(&key);
            } else { for claim in &retiring { held.claims.remove(claim); } }
            self.aliases.retain(|(owner, _), physical| *physical != key || !retiring.contains(owner));
        }
        complete
    }
    /// Scan bounded stable receipt instances. Stale queue entries never alias
    /// a later Down of the same physical key and count against the work budget.
    pub fn retire_some(&mut self, maximum:usize,mut selected:impl FnMut(&Claim,Instant)->bool,
        mut emit:impl FnMut(u16,bool)->Result<(),String>)->bool {
        let count=maximum.min(self.pending.len());let mut complete=true;
        for _ in 0..count {
            let (key,stamp)=self.pending.pop_front().unwrap();
            let Some(held)=self.keys.get_mut(&key).filter(|held|held.stamp==stamp) else {continue;};
            let retiring:Vec<_>=held.claims.iter().filter(|(claim,time)|selected(claim,**time)).map(|(claim,_)|claim.clone()).collect();
            if !retiring.is_empty() {
                if retiring.len()==held.claims.len() {
                    if emit(key,false).is_err(){complete=false;}else{
                        self.keys.remove(&key);
                        self.aliases.retain(|(claim,_),physical|*physical!=key || !retiring.contains(claim));
                    }
                }else{
                    for claim in &retiring {held.claims.remove(claim);}
                    self.aliases.retain(|(claim,_),physical|*physical!=key || !retiring.contains(claim));
                }
            }
            if self.keys.get(&key).is_some_and(|held|held.stamp==stamp){self.pending.push_back((key,stamp));}
        }
        complete
    }
    pub fn held(&self, owner: &str) -> bool { self.keys.values().any(|held| held.claims.keys().any(|claim| claim.owner == owner)) }
    pub fn pressed(&self, key: u16) -> bool { self.keys.contains_key(&key) }
    pub fn pressed_by(&self, key:u16, owner:&str)->bool { self.keys.get(&key).is_some_and(|held|held.claims.keys().any(|claim|claim.owner==owner)) }
    pub fn empty(&self) -> bool { self.keys.is_empty() }
    pub fn expired(time: Instant) -> bool { time.elapsed() >= Duration::from_secs(360) }
}

/// No registry limit can evict an active or failed-final-Up receipt.
pub(super) fn reap_retired<T>(caps:&mut HashMap<String,T>,ledger:&Ledger,mut retired:impl FnMut(&T)->bool) {
    caps.retain(|owner,cap|!retired(cap) || ledger.held(owner));
}

/// This is only an RPC optimization. Forgetting a binding must re-Bind the same
/// authenticated capability; it never deletes service claims or changes identity.
pub(super) fn binding_known(bound:&mut HashSet<String>,nonce:&str)->bool {
    if bound.contains(nonce) {return true;}
    if bound.len()>=512 {bound.clear();}
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    fn who(owner: &str, temporary: u64) -> Claim { Claim { owner: owner.into(), temporary } }
    fn press(state: &mut Ledger, owner: &str, temporary: u64, key: u16) {
        state.down(who(owner,temporary),"key",key,false,||true,|_,_|Ok(())).unwrap();
    }
    #[test] fn every_owner_reaches_authoritative_service() {
        let mut state = Ledger::default(); let mut native = Vec::new();
        for owner in ["A","B"] { state.down(who(owner,0),"legacy",29,true,||true,|key,down|{native.push((key,down));Ok(())}).unwrap(); }
        assert_eq!(native,[(29,true)]);
        assert!(state.retire(|owner,_| owner.owner=="A",|_,_|panic!("A released B")));
        assert!(state.held("B"));
        assert!(state.retire(|owner,_| owner.owner=="B",|key,down|{native.push((key,down));Ok(())}));
        assert_eq!(native,[(29,true),(29,false)]);
    }
    #[test] fn modifier_ownership_query_cannot_borrow_other_owner() {
        let mut state=Ledger::default();press(&mut state,"A",0,42);
        assert!(state.pressed_by(42,"A"));assert!(!state.pressed_by(42,"B"));
        press(&mut state,"B",9,42);assert!(state.pressed_by(42,"B"));
    }
    #[test] fn binding_cache_is_bounded_and_forgetting_preserves_claims() {
        let mut bound=HashSet::new();let mut state=Ledger::default();press(&mut state,"active",0,29);
        for index in 0..5000 {
            let nonce=format!("generation-{}",index);assert!(!binding_known(&mut bound,&nonce));bound.insert(nonce.clone());
            assert!(binding_known(&mut bound,&nonce));assert!(bound.len()<=512);assert!(state.held("active"));
        }
    }
    #[test] fn reaper_preserves_active_and_failed_up_then_reclaims_completed() {
        let mut state=Ledger::default();press(&mut state,"active",0,29);
        let mut caps=HashMap::from([("active".to_owned(),true),("completed".to_owned(),true),("live".to_owned(),false)]);
        reap_retired(&mut caps,&state,|retired|*retired);assert!(caps.contains_key("active"));assert!(caps.contains_key("live"));assert!(!caps.contains_key("completed"));
        assert!(!state.retire(|_,_|true,|_,_|Err("Up failed".into())));reap_retired(&mut caps,&state,|retired|*retired);assert!(caps.contains_key("active"));
        assert!(state.retire(|_,_|true,|_,_|Ok(())));reap_retired(&mut caps,&state,|retired|*retired);assert!(!caps.contains_key("active"));
    }
    #[test] fn bounded_cleanup_visits_receipts_fairly_and_retains_failed_up() {
        let mut state=Ledger::default();for key in 1..=300 {state.down(who("A",0),&format!("key-{}",key),key,false,||true,|_,_|Ok(())).unwrap();}
        let mut all=Vec::new();
        for _ in 0..3 {
            let mut this=Vec::new();state.retire_some(128,|_,_|true,|key,_|{this.push(key);if key==1 {Err("failed final Up".into())}else{Ok(())}});
            assert!(this.len()<=128);all.extend(this);
        }
        assert!((2..=300).all(|key|all.contains(&key)));assert!(state.pressed(1));
        state.retire_some(128,|_,_|true,|_,_|Ok(()));assert!(state.empty());
    }
    #[test] fn stale_cleanup_entry_never_retires_new_key_instance_twice() {
        let mut state=Ledger::default();press(&mut state,"A",0,29);state.up(&who("A",0),"key",Some(29),|_,_|Ok(())).unwrap();press(&mut state,"B",0,29);
        let mut calls=0;state.retire_some(1,|_,_|true,|_,_|{calls+=1;Ok(())});assert_eq!(calls,0);assert!(state.held("B"));
        state.retire_some(1,|_,_|true,|_,_|{calls+=1;Ok(())});assert_eq!(calls,1);assert!(state.empty());
    }
    #[test] fn failed_actual_down_never_becomes_receipt() {
        let mut state=Ledger::default();
        assert!(state.down(who("A",0),"map",29,true,||true,|_,_|Err("write failed".into())).is_err());
        state.up(&who("A",0),"map",Some(29),|_,_|panic!("unowned Up")).unwrap();
        assert!(state.empty());
    }
    #[test] fn failed_actual_up_retains_until_retry() {
        let mut state=Ledger::default();press(&mut state,"A",0,29);
        assert!(!state.retire(|_,_|true,|_,_|Err("write failed".into())));assert!(state.held("A"));
        assert!(state.retire(|_,_|true,|key,down|{assert_eq!((key,down),(29,false));Ok(())}));assert!(!state.held("A"));
    }
    #[test] fn pending_fifo_down_denied_at_actual_effect() {
        let mut state=Ledger::default();let mut check=0;
        assert!(state.down(who("A",0),"map",29,true,||{check+=1;check==1},|_,_|panic!("post-revoke effect")).is_err());
        assert!(state.empty());
    }
    #[test] fn pulse_and_cross_mode_alias_preserve_direct_hold() {
        let mut state=Ledger::default();press(&mut state,"A",0,42);
        state.down(who("B",17),"text",42,false,||true,|_,_|panic!("shared pulse Down")).unwrap();
        state.up(&who("B",17),"new-layout",Some(42),|_,_|panic!("pulse released A")).unwrap();
        assert!(state.pressed(42));state.up(&who("A",0),"new-mode",Some(42),|_,_|Ok(())).unwrap();assert!(state.empty());
    }
    #[test] fn failed_release_keeps_original_alias_mapping() {
        let mut state=Ledger::default();press(&mut state,"A",0,29);
        assert!(state.up(&who("A",0),"key",Some(30),|_,_|Err("blocked".into())).is_err());
        state.up(&who("A",0),"key",Some(30),|key,_|{assert_eq!(key,29);Ok(())}).unwrap();assert!(state.empty());
    }
    #[test] fn denied_owner_cleanup_is_allowed_and_never_global() {
        let mut state=Ledger::default();press(&mut state,"A",0,29);press(&mut state,"B",0,42);
        state.up(&who("unknown",0),"key",Some(42),|_,_|panic!("foreign Up")).unwrap();
        let mut keys=Vec::new();assert!(state.retire(|owner,_|owner.owner=="A",|key,down|{keys.push((key,down));Ok(())}));
        assert_eq!(keys,[(29,false)]);assert!(state.held("B"));
    }
    #[test] fn cleanup_attempts_other_receipts_after_failure() {
        let mut state=Ledger::default();press(&mut state,"A",0,29);state.down(who("A",0),"other",42,false,||true,|_,_|Ok(())).unwrap();
        let mut attempted=Vec::new();assert!(!state.retire(|_,_|true,|key,_|{attempted.push(key);if key==29 {Err("failure".into())}else{Ok(())}}));
        attempted.sort();assert_eq!(attempted,[29,42]);assert!(state.pressed(29));assert!(!state.pressed(42));
    }
}
