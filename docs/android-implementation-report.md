# Windows連携型Android版 実装・検証報告

対象ブランチは `feat/android-offline-companion`、基点は既存Windows版の `3cc1606cfcdec6ce1d5dc85f699420df06fce1e6`。既存Windowsソースは移動していない。GitHubへのpush・CI起動・mainへのマージは未実行。ユーザー指定に従い、pushとCI起動は承認後に行う。

## 実装内容とファイル

| 対象 | 主な変更 | ファイル |
| --- | --- | --- |
| Android基盤 | Kotlin / Compose / Material3、Android 8以上、SQLite、自動保存、ライト・ダーク・システム、端末表示モード | `android/app/`、`android/gradle/` |
| 閲覧 | Windowsの座標・UID・実績・評価色を保持した売り場、検索時の非一致減光、ピンチ・パン、詳細、実数値レーダー、提供済み期間の切替 | `Screens.kt`、`AppModel.kt` |
| 指示書 | 商品未定・手動追加、編集、選択・一括運営方法、複製・削除・順序、Undo/Redo、共通番号の入替予定地図 | `Documents.kt`、`Screens.kt`、`AppModel.kt` |
| 共通形式 | 固定8ファイルZIP、JSON Schema、チェックサム、0/nullの区別、型・ID・参照検証 | `shared/schemas/package-v1.schema.json`、`mobile_package.py`、`Protocol.kt`、`ZipDirectory.kt` |
| Windows交換 | 既存Productへの取込、改訂と祖先による競合検出、三者差分、バックアップ、既存Undo、連携ページ | `mobile_bridge.py`、`mobile_sync.py`、`mobile_ui.py` |
| QR | 既存UIDのみのURI、WindowsのA4ラベル/PDF/PNG、AndroidのオフラインZXing読取 | `mobile_qr.py`、`MainActivity.kt` |
| USB | 認証済み・明示選択したADB端末、固定inbox/outbox、部分ファイルからのrename、確認後ACK | `mobile_adb.py`、`AppModel.kt` |
| データ保護 | 不正取込を適用しない、取込前バックアップ、DB破損時に元DBを削除しない、読込失敗後の保存を停止 | `Repository.kt`、`AppModel.kt`、`StorageSafetyTest.kt` |
| ビルド | Android APKの独立ワークフロー、Windows EXEへの共通スキーマ・依存データ同梱 | `.github/workflows/build-android.yml`、`build-windows.yml`、`mobile_smoke.py` |

既存ファイルの変更は `ui.py` の連携ページ登録、`smoke.py` の交換形式確認、依存・ignore・改行・ビルド設定とREADMEの案内への追加だけ。既存テストは削除・無効化していない。新規テストは `tests/test_mobile_package.py`、`tests/test_mobile_adb.py`、AndroidのJVM/端末テスト。

Androidに作業進捗・作業完了チェック・実際の商品変更記録・写真記録の画面、テーブル、ダミー操作は追加していない。Windowsの既存機能は維持している。

## 調査・修正した問題

- 参照フレームの出力は既存の照合・集計・色分け・レーダー処理を再利用。レイアウト由来の売上でも全台比較基準を使用し、未照合実績を別ブースへ割り当てない。
- 機械番号と管理番号を別々に交換。UIDが分かっている番号未取得ブースも指示書に保持する。曖昧な照合は確定しない。
- 同日に複数の交換文書がある場合の行順を共通管理。Windows取込で変更のない同順位の行が移動しないようにし、帳票・地図番号を保護する。
- スマホで上部操作が編集領域を圧迫していたため、操作をメニューへまとめ、行編集を独立したスクロール画面にした。小数の入力途中で値を整形しないようにし、CS数の `1` → `.` → `5` 入力を実操作で検証する。個数・位置メモもUndo/Redo後の表示が保存値に追従するよう修正した。
- ダークモードで背景だけが暗くなり一部見出しが黒のままになる経路を、ルートSurfaceの共通文字色設定で修正。端末テストで見出しの描画ピクセルのコントラストも検証する。
- SQLiteの標準破損ハンドラーによるDB削除を独自ハンドラーで防止。読込失敗後に空のモデルを自動保存して既存指示書を削除する経路も停止。検証には本体DBと異なる一意のテストDBだけを使用する。
- 300ブースの取込確定時の全体検証をバックグラウンドへ移動。数値・形式の正規表現を再利用し、型が違うSchema候補の例外生成を省略する。oneOfの重複一致や値の制約を維持するテストを追加した。
- 編集・出力時に古い自動保存を通常キャンセルしただけで保存失敗を表示する経路を修正。キャンセルは再送出し、本当の保存例外と区別する。
- USB通知で別のMainActivityモデルが開かないよう `singleTask` を使用。コピー完了と取込完了は分離し、実際の取込ACKを待つ。

## 操作開始

1. Android Debug APKを端末にインストールする。開発用署名のためストア配布用ではない。
2. 新しいWindows版を起動し「Android連携」で「全体パッケージ」を手動ZIP出力する。
3. Androidの「データ連携」でZIPを選び、プレビューを確認して取り込む。初回はこのWindows参照データが必要。
4. 売り場やQRから詳細を開き、指示書へ追加する。現在の商品名を新景品名へ自動転記しない。商品未定のまま保存できる。
5. Androidから指示書ZIPを保存・共有し、Windowsで取り込む。競合があれば保持・採用・コピー・差分統合を選ぶ。既存の指示書画面で編集・印刷する。

USB初回設定と固定パスは [USB手順](usb-transfer.md)、形式は [ZIP仕様](package-spec.md)、QRは [QR仕様](qr-spec.md)、ビルドは [Android README](../android/README.md) を参照。Android連携ページへのZIPドロップ、AndroidのSAF/共有受信も対応する。

## 検証結果

- Windows既存643件を含む回帰テストを実行。655件の全件成功を確認した後、追加検証を含む657件の実行では656件成功、150%DPI・300ブースの描画テスト1件がエミュレーター同時実行中に120秒制限で失敗した。テストも制限も変更せず、エミュレーターを一時停止して同じテストを単独再実行し、95.37秒で成功した。
- 最終追加テスト16件はすべて成功。ZIP、空欄商品・0と欠損、既存Product、改訂競合・削除競合、履歴集計、取得元、QRラベル、型不正・レーダー範囲、同順位行の番号維持、ADBの明示端末・ACK待ちを検証。
- Kotlin/JVMテスト10件成功。Windows生成QRの画像をZXingでオフラインデコードし、Kotlin生成ZIPをPythonの既存Productへ戻す往復も成功。
- AndroidのAPKビルド成功、Lintは問題0件。端末テストは専用Android 9 / API28エミュレーターで実施し、取込→確認→編集→Undo/Redo→小数入力→地図→縦横回転→候補追加→USB用ZIP準備→不正取込時の保護を検証。
- 端末テスト3件はすべて成功。うちデータ保護テスト2件で、破損DBの原本・退避コピーの保持と、JSON読込失敗後の保存拒否を確認した。
- 実ADBの双方向交換をエミュレーターで実施。Android生成ZIPをWindows Productへ取り込み、Windowsで編集したZIPをAndroidへ送信し、`copied_pending` → `pending` → `imported` の実ACKを確認した。途中にエミュレーターのSystem UI応答待ちダイアログが出たため「待機」を選択して再開した。コピーだけを成功扱いしていない。
- 合成300ブースの参照ZIPを、Windowsの共通評価処理から生成して実ADBで取込・描画した。取込ACKと画面上の300台を確認。無加速API28エミュレーターでPSSは約124MiBだった。最初の検証ではACK監視が先にタイムアウトしたため、後で実ACKを確認し、検証処理のUIスレッド分離・型候補の絞り込みを追加した。実機でのFPS・読込時間は未測定。
- 既存47商品の帳票を、A4/A3・行配置/列配置で変更前後比較。11ページすべて描画一致。元の帳票描画モジュールは無変更。
- Linux上のPyInstaller配布形式で、共通Schema・ZIP・QRを含むsmokeが成功。これはWindowsネイティブEXEの確認を代替しない。

検証ログ・XML・画像・帳票比較は作業領域 `/workspace/android-review/` に保存し、業務データや端末の秘密設定はリポジトリへ入れていない。共通テスト例は合成データだけ。

## ビルド成果物と未検証範囲

ローカルAndroid Debug APKは `android/app/build/outputs/apk/debug/app-debug.apk`。渡しやすいコピーを `/workspace/PrizeSwapCompanion-android-debug.apk` に保存する。

GitHub Actionsは未起動のため、今回の実行URL・CI成果物はまだない。承認後にこの作業ブランチをpushすると、Windows/Androidの対応するワークフローが起動する。成功時の成果物名は次の予定。

- `PrizeSwapCompanion-android-debug`：Android Debug APK
- `Android-test-results`：JVMテスト・Lint等のレポート（端末UIテストは今回ローカル実施）
- `PrizeSwapInstructionMaker-windows-x64`：WindowsポータブルEXEを含むZIP
- `Pytest-results` / `Windows-validation`：Windowsテスト・配布EXEの検証結果

## 既知の制約

- 今回のブランチのWindowsネイティブEXEビルド・起動確認は承認後のCIで実施する。物理プリンター、実端末カメラ、OEM固有ストレージ制限・Windows USBドライバーは未検証。
- 日別・月別・選択期間はWindowsが計算して出力したフレームを切り替える。Android独自の任意期間再集計・タイムラプス再生は行わない。
- 履歴範囲は最大90日・初期7日。詳細が多くZIP上限を超える場合は、Windowsで月別または選択期間のみを指定する。超過時に既存データを削除したり無断で履歴を間引いたりしない。
- ZIPは暗号化・電子署名されない。保存先・共有相手を確認する。サイズ上限は圧縮前後64MiB、1ファイル32MiB。
- 参照データはWindowsが管理元。初回Windows ZIPなしで新しい売り場をAndroidだけに作る機能は対象外。異なる売り場IDの混合取込も行わない。
- Release署名は未設定。ローカルとCIのDebug署名は異なる場合がある。異なる署名へ切り替える前に、必ず指示書ZIPを端末外へ保存する。署名鍵・パスワードをコミットしない。
- DB破損を検出した場合は編集・取込・保存を停止し原本を保持する。自動的なデータ消去・初期化・不完全復元はしない。復旧は原本/バックアップを保全して実施する。
