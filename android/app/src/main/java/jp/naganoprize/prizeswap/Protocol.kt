package jp.naganoprize.prizeswap

import java.io.*
import java.security.MessageDigest
import java.time.*
import java.util.UUID
import java.util.zip.*
import kotlinx.serialization.json.*

fun id() = UUID.randomUUID().toString().replace("-", "")

fun instant() = Instant.now().toString()

fun obj(vararg pairs: Pair<String, JsonElement>) = JsonObject(mapOf(*pairs))

fun text(s: String?) = s?.let(::JsonPrimitive) ?: JsonNull

fun JsonObject.s(key: String) = (get(key) as? JsonPrimitive)?.contentOrNull ?: ""

fun JsonObject.n(key: String) = (get(key) as? JsonPrimitive)?.doubleOrNull

fun JsonObject.o(key: String) = get(key) as? JsonObject ?: JsonObject(emptyMap())

fun JsonObject.a(key: String) = get(key) as? JsonArray ?: JsonArray(emptyList())

fun JsonObject.with(vararg pairs: Pair<String, JsonElement>) =
    JsonObject(toMutableMap().apply { putAll(pairs) })

fun List<JsonElement>.array() = JsonArray(this)

fun validId(s: String) = Regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}").matches(s)

fun requireData(ok: Boolean, message: String) {
    if (!ok) throw IllegalArgumentException(message)
}

/** Strict parser: UTF-8, unique keys, bounded nesting and finite numbers on both platforms. */
object StrictJson {
    private val numberPattern = Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?")

    fun parse(bytes: ByteArray): JsonElement {
        val decoder =
            Charsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
        return Reader(decoder.decode(java.nio.ByteBuffer.wrap(bytes)).toString()).parse()
    }

    private class Reader(val src: String) {
        var p = 0

        fun ws() {
            while (p < src.length && src[p] in " \t\r\n") p++
        }

        fun parse(): JsonElement {
            val v = value(0)
            ws()
            requireData(p == src.length, "JSON末尾が不正です")
            return v
        }

        fun take(c: Char): Boolean {
            ws()
            if (p < src.length && src[p] == c) {
                p++
                return true
            }
            return false
        }

        fun string(): String {
            ws()
            val start = p
            requireData(p < src.length && src[p++] == '"', "JSON文字列が不正です")
            var escaped = false
            while (p < src.length) {
                val c = src[p++]
                if (c == '"' && !escaped) {
                    val s = Json.parseToJsonElement(src.substring(start, p)).jsonPrimitive.content
                    var i = 0
                    while (i < s.length) {
                        val ch = s[i++]
                        if (ch.isHighSurrogate()) {
                            requireData(i < s.length && s[i].isLowSurrogate(), "不正なUnicode")
                            i++
                        } else requireData(!ch.isLowSurrogate(), "不正なUnicode")
                    }
                    return s
                }
                escaped = if (escaped) false else c == '\\'
            }
            error("JSON文字列が終了していません")
        }

        fun value(depth: Int): JsonElement {
            requireData(depth <= 64, "JSONが深すぎます")
            ws()
            requireData(p < src.length, "JSONが途中で終了しました")
            return when (src[p]) {
                '{' -> {
                    p++
                    val m = linkedMapOf<String, JsonElement>()
                    if (!take('}')) {
                        do {
                            val k = string()
                            requireData(!m.containsKey(k), "JSONキー重複: $k")
                            requireData(take(':'), "JSON区切り不正")
                            m[k] = value(depth + 1)
                        } while (take(','))
                        requireData(take('}'), "JSONオブジェクト不正")
                    }
                    JsonObject(m)
                }
                '[' -> {
                    p++
                    val a = mutableListOf<JsonElement>()
                    if (!take(']')) {
                        do {
                            a += value(depth + 1)
                        } while (take(','))
                        requireData(take(']'), "JSON配列不正")
                    }
                    JsonArray(a)
                }
                '"' -> JsonPrimitive(string())
                else -> {
                    val start = p
                    while (p < src.length && src[p] !in ",]} \t\r\n") p++
                    val token = src.substring(start, p)
                    requireData(
                        token in listOf("true", "false", "null") || numberPattern.matches(token),
                        "JSON値不正",
                    )
                    val v = Json.parseToJsonElement(token)
                    if (token !in listOf("true", "false", "null"))
                        requireData(token.toDouble().isFinite(), "有限数以外は使えません")
                    v
                }
            }
        }
    }
}

class Schema(val root: JsonObject) {
    private val patterns = java.util.concurrent.ConcurrentHashMap<String, Regex>()

    fun validate(name: String, value: JsonElement) = check(root.o("\$defs").o(name), value, name)

    private fun check(spec: JsonObject, v: JsonElement, path: String) {
        spec
            .s("\$ref")
            .takeIf { it.isNotEmpty() }
            ?.let {
                check(root.o("\$defs").o(it.substringAfterLast('/')), v, path)
                return
            }
        val types =
            when (val t = spec["type"]) {
                is JsonPrimitive -> listOf(t.content)
                is JsonArray -> t.map { it.jsonPrimitive.content }
                else -> emptyList()
            }
        fun isType(t: String) =
            when (t) {
                "null" -> v == JsonNull
                "object" -> v is JsonObject
                "array" -> v is JsonArray
                "string" -> v is JsonPrimitive && v.isString
                "boolean" -> v is JsonPrimitive && !v.isString && v.booleanOrNull != null
                "number" -> v is JsonPrimitive && !v.isString && v.doubleOrNull?.isFinite() == true
                "integer" ->
                    v is JsonPrimitive &&
                        !v.isString &&
                        v.content.toBigDecimalOrNull()?.stripTrailingZeros()?.scale()?.let {
                            it <= 0
                        } == true
                else -> false
            }
        requireData(types.isEmpty() || types.any(::isType), "$path: 型が不正です")
        spec["const"]?.let { requireData(v == it, "$path: 未対応の形式・バージョンです") }
        spec["enum"]?.let { requireData(v in it.jsonArray, "$path: 未対応の値です") }
        // A type mismatch is guaranteed to fail check; avoid constructing exceptions
        // for every incompatible generic JSON branch in large reference frames.
        fun mayMatch(option: JsonElement): Boolean {
            val candidate = option.jsonObject
            if (candidate.s("\$ref").isNotEmpty()) return true
            return when (val hint = candidate["type"]) {
                is JsonPrimitive -> isType(hint.content)
                is JsonArray -> hint.any { isType(it.jsonPrimitive.content) }
                else -> true
            }
        }
        spec["oneOf"]?.let { options ->
            requireData(
                options.jsonArray.count {
                    mayMatch(it) && runCatching { check(it.jsonObject, v, path) }.isSuccess
                } == 1,
                "$path: 値が不正です",
            )
        }
        spec["anyOf"]?.let { options ->
            requireData(
                options.jsonArray.any {
                    mayMatch(it) && runCatching { check(it.jsonObject, v, path) }.isSuccess
                },
                "$path: 値が不正です",
            )
        }
        when (v) {
            is JsonObject -> {
                spec.a("required").forEach {
                    requireData(
                        v.containsKey(it.jsonPrimitive.content),
                        "$path: ${it.jsonPrimitive.content} がありません",
                    )
                }
                val props = spec.o("properties")
                (spec["propertyNames"] as? JsonObject)?.let { s ->
                    v.keys.forEach { check(s, text(it), "$path/key") }
                }
                v.forEach { (k, x) ->
                    when {
                        props[k] is JsonObject -> check(props[k]!!.jsonObject, x, "$path/$k")
                        spec["additionalProperties"] == JsonPrimitive(false) ->
                            error("$path: 未対応項目 $k")
                        spec["additionalProperties"] is JsonObject ->
                            check(spec["additionalProperties"]!!.jsonObject, x, "$path/$k")
                    }
                }
            }
            is JsonArray -> {
                spec.n("maxItems")?.let { requireData(v.size <= it, "$path: 件数超過") }
                spec.n("minItems")?.let { requireData(v.size >= it, "$path: 件数不足") }
                (spec["items"] as? JsonObject)?.let { s ->
                    v.forEachIndexed { i, x -> check(s, x, "$path/$i") }
                }
            }
            is JsonPrimitive ->
                if (v.isString) {
                    spec.n("minLength")?.let {
                        requireData(v.content.length >= it, "$path: 空欄は使えません")
                    }
                    spec.n("maxLength")?.let { requireData(v.content.length <= it, "$path: 長すぎます") }
                    spec
                        .s("pattern")
                        .takeIf { it.isNotEmpty() }
                        ?.let {
                            requireData(
                                patterns
                                    .computeIfAbsent(it) { value -> Regex(value) }
                                    .containsMatchIn(v.content),
                                "$path: 形式不正",
                            )
                        }
                    when (spec.s("format")) {
                        "date" -> LocalDate.parse(v.content)
                        "date-time" -> OffsetDateTime.parse(v.content)
                    }
                } else
                    v.doubleOrNull?.let { n ->
                        spec.n("minimum")?.let { requireData(n >= it, "$path: 小さすぎます") }
                        spec.n("maximum")?.let { requireData(n <= it, "$path: 大きすぎます") }
                    }
            else -> {}
        }
    }
}

data class PackageData(val manifest: JsonObject, val data: Map<String, JsonObject>)

class PackageCodec(val schema: Schema) {
    companion object {
        const val MAX_FILE = 32 * 1024 * 1024
        const val MAX_TOTAL = 64 * 1024 * 1024
        val names = listOf("machines", "layout", "sales", "history", "settings", "instructions")
        val paths = names.map { "data/$it.json" }
        val files = (paths + listOf("manifest.json", "metadata/checksums.json")).toSet()

        fun hash(bytes: ByteArray) =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") {
                "%02x".format(it)
            }
    }

    fun validateData(data: Map<String, JsonObject>) {
        names.forEach { schema.validate(it, data.getValue(it)) }
        fun unique(ids: List<String>, label: String) {
            requireData(ids.size == ids.toSet().size, "$label が重複しています")
        }
        val ids = data.getValue("machines").a("items").map { it.jsonObject.s("machine_id") }
        unique(ids, "マシンID")
        val coords = data.getValue("layout").a("items").map { it.jsonObject }
        unique(coords.map { it.s("machine_id") }, "座標ID")
        coords.forEach {
            requireData(
                it.s("machine_id") in ids &&
                    (it.n("width") ?: 0.0) >= 80 &&
                    (it.n("height") ?: 0.0) >= 60,
                "レイアウト参照・サイズ不正",
            )
        }
        val frames = data.getValue("sales").a("frames") + data.getValue("history").a("frames")
        unique(frames.map { it.jsonObject.s("frame_id") }, "期間ID")
        frames.forEach { f ->
            val frame = f.jsonObject
            val a = frame.s("period_start")
            val b = frame.s("period_end")
            requireData(a.isEmpty() == b.isEmpty(), "期間境界が不足しています")
            if (a.isNotEmpty()) requireData(LocalDate.parse(a) <= LocalDate.parse(b), "期間が逆です")
            val records = frame.a("records").map { it.jsonObject }
            unique(records.map { it.s("machine_id") }, "実績ID")
            records.forEach { r ->
                val axes = r.a("radar")
                requireData(
                    axes.isEmpty() ||
                        axes.map { it.jsonObject.s("key") } ==
                            listOf("ratio", "sales", "days", "cost_rate", "po_rate"),
                    "レーダーの軸構成が不正です",
                )
                requireData(r.s("machine_id") in ids && r["grades"] is JsonObject, "実績参照不正")
                r.o("grades").values.forEach {
                    requireData(Regex("#[0-9A-Fa-f]{6}").matches(it.jsonObject.s("color")), "評価色不正")
                }
            }
        }
        val docs = data.getValue("instructions").a("documents").map { it.jsonObject }
        unique(docs.map { it.s("document_id") }, "指示書ID")
        val entries = docs.flatMap {
            validateDoc(it)
            it.o("body").a("entries").map { e -> e.jsonObject }
        }
        unique(entries.map { it.s("entry_id") }, "指示行ID")
        unique(
            entries.flatMap {
                it.a("booth_assignments").map { a -> a.jsonObject.s("assignment_id") }
            },
            "割当ID",
        )
    }

    fun validateDoc(d: JsonObject) {
        schema.validate("document", d)
        val ancestors = d.a("ancestors").map { it.jsonPrimitive.content }
        requireData(
            d.s("revision_id") !in ancestors && ancestors.size == ancestors.toSet().size,
            "改訂履歴不正",
        )
        d.o("body").a("entries").forEach { value ->
            val e = value.jsonObject
            val tokens =
                e.a("machines")
                    .map { it.jsonPrimitive.content }
                    .ifEmpty { e.a("management_numbers").map { it.jsonPrimitive.content } }
                    .ifEmpty { listOf("") }
            requireData(
                e.a("booth_assignments").map { it.jsonObject.s("token") } == tokens,
                "ブース割当の番号が指示行と一致しません",
            )
        }
    }

    fun read(raw: ByteArray): PackageData {
        requireData(raw.isNotEmpty() && raw.size <= MAX_TOTAL, "ZIPは64MiB以下にしてください")
        val directory = ZipDirectory.inspect(raw)
        val contents = linkedMapOf<String, ByteArray>()
        var total = 0
        ZipInputStream(ByteArrayInputStream(raw)).use { z ->
            while (true) {
                val e = z.nextEntry ?: break
                requireData(e.name in files && e.name !in contents, "ZIP構成・パス・重複が不正です")
                requireData(e.method == ZipEntry.STORED || e.method == ZipEntry.DEFLATED, "未対応圧縮")
                val out = ByteArrayOutputStream()
                val b = ByteArray(8192)
                while (true) {
                    val n = z.read(b)
                    if (n < 0) break
                    total += n
                    requireData(out.size() + n <= MAX_FILE && total <= MAX_TOTAL, "展開サイズ超過")
                    out.write(b, 0, n)
                }
                val value = out.toByteArray()
                requireData(
                    e.compressedSize < 0 || value.size <= maxOf(1, e.compressedSize) * 200,
                    "圧縮率超過",
                )
                val central = directory.getValue(e.name)
                requireData(
                    central.size == value.size.toLong() &&
                        CRC32().apply { update(value) }.value == central.crc,
                    "ZIP中央チェックサム不一致",
                )
                contents[e.name] = value
                z.closeEntry()
            }
        }
        requireData(contents.keys == files, "ZIP内の必須ファイルが不足しています")
        val manifest = StrictJson.parse(contents.getValue("manifest.json")).jsonObject
        schema.validate("manifest", manifest)
        val checks = StrictJson.parse(contents.getValue("metadata/checksums.json")).jsonObject
        requireData(checks.keys == paths.toSet(), "チェックサム構成不正")
        val listings = manifest.a("files").map { it.jsonObject }
        requireData(
            listings.size == 6 && listings.map { it.s("path") }.toSet() == paths.toSet(),
            "ファイル一覧不正",
        )
        paths.forEach { p ->
            val bytes = contents.getValue(p)
            val file = listings.single { it.s("path") == p }
            requireData(
                hash(bytes) == checks.s(p) &&
                    hash(bytes) == file.s("sha256") &&
                    bytes.size.toDouble() == file.n("size"),
                "チェックサムまたはサイズ不一致: $p",
            )
        }
        val data = names.associateWith {
            StrictJson.parse(contents.getValue("data/$it.json")).jsonObject
        }
        validateData(data)
        return PackageData(manifest, data)
    }

    fun write(
        data: Map<String, JsonObject>,
        kind: String,
        device: String,
        dataset: String,
    ): ByteArray {
        validateData(data)
        val contents = names.associate {
            "data/$it.json" to data.getValue(it).toString().toByteArray(Charsets.UTF_8)
        }
        val manifest =
            obj(
                "format" to text("PrizeSwapPackage"),
                "version" to JsonPrimitive(1),
                "min_reader_version" to JsonPrimitive(1),
                "package_id" to text(id()),
                "dataset_id" to text(dataset),
                "created_at" to text(instant()),
                "source_app" to text("android"),
                "source_device_id" to text(device),
                "kind" to text(kind),
                "data_updated_at" to JsonNull,
                "app_version" to text("1.0"),
                "files" to
                    contents
                        .map { (p, b) ->
                            obj(
                                "path" to text(p),
                                "size" to JsonPrimitive(b.size),
                                "sha256" to text(hash(b)),
                            )
                        }
                        .array(),
            )
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { z ->
            (contents +
                    mapOf(
                        "manifest.json" to manifest.toString().toByteArray(),
                        "metadata/checksums.json" to
                            JsonObject(contents.mapValues { text(hash(it.value)) })
                                .toString()
                                .toByteArray(),
                    ))
                .forEach { (p, b) ->
                    z.putNextEntry(ZipEntry(p))
                    z.write(b)
                    z.closeEntry()
                }
        }
        return out.toByteArray().also { read(it) }
    }
}

object MachineQr {
    fun uri(machineId: String): String {
        requireData(validId(machineId), "マシンID不正")
        return "prizeswap://machine/v1/$machineId"
    }

    fun parse(value: String): String {
        val prefix = "prizeswap://machine/v1/"
        requireData(value.startsWith(prefix), "このアプリのマシンQRではありません")
        val key = value.removePrefix(prefix)
        requireData(validId(key), "QRのマシンID不正")
        return key
    }
}
