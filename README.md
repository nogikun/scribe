# Scribe

会議の動画・音声を話者分離付きで文字起こしし、Markdown・TXT・VTT・SRT・JSONに書き出すスキルです。

## 導入

導入には `npx`（Node.js）、実行には [uv](https://docs.astral.sh/uv/getting-started/installation/) が使える環境が必要です。

```bash
npx skills add nogikun/scribe --skill scribe
```

導入時に利用するAIエージェントを選択してください。

## 使い方

AIエージェントに「この会議動画を文字起こしして」「VTTやテキストに書き出して」と依頼します。同梱CLIはuvで実行し、初回はスキルの手順に沿って依存パッケージとモデルをセットアップします。

詳しい手順は [スキルの説明](skills/scribe/SKILL.md)、CLIを直接使う場合は [CLIのREADME](skills/scribe/tools/scribe/README.md) を参照してください。

## 対応ファイル形式

入力できる主な拡張子は次のとおりです。動画は音声トラックが必要です。読み込み可否はファイル内のコーデックや破損状態にも依存します。

| 入力 | 拡張子 |
|---|---|
| 音声 | `.wav`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.ogg`, `.oga`, `.opus`, `.wma`, `.aiff`, `.amr` |
| 動画 | `.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.avi`, `.wmv`, `.flv`, `.ts`, `.mts`, `.3gp`, `.mpg`, `.mpeg` |

## 出力形式

| 形式 | 拡張子 |
|---|---|
| Markdown | `.md` |
| テキスト | `.txt` |
| WebVTT字幕 | `.vtt` |
| SubRip字幕 | `.srt` |
| JSON | `.json` |

## ライセンス

Copyright 2026 nogikun

Scribeのソースコード、スキル定義、ドキュメントは [Apache License 2.0](LICENSE) で公開しています。ライセンスの条件に従って、商用利用・改変・再配布が可能です。無保証で提供されます。

利用するモデルと依存ライブラリには、それぞれのライセンスが適用されます。モデルの商用利用条件と、モデルや実行環境を同梱して再配布する際の注意点は [第三者ライセンス](skills/scribe/THIRD_PARTY_NOTICES.md) を参照してください。
