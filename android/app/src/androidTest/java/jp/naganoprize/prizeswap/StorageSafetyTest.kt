package jp.naganoprize.prizeswap

import android.app.Application
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class StorageSafetyTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app = instrumentation.targetContext.applicationContext as Application

    @Test
    fun corruptDatabaseIsNeverDeletedOrReplaced() {
        val name = "corruption-test-${id()}.db"
        val file = app.getDatabasePath(name)
        file.parentFile!!.mkdirs()
        val original = ByteArray(512) { (it % 127).toByte() }
        file.writeBytes(original)
        val repository = Repository(app, name)
        try {
            assertTrue(runCatching { repository.readableDatabase }.isFailure)
            assertTrue(file.isFile)
            assertArrayEquals(original, file.readBytes())
            val backups =
                File(app.filesDir, "corrupt-backups")
                    .walkTopDown()
                    .filter { it.isFile && it.name == name }
                    .toList()
            assertTrue(backups.isNotEmpty())
            assertArrayEquals(original, backups.first().readBytes())
            backups.forEach { it.delete() }
        } finally {
            runCatching { repository.close() }
            app.deleteDatabase(name) // Only this test's isolated database, never prizeswap.db.
        }
    }

    @Test
    fun failedLoadCannotSaveAnEmptyDocumentList() = runBlocking {
        val name = "load-failure-test-${id()}.db"
        val repository = Repository(app, name)
        repository.writableDatabase.execSQL(
            "INSERT INTO documents VALUES(?,?)",
            arrayOf("preserved-row", "{invalid JSON"),
        )
        lateinit var model: AppModel
        val store = ViewModelStore()
        try {
            instrumentation.runOnMainSync {
                model = AppModel(app, repository)
                store.put("isolated-model", model)
            }
            withTimeout(30000) { while (!model.loaded) delay(50) }
            assertFalse(model.storageHealthy)
            instrumentation.runOnMainSync { model.flush() }
            var rejected = false
            try {
                model.saveNow()
            } catch (_: IllegalStateException) {
                rejected = true
            }
            assertTrue(rejected)
            instrumentation.waitForIdleSync()
            repository.readableDatabase.rawQuery("SELECT id,value FROM documents", null).use {
                assertTrue(it.moveToFirst())
                assertEquals("preserved-row", it.getString(0))
                assertEquals("{invalid JSON", it.getString(1))
                assertFalse(it.moveToNext())
            }
        } finally {
            instrumentation.runOnMainSync { store.clear() }
            repository.close()
            app.deleteDatabase(name)
        }
    }
}
