# Scribe 技術仕様書

## 1. 概要

**Scribe** は、会議の動画・音声データから以下を行うためのCLIツールである。

- 音声抽出・正規化
- 話者分離
- 日本語文字起こし
- 発話と話者の対応付け
- 話者確認用の代表音声生成
- 話者名の反映
- 議事録・Transcriptの出力

最終的には、AI Skill / Agent からScribeを呼び出し、ユーザーとの対話はAI側が担当する。

---

## 2. 想定環境

```text
処理対象:
  約30分の会議動画・音声

実行環境:
  CPU中心
  RAM 8GB程度
  GPU必須ではない

言語:
  日本語中心

開発:
  Python
  uv

提供形態:
  CLI Tool
```

---

## 3. 基本アーキテクチャ

```text
User
  │
  ▼
AI Skill / Agent
  │
  │ CLI実行 / JSON
  ▼
Scribe CLI
  │
  ▼
Processing Pipeline
  │
  ├── Audio Processing
  ├── Speaker Diarization
  ├── Transcription
  ├── Timeline Merge
  └── Speaker Sample Selection
  │
  ▼
Artifacts / JSON
```

### 責務

#### AI Skill / Agent

- ユーザーとの自然言語対話
- Scribe CLIの呼び出し
- 話者確認
- ユーザー回答のScribeへの反映
- 最終成果物の提示

#### Scribe

- 音声解析
- モデル実行
- 状態管理
- 中間データ生成
- JSONによる機械可読な結果返却

Scribe自身は原則としてユーザーと直接対話しない。

---

## 4. 処理フロー

```text
Video / Audio
      │
      ▼
FFmpeg
      │
      ▼
Audio Normalization
16kHz / mono / WAV
      │
      ├─────────────────┐
      │                 │
      ▼                 ▼
Speaker              Speech
Diarization          Recognition
      │                 │
      ▼                 ▼
Speaker Segments     Transcript Segments
      │                 │
      └────────┬────────┘
               ▼
        Timeline Merge
               │
               ▼
      Speaker Sample
        Selection
               │
               ▼
         Scribe JSON
               │
               ▼
            AI Skill
               │
       「この声は誰？」
               │
               ▼
             User
               │
        「田中さん」
               │
               ▼
      Speaker Mapping
               │
               ▼
        Final Transcript
```

---

## 5. Audio Processing

入力された動画・音声はFFmpegで統一形式へ変換する。

```text
Input
  mp4
  mov
  m4a
  mp3
  wav
  etc.

↓

PCM WAV
16kHz
mono
```

Scribe内部では、基本的に正規化済みWAVを各モデルへ渡す。

---

## 6. Speaker Diarization

目的は「誰が、いつ話したか」を取得すること。この段階では人物名を判定しない。

```json
{
  "speaker_id": "SPEAKER_00",
  "start": 12.4,
  "end": 18.8
}
```

### Backend候補

- NVIDIA Nemotron 3 Diarization
- pyannote.audio Community-1

Backendは交換可能にする。

```python
class DiarizationBackend:
    def diarize(self, audio_path):
        ...
```

---

## 7. Speech Recognition

会議音声を文字列へ変換する。

### Backend候補

- NVIDIA Nemotron 3.5 ASR
- faster-whisper small / CPU / INT8
- whisper.cpp small / Q5 / Q8

CPU実行時は、NVIDIA系についてNeMo-Speech.cpp系の軽量Runtimeも候補とする。

```python
class TranscriptionBackend:
    def transcribe(self, audio_path):
        ...
```

---

## 8. メモリ管理

RAM 8GB程度の環境を想定するため、モデルを可能な限り同時ロードしない。

```text
Diarization Model
      │
      ▼
Diarization
      │
      ▼
JSON保存
      │
      ▼
Model Release
      │
      ▼
ASR Model
      │
      ▼
Transcription
      │
      ▼
JSON保存
      │
      ▼
Model Release
```

処理結果をステージ単位で保存し、メモリ消費抑制・途中再開・Backend交換・デバッグを容易にする。

---

## 9. Timeline Merge

Diarization結果とASR結果を時刻情報で統合する。

### Diarization

```json
{
  "speaker_id": "SPEAKER_00",
  "start": 120.0,
  "end": 128.2
}
```

### ASR

```json
{
  "start": 120.4,
  "end": 127.8,
  "text": "それでは今日の会議を始めます。"
}
```

### Merge

```json
{
  "speaker_id": "SPEAKER_00",
  "start": 120.4,
  "end": 127.8,
  "text": "それでは今日の会議を始めます。"
}
```

基本的には時間区間の重なりが最も大きいSpeakerを割り当てる。

---

## 10. Speaker Sample Selection

各Speakerについて、ユーザーが人物を判断するための代表音声を生成する。

```text
SPEAKER_00
↓
発話区間を評価
↓
最も聞き取りやすい部分
↓
5〜10秒程度を抽出
```

優先条件:

- 他話者との重複が少ない
- 発話時間が十分長い
- 無音が少ない
- 音量が十分
- ノイズが比較的少ない

出力例:

```text
speaker_00_sample.wav
speaker_01_sample.wav
speaker_02_sample.wav
```

---

## 11. Speaker Identification

V1では声紋による完全自動識別は行わない。Scribeは話者を `SPEAKER_00` / `SPEAKER_01` / `SPEAKER_02` まで分離する。

その後AI Skillが代表音声をユーザーへ提示し、名前を確認する。

```json
{
  "SPEAKER_00": "田中"
}
```

---

## 12. AI連携用ステータス

話者名入力が必要になった場合、ScribeはJSONで状態を返す。

```json
{
  "status": "speaker_identification_required",
  "job_id": "abc123",
  "speakers": [
    {
      "speaker_id": "SPEAKER_00",
      "sample_audio": "speaker_00_sample.wav"
    },
    {
      "speaker_id": "SPEAKER_01",
      "sample_audio": "speaker_01_sample.wav"
    }
  ]
}
```

AIはこの状態を見て、必要な質問だけユーザーへ行う。

---

## 13. CLI設計

### 基本

```bash
scribe --help
```

```text
Scribe

Meeting transcription and speaker diarization CLI.

Commands:
  process     Process audio or video
  status      Show job status
  speakers    Show detected speakers
  speaker     Manage speaker information
  export      Export transcript
```

### Process

```bash
scribe process meeting.mp4
scribe process meeting.mp4 --json
scribe process meeting.mp4 --asr faster-whisper --diarizer pyannote
```

### Status

```bash
scribe status abc123
scribe status abc123 --json
```

### Speakers

```bash
scribe speakers abc123
```

### Speaker Mapping

```bash
scribe speaker set --job abc123 --speaker SPEAKER_00 --name "田中"
```

### Export

```bash
scribe export abc123 --format markdown
```

対応形式:

```text
json
markdown
txt
srt
```

---

## 14. CLIの設計原則

AI Skillから扱いやすくするため、以下を守る。

### stdout

正常な結果のみ出力。

### stderr

ログ・警告・エラーを出力。

### JSON Mode

`--json` を全主要コマンドで利用可能にする。

### Exit Code

```text
0   Success
1   General Error
2   Invalid Argument
3   Processing Failed
4   User Input Required
```

AIはExit CodeとJSONを見て次の行動を決定できるようにする。

---

## 15. Processing State

Jobは状態を持つ。

```text
created
   ↓
audio_extracted
   ↓
diarized
   ↓
transcribed
   ↓
merged
   ↓
speaker_identification_required
   ↓
ready
   ↓
exported
```

例:

```json
{
  "job_id": "abc123",
  "status": "speaker_identification_required"
}
```

---

## 16. ディレクトリ構成

```text
scribe/
├── pyproject.toml
├── uv.lock
├── src/
│   └── scribe/
│       ├── cli/
│       │   ├── app.py
│       │   └── commands/
│       ├── processing/
│       │   ├── pipeline.py
│       │   ├── audio.py
│       │   ├── timeline.py
│       │   └── speaker_samples.py
│       ├── backends/
│       │   ├── diarization/
│       │   │   ├── base.py
│       │   │   ├── pyannote.py
│       │   │   └── nemotron.py
│       │   └── transcription/
│       │       ├── base.py
│       │       ├── faster_whisper.py
│       │       ├── whisper_cpp.py
│       │       └── nemotron.py
│       ├── models/
│       │   ├── job.py
│       │   ├── speaker.py
│       │   ├── segment.py
│       │   └── transcript.py
│       ├── exporters/
│       │   ├── json_exporter.py
│       │   ├── markdown_exporter.py
│       │   ├── txt_exporter.py
│       │   └── srt_exporter.py
│       └── storage/
│           └── job_store.py
├── tests/
└── data/
    ├── jobs/
    └── models/
```

---

## 17. Processing Layer

`processing/` はScribe内部のオーケストレーションを担当する。

```text
CLI
 ↓
Processing Pipeline
 ↓
Backend
```

`pipeline.py` が以下を管理する。

```text
extract_audio
↓
diarize
↓
release model
↓
transcribe
↓
release model
↓
merge
↓
select speaker samples
```

モデル固有処理は`processing`へ書かず、`backends`側へ隔離する。

---

## 18. Backend Layer

モデルを差し替え可能にする。

```text
DiarizationBackend
       │
       ├── PyannoteBackend
       └── NemotronBackend
```

```text
TranscriptionBackend
       │
       ├── FasterWhisperBackend
       ├── WhisperCppBackend
       └── NemotronBackend
```

CLIとProcessingは具体的なモデルを意識しない。

---

## 19. Python / uv

初期化:

```bash
uv init scribe
cd scribe
```

基本ライブラリ:

```bash
uv add typer
uv add pydantic
uv add numpy
uv add soundfile
```

Backendに応じて追加する。

```bash
uv add faster-whisper
uv add pyannote.audio
```

CLI entry point:

```toml
[project.scripts]
scribe = "scribe.cli.app:app"
```

これにより、`uv run scribe --help` またはインストール後 `scribe --help` で利用できる。

---

## 20. 出力データ

最終的な内部データは、モデル依存ではない共通形式へ統一する。

```json
{
  "job_id": "abc123",
  "duration": 1802.4,
  "speakers": [
    {
      "id": "SPEAKER_00",
      "name": "田中"
    }
  ],
  "segments": [
    {
      "speaker_id": "SPEAKER_00",
      "speaker_name": "田中",
      "start": 120.4,
      "end": 127.8,
      "text": "それでは今日の会議を始めます。"
    }
  ]
}
```

---

## 21. Markdown出力例

```markdown
# Meeting Transcript

## 00:02:00 — 田中

それでは今日の会議を始めます。

## 00:02:08 — 佐藤

よろしくお願いします。
```

---

## 22. MVP

最初のバージョンでは以下まで実装する。

```text
Video / Audio Input
       ↓
FFmpeg
       ↓
Speaker Diarization
       ↓
ASR
       ↓
Timeline Merge
       ↓
Speaker Samples
       ↓
AI/Userによる名前付け
       ↓
Transcript Export
```

### MVP Backend

第一候補:

```text
Diarization:
pyannote Community-1

ASR:
faster-whisper small INT8
```

NVIDIA系BackendはBackend APIを保ったまま追加・比較する。

---

## 23. V1で実装しないもの

以下は初期実装には含めない。

```text
完全自動の人物名判定
長期間の声紋DB
議事録要約
Action Item抽出
RAG
MCP Server
GUI
```

CLI基盤を安定させた後に追加する。

---

## 24. 将来拡張

### Speaker Recognition

ユーザーが `SPEAKER_00 = 田中` と登録したときに、代表音声からSpeaker Embeddingを保存する。

将来的には、過去EmbeddingとのSimilarity計算から候補を出し、AIがユーザーへ確認するHuman-in-the-loop型の識別へ発展できる。

### AI Meeting Assistant

ScribeのTranscriptを使って、要約・決定事項・Action Items・質問応答・RAGへ拡張する。

### MCP / Skill対応

```text
User
 ↓
AI Agent
 ↓
Skill / MCP
 ↓
Scribe CLI
 ↓
Audio Models
```

ScribeはAI専用ツールではなく、CLI単体でも利用可能な汎用音声処理ツールとして設計する。

---

## 25. Scribeの設計思想

### 1. Model Agnostic

特定の音声モデルに依存しない。

### 2. CLI First

人間からもAIからも同じCLIを利用できる。

### 3. Human in the Loop

AIが曖昧な人物識別を勝手に確定せず、AIが候補を作り、人間が確認し、Scribeへ反映する設計を基本とする。

---

## 26. 最終構成

```text
                ┌───────────────┐
                │     User      │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │   AI Skill    │
                │    / Agent    │
                └───────┬───────┘
                        │
                     CLI / JSON
                        │
                        ▼
                ┌───────────────┐
                │    Scribe     │
                │      CLI      │
                └───────┬───────┘
                        │
                        ▼
               ┌────────────────┐
               │   Processing   │
               │    Pipeline    │
               └───────┬────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
 ┌─────────────────┐      ┌─────────────────┐
 │   Diarization   │      │       ASR       │
 │     Backend     │      │     Backend     │
 └─────────────────┘      └─────────────────┘
          │                         │
          └────────────┬────────────┘
                       ▼
               ┌────────────────┐
               │ Timeline Merge │
               └───────┬────────┘
                       ▼
               ┌────────────────┐
               │ Speaker Sample │
               └───────┬────────┘
                       ▼
                  JSON / Files
                       │
                       ▼
                    AI Skill
```
