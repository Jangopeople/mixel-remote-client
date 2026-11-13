//! Keys travel as USB HID usages (page 7), the same numbers the desktop app sends. GTK reports the
//! hardware key code, which on X11 and Wayland is the Linux evdev code plus 8: a key position,
//! independent of the layout, so the remote computer's own layout applies, as with a keyboard
//! plugged into it.

/// Linux evdev key code → HID usage.
const EVDEV: &[(u16, u16)] = &[
    (1, 41), (2, 30), (3, 31), (4, 32), (5, 33), (6, 34), (7, 35), (8, 36), (9, 37), (10, 38), (11, 39),
    (12, 45), (13, 46), (14, 42), (15, 43),
    (16, 20), (17, 26), (18, 8), (19, 21), (20, 23), (21, 28), (22, 24), (23, 12), (24, 18), (25, 19),
    (26, 47), (27, 48), (28, 40), (29, 224),
    (30, 4), (31, 22), (32, 7), (33, 9), (34, 10), (35, 11), (36, 13), (37, 14), (38, 15),
    (39, 51), (40, 52), (41, 53), (42, 225), (43, 49),
    (44, 29), (45, 27), (46, 6), (47, 25), (48, 5), (49, 17), (50, 16), (51, 54), (52, 55), (53, 56), (54, 229),
    (55, 85), (56, 226), (57, 44), (58, 57),
    (59, 58), (60, 59), (61, 60), (62, 61), (63, 62), (64, 63), (65, 64), (66, 65), (67, 66), (68, 67),
    (69, 83), (70, 71),
    (71, 95), (72, 96), (73, 97), (74, 86), (75, 92), (76, 93), (77, 94), (78, 87), (79, 89), (80, 90), (81, 91), (82, 98), (83, 99),
    (86, 100), (87, 68), (88, 69), (89, 135), (92, 138), (93, 136), (94, 139),
    (96, 88), (97, 228), (98, 84), (99, 70), (100, 230),
    (102, 74), (103, 82), (104, 75), (105, 80), (106, 79), (107, 77), (108, 81), (109, 78), (110, 73), (111, 76),
    (113, 127), (114, 129), (115, 128), (117, 103), (119, 72), (121, 133), (124, 137),
    (125, 227), (126, 231), (127, 101),
    (183, 104), (184, 105), (185, 106), (186, 107), (187, 108), (188, 109), (189, 110), (190, 111), (191, 112), (192, 113), (193, 114), (194, 115),
];

/// The HID usage of a GTK hardware key code (X11/Wayland: evdev + 8).
pub fn usage(hardware_keycode: u32) -> Option<u16> {
    let evdev = u16::try_from(hardware_keycode.checked_sub(8)?).ok()?;
    EVDEV.iter().find(|(code, _)| *code == evdev).map(|(_, usage)| *usage)
}

#[cfg(test)]
fn is_modifier(usage: u16) -> bool {
    (224..=231).contains(&usage)
}

pub const CONTROL: u16 = 224;
pub const SHIFT: u16 = 225;
pub const ALT: u16 = 226;
pub const META: u16 = 227;
pub const DELETE: u16 = 76;
pub const ESCAPE: u16 = 41;
pub const TAB: u16 = 43;
pub const F4: u16 = 61;
pub const PRINT: u16 = 70;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letters_digits_and_modifiers_map_by_position() {
        // X11/Wayland key codes: A = 38, Z = 52, 1 = 10, Return = 36, Left Ctrl = 37, Super = 133.
        assert_eq!(usage(38), Some(4));
        assert_eq!(usage(52), Some(29));
        assert_eq!(usage(10), Some(30));
        assert_eq!(usage(36), Some(40));
        assert_eq!(usage(37), Some(CONTROL));
        assert_eq!(usage(133), Some(META));
        assert_eq!(usage(119), Some(DELETE));
        assert_eq!(usage(0), None);
        assert_eq!(usage(7), None);
        assert!(is_modifier(usage(50).unwrap()));
        assert!(!is_modifier(usage(38).unwrap()));
    }

    #[test]
    fn table_has_no_duplicate_keys_or_usages() {
        let mut codes: Vec<u16> = EVDEV.iter().map(|(code, _)| *code).collect();
        let mut usages: Vec<u16> = EVDEV.iter().map(|(_, usage)| *usage).collect();
        codes.sort_unstable();
        usages.sort_unstable();
        let total = codes.len();
        codes.dedup();
        usages.dedup();
        assert_eq!(codes.len(), total);
        assert_eq!(usages.len(), total);
    }
}
