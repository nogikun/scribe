# Scribe 再設計 (v7)

元仕様: [Scribe_technical_specification.md](Scribe_technical_specification.md)。本書は差分と決定事項のみ記す。
実装: [skills/scribe/tools/scribe](../../skills/scribe/tools/scribe) (スキル: [skills/scribe/SKILL.md](../../skills/scribe/SKILL.md)) / 検証: [Scribe_validation_report.md](Scribe_validation_report.md)

## 0. 技術調査の結論 (2026-10 時点)

| 候補 | 状況 | CPU/8GB | 判断 |
|---|---|---|---|
| pyannote Community-1 (pyannote.audio 4.x) | CC-BY-4.0, HF gated (HF_TOKEN + 規約同意), torch 依存 | 可 (遅め, torch で ~2GB) | 第一候補。ただし **optional extra** |
| sherpa-onnx 1.13 (pyannote-seg-3.0 ONNX + 3D-Speaker CAM++ zh_en) | Apache-2.0, トークン不要, ONNX Runtime。閾値 0.5 では過分割 (49 分で 135 話者) → **既定 1.0** | 軽量 | **最終フォールバック** (torch 不要) |
| NVIDIA Nemotron 3 Diarization (2026-09) | OpenMDW-1.1, end-to-end, 最大 8 話者。**transformers 5.19 でネイティブ対応 (NeMo 不要)**。NeMo 公式の CUDA torch は Linux のみ | **可** (49 分: CPU 70 秒 / 3.4GB, GPU 4 秒) | **既定の第一候補** (extra `nemotron`) |
| faster-whisper 1.2 small INT8 | MIT, CTranslate2 | 可 (~1GB) | **ASR 既定** |
| Nemotron 3.5 ASR 0.6B (2026-06) | OpenMDW-1.1, ja-JP CER 11.5%, GPU 前提 | NeMo-Speech.cpp で可だが Windows 配布未成熟 | V1 対象外 |
| whisper.cpp | 別バイナリ | 可 | V1 対象外 (faster-whisper と重複) |

## 1. 冗長性

1. **Backend フォールバックチェーン**: `--diarizer nemotron,pyannote,sherpa` のように順序指定 (これが既定)。未インストール・`HF_TOKEN` 未設定の Backend は即スキップ。ASR は `--asr faster-whisper`、`--device auto` は `cuda → cpu`。
   - フォールバックするのは **Backend 起因の失敗のみ** (未導入 / 例外 / クラッシュ / タイムアウト)。入力不正 (ffprobe で音声なし等) は音声抽出ステージで検出し、Backend を試さず exit 3。
2. **ステージ単位のプロセス分離 + タイムアウト**: diarize / transcribe は `python -m scribe.backends ...` の子プロセスで実行。メモリは終了時に OS が回収、segfault は親に波及しない。`--stage-timeout` (既定: 音声長×4, 最低 600 秒) 超過で kill し次の Backend へ。
3. **キャッシュの妥当性検証**: 各ステージは `key` (入力ファイルの size+mtime、Backend チェーン、オプション、**Backend パッケージのバージョン + モデル revision + schema_version**、上流ステージの key) を `job.json` に記録。key 一致 + 成果物存在のときだけ再利用。上流が再計算されたら下流も自動で再計算。
4. **ステージ状態**: `stages.<name> = {status: running|completed|failed, backend, secs, key, error}`。どこで止まったか Agent が判別できる。
5. **原子的書き込み + ジョブロック**: 書き込みは tmp → `os.replace`。ジョブを変更するコマンド (process / speaker set・rename・edit / export) はジョブ単位の OS ファイルロック (`msvcrt.locking` / `fcntl.flock`) を取る。プロセス死亡で自動解放されるので stale lock なし。取れなければ exit 1 `job_busy`。
6. **話者名の安全性**: diarize を再計算したら `speakers.json` を破棄し、`samples/` も丸ごと作り直す (SPEAKER_xx の入れ替わりで別人の名前・別人の声を提示するのを防ぐ)。
7. **オプションの永続化**: `process` のオプションは `job.json` に保存し、再開時は「既定 < 保存値 < 今回指定」で解決 (再開時にフラグを省略しても key が変わらない)。

## 1.5 モデルキャッシュ (仕様追加: 端末内で 1 か所を共有)

同じツールを端末内の複数ディレクトリに複製して使う前提のため、**モデルはツールのディレクトリにも SCRIBE_HOME にも置かない**。デファクトスタンダードである **Hugging Face Hub キャッシュ**に一本化する。

| モデル | HF repo | revision (固定) |
|---|---|---|
| faster-whisper small | `Systran/faster-whisper-small` | `536b0662` |
| sherpa-onnx segmentation | `csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` | `9403a690` |
| sherpa-onnx embedding | `csukuangfj/speaker-embedding-models` | `0743f301` |
| pyannote Community-1 | `pyannote/speaker-diarization-community-1` | `3533c8cf` |

- 置き場所は Scribe が決めず、`huggingface_hub` が解決する HF キャッシュ (`HF_HOME` → `XDG_CACHE_HOME` → 既定) をそのまま使う。全コピー共通。
- **revision を commit SHA で固定**し、ステージ key にも含める。どのコピーから・いつ実行しても同じモデル、オフラインでも再現可能。`small` 以外の `--model` は main 追従。
- `hf_hub_download` の blob 単位ロックで、複数コピーの同時初回実行でも二重 DL・破損しない。sherpa は 2 ファイルとも解決してから推論開始。
- オフライン運用は標準の `HF_HUB_OFFLINE=1`。キャッシュに無ければ `error: model_not_cached` (exit 3) を返し、Agent が「オンラインで 1 回実行」を判断できる。
- Python パッケージは uv のグローバルキャッシュで実体の重複を抑制 (Windows は hardlink、macOS/Linux は clone)。配布最適化であり機能要件ではない。
- ジョブデータは `SCRIBE_HOME` (既定 `~/.scribe`) で全コピー共通。分けたい場合のみ環境変数で切替。

## 1.55 torch のビルド選択 (OS 横断)

torch が必要な Backend (nemotron / pyannote) は extra で導入し、**torch のビルドは別の extra で選ぶ**。

```bash
uv sync --extra nemotron --extra cu128   # Windows / Linux + NVIDIA GPU
uv sync --extra nemotron --extra cpu     # Windows / Linux, GPU なし
uv sync --extra nemotron                 # macOS (PyPI 版 = CPU/MPS)
```

- `cpu` と `cu128` は `[tool.uv] conflicts` で排他。`[tool.uv.sources]` で extra ごとに PyTorch 公式インデックス (`whl/cpu`, `whl/cu128`, `explicit = true`) を割り当て、`sys_platform != 'darwin'` に限定。macOS は常に PyPI。
- 1 つの `uv.lock` に全 OS × 全ビルドが解決済みで入る (確認: cu128→`2.11.0+cu128`, cpu→`2.14.1+cpu`, darwin/未指定→PyPI `2.14.1`)。
- cu128 を選んだのは対応ドライバの幅 (R570+) のため。新しい CUDA が要る場合は extra を 1 つ足すだけ。
- 既定依存 (sherpa-onnx + faster-whisper) は torch 不要のまま。8GB/CPU 環境に torch を強制しない。

## 1.6 実装時に判明した Windows 固有の問題と対策

| 問題 | 対策 |
|---|---|
| sherpa-onnx の Windows wheel は onnxruntime.dll を同梱せず、System32 の古い 1.17 を掴んで segfault | `onnxruntime` を依存に入れ、import 前に pip 版 DLL をフルパスで先読み |
| faster-whisper 1.2.1 の PyAV デコードが新しい `av` で `metadata_errors` 引数エラー | 正規化済み WAV を numpy 配列で渡す (PyAV を通さない) |
| CUDA 11.8 環境で ctranslate2 が `cublas64_12.dll` を要求し失敗 | torch (cu128) 導入時はその `torch/lib` を DLL 検索パスに追加して GPU で実行。torch が無ければ cuda→cpu フォールバック |
| コンソールが cp932 で日本語 JSON が化ける | CLI 起動時に stdout/stderr を UTF-8 に再設定 |

## 1.65 入力モジュール (`inputs.py`)

入力はパスを渡すだけ。**パス → 拡張子 → 変換 → 16kHz mono PCM16 WAV → 推論** の流れ。

| 拡張子 | 変換 |
|---|---|
| `.wav` で既に 16kHz/mono/16bit | コピーのみ |
| 音声 `.wav .mp3 .m4a .aac .flac .ogg .oga .opus .wma .aiff .amr` | PyAV でデコード + リサンプル |
| 動画 `.mp4 .m4v .mov .mkv .webm .avi .wmv .flv .ts .mts .3gp .mpg .mpeg` | PyAV で音声トラックのみデコード |
| 未知の拡張子 | 警告を出して PyAV で試す |

- PyAV は FFmpeg ライブラリを wheel に同梱しているため、**ffmpeg / ffprobe のインストールが不要**になった (Windows / macOS / Linux 共通)。faster-whisper の依存として既に入っていたもの。
- PyAV が失敗した場合 (壊れ気味のファイル等) のみ、PATH 上の ffmpeg CLI で再試行 (冗長性)。音声トラックが無い場合は再試行しない。
- 変換結果 (`format` / `kind` / `converter`) は `stages.audio` に記録。
- URL・ストリーミングは非対応 (キャッシュ key が size+mtime 前提のため)。必要になったらダウンロード段を前に足す。

## 1.66 話者名の修正

| コマンド | 用途 |
|---|---|
| `speaker edit --job ID` | 人間用の対話ツール。発話時間順に代表音声を再生 (OS 既定プレーヤー) し、発話内容を表示して名前を入力。`p` で再生し直し、`-` で取り消し |
| `speaker rename --job ID SPEAKER_00=田中 …` | AI / スクリプト用の一括設定 |
| `speaker set --job ID --speaker … --name …` | 1 人だけ設定 |

いずれも **以前 `-o` で書き出したファイルを新しい名前で書き直す** (`refreshed` に結果)。書き直せなかった形式は `exports` から外れ、status は正しく `ready` に戻る。修正対象は SPEAKER 番号と名前の対応のみで、発言単位の話者付け替えは対象外。

`speakers` の出力には `sample_text` (代表音声区間の発話内容) を含む。音声を聞けない AI がユーザーに確認するときの手がかり。

## 1.7 進捗表示

処理中に何も出ないと止まって見えるため、**常に stderr に進捗を出す** (stdout は結果専用のまま)。`scribe/progress.py` (tqdm) に集約。

| 種類 | 表示 | 使う場所 |
|---|---|---|
| 量が測れる処理 | `bar()`: 進捗バー + ETA | ffmpeg 抽出 (`-progress` の音声秒)、sherpa (チャンク数)、faster-whisper (音声秒 + 直近テキスト)、pyannote (`ProgressHook`) |
| 量が測れない処理 | `heartbeat()`: 経過時間を毎秒更新 | モデルロード、Nemotron の一括推論 |
| ステージ | `[2/5] diarize: start` / `done in 12.3s (nemotron/cuda)` / `cached` | pipeline |

- Backend の子プロセスは stderr を継承するので、子のバーがそのまま親の端末に出る。
- stderr が TTY でない (Agent が取り込む) ときは `
` 再描画をやめ 10 秒ごとに 1 行、ASCII バーにしてログを汚さない。

## 2. 拡張性

Backend / Exporter は「名前 → 関数」の dict 登録のみ。クラス階層・ファクトリ・プラグイン機構は作らない。

```python
DIARIZERS = {"nemotron": diarize_nemotron, "pyannote": diarize_pyannote, "sherpa": diarize_sherpa}
ASRS = {"faster-whisper": transcribe_faster_whisper}
EXPORTERS = {"json": ..., "markdown": ..., "txt": ..., "srt": ...}
```

**内部 canonical schema** (Backend 出力は境界で検証):
- 時刻は秒 float、`0 <= start < end`
- diarization: `[{speaker_id: "SPEAKER_00", start, end}]` (speaker_id は Backend 側でなく共通処理で `SPEAKER_%02d` に正規化)
- transcript: `[{start, end, text, words: [{start, end, word}]}]` (`words` は空でも可)

## 3. 構成 (元仕様 30 ファイル → 9)

```
skills/scribe/tools/scribe/
├── pyproject.toml          # extras: pyannote
├── src/scribe/
│   ├── cli.py              # argparse, JSON 出力, exit code
│   ├── job.py              # job dir, 状態導出, 原子的書き込み, ロック
│   ├── pipeline.py         # ステージ実行, キャッシュ判定, フォールバック, 子プロセス
│   ├── inputs.py           # 入力: 拡張子 → 16kHz mono WAV (PyAV, ffmpeg は予備)
│   ├── audio.py            # wav 読み書き, 代表音声選択
│   ├── backends.py         # diarize_* / transcribe_* と登録 dict, 子プロセス entry
│   ├── merge.py            # timeline merge
│   ├── progress.py         # 進捗バー / heartbeat (stderr)
│   └── export.py           # json/markdown/txt/srt
└── tests/test_core.py
```

依存: `faster-whisper`, `sherpa-onnx`, `numpy`。`typer` / `pydantic` / `soundfile` は不使用 (argparse / 境界検証 / 標準 `wave`)。

## 4. データ保存

`SCRIBE_HOME` (既定 `~/.scribe`):

```
jobs/<job_id>/
  job.json            # {job_id, input, created_at, duration, options, stages:{...}, exports:{fmt:{path,at}}}
  .lock
  audio.wav           # 16kHz mono PCM16
  diarization.json
  transcript.json
  merged.json         # [{speaker_id|null, start, end, text}]
  samples/SPEAKER_00.wav
  speakers.json       # {"SPEAKER_00": "田中"}
```
(モデルは置かない → §1.5)

## 5. 状態

`status` は保存せず **ステージ状態と speakers.json から導出** する (不整合が起きない)。値は元仕様のまま:

`created → audio_extracted → diarized → transcribed → merged → speaker_identification_required → ready → exported` / 失敗時 `failed`。

- 全話者に名前 → `ready`。`export` 後 → `exported` (= transcript / 話者名が最後に変わって以降、少なくとも 1 形式の export に成功)。名前を変えると書き出し済みファイルは自動で書き直され `exported` のまま。書き直せなかったもの・merge 再計算時は `exports` から外れ `ready` に戻る。
- 未命名でも `export` は可能 (SPEAKER_xx 表記)。

## 6. Timeline Merge

- 単語 (日本語は文字/トークン) タイムスタンプがあれば単語単位で重なり最大の話者を割り当て、連続する同一話者の単語を結合。無ければセグメント単位。
- 重なりゼロ: 中心から 1.0 秒以内の最も近い話者区間、それ以上離れていれば `speaker_id: null` (誤帰属より未確定)。

## 7. Speaker Sample Selection

同一話者の 1.0 秒以内の切れ目はつなぎ、他話者と重なる部分を除いた区間を 10 秒以下に切り、5 秒以上の候補をスコア化:
`score = 長さ × 発話率(RMS 閾値超フレーム比) × min(1, RMS/目標)`。5 秒以上が無ければ最長区間で妥協。
ノイズ推定は V1 では行わない。

## 8. CLI

```
scribe process <input> [--job ID] [--asr LIST] [--diarizer LIST] [--num-speakers N]
                       [--model small] [--device auto|cpu|cuda] [--stage-timeout SEC] [--json]
scribe process --job ID            # 再開
scribe jobs [--json]               # ジョブ一覧 (新しい順)
scribe status <job> [--json]
scribe speakers <job> [--json]
scribe speaker set --job <job> --speaker SPEAKER_00 --name 田中 [--json]
scribe speaker rename --job <job> SPEAKER_00=田中 SPEAKER_01=佐藤 [--json]
scribe speaker edit --job <job>    # 対話 (代表音声を再生しながら名前入力)
scribe export <job> --format json|markdown|txt|srt|vtt(webvtt) [-o FILE]
```

- stdout: 結果のみ / stderr: ログ (子プロセスの stdout も stderr へ)
- exit: 0 成功, 1 一般 (job_busy 含む), 2 引数, 3 処理失敗, 4 ユーザー入力要 (話者命名待ち)

## 9. ChatGPT レビューで見送ったもの

| 提案 | 見送り理由 / 追加タイミング |
|---|---|
| overlap フラグ / candidate_speakers | 現用途で消費者がいない。重複発話の表示要件が出たら merge に追加 |
| processing_status と speaker_status の分離 | 元仕様の単一 status を Agent 契約として維持。`stages` で詳細は取れる |
| embedding による話者 ID の再対応付け | V1 は名前破棄 + 再確認で十分。声紋 DB (将来拡張) と同時に |
| 子プロセス失敗の 3 分類 (unavailable / failed / resource) | 正規化済み WAV を入力とするので Backend 段の失敗はほぼ Backend 起因。ディスク不足等で次 Backend を無駄に試しても最終的に exit 3 で止まり、害は時間のみ |

## 10. レビュー履歴 (ChatGPT)

| 回 | 主な採用 | 主な見送り |
|---|---|---|
| 1 (v1→v2) | key によるキャッシュ妥当性、ステージ状態、ジョブロック、子プロセス timeout、merge の max_gap、話者名の破棄、境界での schema 検証 | overlap フラグ、status 分離 |
| 2 (v2→v3) | Backend/モデル version を key に、samples の同時破棄、exports を形式別に、exported の定義 | 失敗の 3 分類 |
| 3 (v3→v4, 仕様追加) | HF キャッシュ一本化、revision 固定、`model_not_cached`、パス決め打ちしない、uv 記述の訂正 | — |
