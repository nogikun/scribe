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
