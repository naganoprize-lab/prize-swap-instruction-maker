# 売り場管理 Android Companion

Kotlin 2.1 / Jetpack Compose / Material3 / SQLite。minSdk26 (Android8)、compile/target35。Windowsコードは移動していません。初回はWindowsで「Android連携 → 手動ZIP出力」、Androidで「データ連携 → ZIPを取り込む」を実行します。

## ビルド

JDK17、AndroidSDK35、build-tools35を用意し、Android Studioからandroid/を開くか、次を実行します。

```sh
./gradlew testDebugUnitTest assembleDebug assembleDebugAndroidTest lintDebug
# 認証済みAndroid端末またはエミュレータがある場合
./gradlew connectedDebugAndroidTest
```

Windowsはgradlew.bat。local.properties、署名鍵、パスワード、SDKはコミットしません。Debug APKは `app/build/outputs/apk/debug/app-debug.apk`。開発用のデバッグ署名で、ストア配布用ではありません。Release署名は未設定です。開発環境とCIで生成するDebug署名は異なる場合があるため、ビルドを切り替える前に指示書ZIPを保存してください。端末テストは専用エミュレータで実行します。

## 画面

ホーム、売り場（検索・期間・色分け・拡大縮小・詳細）、QR読取、入替候補、指示書編集/予定地図、データ連携、表示設定。タブレット横はナビ＋売り場＋幅変更可能な詳細。スマホは下部ナビ・1カラム詳細。自動/手動の端末モードとライト/ダーク/システム設定を保存します。

指示書は商品名/数量/運営方法が空欄でも作成可能。現在の商品名を新商品名に入れません。セル編集はローカルに自動保存、複数選択・長押しハンドルによる上下移動・ボタン移動・複製・一括方法設定・Undo/Redoに対応します。取込前には確認とバックアップを行います。

USBはWindows側で明示的に選んだ認証済みADB端末のみ。Androidを起動しておきます。詳細は [USB手順](../docs/usb-transfer.md)。企業ポリシー等でADBを使えない場合もSAF/共有ZIPで交換できます。ネット接続・クラウド・サーバーは不要です。

Androidには作業進捗・作業完了チェック・商品変更記録・写真記録を実装していません。

## 依存とライセンス

AndroidX/Compose、Kotlin、Coroutines、kotlinx.serialization、ZXing、ZXing Android EmbeddedはいずれもApache-2.0。PNGJ（Apache-2.0）とJUnit（EPL-1.0）はJVMテスト専用。Google SDKは開発環境だけで使用しアプリへ再配布しません。WindowsのsegnoはBSD-3-Clause、jsonschemaはMIT。アプリはCAMERAをQR操作時だけ要求し、全ファイル権限とINTERNET権限を追加しません。

## 交換仕様とテスト

[ZIP仕様](../docs/package-spec.md) / [QR仕様](../docs/qr-spec.md)。合成データ `shared/examples/windows-fixture.zip` を両言語で検証し、Kotlinが出力した指示書を既存Windows Productへ戻すテストを実行します。実売上データや秘密情報はテスト例へ含めません。

GitHub Actionsの定義はWindowsとAndroidを変更パスで分離します。ユーザーの明示承認前にpush/CIを起動しません。
