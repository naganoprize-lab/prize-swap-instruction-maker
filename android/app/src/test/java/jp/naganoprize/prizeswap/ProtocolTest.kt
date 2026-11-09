package jp.naganoprize.prizeswap

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.*
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class ProtocolTest {
    private val shared = File("../../shared")

    private fun codec() =
        PackageCodec(
            Schema(
                StrictJson.parse(File(shared, "schemas/package-v1.schema.json").readBytes())
                    .jsonObject
            )
        )

    private fun fixture() = File(shared, "examples/windows-fixture.zip").readBytes()

    @Test
    fun windowsRoundTrip() {
        val c = codec()
        val p = c.read(fixture())
        assertEquals("fixture-dataset", p.manifest.s("dataset_id"))
        val rows = p.data.getValue("sales").a("frames")[0].jsonObject.a("records")
        assertEquals(0.0, rows[1].jsonObject.n("sales")!!, 0.0)
        assertEquals(JsonNull, rows[1].jsonObject["ratio"])
        val d = p.data.getValue("instructions").a("documents")[0].jsonObject
        val e = d.o("body").a("entries")[0].jsonObject
        assertEquals("", e.s("name"))
        assertEquals(
            "fixture-assignment",
            e.a("booth_assignments")[0].jsonObject.s("assignment_id"),
        )
        val updated = e.with("notes" to text("Android往復テスト"))
        val doc =
            Documents.revise(
                d,
                d.o("body").with("entries" to listOf(updated).array()),
                "fixture-android",
            )
        val data = p.data + mapOf("instructions" to obj("documents" to listOf(doc).array()))
        val raw = c.write(data, "instructions", "fixture-android", p.manifest.s("dataset_id"))
        assertEquals(doc, c.read(raw).data.getValue("instructions").a("documents")[0])
        File("build/outputs/interop").mkdirs()
        File("build/outputs/interop/android-roundtrip.zip").writeBytes(raw)
    }

    @Test
    fun decodesWindowsQrWithoutNetwork() {
        val reader = ar.com.hjg.pngj.PngReader(File(shared, "examples/machine-qr.png"))
        val width = reader.imgInfo.cols
        val height = reader.imgInfo.rows
        val channels = reader.imgInfo.channels
        val pixels = IntArray(width * height)
        for (y in 0 until height) {
            val row = (reader.readRow() as ar.com.hjg.pngj.ImageLineInt).scanline
            for (x in 0 until width) pixels[y * width + x] =
                (255 shl 24) or
                    (row[x * channels] shl 16) or
                    (row[x * channels + 1] shl 8) or
                    row[x * channels + 2]
        }
        reader.end()
        val bitmap =
            com.google.zxing.BinaryBitmap(
                com.google.zxing.common.HybridBinarizer(
                    com.google.zxing.RGBLuminanceSource(width, height, pixels)
                )
            )
        val decoded = com.google.zxing.MultiFormatReader().decode(bitmap)
        assertEquals("fixture-machine", MachineQr.parse(decoded.text))
    }

    @Test
    fun strictParserRejectsUnsafeJson() {
        listOf(
                "{\"a\":1,\"a\":2}",
                "{\"x\":NaN}",
                "[1,]",
                "{\"x\":{}}junk",
                "{\"x\":\"\\ud800\"}",
                "[1e999]",
            )
            .forEach {
                assertTrue(it, runCatching { StrictJson.parse(it.toByteArray()) }.isFailure)
            }
        assertEquals(
            1,
            StrictJson.parse("{\"a\":{\"b\":1}}".toByteArray()).jsonObject.o("a").n("b")!!.toInt(),
        )
    }

    @Test
    fun checksumAndPathErrors() {
        val raw = fixture()
        for (path in listOf("data/settings.json", "../outside")) {
            val out = ByteArrayOutputStream()
            ZipOutputStream(out).use { z ->
                ZipInputStream(raw.inputStream()).use { input ->
                    while (true) {
                        val e = input.nextEntry ?: break
                        z.putNextEntry(ZipEntry(e.name))
                        z.write(if (e.name == path) "{}".toByteArray() else input.readBytes())
                        z.closeEntry()
                    }
                }
                if (path.startsWith("..")) {
                    z.putNextEntry(ZipEntry(path))
                    z.write(byteArrayOf(1))
                    z.closeEntry()
                }
            }
            assertTrue(runCatching { codec().read(out.toByteArray()) }.isFailure)
        }
    }

    @Test
    fun qrOnlyAcceptsVersionedMachineIds() {
        assertEquals("fixture-machine", MachineQr.parse(MachineQr.uri("fixture-machine")))
        listOf(
                "https://a",
                "prizeswap://machine/v1/x?command=1",
                "prizeswap://machine/v1/../../x",
                "prizeswap://machine/v2/a",
            )
            .forEach { assertTrue(runCatching { MachineQr.parse(it) }.isFailure) }
    }

    @Test
    fun identityAndAmbiguity() {
        assertEquals("20088左", Documents.normalize("２００８８-０１"))
        val machines = codec().read(fixture()).data.getValue("machines")
        assertEquals("fixture-machine", Documents.lookup("L_66", machines).single().s("machine_id"))
        val contradictory =
            machines.with(
                "references" to
                    (machines.a("references") +
                            obj("source_key" to text("L_66"), "number" to text("20088左")))
                        .array()
            )
        assertTrue(Documents.lookup("L_66", contradictory).size != 1)
    }

    @Test
    fun concurrentFieldsAndDeleteConflict() {
        val base =
            obj(
                "entries" to
                    listOf(obj("entry_id" to text("e"), "name" to text("old"), "notes" to text("")))
                        .array()
            )
        val a =
            base.with(
                "entries" to
                    listOf(base.a("entries")[0].jsonObject.with("name" to text("Windows"))).array()
            )
        val b =
            base.with(
                "entries" to
                    listOf(base.a("entries")[0].jsonObject.with("notes" to text("Android"))).array()
            )
        val (merged, c) = Documents.merge(base, a, b)
        assertTrue(c.isEmpty())
        assertEquals("Windows", merged!!.jsonObject.a("entries")[0].jsonObject.s("name"))
        val deletion = base.with("entries" to JsonArray(emptyList()))
        assertTrue(Documents.merge(base, deletion, b).second.isNotEmpty())
    }

    @Test
    fun malformedFinancialFieldsAndRadarAreRejected() {
        val c = codec()
        val p = c.read(fixture())
        val frames = p.data.getValue("sales").a("frames")
        val frame = frames[0].jsonObject
        val records = frame.a("records")
        val first = records[0].jsonObject
        for (bad in
            listOf(
                first.with("sales" to text("6,000円")),
                first.with(
                    "radar" to
                        first
                            .a("radar")
                            .mapIndexed { i, a ->
                                if (i == 0) a.jsonObject.with("score" to JsonPrimitive(101)) else a
                            }
                            .array()
                ),
            )) {
            val sales =
                obj(
                    "frames" to
                        listOf(frame.with("records" to (listOf(bad) + records.drop(1)).array()))
                            .array()
                )
            assertTrue(
                runCatching {
                    c.write(
                        p.data + mapOf("sales" to sales),
                        "full",
                        "android-test",
                        "fixture-dataset",
                    )
                }
                    .isFailure
            )
        }
    }

    @Test
    fun sameDayOrderIsSharedAcrossDocuments() {
        fun doc(day: String, order: Int) =
            Documents.new(
                obj(
                    "entries" to
                        listOf(obj("day" to text(day), "order" to JsonPrimitive(order))).array()
                ),
                "test",
            )
        val sameDay = "2026-11-09"
        val docs =
            listOf(
                doc(sameDay, 1),
                doc(sameDay, 4),
                doc("2026-11-08", 99),
                doc(sameDay, 70).with("deleted" to JsonPrimitive(true)),
            )
        assertEquals(5, Documents.nextOrder(docs, sameDay))
        assertEquals(1, Documents.nextOrder(docs, "2026-11-10"))
    }

    @Test
    fun schemaBranchFastPathKeepsOverlapAndConstraintChecks() {
        val schema =
            Schema(
                StrictJson.parse(
                        """{
          "${'$'}defs": {
            "exclusive": {"oneOf": [{"type":"number","minimum":0}, {"type":"number","maximum":10}]},
            "jsonValue": {"anyOf": [
              {"type":"null"}, {"type":"number","minimum":0},
              {"type":"object","required":["x"],"properties":{"x":{"type":"integer","minimum":0}},"additionalProperties":false}
            ]}
          }
        }"""
                            .toByteArray()
                    )
                    .jsonObject
            )
        schema.validate("exclusive", JsonPrimitive(-1))
        schema.validate("exclusive", JsonPrimitive(20))
        assertTrue(runCatching { schema.validate("exclusive", JsonPrimitive(5)) }.isFailure)
        assertTrue(runCatching { schema.validate("exclusive", text("5")) }.isFailure)
        listOf(JsonNull, JsonPrimitive(0), obj("x" to JsonPrimitive(1))).forEach {
            schema.validate("jsonValue", it)
        }
        listOf(
                JsonPrimitive(false),
                JsonPrimitive(-1),
                obj("x" to JsonPrimitive(-1)),
                obj("x" to JsonPrimitive(1), "extra" to JsonNull),
            )
            .forEach {
                assertTrue(runCatching { schema.validate("jsonValue", it) }.isFailure)
            }
    }
}
