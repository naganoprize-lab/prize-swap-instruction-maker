package jp.naganoprize.prizeswap

import android.content.pm.ActivityInfo
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.text.AnnotatedString
import androidx.test.platform.app.InstrumentationRegistry
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class CompanionTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun offlineImportEditMapAndTransfer() {
        val m = compose.activity.model
        compose.waitUntil(30000) { m.loaded }
        val original = compose.activity.assets.open("examples/windows-fixture.zip").readBytes()
        val transferId = id()
        val outputZip = ByteArrayOutputStream()
        ZipOutputStream(outputZip).use { out ->
            ZipInputStream(original.inputStream()).use { input ->
                while (true) {
                    val entry = input.nextEntry ?: break
                    val bytes = input.readBytes()
                    out.putNextEntry(ZipEntry(entry.name))
                    out.write(
                        if (entry.name == "manifest.json")
                            StrictJson.parse(bytes)
                                .jsonObject
                                .with("package_id" to text(transferId))
                                .toString()
                                .toByteArray()
                        else bytes
                    )
                    out.closeEntry()
                }
            }
        }
        compose.runOnIdle { m.preview(outputZip.toByteArray(), transferId) }
        compose.waitUntil(30000) { m.pending != null }
        if (compose.onAllNodesWithText("この端末の内容を保持").fetchSemanticsNodes().isNotEmpty()) {
            compose.onNodeWithText("この端末の内容を保持").performClick()
            compose.onNodeWithText("受信内容を採用").performClick()
        }
        compose.onNodeWithText("確認して取り込む").performClick()
        compose.waitUntil(30000) { m.refs != null && !m.busy }
        assertEquals(2, m.machines().a("items").size)
        assertEquals(
            "imported",
            StrictJson.parse(File(m.transferRoot, "acks/$transferId.json").readBytes()).let {
                (it as kotlinx.serialization.json.JsonObject).s("status")
            },
        )
        compose.runOnIdle { m.qr("prizeswap://machine/v1/fixture-machine") }
        compose.waitForIdle()
        compose.onAllNodesWithText("20111右", substring = true).onFirst().assertIsDisplayed()
        compose.runOnIdle { m.preference("theme", "dark") }
        compose.waitForIdle()
        val titlePixels = compose.onNodeWithText("20111右").captureToImage().toPixelMap()
        var readablePixels = 0
        for (y in 0 until titlePixels.height) for (x in 0 until titlePixels.width) {
            val c = titlePixels[x, y]
            if (c.red > 0.6f && c.green > 0.6f && c.blue > 0.6f) readablePixels++
        }
        assertTrue("Dark machine title must contrast with its background", readablePixels > 20)
        compose.runOnIdle { m.preference("theme", "light") }
        compose.runOnIdle { m.qr("prizeswap://machine/v1/unknown-machine") }
        assertEquals("fixture-machine", m.selectedMachine)
        compose.runOnIdle {
            m.page = "instructions"
            m.selectedDoc = "fixture-document"
        }
        compose.waitForIdle()
        compose.onNodeWithContentDescription("行を編集").performClick()
        compose.onNodeWithText("新しく投入する商品名（空欄可）").performTextInput("テスト新景品")
        compose.runOnIdle {
            assertEquals(
                "テスト新景品",
                m.currentDoc()!!.o("body").a("entries")[0].let {
                    (it as kotlinx.serialization.json.JsonObject).s("name")
                },
            )
            m.undo()
            assertEquals(
                "",
                m.currentDoc()!!.o("body").a("entries")[0].let {
                    (it as kotlinx.serialization.json.JsonObject).s("name")
                },
            )
            m.redo()
        }
        val cases = compose.onNodeWithText("CS数（未定は空欄）").performScrollTo()
        cases.performTextInput("1")
        cases.performTextInput(".")
        cases.performTextInput("5")
        compose.runOnIdle {
            assertEquals(
                1.5,
                m.currentDoc()!!.o("body").a("entries")[0].jsonObject.n("cases")!!,
                0.0,
            )
        }
        val quantity = compose.onNodeWithText("個数・位置メモ（Windows補助情報として保持）").performScrollTo()
        quantity.performTextInput("12個・右側")
        compose.runOnIdle { m.undo() }
        quantity.assert(
            SemanticsMatcher.expectValue(SemanticsProperties.EditableText, AnnotatedString(""))
        )
        compose.runOnIdle { m.redo() }
        quantity.assertTextContains("12個・右側")
        compose.runOnIdle { m.flush() }
        compose.waitUntil(30000) {
            val repository = Repository(compose.activity)
            try {
                repository.documents() == m.docs
            } finally {
                repository.close()
            }
        }
        compose.runOnIdle {
            assertFalse(
                "Normal autosave cancellation must not be shown as a failure",
                m.notice.contains("cancelled", ignoreCase = true),
            )
        }
        if (compose.onAllNodesWithText("← 指示書一覧へ戻る").fetchSemanticsNodes().isNotEmpty())
            compose.onNodeWithText("← 指示書一覧へ戻る").performClick()
        compose.onNodeWithText("入替予定地図").performClick()
        compose.waitForIdle()
        assertEquals(1, planBooths(m).size)
        assertEquals("fixture-machine", planBooths(m)[0].machineId)
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val output =
            File(compose.activity.getExternalFilesDir(null), "test-results").apply { mkdirs() }
        listOf(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, ActivityInfo.SCREEN_ORIENTATION_PORTRAIT)
            .forEachIndexed { i, orientation ->
                compose.runOnIdle { compose.activity.requestedOrientation = orientation }
                compose.waitForIdle()
                instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
                    File(output, "orientation-$i.png").outputStream().use {
                        bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                    }
                    bitmap.recycle()
                }
            }
        val oldLatest =
            File(m.transferRoot, "outbox/latest.json").takeIf { it.exists() }?.readText()
        compose.runOnIdle {
            m.addMachine("fixture-zero", "candidate")
            m.page = "link"
            m.prepareUsb()
        }
        compose.waitUntil(30000) {
            File(m.transferRoot, "outbox/latest.json").let {
                it.exists() && it.readText() != oldLatest
            }
        }
        val latest =
            StrictJson.parse(File(m.transferRoot, "outbox/latest.json").readBytes())
                as kotlinx.serialization.json.JsonObject
        assertEquals(
            "android",
            m.codec
                .read(File(m.transferRoot, "outbox/${latest.s("package_id")}.zip").readBytes())
                .manifest
                .s("source_app"),
        )
        val before = m.docs
        compose.runOnIdle { m.preview("bad ZIP".toByteArray()) }
        compose.waitUntil(30000) { !m.busy }
        assertEquals(before, m.docs)
        assertTrue(Repository(compose.activity).documents().isNotEmpty())
    }
}
