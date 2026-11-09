@file:OptIn(
    androidx.compose.material3.ExperimentalMaterial3Api::class,
    androidx.compose.foundation.layout.ExperimentalLayoutApi::class,
)

package jp.naganoprize.prizeswap

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.*
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.toColorInt
import java.time.LocalDate
import kotlin.math.*
import kotlinx.serialization.json.*

private val pages =
    listOf(
        "home" to "ホーム",
        "floor" to "売り場",
        "candidates" to "入替候補",
        "instructions" to "指示書",
        "link" to "データ連携",
    )

private fun pageIcon(key: String) =
    when (key) {
        "home" -> Icons.Default.Home
        "floor" -> Icons.Default.Map
        "candidates" -> Icons.Default.List
        "instructions" -> Icons.Default.Description
        else -> Icons.Default.Sync
    }

fun amount(value: Double?, unit: String = "") =
    value?.let {
        java.text.NumberFormat.getNumberInstance(java.util.Locale.JAPAN)
            .apply { maximumFractionDigits = 2 }
            .format(it) + unit
    } ?: "—"

fun metric(r: JsonObject?, key: String) =
    when (key) {
        "ratio" -> amount(r?.n(key), "%")
        "cost_rate",
        "po_rate",
        "target_po_rate",
        "excel_po_rate" -> amount(r?.n(key)?.times(100), "%")
        "sales",
        "gross_profit",
        "unit_price",
        "cost" -> amount(r?.n(key), "円")
        "days" -> if ((r?.n(key) ?: 0.0) < 0) "投入予定" else amount(r?.n(key), "日")
        "operating_days" -> amount(r?.n(key), "日")
        else -> amount(r?.n(key))
    }

private fun contrast(color: Color) =
    if (.2126 * color.red + .7152 * color.green + .0722 * color.blue > .55) Color(0xFF111827)
    else Color.White

private fun color(raw: String) = runCatching {
    Color(raw.toColorInt())
}
    .getOrDefault(Color(0xFFAEB7C2))

@Composable
fun PrizeApp(
    m: AppModel,
    onOpen: () -> Unit,
    onExport: () -> Unit,
    onShare: () -> Unit,
    onScan: () -> Unit,
) {
    val dark =
        when (m.theme) {
            "dark" -> true
            "light" -> false
            else -> isSystemInDarkTheme()
        }
    MaterialTheme(
        colorScheme =
            if (dark)
                darkColorScheme(
                    primary = Color(0xFF93C5FD),
                    background = Color(0xFF111827),
                    onBackground = Color(0xFFF3F4F6),
                    surface = Color(0xFF1F2937),
                    surfaceContainerLow = Color(0xFF172033),
                    onSurface = Color(0xFFF3F4F6),
                    onSurfaceVariant = Color(0xFFCBD5E1),
                    secondaryContainer = Color(0xFF1E3A5F),
                    onSecondaryContainer = Color(0xFFDBEAFE),
                    outlineVariant = Color(0xFF475569),
                )
            else
                lightColorScheme(
                    primary = Color(0xFF2563EB),
                    background = Color(0xFFF4F6FA),
                    onBackground = Color(0xFF111827),
                    surface = Color.White,
                    surfaceContainerLow = Color(0xFFF8FAFC),
                    onSurface = Color(0xFF111827),
                    onSurfaceVariant = Color(0xFF64748B),
                    secondaryContainer = Color(0xFFDBEAFE),
                    onSecondaryContainer = Color(0xFF1E3A8A),
                    outlineVariant = Color(0xFFCBD5E1),
                )
    ) {
        Surface(
            Modifier.fillMaxSize(),
            color = MaterialTheme.colorScheme.background,
            contentColor = MaterialTheme.colorScheme.onBackground,
        ) {
            BoxWithConstraints(
                Modifier.fillMaxSize()
                    .background(MaterialTheme.colorScheme.background)
                    .windowInsetsPadding(WindowInsets.safeDrawing)
            ) {
                val tablet =
                    when (m.displayMode) {
                        "tablet" -> true
                        "phone" -> false
                        else -> maxWidth >= 700.dp
                    }
                val fullEditor =
                    m.page == "instructions" &&
                        m.editingEntry.isNotEmpty() &&
                        (!tablet || maxWidth < 700.dp)
                Column {
                    if (!fullEditor)
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "売り場管理",
                                fontWeight = FontWeight.Bold,
                                fontSize = 20.sp,
                                modifier = Modifier.weight(1f),
                            )
                            IconButton(onClick = { m.page = "settings" }) {
                                Icon(Icons.Default.Settings, "表示設定")
                            }
                            IconButton(onClick = onScan) {
                                Icon(Icons.Default.QrCodeScanner, "QR読取")
                            }
                            Choice(
                                m.theme,
                                listOf("system" to "システム", "light" to "ライト", "dark" to "ダーク"),
                                { m.preference("theme", it) },
                            )
                        }
                    if (m.notice.isNotEmpty())
                        Row(
                            Modifier.fillMaxWidth()
                                .background(MaterialTheme.colorScheme.secondaryContainer)
                                .padding(8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                m.notice,
                                modifier = Modifier.weight(1f),
                                style = MaterialTheme.typography.bodySmall,
                            )
                            IconButton(onClick = { m.notice = "" }) {
                                Icon(Icons.Default.Close, "通知を閉じる")
                            }
                        }
                    if (m.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                    Row(Modifier.weight(1f)) {
                        if (tablet && !fullEditor)
                            NavigationRail {
                                pages.forEach { (key, name) ->
                                    NavigationRailItem(
                                        selected = m.page == key,
                                        onClick = { m.page = key },
                                        icon = { Icon(pageIcon(key), name) },
                                        label = { Text(name) },
                                    )
                                }
                            }
                        Box(Modifier.weight(1f).fillMaxHeight()) {
                            if (!m.loaded)
                                CircularProgressIndicator(Modifier.align(Alignment.Center))
                            else if (!m.storageHealthy)
                                Column(
                                    Modifier.fillMaxSize()
                                        .verticalScroll(rememberScrollState())
                                        .padding(24.dp),
                                    verticalArrangement = Arrangement.spacedBy(12.dp),
                                ) {
                                    Text("保存データを開けません", style = MaterialTheme.typography.titleLarge)
                                    Text(
                                        "既存データを保護するため編集・取込・自動保存を停止しています。アプリのデータを削除せず、保存データを確認してください。"
                                    )
                                    Text(m.notice)
                                }
                            else
                                when (m.page) {
                                    "home" -> HomeScreen(m, onOpen, onScan)
                                    "settings" -> SettingsScreen(m)
                                    "floor" -> FloorScreen(m, tablet)
                                    "candidates" -> CandidatesScreen(m)
                                    "instructions" -> InstructionsScreen(m)
                                    else -> LinkScreen(m, onOpen, onExport, onShare)
                                }
                        }
                    }
                    if (!tablet && !fullEditor)
                        NavigationBar {
                            pages.forEach { (key, name) ->
                                NavigationBarItem(
                                    selected = m.page == key,
                                    onClick = { m.page = key },
                                    icon = { Icon(pageIcon(key), name) },
                                    label = { Text(name) },
                                )
                            }
                        }
                }
            }
            m.pending?.let { ImportDialog(m, it) }
        }
    }
}

@Composable
fun Choice(
    value: String,
    options: List<Pair<String, String>>,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(
            onClick = { expanded = true },
            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
        ) {
            Text(
                options.find { it.first == value }?.second ?: value,
                modifier = Modifier.weight(1f, fill = false),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Icon(Icons.Default.ArrowDropDown, null)
        }
        DropdownMenu(expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (key, label) ->
                DropdownMenuItem(
                    text = { Text(label) },
                    onClick = {
                        expanded = false
                        onSelect(key)
                    },
                )
            }
        }
    }
}

@Composable
fun FloorScreen(m: AppModel, tablet: Boolean) {
    BoxWithConstraints(Modifier.fillMaxSize().padding(8.dp)) {
        val compact = !tablet || maxWidth < 816.dp
        Column(Modifier.fillMaxSize()) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.Center,
            ) {
                OutlinedTextField(
                    m.search,
                    { m.search = it },
                    label = {
                        Text("番号・管理番号・商品を検索", maxLines = 1, overflow = TextOverflow.Ellipsis)
                    },
                    singleLine = true,
                    modifier = if (compact) Modifier.fillMaxWidth() else Modifier.width(300.dp),
                )
                Choice(
                    m.filter,
                    listOf(
                        "ratio" to "アベ比",
                        "sales_metric" to "売上",
                        "po_rate" to "PO率",
                        "cost_rate" to "原価率",
                        "age" to "投入日数",
                        "machine_type" to "機種",
                        "assessment" to "入替候補",
                        "planned" to "投入予定",
                    ),
                    { m.filter = it },
                )
                Choice(
                    m.period,
                    m.frames().map { it.jsonObject.let { f -> f.s("frame_id") to f.s("label") } },
                    { m.period = it },
                )
                TextButton(onClick = { m.detailVisible = !m.detailVisible }) {
                    Text(if (m.detailVisible) "詳細を閉じる" else "詳細を開く")
                }
            }
            val f = m.frame()
            Text(
                "${f?.s("period_start").orEmpty().ifEmpty{"期間不明"}} ～ ${f?.s("period_end").orEmpty()} / ${m.machines().a("items").size}台 / データ更新 ${m.metadata.s("data_updated_at").ifEmpty{"不明"}}",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(vertical = 6.dp),
            )
            Box(Modifier.weight(1f)) {
                if (!compact) {
                    Row {
                        FloorCanvas(
                            m,
                            Modifier.weight(if (m.detailVisible) m.split else 1f).fillMaxHeight(),
                        )
                        if (m.detailVisible) {
                            Column(
                                Modifier.weight(1 - m.split).fillMaxHeight().padding(start = 8.dp)
                            ) {
                                Slider(
                                    m.split,
                                    { m.split = it },
                                    valueRange = 0.3f..0.7f,
                                    modifier = Modifier.height(24.dp),
                                )
                                MachineDetail(m)
                            }
                        }
                    }
                } else FloorCanvas(m, Modifier.fillMaxSize())
            }
            if (m.refs == null)
                Text("Windows連携からZIPを取り込んで開始してください。", modifier = Modifier.padding(8.dp))
        }
        if (compact && m.detailVisible && m.selectedMachine.isNotEmpty())
            Surface(Modifier.fillMaxSize()) {
                Column {
                    TextButton(onClick = { m.detailVisible = false }) { Text("← 売り場レイアウトへ戻る") }
                    Box(Modifier.weight(1f)) { MachineDetail(m) }
                }
            }
    }
}

data class NumberedBooth(
    val number: Int,
    val entry: JsonObject,
    val assignment: JsonObject,
    val machineId: String?,
)

fun planBooths(m: AppModel): List<NumberedBooth> {
    val day = m.currentDoc()?.o("body")?.s("date") ?: return emptyList()
    val entries =
        m.activeDocs()
            .flatMap { it.o("body").a("entries") }
            .map { it.jsonObject }
            .filter { it.s("day") == day && it["included"] == JsonPrimitive(true) }
            .sortedBy { it.n("order") }
    var n = 0
    return entries.flatMap { entry ->
        entry.a("booth_assignments").map { value ->
            val a = value.jsonObject
            val known = m.machine(a.s("machine_key"))
            val token =
                a.s("machine_number").ifEmpty { a.s("management_number") }.ifEmpty { a.s("token") }
            val found = known ?: Documents.lookup(token, m.machines()).singleOrNull()
            NumberedBooth(++n, entry, a, found?.s("machine_id"))
        }
    }
}

@Composable
fun FloorCanvas(m: AppModel, modifier: Modifier, map: Boolean = false, context: Boolean = false) {
    val rects = m.refs?.o("layout")?.a("items")?.map { it.jsonObject } ?: emptyList()
    val machines =
        m.machines().a("items").associate { it.jsonObject.let { v -> v.s("machine_id") to v } }
    val records = m.records().associateBy { it.s("machine_id") }
    val booths = if (map) planBooths(m) else emptyList()
    val numbers = booths.filter { it.machineId != null }.groupBy { it.machineId!! }
    val displayed = if (map && !context) rects.filter { it.s("machine_id") in numbers } else rects
    var zoom by remember(map) { mutableFloatStateOf(1f) }
    var pan by remember(map) { mutableStateOf(Offset.Zero) }
    var reset by remember { mutableIntStateOf(0) }
    var bounds by remember { mutableStateOf(Size.Zero) }
    val target =
        if (map) rects.filter { it.s("machine_id") in numbers }.ifEmpty { displayed } else displayed
    val left = target.minOfOrNull { it.n("x") ?: 0.0 }?.toFloat() ?: 0f
    val top = target.minOfOrNull { it.n("y") ?: 0.0 }?.toFloat() ?: 0f
    val right =
        target.maxOfOrNull { (it.n("x") ?: 0.0) + (it.n("width") ?: 180.0) }?.toFloat() ?: 500f
    val bottom =
        target.maxOfOrNull { (it.n("y") ?: 0.0) + (it.n("height") ?: 120.0) }?.toFloat() ?: 500f
    val fit =
        min(
                (bounds.width - 24) / max(1f, right - left),
                (bounds.height - 24) / max(1f, bottom - top),
            )
            .coerceAtLeast(.001f)
    val scale = fit * zoom
    val origin =
        Offset(
            (bounds.width - (right - left) * fit) / 2 - left * fit,
            (bounds.height - (bottom - top) * fit) / 2 - top * fit,
        ) + pan
    val currentOrigin by rememberUpdatedState(origin)
    val paint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.create("sans-serif", Typeface.BOLD)
        }
    }
    val background = MaterialTheme.colorScheme.surfaceContainerLow
    Box(modifier.background(background, RoundedCornerShape(8.dp))) {
        Canvas(
            Modifier.fillMaxSize()
                .pointerInput(map, rects, scale, origin) {
                    detectTapGestures { point ->
                        val x = (point.x - origin.x) / scale
                        val y = (point.y - origin.y) / scale
                        displayed
                            .lastOrNull {
                                val a = it.n("x") ?: 0.0
                                val b = it.n("y") ?: 0.0
                                x >= a &&
                                    x <= a + (it.n("width") ?: 0.0) &&
                                    y >= b &&
                                    y <= b + (it.n("height") ?: 0.0)
                            }
                            ?.let { r ->
                                val key = r.s("machine_id")
                                m.chooseMachine(key)
                                if (map)
                                    numbers[key]?.firstOrNull()?.let {
                                        m.selectedRows = setOf(it.entry.s("entry_id"))
                                    }
                            }
                    }
                }
                .pointerInput(reset) {
                    detectTransformGestures { centroid, delta, factor, _ ->
                        val next = (zoom * factor).coerceIn(.15f, 16f)
                        val actual = next / zoom
                        pan = (pan + delta) + (centroid - currentOrigin) * (1 - actual)
                        zoom = next
                    }
                }
        ) {
            bounds = size
            displayed.forEach { r ->
                val key = r.s("machine_id")
                val machine = machines[key] ?: return@forEach
                val record = records[key]
                val x = origin.x + (r.n("x") ?: 0.0).toFloat() * scale
                val y = origin.y + (r.n("y") ?: 0.0).toFloat() * scale
                val w = (r.n("width") ?: 180.0).toFloat() * scale
                val h = (r.n("height") ?: 120.0).toFloat() * scale
                if (x + w < 0 || y + h < 0 || x > size.width || y > size.height) return@forEach
                val matched =
                    m.search.isBlank() ||
                        listOf(
                                machine.s("number"),
                                machine.s("management_number"),
                                record?.s("product") ?: machine.s("product_name"),
                            )
                            .any { it.contains(m.search, true) }
                val color =
                    if (map)
                        if (key in numbers) Color(0xFFA9D7FF)
                        else Color(0xFFAEB7C2).copy(alpha = .15f)
                    else
                        color(record?.o("grades")?.o(m.filter)?.s("color") ?: "#AEB7C2")
                            .copy(alpha = if (matched) 1f else .28f)
                drawRoundRect(
                    color,
                    Offset(x, y),
                    Size(w, h),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(3f),
                )
                drawRoundRect(
                    if (
                        m.selectedMachine == key ||
                            map &&
                                numbers[key]?.any { it.entry.s("entry_id") in m.selectedRows } ==
                                    true
                    )
                        Color(0xFF2563EB)
                    else Color(0xFF64748B),
                    Offset(x, y),
                    Size(w, h),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(3f),
                    style = Stroke(if (m.selectedMachine == key) 3f else 1f),
                )
                clipRect(x + 2, y + 2, x + w - 2, y + h - 2) {
                    paint.color =
                        android.graphics.Color.argb(
                            if (matched || map) 255 else 100,
                            (contrast(color).red * 255).toInt(),
                            (contrast(color).green * 255).toInt(),
                            (contrast(color).blue * 255).toInt(),
                        )
                    if (map) {
                        val ns = numbers[key]?.joinToString(", ") { it.number.toString() }.orEmpty()
                        if (ns.isNotEmpty()) {
                            paint.textSize = min(w * .6f, h * .65f).coerceAtLeast(10f)
                            val tw = paint.measureText(ns)
                            drawCircle(Color.White, min(w, h) * .42f, Offset(x + w / 2, y + h / 2))
                            paint.color = android.graphics.Color.rgb(15, 35, 60)
                            drawContext.canvas.nativeCanvas.drawText(
                                ns,
                                x + (w - tw) / 2,
                                y + h / 2 - paint.fontMetrics.let { (it.ascent + it.descent) / 2 },
                                paint,
                            )
                        }
                    } else {
                        paint.textSize = max(5f, (r.n("font_size") ?: 12.0).toFloat() * scale)
                        val lines =
                            listOf(
                                machine
                                    .s("number")
                                    .ifEmpty { machine.s("management_number") }
                                    .ifEmpty { "番号未設定" },
                                when (m.filter) {
                                    "ratio" -> "アベ比 ${metric(record,"ratio")}"
                                    "sales_metric" -> "売上 ${metric(record,"sales")}"
                                    "po_rate" -> "PO率 ${metric(record,"po_rate")}"
                                    "cost_rate" -> "原価率 ${metric(record,"cost_rate")}"
                                    "age" -> "投入 ${metric(record,"days")}"
                                    else -> record?.o("grades")?.o(m.filter)?.s("label").orEmpty()
                                },
                                record?.s("product") ?: machine.s("product_name"),
                            )
                        lines.forEachIndexed { i, t ->
                            drawContext.canvas.nativeCanvas.drawText(
                                t,
                                x + 4,
                                y + paint.textSize * (i + 1) * 1.2f,
                                paint,
                            )
                        }
                    }
                }
            }
        }
        Row(
            Modifier.align(Alignment.BottomEnd)
                .background(MaterialTheme.colorScheme.surface.copy(alpha = .9f))
        ) {
            TextButton(
                onClick = {
                    zoom = 1f
                    pan = Offset.Zero
                    reset++
                }
            ) {
                Text("フィット")
            }
            IconButton(onClick = { zoom = (zoom * 1.3f).coerceAtMost(16f) }) {
                Icon(Icons.Default.Add, "拡大")
            }
            IconButton(onClick = { zoom = (zoom / 1.3f).coerceAtLeast(.15f) }) {
                Icon(Icons.Default.Remove, "縮小")
            }
        }
    }
}

@Composable
fun MachineDetail(m: AppModel) {
    val machine = m.machine(m.selectedMachine)
    val r = m.record(m.selectedMachine)
    if (machine == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("マシンを選択してください") }
        return
    }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            machine.s("number").ifEmpty { machine.s("management_number") }.ifEmpty { "番号未設定" },
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )
        Text(
            "管理 ${machine.s("management_number").ifEmpty { "—" }} / ${machine.s("machine_type")}",
            style = MaterialTheme.typography.bodySmall,
        )
        Text(r?.s("product") ?: machine.s("product_name"), fontWeight = FontWeight.Bold)
        Text(
            "${r?.s("match_method").orEmpty()} / ${r?.s("period_text").orEmpty()}",
            style = MaterialTheme.typography.bodySmall,
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf(
                    "ratio" to "アベ比",
                    "sales" to "売上",
                    "cost_rate" to "原価率",
                    "po_rate" to "PO率",
                    "days" to "投入日数",
                )
                .forEach { (k, label) ->
                    ElevatedCard(Modifier.widthIn(min = 126.dp)) {
                        Column(Modifier.padding(10.dp)) {
                            Text(label, style = MaterialTheme.typography.labelMedium)
                            Text(metric(r, k), fontSize = 21.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
        }
        Radar(r?.a("radar") ?: JsonArray(emptyList()))
        r?.a("radar")?.forEach {
            val a = it.jsonObject
            Text(
                "${a.s("label")}  ${a.s("actual")}\n${a.s("goal")}  ${a.s("warning")}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Text(
            r?.o("assessment_detail")
                ?.a("reasons")
                ?.joinToString("\n") { it.jsonPrimitive.content }
                .orEmpty(),
            style = MaterialTheme.typography.bodySmall,
        )
        Button(
            onClick = {
                m.addMachine(m.selectedMachine)
                m.page = "instructions"
            }
        ) {
            Text("入替指示書に追加")
        }
        Text("詳細・取得元", fontWeight = FontWeight.Bold)
        listOf(
                "method" to "運営方法",
                "installed_on" to "設置日",
                "updated_at" to "更新日時",
                "source_file" to "取得元",
            )
            .forEach { (k, l) ->
                Text(
                    "$l：${r?.s(k).orEmpty().ifEmpty{"—"}}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        listOf(
                "operating_days" to "稼働日数",
                "dispensed" to "払出数",
                "per_item" to "個当たり",
                "unit_price" to "正式単価",
                "cost" to "原価総額",
                "gross_profit" to "粗利益",
                "target_po_rate" to "適正PO率",
                "excel_po_rate" to "Excel P/O率（原値・定義確認用）",
            )
            .forEach { (k, l) ->
                Text("$l：${metric(r,k)}", style = MaterialTheme.typography.bodySmall)
            }
        r?.o("po_guidance")?.let { po ->
            Text(
                "使用原価 ${amount(po.n("prize_cost"),"円")} / ${po.s("cost_source")}\nプレイ料金 ${amount(po.n("play_price"),"円")} / ${po.s("price_source")}\n${po.s("explanation")}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
fun Radar(axes: JsonArray) {
    val primary = MaterialTheme.colorScheme.primary
    val grid = MaterialTheme.colorScheme.outlineVariant
    val textColor = MaterialTheme.colorScheme.onSurface
    val paint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.create("sans-serif", Typeface.BOLD)
        }
    }
    Canvas(Modifier.fillMaxWidth().height(270.dp)) {
        val center = Offset(size.width / 2, size.height / 2)
        val radius = min(size.width * .30f, size.height * .32f)
        fun point(i: Int, r: Float): Offset {
            val angle = -PI / 2 + 2 * PI * i / 5
            return center + Offset(cos(angle).toFloat() * r, sin(angle).toFloat() * r)
        }
        for (level in 1..5) {
            val path = androidx.compose.ui.graphics.Path()
            for (i in 0..4) {
                val p = point(i, radius * level / 5)
                if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
            }
            path.close()
            drawPath(path, grid, style = Stroke(1f))
        }
        for (i in 0..4) drawLine(grid, center, point(i, radius))
        if (axes.size == 5 && axes.all { it.jsonObject.n("score") != null }) {
            val path = androidx.compose.ui.graphics.Path()
            axes.forEachIndexed { i, a ->
                val p = point(i, radius * (a.jsonObject.n("score")!!.toFloat() / 100))
                if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
            }
            path.close()
            drawPath(path, primary.copy(alpha = .2f))
            drawPath(path, primary, style = Stroke(3f))
        }
        axes.take(5).forEachIndexed { i, a ->
            val axis = a.jsonObject
            axis.n("score")?.let { drawCircle(primary, 5f, point(i, radius * it.toFloat() / 100)) }
            val p = point(i, radius * 1.35f)
            paint.textSize = 12.sp.toPx()
            paint.color =
                android.graphics.Color.argb(
                    255,
                    (textColor.red * 255).toInt(),
                    (textColor.green * 255).toInt(),
                    (textColor.blue * 255).toInt(),
                )
            val lines = listOf(axis.s("label"), axis.s("actual").ifEmpty { "—" })
            lines.forEachIndexed { j, t ->
                drawContext.canvas.nativeCanvas.drawText(
                    t,
                    p.x - paint.measureText(t) / 2,
                    p.y + j * paint.textSize * 1.25f,
                    paint,
                )
            }
        }
    }
}

@Composable
fun CandidatesScreen(m: AppModel) {
    var search by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Text("入替候補", style = MaterialTheme.typography.headlineSmall)
        Text("判定と数値は選択期間のWindows共通処理によるものです。", style = MaterialTheme.typography.bodySmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                search,
                { search = it },
                label = { Text("番号・商品検索") },
                singleLine = true,
            )
            Choice(
                status,
                listOf(
                    "" to "すべて",
                    "要確認" to "要確認",
                    "入替検討" to "入替検討",
                    "好調" to "好調",
                    "判定不能" to "判定不能",
                    "暫定判定" to "暫定判定",
                    "判定保留" to "判定保留",
                ),
                { status = it },
            )
        }
        val rows =
            m.records().filter { r ->
                (status.isBlank() || r.s("assessment") == status) &&
                    (search.isBlank() ||
                        listOf(r.s("number"), r.s("source_key"), r.s("product")).any {
                            it.contains(search, true)
                        })
            }
        Text("${rows.size}台", modifier = Modifier.padding(vertical = 8.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(rows, key = { it.s("machine_id") }) { r ->
                val statusColor = color(r.o("grades").o("assessment").s("color"))
                Card(
                    colors =
                        CardDefaults.cardColors(containerColor = statusColor.copy(alpha = .18f))
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            "${r.s("assessment")}  ${r.s("number")} / ${r.s("source_key")}",
                            fontWeight = FontWeight.Bold,
                        )
                        Text(r.s("product"))
                        Text(
                            "アベ比 ${metric(r,"ratio")} / 投入 ${metric(r,"days")} / 売上 ${metric(r,"sales")} / PO ${metric(r,"po_rate")} / 原価率 ${metric(r,"cost_rate")}"
                        )
                        val detail = r.o("assessment_detail")
                        Text(
                            detail
                                .a("reasons")
                                .joinToString("\n") { it.jsonPrimitive.content }
                                .ifEmpty { "判定条件はWindowsの設定を参照してください。" },
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Row {
                            TextButton(
                                onClick = {
                                    m.chooseMachine(r.s("machine_id"))
                                    m.page = "floor"
                                }
                            ) {
                                Text("位置・詳細")
                            }
                            TextButton(onClick = { m.addMachine(r.s("machine_id"), "candidate") }) {
                                Text("指示書へ追加")
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun DocumentFields(m: AppModel, body: JsonObject) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            body.s("title"),
            { m.updateBody(body.with("title" to text(it)), "title") },
            label = { Text("指示書名") },
            modifier = Modifier.widthIn(max = 340.dp),
        )
        var dayText by remember(m.selectedDoc, body.s("date")) { mutableStateOf(body.s("date")) }
        OutlinedTextField(
            dayText,
            {
                dayText = it
                runCatching { LocalDate.parse(it) }.onSuccess { d -> m.changeDate(d.toString()) }
            },
            label = { Text("登録日 YYYY-MM-DD") },
            singleLine = true,
            modifier = Modifier.width(210.dp),
            isError = runCatching { LocalDate.parse(dayText) }.isFailure,
        )
    }
}

@Composable
fun InstructionsScreen(m: AppModel) {
    var tab by remember { mutableIntStateOf(0) }
    var delete by remember { mutableStateOf(false) }
    var bulk by remember { mutableStateOf(false) }
    var settings by remember { mutableStateOf(false) }
    var duplicate by remember { mutableStateOf(false) }
    var actions by remember { mutableStateOf(false) }
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val compact = maxWidth < 700.dp || m.displayMode == "phone"
        val current = m.currentDoc()
        val editing =
            current
                ?.o("body")
                ?.a("entries")
                ?.map { it.jsonObject }
                ?.find { it.s("entry_id") == m.editingEntry }
        if (compact && editing != null) {
            Column(Modifier.fillMaxSize().padding(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { m.editingEntry = "" }, modifier = Modifier.weight(1f)) {
                        Text("← 指示書一覧へ戻る")
                    }
                    IconButton(onClick = { m.undo() }, enabled = m.undoCount > 0) {
                        Icon(Icons.Default.Undo, "元に戻す")
                    }
                    IconButton(onClick = { m.redo() }, enabled = m.redoCount > 0) {
                        Icon(Icons.Default.Redo, "やり直す")
                    }
                    TextButton(onClick = { m.save() }) { Text("保存") }
                }
                Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                    InstructionRow(
                        m,
                        editing,
                        current.o("body").o("entry_extras").o(editing.s("entry_id")),
                        true,
                        { m.editingEntry = "" },
                    )
                }
            }
            return@BoxWithConstraints
        }
        Column(Modifier.fillMaxSize().padding(8.dp)) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Choice(
                    m.selectedDoc,
                    m.activeDocs().map { it.s("document_id") to it.o("body").s("title") },
                    {
                        m.selectedDoc = it
                        m.selectedRows = emptySet()
                        m.editingEntry = ""
                    },
                    modifier = Modifier.widthIn(max = if (compact) 220.dp else 360.dp),
                )
                TextButton(
                    onClick = {
                        m.newDoc()
                        if (compact) settings = true
                    }
                ) {
                    Text("新規指示書")
                }
                IconButton(onClick = { m.undo() }, enabled = m.undoCount > 0) {
                    Icon(Icons.Default.Undo, "元に戻す")
                }
                IconButton(onClick = { m.redo() }, enabled = m.redoCount > 0) {
                    Icon(Icons.Default.Redo, "やり直す")
                }
                TextButton(onClick = { m.save() }) { Text("保存") }
            }
            val d = m.currentDoc()
            if (d == null) {
                Text("新規作成、またはWindowsから指示書を取り込んでください。")
                return@Column
            }
            val body = d.o("body")
            if (!compact) DocumentFields(m, body)
            TabRow(tab) {
                Tab(selected = tab == 0, onClick = { tab = 0 }, text = { Text("指示書編集") })
                Tab(selected = tab == 1, onClick = { tab = 1 }, text = { Text("入替予定地図") })
            }
            if (tab == 1) {
                var context by remember { mutableStateOf(true) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(context, { context = it })
                    Text("周辺マシンを表示")
                    TextButton(onClick = { tab = 0 }) { Text("選択行を編集") }
                }
                val booths = planBooths(m)
                Text(
                    "出力ON ${booths.size}ブース / 位置未特定 ${booths.count{it.machineId==null}}",
                    style = MaterialTheme.typography.bodySmall,
                )
                FloorCanvas(m, Modifier.weight(1f).fillMaxWidth(), map = true, context = context)
                booths
                    .filter { it.machineId == null }
                    .take(5)
                    .forEach {
                        Text(
                            "位置未特定 ${it.number}：${it.assignment.s("token")}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                return@Column
            }
            val entries = body.a("entries").map { it.jsonObject }.sortedBy { it.n("order") }
            if (compact) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    TextButton(
                        onClick = {
                            m.addMachine("")
                            m.editingEntry =
                                m.currentDoc()
                                    ?.o("body")
                                    ?.a("entries")
                                    ?.lastOrNull()
                                    ?.jsonObject
                                    ?.s("entry_id")
                                    .orEmpty()
                        }
                    ) {
                        Text("＋手動追加")
                    }
                    Box {
                        OutlinedButton(onClick = { actions = true }) { Text("操作 ▾") }
                        DropdownMenu(actions, onDismissRequest = { actions = false }) {
                            listOf("全選択", "選択解除", "上へ", "下へ", "複製", "運営方法を一括設定", "削除")
                                .forEachIndexed { i, name ->
                                    DropdownMenuItem(
                                        text = { Text(name) },
                                        enabled = i < 2 || m.selectedRows.isNotEmpty(),
                                        onClick = {
                                            actions = false
                                            when (i) {
                                                0 ->
                                                    m.selectedRows =
                                                        entries.map { it.s("entry_id") }.toSet()
                                                1 -> m.selectedRows = emptySet()
                                                2 -> m.moveSelected(-1)
                                                3 -> m.moveSelected(1)
                                                4 -> duplicate = true
                                                5 -> bulk = true
                                                6 -> delete = true
                                            }
                                        },
                                    )
                                }
                        }
                    }
                    TextButton(onClick = { settings = true }) { Text("指示書設定") }
                }
            } else
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = { m.addMachine("") }) { Text("＋手動追加") }
                    TextButton(
                        onClick = { m.selectedRows = entries.map { it.s("entry_id") }.toSet() }
                    ) {
                        Text("全選択")
                    }
                    TextButton(onClick = { m.selectedRows = emptySet() }) { Text("解除") }
                    TextButton(
                        onClick = { m.moveSelected(-1) },
                        enabled = m.selectedRows.isNotEmpty(),
                    ) {
                        Text("↑上へ")
                    }
                    TextButton(
                        onClick = { m.moveSelected(1) },
                        enabled = m.selectedRows.isNotEmpty(),
                    ) {
                        Text("↓下へ")
                    }
                    TextButton(
                        onClick = { duplicate = true },
                        enabled = m.selectedRows.isNotEmpty(),
                    ) {
                        Text("複製")
                    }
                    TextButton(onClick = { bulk = true }, enabled = m.selectedRows.isNotEmpty()) {
                        Text("運営方法を一括設定")
                    }
                    TextButton(onClick = { delete = true }, enabled = m.selectedRows.isNotEmpty()) {
                        Text("削除")
                    }
                }
            Text(
                "${entries.size}行 / 選択 ${m.selectedRows.size}行 / 自動保存",
                style = MaterialTheme.typography.bodySmall,
            )
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(entries, key = { it.s("entry_id") }) { e ->
                    InstructionRow(
                        m,
                        e,
                        body.o("entry_extras").o(e.s("entry_id")),
                        onEdit = if (compact) ({ m.editingEntry = e.s("entry_id") }) else null,
                    )
                }
            }
        }
        if (delete)
            AlertDialog(
                onDismissRequest = { delete = false },
                title = { Text("選択した${m.selectedRows.size}行を削除しますか？") },
                text = { Text("元に戻す操作で復旧できます。") },
                confirmButton = {
                    TextButton(
                        onClick = {
                            m.deleteSelected()
                            delete = false
                        }
                    ) {
                        Text("削除")
                    }
                },
                dismissButton = { TextButton(onClick = { delete = false }) { Text("キャンセル") } },
            )
        if (bulk) {
            var method by remember { mutableStateOf("") }
            AlertDialog(
                onDismissRequest = { bulk = false },
                title = { Text("選択行の運営方法") },
                text = {
                    Column {
                        Choice(
                            method,
                            listOf("" to "未設定", "橋渡し" to "橋渡し", "リング" to "リング", "フック" to "フック"),
                            { method = it },
                        )
                        OutlinedTextField(method, { method = it }, label = { Text("自由記載も可能") })
                    }
                },
                confirmButton = {
                    TextButton(
                        onClick = {
                            m.bulkMethod(method)
                            bulk = false
                        }
                    ) {
                        Text("適用")
                    }
                },
                dismissButton = { TextButton(onClick = { bulk = false }) { Text("キャンセル") } },
            )
        }
        if (settings && compact && current != null)
            AlertDialog(
                onDismissRequest = { settings = false },
                title = { Text("指示書設定") },
                text = {
                    Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                        DocumentFields(m, current.o("body"))
                    }
                },
                confirmButton = { TextButton(onClick = { settings = false }) { Text("閉じる") } },
            )
        if (duplicate)
            AlertDialog(
                onDismissRequest = { duplicate = false },
                title = { Text("選択行を複製しますか？") },
                text = { Text("同じマシンへの指示が増えます。新しい行IDと番号を割り当てます。") },
                confirmButton = {
                    TextButton(
                        onClick = {
                            m.duplicateSelected()
                            duplicate = false
                        }
                    ) {
                        Text("複製")
                    }
                },
                dismissButton = { TextButton(onClick = { duplicate = false }) { Text("キャンセル") } },
            )
    }
}

@Composable
fun InstructionRow(
    m: AppModel,
    e: JsonObject,
    extras: JsonObject,
    forceExpanded: Boolean = false,
    onEdit: (() -> Unit)? = null,
) {
    val key = e.s("entry_id")
    var expanded by remember { mutableStateOf(false) }
    Card(
        Modifier.fillMaxWidth()
            .border(
                if (key in m.selectedRows) 2.dp else 0.dp,
                if (key in m.selectedRows) MaterialTheme.colorScheme.primary else Color.Transparent,
                RoundedCornerShape(12.dp),
            )
    ) {
        Column(Modifier.padding(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                var drag by remember { mutableFloatStateOf(0f) }
                Icon(
                    Icons.Default.DragHandle,
                    "長押しして上下に移動",
                    Modifier.size(40.dp).pointerInput(key) {
                        detectDragGesturesAfterLongPress(
                            onDragStart = {
                                if (key !in m.selectedRows) m.selectedRows = setOf(key)
                                drag = 0f
                            },
                            onDragEnd = { drag = 0f },
                            onDragCancel = { drag = 0f },
                        ) { change, delta ->
                            change.consume()
                            drag += delta.y
                            if (abs(drag) > 56.dp.toPx()) {
                                m.moveSelected(if (drag < 0) -1 else 1)
                                drag = 0f
                            }
                        }
                    },
                )
                Checkbox(
                    key in m.selectedRows,
                    { checked ->
                        m.selectedRows = if (checked) m.selectedRows + key else m.selectedRows - key
                    },
                )
                Column(Modifier.weight(1f)) {
                    Text(
                        "${e.n("order")?.toInt()}  ${e.a("machines").joinToString(" / "){it.jsonPrimitive.content}.ifEmpty{"番号未設定"}}",
                        fontWeight = FontWeight.Bold,
                    )
                    Text(e.s("name").ifEmpty { "商品未定" })
                    if (extras.s("current_product_name").isNotEmpty())
                        Text(
                            "現在：${extras.s("current_product_name")}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                }
                Text("出力", style = MaterialTheme.typography.labelSmall)
                Checkbox(
                    e["included"] == JsonPrimitive(true),
                    { m.updateEntry(key, mapOf("included" to JsonPrimitive(it))) },
                )
                IconButton(onClick = { if (onEdit != null) onEdit() else expanded = !expanded }) {
                    Icon(
                        if (forceExpanded || (onEdit == null && expanded)) Icons.Default.ExpandLess
                        else Icons.Default.Edit,
                        "行を編集",
                    )
                }
            }
            if (forceExpanded || (onEdit == null && expanded)) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    EntryText(m, e, "name", "新しく投入する商品名（空欄可）", 340)
                    EntryText(m, e, "maker", "メーカー", 200)
                    var raw by
                        remember(key, e.a("machines").toString()) {
                            mutableStateOf(
                                e.a("machines").joinToString("\n") { it.jsonPrimitive.content }
                            )
                        }
                    var error by remember { mutableStateOf("") }
                    OutlinedTextField(
                        raw,
                        {
                            raw = it
                            error = ""
                        },
                        label = { Text("マシン番号・管理番号（改行で複数）") },
                        isError = error.isNotEmpty(),
                        supportingText = { Text(error.ifEmpty { "20088-01 / L_66。管理番号も利用できます。" }) },
                        modifier = Modifier.widthIn(min = 220.dp, max = 320.dp),
                    )
                    TextButton(
                        onClick = {
                            runCatching { Documents.tokens(raw) }
                                .onSuccess { tokens ->
                                    m.updateEntry(
                                        key,
                                        mapOf("machines" to tokens.map(::text).array()),
                                    )
                                }
                                .onFailure { error = it.message.orEmpty() }
                        }
                    ) {
                        Text("番号を反映")
                    }
                    EntryText(m, e, "units", "入数（未定は空欄）", 180, numeric = true)
                    EntryText(m, e, "cases", "CS数（未定は空欄）", 180, numeric = true)
                    var method by
                        remember(key, e.a("methods").toString()) {
                            mutableStateOf(
                                e.a("methods").joinToString(" / ") { it.jsonPrimitive.content }
                            )
                        }
                    OutlinedTextField(
                        method,
                        {
                            method = it
                            m.updateEntry(
                                key,
                                mapOf(
                                    "methods" to
                                        (if (it.isBlank()) emptyList() else listOf(text(it)))
                                            .array(),
                                    "machine_methods" to
                                        JsonObject(
                                            e.a("machines").associate { v ->
                                                v.jsonPrimitive.content to
                                                    text(it.takeIf { it.isNotBlank() })
                                            }
                                        ),
                                ),
                                "$key:method",
                            )
                        },
                        label = { Text("運営方法（橋渡し・リング・フック・自由記載）") },
                        modifier = Modifier.widthIn(max = 350.dp),
                    )
                    EntryText(m, e, "notes", "備考", 340)
                }
                var count by
                    remember(key, extras.s("quantity")) { mutableStateOf(extras.s("quantity")) }
                OutlinedTextField(
                    count,
                    {
                        count = it
                        val d = m.currentDoc() ?: return@OutlinedTextField
                        val all =
                            d.o("body")
                                .o("entry_extras")
                                .with(key to extras.with("quantity" to text(it)))
                        m.updateBody(d.o("body").with("entry_extras" to all), "$key:quantity")
                    },
                    label = { Text("個数・位置メモ（Windows補助情報として保持）") },
                )
                val matches =
                    e.a("machines").map {
                        it.jsonPrimitive.content to
                            Documents.lookup(it.jsonPrimitive.content, m.machines())
                    }
                matches.forEach { (token, found) ->
                    if (found.size != 1)
                        Text(
                            "$token：${if(found.isEmpty())"位置未特定。番号を確認してください"else"複数候補。個別の番号を指定してください"}",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                }
            }
        }
    }
}

@Composable
private fun EntryText(
    m: AppModel,
    e: JsonObject,
    key: String,
    label: String,
    width: Int,
    numeric: Boolean = false,
) {
    var raw by remember(e.s("entry_id"), key) { mutableStateOf(e.s(key)) }
    var sent by remember(e.s("entry_id"), key) { mutableStateOf(e[key]) }
    LaunchedEffect(e[key]) {
        if (e[key] != sent) {
            raw = e.s(key)
            sent = e[key]
        }
    }
    val invalid =
        numeric &&
            raw.isNotBlank() &&
            raw.toDoubleOrNull()?.let { it.isFinite() && it >= 0 } != true
    OutlinedTextField(
        raw,
        { value ->
            raw = value
            if (
                !numeric ||
                    value.isBlank() ||
                    value.toDoubleOrNull()?.let { it.isFinite() && it >= 0 } == true
            ) {
                val update =
                    if (!numeric || value.isBlank()) text(value)
                    else JsonPrimitive(value.toDouble())
                sent = update
                m.updateEntry(e.s("entry_id"), mapOf(key to update), e.s("entry_id") + key)
            }
        },
        label = { Text(label) },
        isError = invalid,
        modifier = Modifier.widthIn(min = 150.dp, max = width.dp),
    )
}

@Composable
fun LinkScreen(m: AppModel, onOpen: () -> Unit, onExport: () -> Unit, onShare: () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Windows連携", style = MaterialTheme.typography.headlineSmall)
        Text("オフラインZIP交換。売上・マシン配置・評価はWindowsを管理元とします。")
        Text(
            "マシン ${m.machines().a("items").size}台 / 指示書 ${m.activeDocs().size}件\n最終出力 ${m.transfers.lastOrNull{it.s("direction") in listOf("export","usb")}?.s("timestamp")?:"未出力"}\n最終取込 ${m.metadata.s("last_import").ifEmpty{"未取込"}}\n元データの作成 ${m.metadata.s("source_created_at").ifEmpty{"不明"}}"
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onOpen, enabled = !m.busy) { Text("ZIPを取り込む") }
            OutlinedButton(onClick = onExport, enabled = m.refs != null) { Text("指示書ZIPを保存") }
            OutlinedButton(onClick = onShare, enabled = m.refs != null) { Text("ZIPを共有") }
        }
        ElevatedCard {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("USBでWindowsへ渡す", fontWeight = FontWeight.Bold)
                Text(
                    "PC側で認証済みのUSBデバッグ接続が必要です。ZIPを準備した後、WindowsのAndroid連携画面で「Androidから受信」を押します。受信結果が届くまで完了とは表示しません。"
                )
                Button(onClick = { m.prepareUsb() }, enabled = m.refs != null) {
                    Text("Windowsへ送るZIPを準備")
                }
            }
        }
        Text("ZIPは暗号化されません。業務データの保存先・共有先を確認してください。", style = MaterialTheme.typography.bodySmall)
        Choice(
            m.displayMode,
            listOf("auto" to "表示モード：自動", "tablet" to "タブレット", "phone" to "スマートフォン"),
            { m.preference("displayMode", it) },
        )
        Text("転送履歴", fontWeight = FontWeight.Bold)
        m.transfers.takeLast(20).reversed().forEach {
            Text(
                "${it.s("timestamp")} / ${it.s("direction")} / ${it.s("status")}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
fun ImportDialog(m: AppModel, p: PackageData) {
    val incoming = p.data.getValue("instructions").a("documents").map { it.jsonObject }
    val actions = remember(p.manifest.s("package_id")) { mutableStateMapOf<String, String>() }
    val fields = remember { mutableStateMapOf<String, Map<String, String>>() }
    var replaceRefs by remember { mutableStateOf(true) }
    var mergeDoc by remember { mutableStateOf<JsonObject?>(null) }
    AlertDialog(
        onDismissRequest = { if (!m.busy) m.cancelImport() },
        title = { Text("ZIP取込内容の確認") },
        text = {
            Column(
                Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "作成元 ${p.manifest.s("source_app")} / ${p.manifest.s("created_at")}\nマシン ${p.data.getValue("machines").a("items").size}台 / 指示書 ${incoming.size}件"
                )
                if (p.manifest.s("kind") != "instructions")
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(replaceRefs, { replaceRefs = it })
                        Text("Windowsの参照データを採用（指示書は下記で選択）")
                    }
                incoming.forEach { d ->
                    val key = d.s("document_id")
                    val local = m.docs.find { it.s("document_id") == key }
                    val relation = Documents.relation(local, d)
                    Text(
                        d.o("body").s("title") +
                            when (relation) {
                                "conflict" -> "：双方で編集・競合"
                                "new" -> "：新規"
                                "incoming" -> "：受信側で更新"
                                "local" -> "：この端末で更新"
                                else -> "：同一"
                            },
                        fontWeight = FontWeight.Bold,
                    )
                    val options =
                        mutableListOf(
                            "local" to "この端末の内容を保持",
                            "incoming" to "受信内容を採用",
                            "copy" to "別の指示書として保存",
                        )
                    if (local != null && relation == "conflict") options += "merge" to "差分を確認して統合"
                    Choice(
                        actions[key]
                            ?: if (relation in listOf("new", "incoming")) "incoming" else "local",
                        options,
                        { action ->
                            if (action == "merge") {
                                runCatching { m.mergeInfo(d) }
                                    .onSuccess {
                                        mergeDoc = d
                                        actions[key] = action
                                    }
                                    .onFailure { m.notice = it.message.orEmpty() }
                            } else actions[key] = action
                        },
                    )
                    if (actions[key] == "merge")
                        TextButton(onClick = { mergeDoc = d }) { Text("項目ごとの差分") }
                }
                Text(
                    "確認後に適用します。破損・別データセット・未対応形式は取り込まず、既存データを保持します。",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { m.applyImport(actions.toMap(), fields.toMap(), replaceRefs) },
                enabled = !m.busy,
            ) {
                Text("確認して取り込む")
            }
        },
        dismissButton = {
            TextButton(onClick = { m.cancelImport() }, enabled = !m.busy) { Text("キャンセル") }
        },
    )
    mergeDoc?.let { d ->
        val key = d.s("document_id")
        val conflicts = runCatching { m.mergeInfo(d) }.getOrDefault(emptyList())
        val selected =
            remember(key) {
                mutableStateMapOf<String, String>().apply { putAll(fields[key] ?: emptyMap()) }
            }
        AlertDialog(
            onDismissRequest = { mergeDoc = null },
            title = { Text("競合項目の選択") },
            text = {
                Column(Modifier.heightIn(max = 500.dp).verticalScroll(rememberScrollState())) {
                    if (conflicts.isEmpty()) Text("異なる項目への編集を統合できます。")
                    conflicts.forEach { c ->
                        Text(c.path, fontWeight = FontWeight.Bold)
                        Text("この端末：${c.local}\n受信：${c.incoming}", maxLines = 6)
                        Choice(
                            selected[c.path] ?: "",
                            listOf("" to "選択してください", "local" to "この端末", "incoming" to "受信"),
                            { selected[c.path] = it },
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        fields[key] = selected.toMap()
                        mergeDoc = null
                    },
                    enabled = conflicts.all { selected[it.path] in listOf("local", "incoming") },
                ) {
                    Text("選択を保存")
                }
            },
            dismissButton = { TextButton(onClick = { mergeDoc = null }) { Text("閉じる") } },
        )
    }
}

@Composable
fun HomeScreen(m: AppModel, onOpen: () -> Unit, onScan: () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "売り場管理ホーム",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
        )
        Text("売り場の確認と入替指示書の作成。インターネット接続は不要です。")
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf(
                    "管理ブース" to "${m.machines().a("items").size}台",
                    "指示書" to "${m.activeDocs().size}件",
                    "参照期間" to (m.frame()?.s("label") ?: "未取込"),
                )
                .forEach { (label, value) ->
                    ElevatedCard {
                        Column(Modifier.padding(16.dp)) {
                            Text(label, style = MaterialTheme.typography.labelMedium)
                            Text(value, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
        }
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            listOf(
                    Triple("売り場を見る", "配置とマシン情報を確認", { m.page = "floor" }),
                    Triple("QRコードを読む", "番号を探さず詳細を表示", onScan),
                    Triple("入替指示書を編集", "空欄の商品も登録可能", { m.page = "instructions" }),
                    Triple("Windowsとデータ交換", "ZIPを取り込む", onOpen),
                    Triple("入替候補を見る", "Windowsの数値と判定理由", { m.page = "candidates" }),
                    Triple("表示設定", "テーマと端末モード", { m.page = "settings" }),
                )
                .forEach { (title, description, action) ->
                    Card(
                        onClick = action,
                        modifier = Modifier.widthIn(min = 250.dp, max = 340.dp),
                    ) {
                        Column(Modifier.padding(18.dp)) {
                            Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                            Text(description, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
        }
        Text(
            "最終取込：${m.metadata.s("last_import").ifEmpty{"未取込"}}",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
fun SettingsScreen(m: AppModel) {
    Column(
        Modifier.padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("表示設定", style = MaterialTheme.typography.headlineSmall)
        Choice(
            m.theme,
            listOf("system" to "システム連動", "light" to "ライト", "dark" to "ダーク"),
            { m.preference("theme", it) },
        )
        Choice(
            m.displayMode,
            listOf("auto" to "端末モード：自動", "tablet" to "タブレット", "phone" to "スマートフォン"),
            { m.preference("displayMode", it) },
        )
        Text("判定基準・売上・配置はWindowsで管理します。Windowsから新しい参照パッケージを取り込むと、同じ基準の値と色が反映されます。")
        TextButton(onClick = { m.page = "link" }) { Text("Windows連携を開く") }
    }
}
