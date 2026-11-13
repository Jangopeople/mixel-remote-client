package ch.mixel.remote.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ch.mixel.remote.support.L

/** The name people know a provider by (`keycloak` is Mixel ID). */
fun providerName(provider: String): String = when (provider) {
    "google" -> "Google"
    "azure" -> "Microsoft"
    "apple" -> "Apple"
    else -> "Mixel ID"
}

/** "Continue with Google / Microsoft / Apple" and "Sign in with Mixel ID", as on the portal. */
@Composable
fun ProviderButtons(providers: List<String>, enabled: Boolean, onChoose: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        for (provider in providers) {
            val label = if (provider == "keycloak") L("Sign in with Mixel ID") else L("Continue with %@", providerName(provider))
            OutlinedButton(onClick = { onChoose(provider) }, enabled = enabled, modifier = Modifier.fillMaxWidth().height(48.dp), shape = MaterialTheme.shapes.small) {
                if (provider == "keycloak") MixelMark(20.dp) else Image(providerMark(provider, MaterialTheme.colorScheme.onSurface), contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(10.dp))
                Text(label, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

private fun parsed(data: String) = PathParser().parsePathString(data).toNodes()

/** The providers' marks (the same drawings as the portal's buttons). */
private fun providerMark(provider: String, ink: Color): ImageVector = when (provider) {
    "google" -> ImageVector.Builder("google", 18.dp, 18.dp, 48f, 48f).apply {
        addPath(parsed("M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"), fill = SolidColor(Color(0xFFEA4335)))
        addPath(parsed("M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17z"), fill = SolidColor(Color(0xFF4285F4)))
        addPath(parsed("M10.6 28.6c-.5-1.4-.8-2.9-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"), fill = SolidColor(Color(0xFFFBBC05)))
        addPath(parsed("M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"), fill = SolidColor(Color(0xFF34A853)))
    }.build()
    "azure" -> ImageVector.Builder("microsoft", 18.dp, 18.dp, 21f, 21f).apply {
        for ((x, y, color) in listOf(Triple(1f, 1f, 0xFFF25022), Triple(11f, 1f, 0xFF7FBA00), Triple(1f, 11f, 0xFF00A4EF), Triple(11f, 11f, 0xFFFFB900))) {
            path(fill = SolidColor(Color(color))) { moveTo(x, y); lineTo(x + 9f, y); lineTo(x + 9f, y + 9f); lineTo(x, y + 9f); close() }
        }
    }.build()
    else -> ImageVector.Builder("apple", 18.dp, 18.dp, 814f, 1000f).apply {
        addPath(
            parsed("M788 341c-6 4-108 62-108 190 0 148 130 200 134 202-1 3-21 72-69 142-43 62-88 124-156 124s-86-40-164-40c-77 0-104 41-167 41s-106-57-156-128C44 788 0 668 0 554 0 371 119 274 236 274c62 0 114 41 153 41 37 0 95-43 166-43 27 0 124 2 188 69zM554 159c29-35 50-83 50-131 0-7-1-14-2-19-48 2-104 32-138 72-27 30-52 79-52 127 0 7 1 15 2 17 3 1 8 1 12 1 43 0 97-29 128-67z"),
            fill = SolidColor(ink),
        )
    }.build()
}
