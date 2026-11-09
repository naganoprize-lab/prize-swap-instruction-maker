# USB / 手動ZIP交換

## 初回

1. 公式Android SDK Platform ToolsのadbをWindowsへ用意する（アプリには同梱しない）。
2. Androidの開発者オプション・USBデバッグを有効にし、USB接続する。
3. Android上の確認画面でPCを認証する。Androidアプリを一度起動する。
4. Windows「Android連携」でadb.exeを選択し「接続確認」。認証済みの端末を明示的に選択する。複数台でも勝手に1台を選ばない。

未接続/offline/unauthorizedは転送不可。認証回避、root、run-as、ネットワークデバッグ有効化は行わない。

## 固定転送領域

アプリID `jp.naganoprize.prizeswap`。Android `getExternalFilesDir(null)/transfer`。通常ADBパスは `/sdcard/Android/data/jp.naganoprize.prizeswap/files/transfer`。inbox/outbox/acks/receiptsだけを使用し、端末から受けた任意パスでshellを呼ばない。subprocessは引数配列で起動する。

Windows→Android：検証ZIPを `inbox/{package_id}.zip.part` へpushし、完了後zipへrename。固定Activityを起動し、IDだけ通知する。Androidは起動中/前面時に2秒間隔で検出し、共通検証→プレビュー→ユーザー確認→DB反映→ `acks/{id}.json` をatomic保存。WindowsはACKを取得して imported/already_imported のときだけ完了表示する。コピーだけはcopied_pending。確認待ちは最大約5分、タイムアウト/切断では完了にしない。

Android→Windows：Androidで「Windowsへ送るZIPを準備」。outbox/{id}.zip とlatest.jsonをatomic作成。Windowsは固定最新情報を検証しZIPをpull→検証→プレビュー→確認→既存指示書へ反映。その後 `receipts/{id}.json` をpush。Androidは前面時に結果を検出する。prepared/exportedとimportedを区別する。

ACK形式：package_id/status/message（任意）。statusはpending/imported/already_imported/rejected/error。Windowsのreceiptはimported/rejected/error。重複取込はpackage_idで検出する。

OEM・Androidポリシーによりshellからアプリ専用外部領域へ書込できない場合がある。権限を迂回せず、下記の手動交換へ切り替える。実機ごとのUSBドライバー/ケーブル/組織ポリシーは別途確認が必要。

## 手動交換（必須経路）

Windows：手動ZIP出力/取込、ZIP1ファイルをAndroid連携ページへドロップ。
Android：データ連携 → ZIPを取り込む/指示書ZIPを保存/ZIPを共有。Storage Access Frameworkを使用し、USB転送、SD、USBメモリ、ファイル管理アプリ等の保存先を選べる。共有で受け取る場合はcontent URIだけを読み、外部URLを実行しない。

両経路とも同じPackageCodec/共通スキーマを使用。ZIPは暗号化されないため、保存先と共有相手を確認する。インターネット、外部クラウド、ストレージの全ファイル権限は不要。
