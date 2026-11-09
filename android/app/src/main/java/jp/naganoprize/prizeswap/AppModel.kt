package jp.naganoprize.prizeswap

import android.app.Application
import androidx.compose.runtime.*
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import java.time.LocalDate
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*

class AppModel
@JvmOverloads
constructor(app: Application, private val repository: Repository = Repository(app)) :
    AndroidViewModel(app) {
    val codec =
        PackageCodec(
            Schema(
                StrictJson.parse(app.assets.open("schemas/package-v1.schema.json").readBytes())
                    .jsonObject
            )
        )
    private val template =
        StrictJson.parse(app.assets.open("examples/entry-template.json").readBytes()).jsonObject
    private val mutex = Mutex()
    private var saveJob: Job? = null
    var loaded by mutableStateOf(false)
        private set

    var storageHealthy by mutableStateOf(false)
        private set

    var refs by mutableStateOf<JsonObject?>(null)
        private set

    var docs by mutableStateOf<List<JsonObject>>(emptyList())
        private set

    var transfers by mutableStateOf<List<JsonObject>>(emptyList())
        private set

    var metadata = JsonObject(emptyMap())
    var device = ""
    var selectedDoc by mutableStateOf("")
    var selectedMachine by mutableStateOf("")
    var selectedRows by mutableStateOf<Set<String>>(emptySet())
    var editingEntry by mutableStateOf("")
    var page by mutableStateOf("home")
    var period by mutableStateOf("current")
    var search by mutableStateOf("")
    var filter by mutableStateOf("ratio")
    var theme by mutableStateOf("system")
    var displayMode by mutableStateOf("auto")
    var detailVisible by mutableStateOf(true)
    var split by mutableFloatStateOf(.5f)
    var notice by mutableStateOf("")
    var busy by mutableStateOf(false)
    var pending by mutableStateOf<PackageData?>(null)
        private set

    var pendingTransport: String? = null
    private val undos = ArrayDeque<List<JsonObject>>()
    private val redos = ArrayDeque<List<JsonObject>>()
    var undoCount by mutableIntStateOf(0)
        private set

    var redoCount by mutableIntStateOf(0)
        private set

    private var lastEdit = ""
    private var lastEditAt = 0L
    val transferRoot =
        File(app.getExternalFilesDir(null), "transfer").apply {
            listOf("inbox", "outbox", "acks", "receipts").forEach { File(this, it).mkdirs() }
        }

    init {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val savedDevice = repository.state("device")
                    requireData(
                        savedDevice == null ||
                            savedDevice is JsonPrimitive &&
                                savedDevice.isString &&
                                validId(savedDevice.content),
                        "保存済み端末IDが不正です",
                    )
                    val savedMetadata = repository.state("metadata")
                    val savedRefs = repository.state("references")
                    requireData(
                        savedMetadata == null || savedMetadata is JsonObject,
                        "保存済みメタデータが不正です",
                    )
                    requireData(savedRefs == null || savedRefs is JsonObject, "保存済み参照データが不正です")
                    val loadedMetadata = (savedMetadata as? JsonObject) ?: JsonObject(emptyMap())
                    val loadedRefs = savedRefs as? JsonObject
                    val loadedDocs = repository.documents()
                    if (loadedRefs != null) {
                        requireData(validId(loadedMetadata.s("dataset_id")), "保存済み売り場IDが不正です")
                        codec.validateData(
                            loadedRefs.mapValues { it.value.jsonObject } +
                                mapOf("instructions" to obj("documents" to loadedDocs.array()))
                        )
                    } else requireData(loadedDocs.isEmpty(), "指示書に対応する参照データがありません")
                    val loadedTransfers = repository.transfers()
                    device = (savedDevice as? JsonPrimitive)?.content ?: id()
                    metadata = loadedMetadata
                    refs = loadedRefs
                    docs = loadedDocs
                    transfers = loadedTransfers
                    theme =
                        (repository.state("theme") as? JsonPrimitive)?.content?.takeIf {
                            it in listOf("system", "light", "dark")
                        } ?: "system"
                    displayMode =
                        (repository.state("displayMode") as? JsonPrimitive)?.content?.takeIf {
                            it in listOf("auto", "tablet", "phone")
                        } ?: "auto"
                    if (savedDevice == null) repository.put("device", text(device))
                }
                storageHealthy = true
                editingEntry = ""
                selectedDoc = activeDocs().firstOrNull()?.s("document_id") ?: ""
            } catch (e: Exception) {
                notice = "保存データを開けません。データは削除しません：${e.message}"
            }
            loaded = true
        }
    }

    fun activeDocs() = docs.filter { it["deleted"] != JsonPrimitive(true) }

    fun currentDoc() = activeDocs().find { it.s("document_id") == selectedDoc }

    fun machines() =
        refs?.o("machines")
            ?: obj("items" to JsonArray(emptyList()), "references" to JsonArray(emptyList()))

    fun frames() =
        (refs?.o("sales")?.a("frames") ?: JsonArray(emptyList())) +
            (refs?.o("history")?.a("frames") ?: JsonArray(emptyList()))

    fun frame() =
        frames().map { it.jsonObject }.find { it.s("frame_id") == period }
            ?: frames().firstOrNull()?.jsonObject

    fun records() = frame()?.a("records")?.map { it.jsonObject } ?: emptyList()

    fun machine(key: String) =
        machines().a("items").map { it.jsonObject }.find { it.s("machine_id") == key }

    fun record(key: String) = records().find { it.s("machine_id") == key }

    fun chooseMachine(key: String) {
        if (machine(key) == null) {
            notice = "未登録マシンです。Windowsから参照データを更新してください。"
            return
        }
        selectedMachine = key
        detailVisible = true
    }

    fun qr(value: String) {
        runCatching { MachineQr.parse(value) }
            .onSuccess {
                chooseMachine(it)
                page = "floor"
            }
            .onFailure { notice = it.message ?: "QR不正" }
    }

    fun preference(key: String, value: String) {
        if (key == "theme") theme = value else displayMode = value
        if (!storageHealthy) return
        viewModelScope.launch {
            runCatching {
                withContext(Dispatchers.IO) {
                    mutex.withLock { repository.put(key, text(value)) }
                }
            }
                .onFailure { notice = "設定を保存できません：${it.message}" }
        }
    }

    fun edit(next: List<JsonObject>, coalesce: String = "") {
        if (!storageHealthy || busy || next == docs) return
        val time = System.currentTimeMillis()
        if (coalesce.isEmpty() || coalesce != lastEdit || time - lastEditAt > 750) {
            undos.addLast(docs)
            while (undos.size > 100) undos.removeFirst()
        }
        lastEdit = coalesce
        lastEditAt = time
        redos.clear()
        docs = next
        undoCount = undos.size
        redoCount = 0
        scheduleSave()
    }

    private fun scheduleSave() {
        saveJob?.cancel()
        saveJob = viewModelScope.launch {
            delay(300)
            try {
                saveNow()
            } catch (e: CancellationException) {
                throw e // A newer edit/export intentionally cancelled this autosave.
            } catch (e: Exception) {
                notice = "保存できません：${e.message}"
            }
        }
    }

    suspend fun saveNow() {
        check(storageHealthy) { "保存データの読込が完了していないため、上書きを停止しました" }
        val snapshot = docs
        withContext(Dispatchers.IO) { mutex.withLock { repository.saveDocuments(snapshot) } }
    }

    fun flush() {
        if (!storageHealthy) return
        saveJob?.cancel()
        viewModelScope.launch {
            try {
                saveNow()
            } catch (e: Exception) {
                notice = "保存できません：${e.message}"
            }
        }
    }

    fun save() {
        viewModelScope.launch {
            runCatching { saveNow() }
                .onSuccess { notice = "指示書を保存しました" }
                .onFailure { notice = "保存できません：${it.message}" }
        }
    }

    fun undo() {
        if (undos.isEmpty() || busy) return
        redos.addLast(docs)
        docs = undos.removeLast()
        lastEdit = ""
        undoCount = undos.size
        redoCount = redos.size
        scheduleSave()
    }

    fun redo() {
        if (redos.isEmpty() || busy) return
        undos.addLast(docs)
        docs = redos.removeLast()
        lastEdit = ""
        undoCount = undos.size
        redoCount = redos.size
        scheduleSave()
    }

    fun newDoc() {
        if (metadata.s("dataset_id").isEmpty()) {
            notice = "最初にWindowsのZIPを取り込んでください（マシン0台でも作成できます）。"
            return
        }
        val day = LocalDate.now().toString()
        val d =
            Documents.new(
                obj(
                    "title" to text("$day 入替指示書"),
                    "date" to text(day),
                    "entries" to JsonArray(emptyList()),
                    "entry_extras" to JsonObject(emptyMap()),
                ),
                device,
            )
        edit(docs + d)
        editingEntry = ""
        selectedDoc = d.s("document_id")
        selectedRows = emptySet()
    }

    fun updateBody(body: JsonObject, coalesce: String = "") {
        val d = currentDoc() ?: return
        edit(
            docs.map {
                if (it.s("document_id") == selectedDoc) Documents.revise(it, body, device) else it
            },
            coalesce,
        )
    }

    fun updateEntry(key: String, patch: Map<String, JsonElement>, coalesce: String = "") {
        val d = currentDoc() ?: return
        val entries =
            d.o("body").a("entries").map { v ->
                val e = v.jsonObject
                if (e.s("entry_id") != key) e
                else {
                    var next = JsonObject(e.toMutableMap().apply { putAll(patch) })
                    if (patch.containsKey("machines"))
                        next =
                            Documents.assign(
                                next,
                                patch.getValue("machines").jsonArray.joinToString("\n") {
                                    it.jsonPrimitive.content
                                },
                                machines(),
                            )
                    if (patch.containsKey("name") && next["candidate_assignment"] is JsonObject)
                        next =
                            next.with(
                                "candidate_assignment" to
                                    next
                                        .o("candidate_assignment")
                                        .with("replacement_product_name" to patch.getValue("name"))
                            )
                    next.with(
                        "manually_edited_fields" to
                            (e.a("manually_edited_fields").map { it.jsonPrimitive.content } +
                                    patch.keys)
                                .distinct()
                                .map(::text)
                                .array(),
                        "updated_at" to text(instant()),
                    )
                }
            }
        updateBody(d.o("body").with("entries" to entries.array()), coalesce)
    }

    fun addMachine(key: String, source: String = "manual") {
        if (currentDoc() == null) newDoc()
        val d = currentDoc() ?: return
        val m = machine(key)
        val allEntries = activeDocs().flatMap { it.o("body").a("entries") }
        if (
            key.isNotEmpty() &&
                allEntries.any { e ->
                    e.jsonObject.a("booth_assignments").any {
                        it.jsonObject.s("machine_key") == key
                    }
                }
        ) {
            notice = "このマシンはすでに指示書に登録されています"
            return
        }
        val entries = d.o("body").a("entries")
        val day = d.o("body").s("date")
        var e =
            Documents.template(
                    template,
                    day,
                    Documents.nextOrder(docs, day),
                    machines(),
                    m?.s("number")?.takeIf { it.isNotEmpty() } ?: m?.s("management_number") ?: "",
                )
                .with("source_type" to text(source), "source_method" to text("android"))
        if (m != null)
            e =
                e.with(
                    "booth_assignments" to
                        e.a("booth_assignments")
                            .map { it.jsonObject.with("machine_key" to text(key)) }
                            .array()
                )
        var entry = e
        if (source == "candidate" && m != null) {
            val r = record(key)
            val a = e.a("booth_assignments")[0].jsonObject
            val candidate =
                obj(
                    "assignment_id" to a.getValue("assignment_id"),
                    "machine_key" to text(key),
                    "machine_number" to a.getValue("machine_number"),
                    "management_number" to a.getValue("management_number"),
                    "machine_display_number" to
                        text(m.s("number").ifEmpty { m.s("management_number") }),
                    "current_product_name" to text(r?.s("product") ?: m.s("product_name")),
                    "replacement_product_name" to text(""),
                    "operating_method" to text(m.s("operating_method")),
                    "average_ratio" to (r?.get("ratio") ?: JsonNull),
                    "sales_amount" to (r?.get("sales") ?: JsonNull),
                    "installed_days" to (r?.get("days") ?: JsonNull),
                    "source_candidate_status" to text(r?.s("assessment") ?: "未評価"),
                    "source_candidate_reason" to
                        text(
                            r?.o("assessment_detail")
                                ?.a("reasons")
                                ?.joinToString(" / ") { it.jsonPrimitive.content }
                                .orEmpty()
                        ),
                    "created_at" to e.getValue("created_at"),
                    "confirmed_replacement_date" to JsonNull,
                )
            entry = e.with("candidate_assignment" to candidate)
        }
        val extras =
            d.o("body")
                .o("entry_extras")
                .with(
                    e.s("entry_id") to
                        obj("current_product_name" to text(m?.s("product_name") ?: ""))
                )
        updateBody(
            d.o("body").with("entries" to (entries + entry).array(), "entry_extras" to extras)
        )
        notice = "新商品名を空欄のまま追加しました"
    }

    fun deleteSelected() {
        val d = currentDoc() ?: return
        updateBody(
            d.o("body")
                .with(
                    "entries" to
                        d.o("body")
                            .a("entries")
                            .filter { it.jsonObject.s("entry_id") !in selectedRows }
                            .array()
                )
        )
        selectedRows = emptySet()
    }

    fun changeDate(day: String) {
        val d = currentDoc() ?: return
        if (day == d.o("body").s("date")) return
        LocalDate.parse(day)
        var order = Documents.nextOrder(docs.filter { it.s("document_id") != selectedDoc }, day)
        val entries =
            d.o("body")
                .a("entries")
                .sortedBy { it.jsonObject.n("order") }
                .map {
                    Documents.edited(
                        it.jsonObject.with("day" to text(day), "order" to JsonPrimitive(order++)),
                        listOf("day", "order"),
                    )
                }
        updateBody(d.o("body").with("date" to text(day), "entries" to entries.array()), "date")
    }

    fun duplicateSelected() {
        val d = currentDoc() ?: return
        val entries = d.o("body").a("entries")
        val copy =
            Documents.copy(
                    d.with(
                        "body" to
                            d.o("body")
                                .with(
                                    "entries" to
                                        entries
                                            .filter { it.jsonObject.s("entry_id") in selectedRows }
                                            .array()
                                )
                    ),
                    device,
                )
                .o("body")
        val next = mutableMapOf<String, Int>()
        val copied =
            copy.a("entries").map { value ->
                val e = value.jsonObject
                val day = e.s("day")
                val order = next[day] ?: Documents.nextOrder(docs, day)
                next[day] = order + 1
                Documents.edited(e.with("order" to JsonPrimitive(order)), listOf("order"))
            }
        updateBody(
            d.o("body")
                .with(
                    "entries" to (entries + copied).array(),
                    "entry_extras" to
                        JsonObject(d.o("body").o("entry_extras") + copy.o("entry_extras")),
                )
        )
    }

    fun moveSelected(delta: Int) {
        val d = currentDoc() ?: return
        val entries = d.o("body").a("entries").map { it.jsonObject }
        val output =
            entries
                .groupBy { it.s("day") }
                .values
                .flatMap { group ->
                    val rows = group.sortedBy { it.n("order") }.toMutableList()
                    val slots = rows.map { it.getValue("order") }
                    val indexes = if (delta < 0) rows.indices.toList() else rows.indices.reversed()
                    for (i in indexes) {
                        val j = i + delta
                        if (
                            j in rows.indices &&
                                rows[i].s("entry_id") in selectedRows &&
                                rows[j].s("entry_id") !in selectedRows
                        ) {
                            val item = rows[i]
                            rows[i] = rows[j]
                            rows[j] = item
                        }
                    }
                    rows.mapIndexed { i, e ->
                        if (e["order"] == slots[i]) e
                        else Documents.edited(e.with("order" to slots[i]), listOf("order"))
                    }
                }
        updateBody(d.o("body").with("entries" to output.array()))
    }

    fun bulkMethod(method: String) {
        val d = currentDoc() ?: return
        val entries =
            d.o("body").a("entries").map { v ->
                val e = v.jsonObject
                if (e.s("entry_id") !in selectedRows) e
                else
                    e.with(
                        "methods" to
                            (if (method.isBlank()) emptyList() else listOf(text(method))).array(),
                        "machine_methods" to
                            JsonObject(
                                e.a("machines").associate {
                                    it.jsonPrimitive.content to
                                        text(method.takeIf { it.isNotBlank() })
                                }
                            ),
                        "manually_edited_fields" to
                            (e.a("manually_edited_fields") +
                                    text("methods") +
                                    text("machine_methods"))
                                .distinct()
                                .array(),
                        "updated_at" to text(instant()),
                    )
            }
        updateBody(d.o("body").with("entries" to entries.array()))
    }

    fun preview(raw: ByteArray, transport: String? = null) {
        if (busy) return
        busy = true
        viewModelScope.launch {
            try {
                saveJob?.cancel()
                saveNow()
                val p = withContext(Dispatchers.IO) { codec.read(raw) }
                transport?.let {
                    requireData(it == p.manifest.s("package_id"), "転送ファイル名とパッケージIDが一致しません")
                }
                val dataset = metadata.s("dataset_id")
                requireData(
                    dataset.isEmpty() || p.manifest.s("dataset_id") == dataset,
                    "別の売り場のパッケージです。データを混同しないため取り込めません。",
                )
                requireData(
                    p.manifest.s("source_app") == "windows" ||
                        p.manifest.s("kind") == "instructions",
                    "参照データの管理元はWindowsです",
                )
                if (text(p.manifest.s("package_id")) in metadata.a("packages")) {
                    notice = "このパッケージは取込済みです"
                    transport?.let { ack(it, "already_imported") }
                } else {
                    p.data.getValue("instructions").a("documents").forEach { incoming ->
                        Documents.relation(
                            docs.find {
                                it.s("document_id") == incoming.jsonObject.s("document_id")
                            },
                            incoming.jsonObject,
                        )
                    }
                    pending = p
                    pendingTransport = transport
                    transport?.let { ack(it, "pending") }
                }
            } catch (e: Exception) {
                notice = "取込できません：${e.message}"
                transport?.let { ack(it, "error", notice) }
            } finally {
                busy = false
            }
        }
    }

    fun cancelImport() {
        pendingTransport?.let { ack(it, "rejected") }
        pending = null
        pendingTransport = null
    }

    fun base(local: JsonObject, incoming: JsonObject): JsonObject? {
        val all = repository.revisions().associateBy { it.s("revision_id") }
        return (local.a("ancestors") + text(local.s("revision_id")))
            .reversed()
            .firstNotNullOfOrNull {
                if (it in incoming.a("ancestors")) all[it.jsonPrimitive.content] else null
            }
    }

    fun mergeInfo(incoming: JsonObject): List<Documents.Conflict> {
        val local =
            docs.find { it.s("document_id") == incoming.s("document_id") } ?: return emptyList()
        val b = base(local, incoming) ?: error("共通基点がありません")
        requireData(local["deleted"] == incoming["deleted"], "削除と編集の競合は保持・採用・別名保存で解決してください")
        return Documents.merge(b.o("body"), local.o("body"), incoming.o("body")).second
    }

    fun applyImport(
        actions: Map<String, String>,
        fields: Map<String, Map<String, String>>,
        replaceReferences: Boolean,
    ) {
        val p = pending ?: return
        if (busy) return
        busy = true
        viewModelScope.launch {
            try {
                val next = docs.associateBy { it.s("document_id") }.toMutableMap()
                withContext(Dispatchers.IO) {
                    p.data.getValue("instructions").a("documents").forEach { value ->
                        val incoming = value.jsonObject
                        val key = incoming.s("document_id")
                        val local = next[key]
                        val relation = Documents.relation(local, incoming)
                        when (
                            actions[key]
                                ?: if (relation in listOf("new", "incoming")) "incoming"
                                else "local"
                        ) {
                            "incoming" -> next[key] = incoming
                            "copy" -> {
                                val copy = Documents.copy(incoming, device)
                                next[copy.s("document_id")] = copy
                            }
                            "merge" -> {
                                requireData(local != null, "統合元がありません")
                                val old = local!!
                                val b = base(old, incoming) ?: error("共通基点がありません")
                                requireData(old["deleted"] == incoming["deleted"], "削除と編集が競合しています")
                                val (body, conflicts) =
                                    Documents.merge(
                                        b.o("body"),
                                        old.o("body"),
                                        incoming.o("body"),
                                        fields[key] ?: emptyMap(),
                                    )
                                requireData(conflicts.isEmpty(), "項目ごとの競合を解決してください")
                                var merged =
                                    Documents.revise(
                                        old,
                                        body!!.jsonObject,
                                        device,
                                        old["deleted"] == JsonPrimitive(true),
                                    )
                                if (merged.s("revision_id") == old.s("revision_id"))
                                    merged =
                                        merged.with(
                                            "revision_id" to text(id()),
                                            "revision" to
                                                JsonPrimitive(
                                                    (old.n("revision") ?: 0.0).toInt() + 1
                                                ),
                                            "updated_at" to text(instant()),
                                            "updated_by" to text(device),
                                            "ancestors" to
                                                (old.a("ancestors") + text(old.s("revision_id")))
                                                    .takeLast(128)
                                                    .array(),
                                        )
                                next[key] =
                                    merged.with(
                                        "ancestors" to
                                            (merged.a("ancestors") +
                                                    incoming.a("ancestors") +
                                                    text(incoming.s("revision_id")))
                                                .distinct()
                                                .takeLast(128)
                                                .array()
                                    )
                            }
                            "local" -> {}
                            else -> error("競合処理が不正です")
                        }
                    }
                }
                val newRefs =
                    if (refs == null || replaceReferences && p.manifest.s("kind") != "instructions")
                        JsonObject(p.data.filterKeys { it != "instructions" })
                    else refs
                requireData(newRefs != null, "参照データがありません")
                val proposed =
                    newRefs!!.mapValues { it.value.jsonObject } +
                        mapOf("instructions" to obj("documents" to next.values.toList().array()))
                withContext(Dispatchers.Default) { codec.validateData(proposed) }
                val meta =
                    metadata.with(
                        "dataset_id" to p.manifest.getValue("dataset_id"),
                        "packages" to
                            (metadata.a("packages") + p.manifest.getValue("package_id"))
                                .takeLast(1000)
                                .array(),
                        "last_import" to text(instant()),
                        "source_created_at" to p.manifest.getValue("created_at"),
                        "data_updated_at" to (p.manifest["data_updated_at"] ?: JsonNull),
                    )
                val event =
                    obj(
                        "package_id" to p.manifest.getValue("package_id"),
                        "timestamp" to text(instant()),
                        "direction" to text("import"),
                        "status" to text("imported"),
                    )
                withContext(Dispatchers.IO) {
                    mutex.withLock {
                        repository.importData(newRefs, meta, next.values.toList(), event)
                    }
                }
                refs = newRefs
                docs = next.values.toList()
                metadata = meta
                transfers = transfers + event
                undos.clear()
                redos.clear()
                undoCount = 0
                redoCount = 0
                pendingTransport?.let { ack(it, "imported") }
                pending = null
                pendingTransport = null
                editingEntry = ""
                selectedDoc = activeDocs().firstOrNull()?.s("document_id") ?: ""
                notice = "取込が完了しました"
            } catch (e: Exception) {
                notice = "取込できません。既存データを維持しました：${e.message}"
                pendingTransport?.let { ack(it, "error", notice) }
            } finally {
                busy = false
            }
        }
    }

    suspend fun export(kind: String = "instructions"): ByteArray {
        saveJob?.cancel()
        saveNow()
        val data = refs ?: error("先にWindowsのZIPを取り込んでください")
        val all =
            data.mapValues { it.value.jsonObject } +
                mapOf(
                    "instructions" to
                        obj("documents" to (if (kind == "reference") emptyList() else docs).array())
                )
        return withContext(Dispatchers.IO) {
            codec.write(all, kind, device, metadata.s("dataset_id"))
        }
    }

    fun exported(raw: ByteArray, direction: String = "export") {
        viewModelScope.launch {
            val p = withContext(Dispatchers.IO) { codec.read(raw) }
            val e =
                obj(
                    "package_id" to p.manifest.getValue("package_id"),
                    "timestamp" to text(instant()),
                    "direction" to text(direction),
                    "status" to text(if (direction == "usb") "prepared" else "exported"),
                )
            withContext(Dispatchers.IO) { repository.transfer(e) }
            transfers = transfers + e
        }
    }

    private fun ack(pid: String, status: String, message: String = "") {
        if (!validId(pid)) return
        val file = File(transferRoot, "acks/$pid.json")
        val tmp = File(file.path + ".part")
        tmp.writeText(
            obj("package_id" to text(pid), "status" to text(status), "message" to text(message))
                .toString()
        )
        requireData(tmp.renameTo(file), "転送応答を保存できません")
    }

    fun pollTransfer() {
        if (!loaded || !storageHealthy || busy || pending != null) return
        viewModelScope.launch {
            try {
                val receipts =
                    withContext(Dispatchers.IO) {
                        File(transferRoot, "receipts")
                            .listFiles()
                            ?.filter { it.extension == "json" }
                            ?.mapNotNull { file ->
                                runCatching {
                                    val e = StrictJson.parse(file.readBytes()).jsonObject
                                    requireData(
                                        validId(e.s("package_id")) &&
                                            e.s("status") in
                                                listOf("imported", "rejected", "error"),
                                        "応答不正",
                                    )
                                    file to e
                                }
                                    .getOrNull()
                            } ?: emptyList()
                    }
                for ((file, e) in receipts) {
                    val event =
                        e.with("timestamp" to text(instant()), "direction" to text("usb-receipt"))
                    withContext(Dispatchers.IO) {
                        repository.transfer(event)
                        file.delete()
                    }
                    transfers = transfers.filter { it.s("package_id") != e.s("package_id") } + event
                    notice = "Windowsからの受信結果：${e.s("status")}"
                }
                val file =
                    withContext(Dispatchers.IO) {
                        File(transferRoot, "inbox")
                            .listFiles()
                            ?.filter {
                                it.extension == "zip" &&
                                    validId(it.nameWithoutExtension) &&
                                    !File(transferRoot, "acks/${it.nameWithoutExtension}.json")
                                        .exists()
                            }
                            ?.minByOrNull { it.lastModified() }
                    }
                if (file != null) {
                    requireData(file.length() <= PackageCodec.MAX_TOTAL, "ZIPが大きすぎます")
                    val bytes = withContext(Dispatchers.IO) { file.readBytes() }
                    preview(bytes, file.nameWithoutExtension)
                }
            } catch (e: Exception) {
                notice = "USB受信を確認してください：${e.message}"
            }
        }
    }

    fun prepareUsb() {
        viewModelScope.launch {
            try {
                val raw = export()
                val p = withContext(Dispatchers.IO) { codec.read(raw) }
                val pid = p.manifest.s("package_id")
                withContext(Dispatchers.IO) {
                    val out = File(transferRoot, "outbox/$pid.zip")
                    val temp = File(out.path + ".part")
                    temp.writeBytes(raw)
                    requireData(temp.renameTo(out), "ZIP保存失敗")
                    val latest = File(transferRoot, "outbox/latest.json")
                    val t = File(latest.path + ".part")
                    t.writeText(obj("package_id" to text(pid)).toString())
                    requireData(t.renameTo(latest), "送信情報保存失敗")
                }
                exported(raw, "usb")
                notice = "Windowsへ送るZIPを準備しました。Windows側で「Androidから受信」を選んでください。"
            } catch (e: Exception) {
                notice = "送信準備できません：${e.message}"
            }
        }
    }
}
