package jp.naganoprize.prizeswap

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import java.io.File
import kotlinx.coroutines.*

class MainActivity : ComponentActivity() {
    lateinit var model: AppModel
    private val open =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            uri?.let(::readUri)
        }
    private val create =
        registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri
            ->
            if (uri != null)
                lifecycleScope.launch {
                    try {
                        val raw = model.export()
                        withContext(Dispatchers.IO) {
                            contentResolver.openOutputStream(uri)?.use { it.write(raw) }
                                ?: error("保存先を開けません")
                        }
                        model.exported(raw)
                        model.notice = "ZIPを保存しました"
                    } catch (e: Exception) {
                        model.notice = "保存できません：${e.message}"
                    }
                }
        }
    private val scanner =
        registerForActivityResult(ScanContract()) { result ->
            result.contents?.let { model.qr(it) }
        }

    private fun scan() {
        scanner.launch(
            ScanOptions()
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt("マシンのQRコードを読み取ってください")
                .setBeepEnabled(false)
                .setOrientationLocked(false)
        )
    }

    private val permission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) scan() else model.notice = "カメラ権限が必要です。番号検索でもマシンを開けます。"
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        model = ViewModelProvider(this)[AppModel::class.java]
        setContent {
            PrizeApp(
                model,
                onOpen = {
                    open.launch(
                        arrayOf(
                            "application/zip",
                            "application/octet-stream",
                            "application/x-zip-compressed",
                        )
                    )
                },
                onExport = { create.launch("PrizeSwapInstructions.zip") },
                onShare = ::share,
                onScan = {
                    if (
                        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
                            PackageManager.PERMISSION_GRANTED
                    )
                        scan()
                    else permission.launch(Manifest.permission.CAMERA)
                },
            )
        }
        lifecycleScope.launch {
            while (!model.loaded) delay(50)
            handle(intent)
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (isActive) {
                    model.pollTransfer()
                    delay(2000)
                }
            }
        }
    }

    override fun onStop() {
        super.onStop()
        if (model.loaded) model.flush()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (model.loaded) handle(intent)
    }

    private fun handle(value: Intent) {
        when (value.action) {
            Intent.ACTION_VIEW -> value.dataString?.let { model.qr(it) }
            Intent.ACTION_SEND -> {
                @Suppress("DEPRECATION")
                val uri = value.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
                if (uri?.scheme == "content") readUri(uri) else model.notice = "共有されたZIPを開けません"
            }
        }
        value.getStringExtra("transfer_request")?.let {
            if (!validId(it)) model.notice = "転送IDが不正です" else model.pollTransfer()
        }
    }

    private fun readUri(uri: Uri) {
        lifecycleScope.launch {
            try {
                val raw =
                    withContext(Dispatchers.IO) {
                        contentResolver.openInputStream(uri)?.use { input ->
                            val out = java.io.ByteArrayOutputStream()
                            val buffer = ByteArray(8192)
                            while (true) {
                                val n = input.read(buffer)
                                if (n < 0) break
                                requireData(
                                    out.size() + n <= PackageCodec.MAX_TOTAL,
                                    "ZIPは64MiB以下にしてください",
                                )
                                out.write(buffer, 0, n)
                            }
                            out.toByteArray()
                        } ?: error("ファイルを開けません")
                    }
                model.preview(raw)
            } catch (e: Exception) {
                model.notice = "ZIPを開けません：${e.message}"
            }
        }
    }

    private fun share() {
        lifecycleScope.launch {
            try {
                val raw = model.export()
                val file =
                    withContext(Dispatchers.IO) {
                        File(cacheDir, "exports").apply { mkdirs() }
                        File(cacheDir, "exports/PrizeSwapInstructions-${id()}.zip").apply {
                            writeBytes(raw)
                        }
                    }
                val uri =
                    FileProvider.getUriForFile(
                        this@MainActivity,
                        "${BuildConfig.APPLICATION_ID}.files",
                        file,
                    )
                startActivity(
                    Intent.createChooser(
                        Intent(Intent.ACTION_SEND)
                            .setType("application/zip")
                            .putExtra(Intent.EXTRA_STREAM, uri)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                        "ZIPを共有",
                    )
                )
                model.exported(raw)
            } catch (e: Exception) {
                model.notice = "共有できません：${e.message}"
            }
        }
    }
}
