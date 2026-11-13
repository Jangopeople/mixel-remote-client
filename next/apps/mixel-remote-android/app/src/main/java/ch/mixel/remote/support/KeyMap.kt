package ch.mixel.remote.support

import android.view.KeyEvent

/**
 * Keys travel as USB HID usages (page 7), the same numbers the desktop app sends. Android reports
 * key codes (positions on a hardware keyboard); this table turns them into usages.
 */
object KeyMap {
    const val CONTROL = 224
    const val SHIFT = 225
    const val ALT = 226
    const val COMMAND = 227
    const val ENTER = 40
    const val BACKSPACE = 42

    private val android: Map<Int, Int> = buildMap {
        for (index in 0 until 26) put(KeyEvent.KEYCODE_A + index, 4 + index)
        for (index in 1..9) put(KeyEvent.KEYCODE_0 + index, 29 + index)
        put(KeyEvent.KEYCODE_0, 39)
        put(KeyEvent.KEYCODE_ENTER, 40); put(KeyEvent.KEYCODE_ESCAPE, 41); put(KeyEvent.KEYCODE_DEL, 42)
        put(KeyEvent.KEYCODE_TAB, 43); put(KeyEvent.KEYCODE_SPACE, 44); put(KeyEvent.KEYCODE_MINUS, 45)
        put(KeyEvent.KEYCODE_EQUALS, 46); put(KeyEvent.KEYCODE_LEFT_BRACKET, 47); put(KeyEvent.KEYCODE_RIGHT_BRACKET, 48)
        put(KeyEvent.KEYCODE_BACKSLASH, 49); put(KeyEvent.KEYCODE_SEMICOLON, 51); put(KeyEvent.KEYCODE_APOSTROPHE, 52)
        put(KeyEvent.KEYCODE_GRAVE, 53); put(KeyEvent.KEYCODE_COMMA, 54); put(KeyEvent.KEYCODE_PERIOD, 55)
        put(KeyEvent.KEYCODE_SLASH, 56); put(KeyEvent.KEYCODE_CAPS_LOCK, 57)
        for (index in 0 until 12) put(KeyEvent.KEYCODE_F1 + index, 58 + index)
        put(KeyEvent.KEYCODE_SYSRQ, 70); put(KeyEvent.KEYCODE_SCROLL_LOCK, 71); put(KeyEvent.KEYCODE_BREAK, 72)
        put(KeyEvent.KEYCODE_INSERT, 73); put(KeyEvent.KEYCODE_MOVE_HOME, 74); put(KeyEvent.KEYCODE_PAGE_UP, 75)
        put(KeyEvent.KEYCODE_FORWARD_DEL, 76); put(KeyEvent.KEYCODE_MOVE_END, 77); put(KeyEvent.KEYCODE_PAGE_DOWN, 78)
        put(KeyEvent.KEYCODE_DPAD_RIGHT, 79); put(KeyEvent.KEYCODE_DPAD_LEFT, 80); put(KeyEvent.KEYCODE_DPAD_DOWN, 81)
        put(KeyEvent.KEYCODE_DPAD_UP, 82); put(KeyEvent.KEYCODE_NUM_LOCK, 83)
        put(KeyEvent.KEYCODE_NUMPAD_DIVIDE, 84); put(KeyEvent.KEYCODE_NUMPAD_MULTIPLY, 85); put(KeyEvent.KEYCODE_NUMPAD_SUBTRACT, 86)
        put(KeyEvent.KEYCODE_NUMPAD_ADD, 87); put(KeyEvent.KEYCODE_NUMPAD_ENTER, 88)
        for (index in 1..9) put(KeyEvent.KEYCODE_NUMPAD_0 + index, 88 + index)
        put(KeyEvent.KEYCODE_NUMPAD_0, 98); put(KeyEvent.KEYCODE_NUMPAD_DOT, 99); put(KeyEvent.KEYCODE_MENU, 101)
        put(KeyEvent.KEYCODE_CTRL_LEFT, 224); put(KeyEvent.KEYCODE_SHIFT_LEFT, 225); put(KeyEvent.KEYCODE_ALT_LEFT, 226)
        put(KeyEvent.KEYCODE_META_LEFT, 227); put(KeyEvent.KEYCODE_CTRL_RIGHT, 228); put(KeyEvent.KEYCODE_SHIFT_RIGHT, 229)
        put(KeyEvent.KEYCODE_ALT_RIGHT, 230); put(KeyEvent.KEYCODE_META_RIGHT, 231)
    }

    /** Android key code → HID usage, or null for keys that have none (volume, back, …). */
    fun usage(keyCode: Int): Int? = android[keyCode]

    /** Letters, digits and space as keys, for Ctrl+C and similar from the on-screen keyboard. */
    fun usage(character: Char): Int? = when (val c = character.lowercaseChar()) {
        in 'a'..'z' -> c - 'a' + 4
        in '1'..'9' -> c - '1' + 30
        '0' -> 39
        ' ' -> 44
        else -> null
    }

    /**
     * A character as a key press on a US layout (usage and whether Shift is held), for computers that
     * cannot type text directly (Linux X11): letters, digits, space, Enter, Tab and US punctuation.
     */
    fun press(character: Char): Pair<Int, Boolean>? {
        if (character in 'a'..'z') return (character - 'a' + 4) to false
        if (character in 'A'..'Z') return (character - 'A' + 4) to true
        if (character in '1'..'9') return (character - '1' + 30) to false
        return when (character) {
            '0' -> 39 to false
            ' ' -> 44 to false
            '\n' -> 40 to false
            '\t' -> 43 to false
            '!' -> 30 to true
            '@' -> 31 to true
            '#' -> 32 to true
            '$' -> 33 to true
            '%' -> 34 to true
            '^' -> 35 to true
            '&' -> 36 to true
            '*' -> 37 to true
            '(' -> 38 to true
            ')' -> 39 to true
            '-' -> 45 to false
            '_' -> 45 to true
            '=' -> 46 to false
            '+' -> 46 to true
            '[' -> 47 to false
            '{' -> 47 to true
            ']' -> 48 to false
            '}' -> 48 to true
            '\\' -> 49 to false
            '|' -> 49 to true
            ';' -> 51 to false
            ':' -> 51 to true
            '\'' -> 52 to false
            '"' -> 52 to true
            '`' -> 53 to false
            '~' -> 53 to true
            ',' -> 54 to false
            '<' -> 54 to true
            '.' -> 55 to false
            '>' -> 55 to true
            '/' -> 56 to false
            '?' -> 56 to true
            else -> null
        }
    }

    /** Special keys offered on the session's key bar. */
    enum class Special(val usage: Int, val label: String, val spoken: String) {
        Escape(41, "Esc", "Escape"),
        Tab(43, "Tab", "Tab"),
        Enter(40, "⏎", "Enter"),
        Backspace(42, "⌫", "Backspace"),
        Delete(76, "⌦", "Delete"),
        Up(82, "↑", "Arrow up"),
        Down(81, "↓", "Arrow down"),
        Left(80, "←", "Arrow left"),
        Right(79, "→", "Arrow right"),
        Home(74, "Home", "Home"),
        End(77, "End", "End"),
        PageUp(75, "PgUp", "Page up"),
        PageDown(78, "PgDn", "Page down"),
    }
}
