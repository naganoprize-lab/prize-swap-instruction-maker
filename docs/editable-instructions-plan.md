# 既存帳票を維持する指示書編集・入替予定地図

基点：517f368。作業ブランチ：feat/editable-instructions-plan-map。

1. 出力判定を既存Product.includedに統一。候補追加時のincluded=False、空名をONにできないProductModel、Window.print_productsの空名拒否、全選択時の空名除外が重なっていた。既存OFFは保持し、新規追加のみONにする。運営方法未設定は印字を空欄にする。
2. Productを維持し、永続entry_id、追加元、備考、編集項目、ブース割当IDを追加。既存JSONは補完して読み込む。セル編集・詳細編集・追加／複製／削除／順番変更をUndoで記録。Excel再読込は手動優先で統合する。
3. 候補の手動登録属性と追加元を保存。既存自動判定・確認付き単体／一括追加を再利用する。
4. ブースの表示連番と永続assignment_idを分離。現在の出力対象を登録順に番号付けし、既存のマシン欄の中だけに小さな丸枠番号を描く。列幅・余白・最適化は変更しない。
5. 指示書内の地図タブは既存floor.machinesの座標を参照。UID→機械番号→参考表→管理番号で照合し、曖昧／不明は一覧に出す。地図からの選択は指示書のentry_idに戻す。地図側の配置編集は行わない。
6. 地図専用A4/A3・向き・読める番号を基準に分割。プレビュー／PDF／直接印刷を同じ計画で描画し、指示書＋地図では用紙設定をページごとに切替える。
7. tests/baselines/instructionsの変更前20／40商品PDFと寸法JSONを基準として、罫線・列幅・行高・ページ構成と番号以外の日本語描画を検証。既存テストを保持して全件／Windows EXEを検証する。

## 実装した段階と検証の対応

| 段階 | 実装 | 主なファイル | 検証 |
|---|---|---|---|
| 1 印刷対象 | 空名・数量未定・運営方法未設定を許可、OFF維持、0件時に現在の範囲だけ全選択 | ui.py / table.py / instruction_fields.py / candidate_transfer.py | test_instruction_blank_print.py、既存PDF・Excelテスト |
| 2–3 編集と手動登録 | 既存Productを拡張、セル・詳細編集、追加・複製・削除・順序・Undo、Excel手動優先、バックアップ付き自動保存 | core.py / instructions.py / instruction_editor.py | test_instruction_editing.py、旧JSON復元 |
| 4 候補連携 | 選択台の手動候補属性、自動判定維持、単体／一括追加、追加元と方式の保存 | selection.py / candidate_page.py / candidate_transfer.py | 編集・選択転送テスト |
| 5 番号 | 永続entry_id／assignment_idと整数連番を分離、マシン欄内の小さな丸枠、番号以外の帳票寸法維持 | instructions.py / instruction_badges.py / printing.py / exporter.py | 20・40商品の基準寸法／日本語PDF、21以上の番号 |
| 6 地図 | 内部タブ、既存座標参照、安全な照合、曖昧・不明の一覧と手動選択、行との双方向選択、ズーム | instruction_map.py / instruction_map_ui.py | test_instruction_map.py / test_instruction_map_ui.py |
| 7 印刷統合 | 地図A4/A3・各向き・自動分割、指示書／地図／両方、実PDFを使う混在用紙プレビュー、共通描画 | instruction_output.py / instruction_preview.py / printing.py | 紙寸法・共通対象・PDFと直接印刷の文字比較 |
| 8 完成確認 | 300台、実セル・追加フォーム・確認・地図クリック・保存復元・ライト／ダーク・DPI検証をWindowsとEXEへ追加 | instruction_visual_check.py / main.py / build-windows.yml | 全pytest、Windows 100/125/150%、EXE 150% |

変更前PDFは `tests/baselines/instructions/before-20.pdf` と `before-40.pdf`。環境ごとのフォント差を除いて比較するため、変更前描画コードをテスト専用の参照として固定し、同じ環境で列幅・行高・フォント・改ページを比較する。アプリは既存の描画処理を継続して使用する。

## 制約と運用

- 物理プリンターの紙送り・実インク出力は接続機器で確認が必要。テストは実際の印刷ダイアログ承認後の共通描画をPDF出力先で検証する。
- 混在用紙を拒否するドライバーには明確な警告を出す。指示書のみ／地図のみで分けて印刷できる。
- 地図で位置不明のブースは一覧を独立ページにも印刷する。誤った位置を補わず、指示そのものの出力は許可する。
- Excelで同一商品が複数あり更新先を一意に特定できない場合、既存行を保持して新しい行を追加し、重複はユーザーが確認・編集する。
- 商品未定の指示は通常帳票で出力できるが、景品名だけを印刷する入荷ラベルは空名を除く。
