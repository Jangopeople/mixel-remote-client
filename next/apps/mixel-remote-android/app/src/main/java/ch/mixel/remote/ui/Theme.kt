package ch.mixel.remote.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Mixel's colours (dynamic colour is off on purpose: the app keeps the Mixel brand): the Mixel red
 * as accent, calm neutral surfaces, green for "online". Every colour has a light and a dark value,
 * and text always sits on its own surface colour, so nothing ends up white on white.
 */
@Immutable
data class MixelColors(val success: Color, val warning: Color, val brandSoft: Color, val line: Color, val muted: Color)

val LocalMixelColors = staticCompositionLocalOf {
    MixelColors(Color(0xFF258456), Color(0xFFA86400), Color(0xFFFCEEF0), Color(0xFFDADCE2), Color(0xFF5E636E))
}

private val Light = lightColorScheme(
    primary = Color(0xFFBA2034),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFCEEF0),
    onPrimaryContainer = Color(0xFF5C0A16),
    secondary = Color(0xFF4A4F5A),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE8E9ED),
    onSecondaryContainer = Color(0xFF1B1D22),
    background = Color(0xFFF5F6F9),
    onBackground = Color(0xFF16181D),
    surface = Color(0xFFF5F6F9),
    onSurface = Color(0xFF16181D),
    surfaceVariant = Color(0xFFE8E9ED),
    onSurfaceVariant = Color(0xFF5E636E),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color.White,
    surfaceContainer = Color(0xFFFFFFFF),
    surfaceContainerHigh = Color(0xFFF0F1F4),
    surfaceContainerHighest = Color(0xFFE8E9ED),
    outline = Color(0xFFB9BCC4),
    outlineVariant = Color(0xFFDADCE2),
    error = Color(0xFFBA2034),
    onError = Color.White,
)

private val Dark = darkColorScheme(
    primary = Color(0xFFE2485B),
    onPrimary = Color.White,
    primaryContainer = Color(0xFF3A1A20),
    onPrimaryContainer = Color(0xFFFFD9DD),
    secondary = Color(0xFFC3C6CF),
    onSecondary = Color(0xFF1B1D22),
    secondaryContainer = Color(0xFF34363D),
    onSecondaryContainer = Color(0xFFE6E7EB),
    background = Color(0xFF111215),
    onBackground = Color(0xFFE9EAEE),
    surface = Color(0xFF111215),
    onSurface = Color(0xFFE9EAEE),
    surfaceVariant = Color(0xFF2A2C32),
    onSurfaceVariant = Color(0xFFA9ADB7),
    surfaceContainerLowest = Color(0xFF0C0D10),
    surfaceContainerLow = Color(0xFF1B1C21),
    surfaceContainer = Color(0xFF1E2025),
    surfaceContainerHigh = Color(0xFF26282E),
    surfaceContainerHighest = Color(0xFF30323A),
    outline = Color(0xFF5B5F69),
    outlineVariant = Color(0xFF34363D),
    error = Color(0xFFF0707F),
    onError = Color(0xFF1B0005),
)

private val MixelShapes = Shapes(
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
)

private val MixelType = Typography().let {
    it.copy(
        headlineMedium = it.headlineMedium.copy(fontWeight = FontWeight.SemiBold),
        titleLarge = it.titleLarge.copy(fontWeight = FontWeight.SemiBold),
        titleMedium = it.titleMedium.copy(fontWeight = FontWeight.SemiBold),
        labelSmall = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp),
    )
}

@Composable
fun MixelTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val extra = if (dark) {
        MixelColors(Color(0xFF3DBA7C), Color(0xFFF0A43A), Color(0xFF3A1A20), Color(0xFF34363D), Color(0xFFA9ADB7))
    } else {
        MixelColors(Color(0xFF258456), Color(0xFFA86400), Color(0xFFFCEEF0), Color(0xFFDADCE2), Color(0xFF5E636E))
    }
    CompositionLocalProvider(LocalMixelColors provides extra) {
        MaterialTheme(colorScheme = if (dark) Dark else Light, shapes = MixelShapes, typography = MixelType, content = content)
    }
}

object Mixel {
    val colors: MixelColors @Composable get() = LocalMixelColors.current
}
