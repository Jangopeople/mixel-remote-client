//! Pure production receipt transitions. Native sinks supply checked acceptance.
use std::{collections::HashMap, hash::Hash, time::Instant};

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct Claim { pub owner: String, pub temporary: u64 }
pub(super) struct Held<R> { pub route: R, pub claims: HashMap<Claim, Instant> }
pub(super) struct Ledger<K, A, R> {
    pub keys: HashMap<K, Held<R>>,
    aliases: HashMap<(Claim, A), K>,
}
impl<K, A, R> Default for Ledger<K, A, R> {
    fn default() -> Self { Self { keys: HashMap::new(), aliases: HashMap::new() } }
}
impl<K: Copy + Eq + Hash, A: Copy + Eq + Hash, R: Copy> Ledger<K, A, R> {
    pub fn down(&mut self, owner: Claim, alias: A, key: K, route: R, repeat: bool,
                now: Instant, mut admitted: impl FnMut() -> bool,
                mut emit: impl FnMut(R, bool) -> Result<(), String>) -> Result<(), String> {
        if !admitted() { return Err("receiver generation was revoked".into()); }
        if self.keys.len() >= 4096 && !self.keys.contains_key(&key) { return Err("native key receipt bound exceeded".into()); }
        if self.aliases.len() >= 16384 && !self.aliases.contains_key(&(owner.clone(), alias)) { return Err("native key alias bound exceeded".into()); }
        if let Some(previous) = self.aliases.get(&(owner.clone(), alias)) {
            if *previous != key { return Err("held key mapping changed before release".into()); }
        }
        if let Some(held) = self.keys.get_mut(&key) {
            if held.claims.contains_key(&owner) && repeat {
                if !admitted() { return Err("receiver generation was revoked".into()); }
                emit(held.route, true)?;
            }
            held.claims.insert(owner.clone(), now);
        } else {
            if !admitted() { return Err("receiver generation was revoked".into()); }
            emit(route, true)?;
            self.keys.insert(key, Held { route, claims: HashMap::from([(owner.clone(), now)]) });
        }
        self.aliases.insert((owner, alias), key);
        Ok(())
    }
    pub fn up(&mut self, owner: &Claim, alias: A, fallback: Option<K>,
              mut emit: impl FnMut(R, bool) -> Result<(), String>) -> Result<(), String> {
        // Prefer retained alias: keyboard-layout changes must not redirect Up.
        let Some(key) = self.aliases.get(&(owner.clone(), alias)).copied().or(fallback) else { return Ok(()); };
        let Some(held) = self.keys.get_mut(&key) else { return Ok(()); };
        if !held.claims.contains_key(owner) { return Ok(()); }
        if held.claims.len() == 1 {
            emit(held.route, false)?;
            self.keys.remove(&key);
        } else { held.claims.remove(owner); }
        self.aliases.retain(|(claim, _), physical| claim != owner || *physical != key);
        Ok(())
    }
    pub fn retire(&mut self, selected: impl Fn(&Claim, &Instant) -> bool,
                  mut emit: impl FnMut(R, bool) -> Result<(), String>) -> Vec<String> {
        let mut errors = Vec::new();
        let keys: Vec<K> = self.keys.keys().copied().collect();
        for key in keys {
            let held = self.keys.get_mut(&key).unwrap();
            let retiring: Vec<Claim> = held.claims.iter().filter(|(claim, time)| selected(claim, time)).map(|(claim, _)| claim.clone()).collect();
            if retiring.is_empty() { continue; }
            if retiring.len() == held.claims.len() {
                if let Err(error) = emit(held.route, false) { errors.push(error); continue; }
                self.keys.remove(&key);
            } else { for claim in &retiring { held.claims.remove(claim); } }
            self.aliases.retain(|(claim, _), physical| *physical != key || !retiring.contains(claim));
        }
        errors
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    type Test = Ledger<u32, u32, u32>;
    fn owner(name: &str, temporary: u64) -> Claim { Claim { owner: name.into(), temporary } }
    fn down(state: &mut Test, who: &str, alias: u32, key: u32, route: u32) {
        state.down(owner(who,0), alias,key,route,true,Instant::now(),||true,|_,_|Ok(())).unwrap();
    }
    #[test] fn failed_down_has_no_receipt_or_up() {
        let mut s=Test::default();
        assert!(s.down(owner("a",0),1,29,29,true,Instant::now(),||true,|_,_|Err("zero accepted".into())).is_err());
        let mut calls=0;s.up(&owner("a",0),1,Some(29),|_,_|{calls+=1;Ok(())}).unwrap();assert_eq!(calls,0);
    }
    #[test] fn modes_share_one_physical_claim() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);down(&mut s,"a",2,29,999);
        let mut calls=Vec::new();s.up(&owner("a",0),3,Some(29),|r,d|{calls.push((r,d));Ok(())}).unwrap();
        assert_eq!(calls,vec![(29,false)]);assert!(s.keys.is_empty());
    }
    #[test] fn two_owners_do_not_release_each_other() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);down(&mut s,"b",2,29,999);
        s.up(&owner("a",0),1,None,|_,_|panic!("shared Up")).unwrap();
        assert_eq!(s.keys[&29].claims.len(),1);s.up(&owner("b",0),2,None,|r,d|{assert_eq!((r,d),(29,false));Ok(())}).unwrap();
    }
    #[test] fn failed_up_retains_owner_and_original_route() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);
        assert!(s.up(&owner("a",0),1,None,|_,_|Err("denied".into())).is_err());
        assert!(s.keys[&29].claims.contains_key(&owner("a",0)));
        s.up(&owner("a",0),1,Some(999),|r,_|{assert_eq!(r,29);Ok(())}).unwrap();assert!(s.keys.is_empty());
    }
    #[test] fn failed_repeat_preserves_earlier_receipt() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);
        assert!(s.down(owner("a",0),1,29,29,true,Instant::now(),||true,|_,_|Err("repeat denied".into())).is_err());
        assert!(s.keys[&29].claims.contains_key(&owner("a",0)));
    }
    #[test] fn layout_change_refused_before_second_down() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);
        assert!(s.down(owner("a",0),1,30,30,true,Instant::now(),||true,|_,_|panic!("mapping drift injected")).is_err());
    }
    #[test] fn pulse_preserves_direct_hold() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);
        s.down(owner("a",7),1,29,29,false,Instant::now(),||true,|_,_|panic!("pulse while held")).unwrap();
        s.up(&owner("a",7),1,None,|_,_|panic!("pulse released direct hold")).unwrap();assert!(s.keys[&29].claims.contains_key(&owner("a",0)));
    }
    #[test] fn temporary_modifier_preserves_other_owner() {
        let mut s=Test::default();down(&mut s,"a",1,42,42);
        s.down(owner("b",7),2,42,42,false,Instant::now(),||true,|_,_|panic!("duplicate modifier")).unwrap();
        assert!(s.retire(|c,_|c.owner=="b",|_,_|panic!("shared modifier Up")).is_empty());
        assert!(s.keys[&42].claims.contains_key(&owner("a",0)));
    }
    #[test] fn retire_attempts_others_and_retains_failed_up() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);down(&mut s,"a",2,42,42);
        let mut calls=Vec::new();let errors=s.retire(|c,_|c.owner=="a",|r,_|{calls.push(r);if r==29{Err("failed".into())}else{Ok(())}});
        calls.sort();assert_eq!(calls,vec![29,42]);assert_eq!(errors.len(),1);assert!(s.keys.contains_key(&29));assert!(!s.keys.contains_key(&42));
    }
    #[test] fn timeout_is_per_claim() {
        let mut s=Test::default();let base=Instant::now();
        let now=base+Duration::from_secs(400);
        s.down(owner("a",0),1,29,29,true,base,||true,|_,_|Ok(())).unwrap();
        s.down(owner("b",0),2,29,29,true,now,||true,|_,_|Ok(())).unwrap();
        s.retire(|_,t|now.duration_since(*t)>Duration::from_secs(360),|_,_|panic!("fresh owner released"));
        assert!(!s.keys[&29].claims.contains_key(&owner("a",0)));assert!(s.keys[&29].claims.contains_key(&owner("b",0)));
    }
    #[test] fn denied_generation_does_not_emit() {
        let mut s=Test::default();assert!(s.down(owner("a",0),1,29,29,true,Instant::now(),||false,|_,_|panic!("revoked Down")).is_err());assert!(s.keys.is_empty());
    }
    #[test] fn deny_between_checks_prevents_native_primitive() {
        let mut s=Test::default();let mut checks=0;
        assert!(s.down(owner("a",0),1,29,29,true,Instant::now(),||{checks+=1;checks==1},|_,_|panic!("late denied primitive")).is_err());
    }
    #[test] fn cleanup_is_allowed_after_deny() {
        let mut s=Test::default();down(&mut s,"a",1,29,29);let mut calls=0;
        assert!(s.retire(|_,_|true,|_,d|{assert!(!d);calls+=1;Ok(())}).is_empty());assert_eq!(calls,1);
    }
    #[test] fn right_and_left_sides_are_distinct() {
        let mut s=Test::default();down(&mut s,"a",1,0x1d,0x1d);down(&mut s,"a",2,0xe01d,0xe01d);
        s.up(&owner("a",0),1,None,|_,_|Ok(())).unwrap();assert!(s.keys.contains_key(&0xe01d));
    }
}
