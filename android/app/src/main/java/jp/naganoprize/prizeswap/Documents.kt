package jp.naganoprize.prizeswap

import java.text.Normalizer
import kotlinx.serialization.json.*

object Documents {
    fun new(body: JsonObject, device: String, documentId: String = id()) =
        obj(
            "document_id" to text(documentId),
            "revision" to JsonPrimitive(1),
            "revision_id" to text(id()),
            "ancestors" to JsonArray(emptyList()),
            "updated_at" to text(instant()),
            "updated_by" to text(device),
            "deleted" to JsonPrimitive(false),
            "body" to body,
        )

    fun revise(
        d: JsonObject,
        body: JsonObject,
        device: String,
        deleted: Boolean = false,
    ): JsonObject {
        if (d.o("body") == body && d["deleted"] == JsonPrimitive(deleted)) return d
        return d.with(
            "body" to body,
            "revision" to JsonPrimitive((d.n("revision") ?: 0.0).toInt() + 1),
            "revision_id" to text(id()),
            "ancestors" to (d.a("ancestors") + text(d.s("revision_id"))).takeLast(128).array(),
            "updated_at" to text(instant()),
            "updated_by" to text(device),
            "deleted" to JsonPrimitive(deleted),
        )
    }

    fun relation(local: JsonObject?, incoming: JsonObject): String {
        if (local == null) return "new"
        if (local.s("revision_id") == incoming.s("revision_id")) {
            requireData(
                local.o("body") == incoming.o("body") && local["deleted"] == incoming["deleted"],
                "同じ改訂IDの内容が異なります",
            )
            return "same"
        }
        if (text(local.s("revision_id")) in incoming.a("ancestors")) return "incoming"
        if (text(incoming.s("revision_id")) in local.a("ancestors")) return "local"
        return "conflict"
    }

    data class Conflict(val path: String, val local: JsonElement?, val incoming: JsonElement?)

    fun merge(
        base: JsonElement?,
        local: JsonElement?,
        incoming: JsonElement?,
        choices: Map<String, String> = emptyMap(),
    ): Pair<JsonElement?, List<Conflict>> {
        val conflicts = mutableListOf<Conflict>()
        fun walk(b: JsonElement?, l: JsonElement?, r: JsonElement?, path: String): JsonElement? {
            if (l == r) return l
            if (l == b) return r
            if (r == b) return l
            if (b is JsonObject && l is JsonObject && r is JsonObject)
                return JsonObject(
                    (b.keys + l.keys + r.keys)
                        .sorted()
                        .mapNotNull { k -> walk(b[k], l[k], r[k], "$path/$k")?.let { k to it } }
                        .toMap()
                )
            if (path.endsWith("/entries") && b is JsonArray && l is JsonArray && r is JsonArray) {
                val maps =
                    listOf(b, l, r).map { it.associateBy { e -> e.jsonObject.s("entry_id") } }
                return (l + r + b)
                    .map { it.jsonObject.s("entry_id") }
                    .distinct()
                    .mapNotNull { k -> walk(maps[0][k], maps[1][k], maps[2][k], "$path/$k") }
                    .array()
            }
            return when (choices[path]) {
                "local" -> l
                "incoming" -> r
                else -> {
                    conflicts += Conflict(path, l, r)
                    l
                }
            }
        }
        return walk(base, local, incoming, "") to conflicts
    }

    fun copy(d: JsonObject, device: String): JsonObject {
        val extras = mutableMapOf<String, JsonElement>()
        val entries =
            d.o("body").a("entries").map { value ->
                val e = value.jsonObject
                val eid = id()
                e.let {
                    d.o("body").o("entry_extras")[it.s("entry_id")]?.let { x -> extras[eid] = x }
                }
                val map = mutableMapOf<String, String>()
                val assignments =
                    e.a("booth_assignments").map {
                        val a = it.jsonObject
                        val aid = id()
                        map[a.s("assignment_id")] = aid
                        a.with("assignment_id" to text(aid))
                    }
                val candidate =
                    (e["candidate_assignment"] as? JsonObject)?.let {
                        it.with("assignment_id" to text(map[it.s("assignment_id")] ?: id()))
                    } ?: JsonNull
                e.with(
                    "entry_id" to text(eid),
                    "booth_assignments" to assignments.array(),
                    "candidate_assignment" to candidate,
                )
            }
        return new(
            d.o("body").with("entries" to entries.array(), "entry_extras" to JsonObject(extras)),
            device,
        )
    }

    fun normalize(raw: String): String {
        val value =
            Normalizer.normalize(raw, Normalizer.Form.NFKC)
                .replace(Regex("\\s+"), "")
                .replace('－', '-')
                .replace('−', '-')
                .replace('‐', '-')
        if (Regex("[LR]_[0-9]+", RegexOption.IGNORE_CASE).matches(value)) return value.uppercase()
        val m = Regex("([0-9]+)-0?([12])").matchEntire(value)
        return if (m != null) m.groupValues[1] + if (m.groupValues[2] == "1") "左" else "右"
        else value
    }

    fun tokens(raw: String) =
        raw.split(Regex("[,、，;；\\r\\n]+"))
            .map(::normalize)
            .filter { it.isNotEmpty() }
            .distinct()
            .also { xs ->
                requireData(
                    xs.all { Regex("(?:[0-9]+[左右]?|[LR]_[0-9]+)").matches(it) },
                    "番号は数字と左右、またはL_66等で入力してください",
                )
            }

    fun lookup(token: String, machines: JsonObject): List<JsonObject> {
        val key = normalize(token)
        if (key.isEmpty()) return emptyList()
        val items = machines.a("items").map { it.jsonObject }
        val refs = machines.a("references").map { it.jsonObject }
        val direct = items.filter {
            normalize(it.s("number")) == key ||
                normalize(it.s("management_number")) == key ||
                it.s("machine_id") == token
        }
        val numbers =
            refs
                .filter { normalize(it.s("source_key")) == key }
                .map { normalize(it.s("number")) }
                .distinct()
        if (numbers.size > 1) return emptyList()
        if (numbers.size == 1) {
            val n = numbers.single()
            val owners =
                refs
                    .filter { normalize(it.s("number")) == n }
                    .map { normalize(it.s("source_key")) }
                    .distinct()
            if (owners.size > 1) return emptyList()
            return (direct + items.filter { normalize(it.s("number")) == n }).distinctBy {
                it.s("machine_id")
            }
        }
        return direct
    }

    fun assign(entry: JsonObject, raw: String, machines: JsonObject): JsonObject {
        val tokens = tokens(raw)
        val old = entry.a("booth_assignments").map { it.jsonObject }
        val assignments =
            (tokens.ifEmpty { listOf("") }).map { t ->
                val matches = lookup(t, machines)
                val m = matches.singleOrNull()
                val previous =
                    old.firstOrNull { it.s("token") == t }
                        ?: old.singleOrNull().takeIf { tokens.size <= 1 }
                obj(
                    "assignment_id" to text(previous?.s("assignment_id") ?: id()),
                    "token" to text(t),
                    "machine_key" to text(m?.s("machine_id") ?: ""),
                    "machine_number" to
                        text(
                            m?.s("number")?.takeIf { Regex("[0-9]+[左右]?").matches(it) }
                                ?: t.takeIf { Regex("[0-9]+[左右]?").matches(it) }
                                ?: ""
                        ),
                    "management_number" to
                        text(
                            m?.s("management_number")
                                ?: t.takeIf { Regex("[LR]_[0-9]+").matches(it) }
                                ?: ""
                        ),
                )
            }
        val previous = entry.o("machine_methods")
        val retired = entry.o("retired_machine_methods").toMutableMap()
        previous.filterKeys { it !in tokens }.forEach { (k, v) -> retired[k] = v }
        val methods = tokens.associateWith { t -> previous[t] ?: retired.remove(t) ?: JsonNull }
        return entry.with(
            "machine_methods" to JsonObject(methods),
            "retired_machine_methods" to JsonObject(retired),
            "machines" to tokens.map(::text).array(),
            "management_numbers" to
                assignments
                    .map { it.s("management_number") }
                    .filter { it.isNotEmpty() }
                    .distinct()
                    .map(::text)
                    .array(),
            "booth_assignments" to assignments.array(),
        )
    }

    fun nextOrder(documents: List<JsonObject>, day: String): Int =
        documents
            .filter { it["deleted"] != JsonPrimitive(true) }
            .flatMap { it.o("body").a("entries") }
            .map { it.jsonObject }
            .filter { it.s("day") == day }
            .maxOfOrNull { it.n("order")?.toInt() ?: 0 }
            ?.plus(1) ?: 1

    fun edited(entry: JsonObject, fields: List<String>) =
        entry.with(
            "manually_edited_fields" to
                (entry.a("manually_edited_fields").map { it.jsonPrimitive.content } + fields)
                    .distinct()
                    .map(::text)
                    .array(),
            "updated_at" to text(instant()),
        )

    fun template(
        template: JsonObject,
        day: String,
        order: Int,
        machines: JsonObject,
        token: String = "",
    ): JsonObject {
        val stamp = instant()
        return assign(
            template.with(
                "entry_id" to text(id()),
                "day" to text(day),
                "order" to JsonPrimitive(order),
                "created_at" to text(stamp),
                "updated_at" to text(stamp),
                "included" to JsonPrimitive(true),
            ),
            token,
            machines,
        )
    }
}
