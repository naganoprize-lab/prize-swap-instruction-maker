# Windows 機器番号照合・フルHD修正：CI／EXE取得結果

専用ブランチ：`fix/windows-fullhd-equipment-matching`。

アプリ修正コミット：`1c2ff2f5ebe544a1586a2b11ca6175d7b0f0d408`。
ビルド対象コミット：`2d45a05261ed7f6a1f5733f82194e56f9b4f56fd`。
両者のアプリ本体は同一で、差分はDPIテストのQt設定パス指定だけです。

ユーザーの明示承認後にpushし、[Windows CI #58](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359) が成功しました。mainへのpush・マージ、タグ作成、リリース公開は行っていません。

## 取得方法

1. GitHubにログインし、[Windows用EXE成果物](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359/artifacts/13750056874)を開きます。
2. `PrizeSwapInstructionMaker-windows-x64` をダウンロードします。
3. ZIPを展開し、中に `PrizeSwapInstructionMaker-windows-x64.zip` がある場合は、そのZIPも別フォルダーへ展開します。
4. `PrizeSwapInstructionMaker.exe` を起動します。

成果物のGitHub表示サイズ：**52.5 MB**。保存期間は30日です。リンクで取得できない場合は、上記CI実行ページ下部の **Artifacts** から同名の成果物を選択してください。

GitHub成果物アーカイブのSHA-256（EXE単体のハッシュではありません）：

`81026028cad8eac589bac8cb9a9b7d10fca962f336278a82a95b0d7811263e3e`

## CIの検証結果

- 日本語PDFのフォント・描画診断、既存PDF回帰テスト：成功。
- 全体pytest：成功。構成は既存・追加合わせて687項目。詳細結果は `Pytest-results` のXMLに保存されています。
- Windowsネイティブで全画面・DPI・テーマ・分析・詳細・指示書・地図・ドッキング・原価・読込安全性・日別参照・タイムラプスを検証：成功。
- Windowsの300ブース性能測定（比較元・通常・軽量）：実行成功。測定値は証跡成果物に保存されています。
- PyInstallerによるWindowsポータブルEXE生成：成功。
- **生成したEXEそのもの**の基本起動、200%全画面、150%の照合・テーマ・分析・詳細・指示書・地図・ドッキング・フィルター・日別参照・タイムラプス検証：成功。
- 配布ZIP内のEXEとREADMEが存在し、空ファイルでないことを確認：成功。

証跡の成果物：

| 成果物 | 内容 | 取得 |
|---|---|---|
| `PDF-diagnostics` | CIの診断・テスト・画面検証ログ | [開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359/artifacts/13749696105) |
| `Pytest-results` | CIの診断・テスト・画面検証ログ | [開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359/artifacts/13749123764) |
| `Windows-native-visual-validation` | CIの診断・テスト・画面検証ログ | [開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359/artifacts/13749529987) |
| `Windows-validation` | CIの診断・テスト・画面検証ログ | [開く](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42785333359/artifacts/13750506160) |

## 初回CIで修正した問題

[初回 #57](https://github.com/naganoprize-lab/prize-swap-instruction-maker/actions/runs/42784819921) では、追加DPIテスト3件の子プロセス起動に失敗しました。Qtのplatform引数はコロンで区切られるため、Windowsの `C:` を含む絶対パスを設定ファイルとして渡す方法が不適切でした。

子プロセスの作業フォルダーに `screen.json` を作成しているため、相対ファイル名を指定するよう修正しました。アプリの実装や印刷内容を変更せず、ローカル3倍率の再テストと、修正版 #58の全体テストが成功しています。

## 残る実機確認

Windows CIでのネイティブ実行とEXE実行は確認済みです。利用者の実際の店舗PC・保存済みデータ・実プリンターでの操作は代行確認していません。[修正報告書](windows-fullhd-equipment-fix.md) の手順で、1920×1080／100%・125%・150%、実データの読込、直接印刷、既存AndroidとのZIP往復を確認してください。

GitHubに送信したのはソース・合成テスト・文書です。添付の業務Excelや利用者環境の設定・保存データはコミットしていません。印刷レイアウトの実装、ZIPスキーマ、Androidコード、QRのUID生成は変更していません。
