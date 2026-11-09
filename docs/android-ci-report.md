# Windows / Android CI ビルド結果

ユーザーの明示承認後、既存リポジトリ `naganoprize-lab/prize-swap-instruction-maker` の `feat/android-offline-companion` をpushした。mainへのpush・マージは行っていない。

アプリ本体のコミットは `eff8ac2428107e88422e870f09851299a9633653`。CI設定修正と診断を含むコミットは `3638df6`。これらの間で `prize_swap/`、`main.py`、`requirements.txt`、`shared/` の内容は変わっていない。Androidのコードも同一。

## 実行結果

| 対象 | 結果 | CIのソースコミット | 実行URL |
| --- | --- | --- | --- |
| Windows #56 | 成功 | `5d7d665` | [Windows CI](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42656528467) |
| Android #4 | 成功 | `3638df6` | [Android CI](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42657556303) |

同一のWindowsアプリを検証した先行 [Windows #55](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42654161917) も成功。mainは `3880a5450c6cbe0ed7f4fcf1329795318c3ee71a` のまま。


## CIで修正した設定

1. AndroidのSDK準備で失敗した。`android-actions/setup-android@v3` の既定インストール対象に、Googleが現在配布していない旧 `tools` が含まれていた。`platform-tools`、`platforms;android-35`、`build-tools;35.0.0` を明示した後、SDK準備は成功した。
2. APK生成・単体テスト・Lintは成功したが、Windowsモデルを使うZIP互換性検証で失敗した。診断注釈を追加して、Ubuntu runnerの `libEGL.so.1` 不足を特定した。Qtの実行ライブラリをCIに追加し、既存Windowsモデルへの変換チェックを通した。検証の削除・緩和はしていない。
3. 準備段階で失敗した場合、まだ存在しないテストレポートをアップロードして二次エラーにしないよう、レポートの存在確認を追加した。正常ビルドではAPK・レポートの存在を必須とする。

変更ファイルは `.github/workflows/build-android.yml` と `scripts/verify_mobile_roundtrip.py`。既存の帳票描画やアプリ本体には、このCI修正による変更を加えていない。

初回Android #1、#2、診断追加後の#3の失敗は記録として残している。成功した実行と区別して上表に示す。

## 検証範囲

- Windows：PDF回帰、全pytest、高DPI、照合・テーマ、分析・詳細、指示書・地図、原価・ドッキング・取込保護、売上フィルター、日別・期間・タイムラプス、300ブース性能検証、PyInstaller EXE生成、配布EXEでのsmokeと各画面の再検証。
- Android CI：JVM単体テスト10件、Debug APKと端末テストAPKの生成、Lint、Kotlin生成ZIPを既存Windows Productに戻す検証。
- ローカル：Android 9/API28の端末テスト3件、合成300ブースの取込・描画、実ADBの双方向受渡し。既存47商品のA4/A3・行/列配置は計11ページの描画一致。追加Windowsテスト16件成功。
- 端末UIテストはローカルの専用エミュレーターで実施した。GitHub上で実機カメラ・物理プリンター・店舗端末のUSBドライバーを検証したものではない。

## 取得と起動

| 対象 | 成果物名 | ダウンロード | アーカイブサイズ |
| --- | --- | --- | --- |
| Windows | `PrizeSwapInstructionMaker-windows-x64` | [成果物を開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42656528467/artifacts/13693803686) | 52.4 MB |
| Android | `PrizeSwapCompanion-android-debug` | [成果物を開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42657556303/artifacts/13693471671) | 16.4 MB |

GitHubへログインし、リンク先または各実行ページのArtifactsから取得する。成果物の保持期間は30日。

GitHub成果物アーカイブのSHA-256（EXE/APK単体のハッシュではない）：

- `PrizeSwapInstructionMaker-windows-x64`：`b3e871ebafee96fe7d367d9e80d0c93865283939e1d12ed410a3db8a6b7c043c`
- `PrizeSwapCompanion-android-debug`：`2a1eb04dbe3d91a93eabce3c8a1c520bcf149f35776a54890a8d9529892bd6ef`


Windowsの成果物はダウンロードしたアーカイブを展開し、内側に配布用ZIPがある場合はそれも展開して `PrizeSwapInstructionMaker.exe` を起動する。Pythonのインストールは不要。

Androidの成果物を展開して `app-debug.apk` をインストールする。初回はWindowsの「Android連携」で全体ZIPを出力し、Androidの「データ連携」で確認して取り込む。操作・初回ADB設定は [USB/手動交換の手順](usb-transfer.md) を参照。

**Debug APKの署名について**：ローカルビルド、別のCI実行のAPKは署名が異なる場合がある。既存のDebug版から切り替える前に、指示書ZIPを端末外へ保存する。署名が違う場合のアンインストールではアプリ内DBが消えるため、未退避のデータがある状態で行わない。Release署名・配布鍵は今回設定していない。

## 既知の制約

実機カメラ、実プリンター、実際のWindows USBドライバーやOEM端末のストレージ制約は別途確認が必要。Android側の任意期間再集計・タイムラプス再生は対象外で、Windowsで生成した参照フレームを表示する。詳細は [実装報告](android-implementation-report.md) を参照。
