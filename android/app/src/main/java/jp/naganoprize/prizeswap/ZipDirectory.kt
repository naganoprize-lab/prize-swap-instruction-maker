package jp.naganoprize.prizeswap

/**
 * Read central records without extracting. Reject links, encryption, ZIP64, duplicates and trailing
 * payloads.
 */
object ZipDirectory {
    data class Entry(val crc: Long, val size: Long, val compressed: Long)

    fun inspect(raw: ByteArray): Map<String, Entry> {
        fun u16(p: Int): Int {
            requireData(p >= 0 && p + 2 <= raw.size, "ZIPヘッダーが途中で終了しました")
            return (raw[p].toInt() and 255) or ((raw[p + 1].toInt() and 255) shl 8)
        }
        fun u32(p: Int): Long = u16(p).toLong() or (u16(p + 2).toLong() shl 16)
        val end =
            (raw.size - 22 downTo maxOf(0, raw.size - 65557)).firstOrNull {
                u32(it) == 0x06054b50L && it + 22 + u16(it + 20) == raw.size
            } ?: error("ZIP終端が不正です")
        requireData(
            u16(end + 4) == 0 && u16(end + 6) == 0 && u16(end + 8) == 8 && u16(end + 10) == 8,
            "分割ZIP・ZIP64・ファイル数不正",
        )
        val start = u32(end + 16)
        val length = u32(end + 12)
        requireData(start + length == end.toLong(), "ZIPディレクトリが不正です")
        var p = start.toInt()
        val entries = linkedMapOf<String, Entry>()
        repeat(8) {
            requireData(u32(p) == 0x02014b50L, "ZIP中央ヘッダーが不正です")
            val flags = u16(p + 8)
            val method = u16(p + 10)
            val mode = (u32(p + 38) shr 16).toInt()
            val size = u32(p + 24)
            val compressed = u32(p + 20)
            val n = u16(p + 28)
            val extra = u16(p + 30)
            val comment = u16(p + 32)
            requireData(
                flags and 1 == 0 && method in listOf(0, 8) && mode and 0xF000 != 0xA000,
                "暗号化・リンク・未対応圧縮のZIP",
            )
            requireData(
                size <= PackageCodec.MAX_FILE && size <= maxOf(1L, compressed) * 200,
                "ZIPサイズ・圧縮率超過",
            )
            requireData(p + 46 + n + extra + comment <= end, "ZIPディレクトリが途中で終了しました")
            val name = String(raw, p + 46, n, Charsets.UTF_8)
            requireData(name in PackageCodec.files && name !in entries, "ZIPパス・重複が不正です")
            entries[name] = Entry(u32(p + 16), size, compressed)
            p += 46 + n + extra + comment
        }
        requireData(p == end, "ZIPディレクトリのサイズ不一致")
        return entries
    }
}
