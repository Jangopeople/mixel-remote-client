package ch.mixel.remote.ui

import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import ch.mixel.remote.app.ReportsModel
import ch.mixel.remote.support.Format
import ch.mixel.remote.support.L
import java.io.ByteArrayOutputStream
import java.time.Instant

/** The session report as a PDF: landscape A4, the same columns as the desktop app's PDF, paginated. */
object ReportPdf {
    private const val WIDTH = 842
    private const val HEIGHT = 595
    private const val MARGIN = 36f

    fun build(model: ReportsModel, company: String): ByteArray {
        val document = PdfDocument()
        val text = Paint().apply { textSize = 9f; isAntiAlias = true; color = 0xFF16181D.toInt() }
        val bold = Paint(text).apply { typeface = Typeface.DEFAULT_BOLD }
        val title = Paint(bold).apply { textSize = 16f }
        val muted = Paint(text).apply { color = 0xFF5E636E.toInt() }
        val line = Paint().apply { color = 0xFFDADCE2.toInt(); strokeWidth = 0.5f }
        val columns = listOf(
            L("Start") to 0.13f, L("End") to 0.13f, L("Technician") to 0.16f, L("Device") to 0.17f,
            L("Duration") to 0.08f, L("Type") to 0.12f, L("Note") to 0.21f,
        )
        val usable = WIDTH - 2 * MARGIN
        val now = Instant.now()
        val rows = model.rows.map { row ->
            listOf(
                model.stamp(row.startedAt), if (row.endedAt != null) model.stamp(row.endedAt) else L("Running"),
                row.technicianName ?: row.technicianEmail ?: "", row.deviceName ?: "",
                ReportsModel.duration(model.seconds(row, now)), model.kindLabel(row), (row.note ?: "").replace('\n', ' '),
            )
        }
        val perPage = ((HEIGHT - 2 * MARGIN - 110) / 14).toInt()
        val pages = maxOf(1, (rows.size + perPage - 1) / perPage)
        for (index in 0 until pages) {
            val page = document.startPage(PdfDocument.PageInfo.Builder(WIDTH, HEIGHT, index + 1).create())
            val canvas = page.canvas
            canvas.drawColor(android.graphics.Color.WHITE)
            var y = MARGIN + 16
            if (index == 0) {
                canvas.drawText(L("Session report") + " · " + company, MARGIN, y, title)
                y += 18
                canvas.drawText("${Format.day(model.from + "T12:00:00Z")} – ${Format.day(model.to + "T12:00:00Z")} · ${L("Generated")} ${model.stamp(now.toString())}", MARGIN, y, muted)
                y += 14
                canvas.drawText(model.summary, MARGIN, y, text)
                y += 22
            }
            var x = MARGIN
            for ((name, share) in columns) { canvas.drawText(fit(name, usable * share - 6, bold), x, y, bold); x += usable * share }
            y += 6
            canvas.drawLine(MARGIN, y, WIDTH - MARGIN, y, line)
            y += 12
            for (row in rows.drop(index * perPage).take(perPage)) {
                x = MARGIN
                row.forEachIndexed { column, value ->
                    val share = columns[column].second
                    canvas.drawText(fit(value, usable * share - 6, text), x, y, text)
                    x += usable * share
                }
                y += 14
            }
            canvas.drawText("Mixel Remote · ${L("Page")} ${index + 1} ${L("of")} $pages", MARGIN, HEIGHT - MARGIN / 2, muted)
            document.finishPage(page)
        }
        val out = ByteArrayOutputStream()
        document.writeTo(out)
        document.close()
        return out.toByteArray()
    }

    private fun fit(value: String, width: Float, paint: Paint): String {
        if (paint.measureText(value) <= width) return value
        var end = value.length
        while (end > 0 && paint.measureText(value.substring(0, end) + "…") > width) end--
        return value.substring(0, end) + "…"
    }
}
