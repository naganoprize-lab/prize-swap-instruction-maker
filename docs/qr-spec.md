# マシンQR v1

URI: `prizeswap://machine/v1/{machine_id}`。machine_idは既存Windows Machine.uidのみ。売上・商品名・ローカルパス・命令を含めない。大文字小文字を区別し、クエリ・フラグメント・パーセントエンコード・追加パスは許可しない。

WindowsのAndroid連携 → QRコード管理で、全台/共通選択中/機械番号または管理番号の一意候補を選ぶ。標準65×35mm（変更可）のA4ラベル、プレビュー、直接印刷、PDF、1台1PNG（300dpi）を出力する。QRは4モジュール余白・誤り訂正M、segnoの純Python生成＋Qt描画。既存指示書の印刷経路を変更しない。

AndroidはQR操作時だけCAMERA権限を要求。ZXing内蔵デコーダーを使い、モデルのダウンロード・通信は不要。URI検証後、ローカルマスターのUIDに一致する詳細を開く。未知UIDは参照更新を案内し、マシンを新規作成しない。カメラ拒否時も番号検索で閲覧できる。

segnoはBSD-3-Clause。ZXing/ZXing Android EmbeddedはApache-2.0。QR用外部UIライブラリやWindowsへの画像処理バイナリ追加はない。
