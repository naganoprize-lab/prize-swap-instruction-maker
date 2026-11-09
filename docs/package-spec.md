# PrizeSwapPackage v1

Windows (`3cc1606`モデル) とAndroidのオフライン交換境界。規範スキーマは [`shared/schemas/package-v1.schema.json`](../shared/schemas/package-v1.schema.json)。UTF-8 JSON、有限数のみ、重複キー禁止。数値0とnull（未取得）と空文字（未設定の入力欄）を区別する。日時はタイムゾーン付きISO8601、対象期間は別のISO日付。

## ZIP

固定8ファイル。任意ファイルを展開しない。

```
manifest.json
data/machines.json
data/layout.json
data/sales.json
data/history.json
data/settings.json
data/instructions.json
metadata/checksums.json
```

圧縮前後64MiB、1ファイル32MiB、深さ64、圧縮率200倍以下。STORE/DEFLATEのみ。暗号化、リンク、重複、追加パス、上位形式、チェックサム不一致を拒否。SHA-256はデータ6ファイルの**実際のバイト列**を対象とする。署名・暗号化方式ではないため、信用できる相手から受け取る。

manifestには形式名 `PrizeSwapPackage`、version/min_reader_version=1、package_id、dataset_id、作成時刻、source_app (`windows`/`android`)、source_device_id、kind (`full`/`reference`/`instructions`)、データ更新日時（不明時null）、app_version、6ファイルのパス・サイズ・SHA256を格納。checksums.jsonも同じ6パスのSHA256。参照のみの種別は指示書配列を空にする。

## IDと参照

- machine_id = Windows `Machine.uid`。機械番号と管理番号は別の表示/照合項目。レイアウトは既存座標・幅・高さ・フォントサイズをそのまま保持する。
- entry_id = 既存Product.entry_id、assignment_id = 既存booth_assignments.assignment_id。商品名をキーにしない。
- document_idは複数のProductをまとめる交換用の永続ID。Windows既存行を別の帳票モデルへ移さない。
- dataset_idはWindowsの売り場連携サイドカーで発行。別データセットは混合せず拒否する。device_idは端末のローカル保存ID。
- ID文字集合 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`。OSのパス、認証情報、ADB設定は交換しない。

machinesは管理番号参考表を含む。未解決・複数候補を商品名から推測しない。指示書に未登録番号を保持しても、架空のマシンや座標は作らない。

## 数値と表示

sales/historyのframesはWindowsの共通サービスから生成した値、取得元、照合状態、色・判定・レーダー内部座標を持つ。Androidは再順位付けしない。売上比較の基準には未照合の有効Excel実績も含み、別ブースへの割当には使用しない。レーダーのscoreは描画専用で、画面にはactualを表示する。

Windowsの対象日/期間をcurrent、選択した履歴範囲（最大90日）を日別・月別・選択期間フレームとして出力できる。Androidではこれらの用意された期間を切り替える。履歴のない期間を補完しない。未取得日のフレームは欠損を表示する。Android独自の任意期間再集計・タイムラプスは実装しない。

## 指示書

bodyはtitle/date/entries/entry_extras。entriesは既存Productのフィールドを同じ型で保持する。空欄の商品・入数・CS数、未割当方法は有効。現在景品名は補助情報に保持し、新商品名へ流用しない。entry_extrasには既存Productにない個数/位置メモを行IDで保存し、Windowsでも再出力時に保持する。

出力ONのProductを同日のorder順に並べ、各booth_assignmentsへ1から番号を振る。同一日の複数指示書がある場合も共通の連番。番号は表示用で、永続IDは変更しない。

## 改訂と競合

revisionは単調増加の表示番号、revision_idは毎回発行するUUID、ancestorsは過去の改訂ID（直近128件）。更新時刻の大小では上書きしない。双方が共通基点から変更すると競合。選択肢は保持/採用/コピー/項目ごとの三者差分。コピーは文書・行・割当IDを再発行する。削除と編集の競合は明示的に保持/採用/コピーを選ぶ。

package_idによる再取込は冪等。取込前にプレビュー・確認・バックアップを作成し、完全検証後に反映する。Windowsは既存Product/LabelStore/UndoStackを使用。AndroidはSQLiteトランザクションを使用。参照はWindowsが管理元で、Windows取込ではAndroidの参照データを適用しない。

共通例は `shared/examples/windows-fixture.zip`（合成データ）。Kotlinテストが出力したZIPを `scripts/verify_mobile_roundtrip.py` で既存Windows Productへ戻して検証する。

履歴出力は日別＋月別/月別のみ/選択期間のみから選べる。初期範囲は直近7日。300台の詳細・取得元・レーダーを含むため、日別の長期間出力はサイズ上限を超える場合がある。その場合は月別または選択期間だけを出力し、欠損値や履歴を無断で削らない。
