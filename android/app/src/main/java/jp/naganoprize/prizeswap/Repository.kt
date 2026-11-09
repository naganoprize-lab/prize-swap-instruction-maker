package jp.naganoprize.prizeswap

import android.content.Context
import android.database.DatabaseErrorHandler
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteDatabaseCorruptException
import android.database.sqlite.SQLiteOpenHelper
import androidx.core.database.sqlite.transaction
import java.io.File
import kotlinx.serialization.json.*

/** References and documents live separately. Every import is one SQLite transaction + backup. */
/** Preserve the original database on corruption; Android's default handler deletes it. */
private class PreserveCorruptDatabase(private val context: Context) : DatabaseErrorHandler {
    override fun onCorruption(db: SQLiteDatabase) {
        val path = db.path
        runCatching { db.close() }
        runCatching {
            val folder = File(context.filesDir, "corrupt-backups/${System.currentTimeMillis()}")
            check(folder.mkdirs())
            listOf("", "-wal", "-shm", "-journal").forEach { suffix ->
                val original = File(path + suffix)
                if (original.isFile) original.copyTo(File(folder, original.name))
            }
        }
        throw SQLiteDatabaseCorruptException("保存データの破損を検出しました。元ファイルは削除していません。")
    }
}

class Repository(val context: Context, private val databaseFile: String = "prizeswap.db") :
    SQLiteOpenHelper(context, databaseFile, null, 1, PreserveCorruptDatabase(context)) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execSQL("CREATE TABLE documents(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execSQL("CREATE TABLE revisions(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execSQL("CREATE TABLE transfers(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
        error("未対応のDBバージョンです。データは削除しません。")
    }

    private fun read(table: String) =
        readableDatabase.rawQuery("SELECT value FROM $table ORDER BY rowid", null).use { c ->
            buildList {
                while (c.moveToNext()) add(
                    StrictJson.parse(c.getString(0).toByteArray()).jsonObject
                )
            }
        }

    fun state(key: String): JsonElement? =
        readableDatabase.rawQuery("SELECT value FROM state WHERE key=?", arrayOf(key)).use {
            if (it.moveToFirst()) StrictJson.parse(it.getString(0).toByteArray()) else null
        }

    fun put(key: String, value: JsonElement) {
        writableDatabase.execSQL(
            "INSERT OR REPLACE INTO state VALUES(?,?)",
            arrayOf(key, value.toString()),
        )
    }

    fun documents() = read("documents")

    fun revisions() = read("revisions")

    fun transfers() = read("transfers")

    fun saveDocuments(docs: List<JsonObject>) {
        val db = writableDatabase
        db.transaction {
            val old =
                db.rawQuery("SELECT id,value FROM documents", null).use { c ->
                    buildMap { while (c.moveToNext()) put(c.getString(0), c.getString(1)) }
                }
            val present = docs.map { it.s("document_id") }.toSet()
            old.keys
                .filter { it !in present }
                .forEach { db.delete("documents", "id=?", arrayOf(it)) }
            docs.forEach { d ->
                val json = d.toString()
                if (old[d.s("document_id")] != json) {
                    if (old.containsKey(d.s("document_id")))
                        db.execSQL(
                            "UPDATE documents SET value=? WHERE id=?",
                            arrayOf(json, d.s("document_id")),
                        )
                    else
                        db.execSQL(
                            "INSERT INTO documents VALUES(?,?)",
                            arrayOf(d.s("document_id"), json),
                        )
                    db.execSQL(
                        "INSERT OR REPLACE INTO revisions VALUES(?,?)",
                        arrayOf(d.s("revision_id"), json),
                    )
                }
            }
            db.execSQL(
                "DELETE FROM revisions WHERE id IN (SELECT id FROM revisions ORDER BY rowid DESC LIMIT -1 OFFSET 2048)"
            )
        }
    }

    fun backup() {
        writableDatabase.rawQuery("PRAGMA wal_checkpoint(FULL)", null).use {
            check(it.moveToFirst() && it.getInt(0) == 0) { "バックアップ前の書き込み確定に失敗しました" }
        }
        val folder = File(context.filesDir, "backups").apply { mkdirs() }
        val target = File(folder, "before-import-${System.currentTimeMillis()}.db")
        context.getDatabasePath(databaseFile).copyTo(target)
        folder
            .listFiles()
            ?.sortedByDescending { it.lastModified() }
            ?.drop(10)
            ?.forEach { it.delete() }
    }

    fun importData(
        refs: JsonObject?,
        metadata: JsonObject,
        docs: List<JsonObject>,
        event: JsonObject,
    ) {
        backup()
        val db = writableDatabase
        db.transaction {
            if (refs != null) put("references", refs)
            put("metadata", metadata)
            saveDocuments(docs)
            transfer(event)
        }
    }

    fun transfer(event: JsonObject) {
        writableDatabase.execSQL(
            "INSERT OR REPLACE INTO transfers VALUES(?,?)",
            arrayOf(event.s("package_id"), event.toString()),
        )
    }
}
