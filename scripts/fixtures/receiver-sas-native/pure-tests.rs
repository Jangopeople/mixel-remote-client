
#[cfg(test)]
mod pure_decision_controls {
use super::*;
fn valid() -> Authority { Authority { pipe_pid:Some(10),launched_pid:10,born:20,owner:Some((10,20)),publisher_session:Some(3),token_session:Some(3),target_session:3,service_session:Some(0),service_system:true,publisher_system:true,image_match:true,process_alive:true } }
#[test] fn current_authority() { assert!(authority_eligible(&valid())); }
#[test] fn wrong_peer_birth_or_owner() { for pair in [None,Some((11,20)),Some((10,21))] { let mut a=valid();a.owner=pair;assert!(!authority_eligible(&a)); } let mut a=valid();a.pipe_pid=Some(99);assert!(!authority_eligible(&a)); }
#[test] fn missing_kernel_queries_fail_closed() { let mut a=valid();a.pipe_pid=None;assert!(!authority_eligible(&a));let mut a=valid();a.publisher_session=None;assert!(!authority_eligible(&a));let mut a=valid();a.token_session=None;assert!(!authority_eligible(&a));let mut a=valid();a.service_session=None;assert!(!authority_eligible(&a)); }
#[test] fn wrong_session_or_authority() { let mut a=valid();a.target_session=0;assert!(!authority_eligible(&a));let mut a=valid();a.target_session=u32::MAX;assert!(!authority_eligible(&a));let mut a=valid();a.token_session=Some(4);assert!(!authority_eligible(&a));let mut a=valid();a.publisher_system=false;assert!(!authority_eligible(&a));let mut a=valid();a.service_system=false;assert!(!authority_eligible(&a));let mut a=valid();a.image_match=false;assert!(!authority_eligible(&a));let mut a=valid();a.process_alive=false;assert!(!authority_eligible(&a)); }
#[test] fn services_policy_only() { for value in [None,Some(0),Some(2),Some(4),Some(u32::MAX)] { assert!(!policy_eligible(value)); } assert!(policy_eligible(Some(1)));assert!(policy_eligible(Some(3))); }
#[test] fn exact_owner_identity() { assert_eq!(identity("10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),Some((10,20)));for value in ["0-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-0-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","+10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","010-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-020-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-20-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","10-20-aaaa","10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-extra"] { assert_eq!(identity(value),None); } }
#[test] fn replays_and_sequence_exhaustion() { let mut seen=(0,0,0);assert!(reserve(&mut seen,10,20,1));assert!(!reserve(&mut seen,10,20,1));assert!(!reserve(&mut seen,10,20,0));assert!(reserve(&mut seen,10,20,2));assert!(!reserve(&mut seen,10,20,1));assert!(reserve(&mut seen,10,21,1));assert!(reserve(&mut seen,10,21,u64::MAX));assert!(!reserve(&mut seen,10,21,u64::MAX));let before=seen;assert!(!reserve(&mut seen,0,1,9));assert_eq!(seen,before); }

}
