# Windows連携型Android 実装計画

基点はWindows検証済み `3cc1606`。Windowsソースは既存の場所を維持し、Androidは `android/`、交換仕様は `shared/` に分離する。push/CI起動はユーザーの実行前承認待ちとし、ローカル実装・検証・コミットを先に完成させる。

## 調査結果

- `Machine.uid` が配置・共通選択・指示書位置照合の永続ID。機械番号・管理番号は別項目で、`machine_matching` の既存ルールを使用する。
- 指示行は `core.Product` と `entry_id`、ブースは `booth_assignments.assignment_id`。商品名空欄・未設定数量は有効。`instructions.booths` が出力ONの行・順序から共通番号を生成する。
- 指示書の編集/保存は既存Productを使用し、Android取込もこのモデルへ変換する。独立したWindows指示書ストアや別帳票を作らない。
- 日別・期間は `ReferenceLibrary` / `temporal_data`、画面の値は `AnalyticsService`、色は `floor_status` と共通評価サービス。全台売上比較の母集団を維持する。
- `printing.py` ほか帳票描画は変更しない。QRラベルは独立した描画経路。

## 段階

1. バージョン付きJSON/ZIP仕様、ID、null/0、Product互換性、改訂と三者差分の競合処理、共通テスト例を確定。
2. WindowsのZIP作成/検証/プレビュー/取込。破損・重複・上位版・不正パス・過大ZIPを拒否。適用前バックアップと既存Undoに接続。
3. Kotlin/Compose/Material3/SQLite/CoroutinesのAndroid基盤とSAF手動ZIP交換。
4. Windowsが計算した対象日/期間ごとの参照フレームを表示。Androidで別の順位や比率を推測計算せず、用意された日別・月別・任意期間を選択する。
5. 永続UIDのみのQR、WindowsのA4ラベル/PDF/PNG、AndroidのオフラインQR読取。
6. Product互換の指示書作成/編集/複製/順序/複数選択/一括編集/Undo/Redo/自動保存と、同じブース番号の地図。
7. 認証済みADBの端末選択、専用外部領域の固定inbox/outbox、確認パネルと結果ACK。コピー完了を同期完了と扱わない。利用不能ならSAFに切替。
8. 共通仕様・往復・競合・セキュリティ・Qt/Androidテスト、Windows全回帰と帳票比較、ローカルAPKビルド。
9. Windows/Androidの変更パスで分離するActions定義を作成。push/CI起動の承認は具体的な差分・テスト結果・ブランチを提示して最後に依頼。

## 重要な方針

- Androidに作業進捗、作業完了チェック、商品変更記録、写真記録の画面/テーブル/ダミー操作を追加しない。既存Windows機能は削除しない。
- 双方向編集は指示書だけ。参照データはWindowsが正。日時だけで上書きせず、改訂ID・祖先と共通基点で競合を判定する。
- 端末間パッケージにローカルファイルパス、ADB設定、認証情報を含めない。異なるデータセットは混同しない。
- ADBは利用者がインストールしたものを使用し、認証回避やrun-as/rootを必要としない。OEM等により専用外部領域が書込不可なら手動交換を案内する。
- 設定、手動補正、レイアウトの数値をAndroid側で勝手に書き換えない。

## 実装上の保存・表示境界

Windowsの日別集計・比較付与まで共通処理で実行し、Androidには参照フレームを渡す。全台Excel比較の母集団は保持し、レイアウトだけの値を使う場合も既存レーダーの比較処理を再利用する。作成時刻と実データ更新時刻は別に表示する。

Windowsの交換サイドカーは既存のレイアウト/一時保存ファイルを変更しない。改訂基点の保持は文書ごと128件・全体512件/32MiBに制限し、共通基点がなくなった場合は自動統合せず保持/採用/コピーで扱う。Androidの改訂基点は全体2048件、Undoは100操作。指示書本体を履歴の整理で削除しない。

Windowsの印刷モジュールは無変更。凍結アプリのsmokeにJSON Schema/ZIP/QR確認だけ追加し、Windows配布物へshared/とjsonschema_specificationsを同梱する。依存のライセンスはshared/licenses/に含める。
