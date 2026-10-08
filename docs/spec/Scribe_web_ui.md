# Scribe Web UI 設計 (v1)

前提: [Scribe_redesign.md](Scribe_redesign.md) の CLI をバックエンドとし、本書はその上に載せるローカル Web UI の差分と決定事項のみ記す。

## 0. 目的と範囲

| フェーズ | 内容 |
|---|---|
| **P1 タスクモニタ** | ジョブ一覧 (サイドパネル) + 選択したジョブのフロー図 + 全体進捗バー。処理中の状態をリアルタイムに表示 |
| **P2 話者エディタ** | 話者ごとのトラック (タイムライン) を表示し、トラックに名前を付ける。`Ctrl+S` で保存すると内部データと書き出し済みファイルがすべて新しい名前になる |

使う場所は「AI エージェントのアプリ内ブラウザ」(Claude デスクトップの Browser pane、ChatGPT 等のプレビュー) と通常のブラウザ。**1 台のローカル PC で 1 人が見る**前提。

対象外 (§9): UI からの処理開始、発言単位の話者付け替え、複数ユーザー、リモート公開。

## 1. 技術選定: Plain HTML + Vanilla JS (ビルドなし)

| 候補 | 評価 |
|---|---|
| **Plain HTML + Vanilla JS (ES modules)** | **採用**。実行環境は uv のみで Node を要求しない。画面は 3 つ、状態はサーバー側のファイルが正。必要な部品 (`EventSource`, `fetch`, `<audio>`, `<progress>`, `<canvas>`, SVG, CSS アニメーション) はすべてブラウザ標準 |
| Preact + htm (ベンダリング, ビルドなし) | 保留。**P2 で undo 履歴や発言単位の付け替えを入れて状態管理が辛くなったら**移行する。単一 JS ファイルを `web/vendor/` に置くだけなのでビルドは増えない |
| React + Vite | 不採用。ビルド工程 (Node) が入り、`dist/` をコミットするかインストール時にビルドするかの二択になる。この規模に見合わない |

- CDN は使わない。オフラインでも動き、ファイルはすべて Python パッケージ内 (`src/scribe/web/`) に同梱。
- サーバーは Python 標準の `http.server.ThreadingHTTPServer`。**新しい Python 依存は追加しない**。

## 2. 全体構成

```
エージェント ──(CLI)──► scribe process ──► SCRIBE_HOME/jobs/<id>/
                          └ 子プロセス (backend)    job.json / progress.json / *.json / audio.wav
                                                        ▲ 読む (ポーリング)      ▲ 書く (_apply_names)
ブラウザ ◄──(HTTP + SSE)── scribe serve ─────────────────┘                        │
         ──(PUT 話者名)──►                                ────────────────────────┘
```

- **ファイルが唯一の正**。サーバーは `SCRIBE_HOME` を読むだけで、処理プロセスとは IPC しない。誰が・どこから `scribe process` を実行しても一覧に出る。
- サーバーが書くのは話者名だけで、CLI と**同じ関数** (`_apply_names`) を呼ぶ (§6)。ロジックを二重に持たない。

## 3. 進捗の構造化 (`progress.json`)

現状の進捗は tqdm で stderr に出るだけで、UI からは読めない。**同じ `bar()` / `heartbeat()` が、ジョブディレクトリにも状態を書く**ようにする。

```json
// jobs/<id>/progress.json
{"stage": "transcribe", "attempt": "faster-whisper/cuda", "desc": "faster-whisper (cuda)",
 "n": 812.4, "total": 2940.0, "unit": "s", "text": "では次の議題に",
 "stage_started_at": "...", "updated_at": "..."}
```

- `pipeline._stage` / `_run_chain` が実行前に環境変数 `SCRIBE_PROGRESS=<job_dir>/progress.json` と `stage` / `attempt` を設定する。backend 子プロセスは環境変数を継承するので、子の `bar()` もそのまま書ける。
- `progress.py` は tqdm の再描画のたびに、**最短 1 秒間隔**で `progress.json` を書く。`heartbeat()` は `total: null` (量が測れない) で毎秒書く。
- 書き込みは best-effort。`OSError` は握りつぶして次の tick で書き直す (進捗のために処理を落とさない)。
- `text` は faster-whisper の直近テキスト (既存の `set_postfix_str`)。

### 3.1 全体のパーセント

```
pct = (完了ステージの重みの合計 + 実行中ステージの重み × n/total) / 重みの合計
重み: audio 5 / diarize 35 / transcribe 55 / merge 2 / samples 3 (合計 100)
```

- `total` が無い (heartbeat 中) ステージは 0 として扱い、バーのそのステージ部分を「不定 (ストライプのアニメーション)」で表示する。
- 計算はサーバー側で 1 か所 (`server.progress_pct`)。フロントはバーの区切り幅にだけ同じ重みを使う。
- `ponytail:` 重みは固定値。ジョブが溜まったら `stages.*.secs` の実測比で置き換える。
- 話者名入力・書き出しは人の作業なので % に含めず、別バッジで出す (§4.1)。

### 3.2 実行中の判定

`J.status()` は実行中を `is_locked()` (ロックを一瞬取りにいく) で判定するため、サーバーが毎秒呼ぶと CLI 側の `speaker rename` 等が稀に `job_busy` になる。そこで:

- `running` のステージがあり、`progress.json` の `updated_at` が 30 秒以内 → **実行中** (ロックに触らない)
- それ以外 → 従来どおり `J.status()` (プロセスが死んでいれば `failed`)

## 4. 画面

```
┌──────────────┬──────────────────────────────────────────────┐
│ ジョブ        │ meeting_0930.mp4 [処理中·文字起こし]          [形式▾][DL] │
│              │ [Workflow ◌] [Preview ●]  ← タブ (処理中 / 命名待ちの印)  │
│ ◌ 処理中 62%  │ ██████████████▒▒▒▒▒░░░  62%                   │
│ ! 要命名      │ 文字起こし (faster-whisper/cuda) 812/2940秒 · 「では次の…」 │
│ ✓ 完了        │ ┌── フロー ──┐  ┌── 選択ノードの詳細 ──────┐  │
│ ✕ 失敗        │ │   入力     │  │ 話者分離                  │  │
│ ❚❚ 中断       │ │  音声抽出   │  │ 試した順: ✕ nemotron/cuda │  │
│              │ │分離  文字起こし│  │           ✓ sherpa/cpu    │  │
│              │ │代表音声  統合 │  │ 所要時間: 10.5秒          │  │
│              │ │  話者名入力  │  └───────────────────────────┘  │
│              │ │  書き出し    │  (幅が狭いと詳細はフローの下)     │
└──────────────┴──────────────────────────────────────────────┘
```

### 4.1 サイドパネル (ジョブ一覧)

チャットアプリの会話一覧と同じ並び (新しい順)。1 行 = 1 ジョブ: 入力ファイル名、作成日時、状態アイコン、実行中ならミニ進捗バー。

| 状態 (`status`) | アイコン | 備考 |
|---|---|---|
| 実行中 (§3.2) | くるくる (CSS 回転) | 現在のステージ名を併記 |
| `speaker_identification_required` | ⚠ 要対応 | 人の作業待ち。目立たせる |
| `ready` / `exported` | ✓ | `exported` は書き出し済みバッジ |
| `failed` | ✕ | 行にエラーコードを表示 |
| `created` 〜 `merged` で停止中 | ⏸ | 中断。`scribe process --job ID` で再開できる旨を表示 |

### 4.2 ジョブ詳細 (フロー図)

ステージの依存は固定なので、**レイアウト済みの SVG** で描く (グラフライブラリ不要)。アプリ内ブラウザは幅が狭いので**上から下へ**流す:

```
          入力
          音声抽出
   話者分離      文字起こし
   代表音声      統合          (話者分離 → 代表音声 / 統合、文字起こし → 統合)
          話者名入力
          書き出し
```

- 実際の実行は直列 (audio → diarize → transcribe → merge → samples) だが、図は依存関係で描く。再実行時に「どこから下が無効になるか」がそのまま読める。
- ノード状態: 未着手 / 実行中 (くるくる + そのステージの進捗) / 完了 (所要秒・backend) / 失敗 (エラー) / 人の作業待ち。
- ノードをクリックすると詳細を開く: `stages.<name>.attempts` (フォールバックの試行履歴)、`backend`、`secs`、`error`、オプション。
- 上部に全体進捗バー (§3.1) と、faster-whisper 実行中は直近の認識テキスト。

### 4.3 リアルタイム更新

- **SSE** (`GET /api/events`)。一方向で足り、`EventSource` が自動再接続する。WebSocket は不要。
- サーバーは 1 秒ごとに `jobs/*/job.json` と `progress.json` の mtime を見て、変わったジョブだけ `event: job` (一覧 1 行分 + 詳細) を送る。
- 接続直後に全ジョブのスナップショットを 1 回送る。

## 5. 話者エディタ (P2)

ご要望の「トラックごとに分かれた表示」は、DAW や Audacity のような**話者ごとのレーン (マルチトラック・タイムライン)** と解釈した。

```
            0:00        5:00        10:00       15:00
波形        ▁▃▅▇▅▃▁▂▅▇▆▃▁▁▃▅▇▅▃▂▁▃▅▇▅▃▁▂▅▇▆▃▁▁▃▅▇
▶ [田中      ] 812s ██  ███     ██████   ██      ███
▶ [佐藤      ] 640s    ██  ████       ███  █████
▶ [SPEAKER_02] 31s          █                  █       ← 未命名は強調
              ▲ 再生位置
─────────────────────────────────────────────────────
00:02:00 田中   では始めます。                       ← 文字起こし (クリックで頭出し)
00:02:08 佐藤*  資料を共有します。                   * = 未保存の名前
```

- **レーン** = `diarization.json` の `speaker_id` ごと。発話時間の長い順。区間バーは SVG の rect。
- **レーン見出し**: 名前入力欄、代表音声の再生ボタン、発話秒数、代表区間の発話内容 (`sample_text`)。代表音声は `samples/<id>.wav` と同じ区間 (`sample_span`) をメインの `audio.wav` で再生する (配信ファイルを増やさない)。
- **波形**: サーバーが `audio.wav` から numpy で peaks (最大・最小の組) を計算して返す。49 分の音声でも 1 秒未満なのでキャッシュしない。
- **再生**: `<audio>` 1 個で `audio.wav` を再生 (Range 対応必須、§7)。区間バーや文字起こしの行をクリックするとその時刻へシーク。`Space` で再生/停止。
- **文字起こしペイン**: `merged.json` を時刻順に表示。名前の欄は未保存の編集内容も即座に反映 (未保存の印つき)。
- **話者の統合**: 2 つの ID に同じ名前を付ければ、書き出しでは 1 人として扱われる (既存仕様)。UI では同名のレーンを同じ色にする。
- ズーム: 全体表示 + スライダーによる横方向の拡大のみ。

### 5.1 保存 (`Ctrl+S`)

| 項目 | 決定 |
|---|---|
| キー | `Ctrl+S` / `Cmd+S` (`preventDefault` でブラウザの「ページを保存」を止める)。同じ処理の保存ボタンも置く |
| 単位 | 変更した名前を**まとめて 1 回の PUT** で送る。サーバーは 1 回のロックで `speakers.json` を書き、書き出し済みファイルをすべて書き直す (= `_apply_names`)。途中の状態は外から見えない |
| 結果表示 | `refreshed` を使って「保存しました・書き出し済み 2 件を更新」。`aria-live` で読み上げにも対応 |
| 処理中で書けない | `409 job_busy` → 未保存のまま残し「処理中のため保存できません」と表示 |
| 古い画面からの保存 | 開いた後に話者分離がやり直されると SPEAKER 番号の意味が変わる。読込時に受け取った `diarize` ステージの key ハッシュを `If-Match` で送り、一致しなければ `409 stale` → 再読込を促す (別人に名前を付ける事故を防ぐ) |
| 未保存で離脱 | `beforeunload` で警告 |
| 空欄 | 名前を消す (`null`) = CLI の `-` と同じ |

**「書き出したら必ず反映されている」の保証** は既存の仕組みでそのまま満たせる:

1. `export` は毎回 `merged.json` + `speakers.json` から描画するので、保存後の書き出しは必ず新しい名前になる。
2. 保存前に `-o` で書き出したファイルは、`_apply_names` が保存と同時に書き直す。書き直せなかったものは `exports` から外れ、状態は `ready` に戻る (再書き出しが必要だと分かる)。
3. UI からの書き出しは「ダウンロード」(`GET .../export?format=`) のみ。サーバー上の描画結果をそのまま返し、`exports` には記録しない (保存先をサーバーが知らないため、後から書き直せない)。

## 6. サーバー (`scribe serve`)

```
scribe serve [--host 127.0.0.1] [--port 8765]
```

- 起動時に stdout へ `{"url": "http://127.0.0.1:8765/"}` を 1 行出し、その後はブロックする (stdout は結果のみの原則どおり)。ポート使用中は exit 1 `port_in_use`。
- 実装は `src/scribe/server.py` 1 ファイル。`cli.py` の `_summary` / `_speakers` / `_apply_names` / `_render` を import して使う (先頭の `_` は外す)。

| メソッド・パス | 内容 |
|---|---|
| `GET /` , `/static/*` | `web/index.html`, `app.js`, `style.css` |
| `GET /api/jobs` | 一覧 (`jobs` 相当 + `pct` + 実行中フラグ) |
| `GET /api/jobs/{id}` | `status --json` 相当 + `progress` + `pct` |
| `GET /api/events` | SSE (§4.3) |
| `GET /api/jobs/{id}/speakers` | `speakers --json` 相当 + `diarize` key ハッシュ (ETag) |
| `GET /api/jobs/{id}/timeline` | `diarization.json` + `merged.json` |
| `GET /api/jobs/{id}/peaks?n=2000` | 波形 peaks |
| `GET /api/jobs/{id}/audio` | `audio.wav` (Range 対応) |
| `PUT /api/jobs/{id}/speakers` | `{"SPEAKER_00": "田中", ...}` → `_apply_names` |
| `GET /api/jobs/{id}/export?format=md` | ダウンロード (記録しない) |

エラーは CLI と同じ `{"error": code, "message": ...}`。`job_not_found` → 404、`job_busy` / `stale` → 409、引数不正 → 400。

## 7. 安全性・堅牢性

| 項目 | 対策 |
|---|---|
| 外部公開 | `127.0.0.1` にのみ bind。`--host` を変える場合は警告を出す |
| DNS リバインディング | `Host` ヘッダーが `127.0.0.1` / `localhost` 以外なら 403 |
| 他サイトからの書き換え (CSRF) | `PUT` は `Content-Type: application/json` 必須 + `Origin` が自分と一致しなければ 403 |
| パスの走査 | ジョブ ID は既存の `job_dir()` の検証を通す。話者 ID は `apply_names` が `speaker_ids()` と照合する。配信できるのは `web/` の 3 ファイルとジョブの `audio.wav` のみ |
| 音声のシーク | `http.server` は Range 非対応なので、`Range: bytes=a-b` → `206` を自前で実装する (十数行)。無いと `<audio>` でシークできない |
| **Windows の置換失敗** | Windows では、他プロセスが開いているファイルへの `os.replace` が `PermissionError` になる。サーバーが `job.json` を読んだ瞬間に CLI の `save()` が失敗し、**処理が落ちる恐れがある**。`job.write_json` で `PermissionError` のとき短い間隔で数回リトライする (CLI 側の修正、UI 導入の前提条件) |
| 処理と UI の独立 | サーバーが落ちても処理は続く。処理が落ちても UI は `failed` を表示する |

## 8. エージェントからの使い方 (SKILL.md に追記)

1. `scribe serve` をバックグラウンドで起動し、stdout の URL を読む。
2. その URL をアプリ内ブラウザで開く (Claude デスクトップなら Browser pane)。
3. `scribe process ...` を実行する。UI に自動で表示される。
4. `exit 4` (話者名入力待ち) のとき、ユーザーに「画面の話者エディタで名前を付けて Ctrl+S」と案内する。音声を聞けないエージェントは、従来どおり `sample_text` を手がかりに `speaker rename` でもよい。

## 8.5 文字起こし漏れのチェックと補完 (v1.1 追加)

**問題**: 49 秒のニュース動画で、インタビュー部分 (話者分離では SPEAKER_01 が 10 秒) に文字が 1 つも付かなかった。同じ区間だけを切り出すと正しく文字起こしできるので無音ではなく、Whisper が 30 秒窓の中でタイムスタンプを飛ばして発話をまるごとスキップしていた (VAD の有無に関係なく再現)。

| 項目 | 決定 |
|---|---|
| チェック | `merge.uncovered(話者分離, 文字起こし)`: 話者分離の発話区間のうち、どの文字起こし区間とも重ならない部分 (`MIN_GAP` = 1.5 秒以上) を返す |
| 補完 | 新ステージ `fill` (transcribe と merge の間)。抜けた区間を前後 0.3 秒広げ、faster-whisper の `clip_timestamps` で**その区間だけ**を文字起こしし直す (`fill.json`)。merge は transcript + fill を時刻順に結合 |
| キャッシュ | `fill` の key = 話者分離 key + 文字起こし key + 閾値。どちらかが変われば再実行。補完前に処理済みのジョブは読み込み時に `fill` を key なしの完了扱いにし (状態が後退しない)、次の `process` で補完される |
| 報告 | 補完後も残る抜けを `status` / `process` の `warnings` (`untranscribed_speech`: 秒数と区間) と、`speakers` の話者ごとの `transcribed_ratio` で返す |
| 画面 | Workflow: 警告バナーと「抜け補完」ノード (補完した区間数)。Preview: トラック上の赤い区間、話者見出しの「文字起こし n%」(80% 未満)、文字起こし欄に「文字起こしされていない発話」の行 (クリックで再生) |

`ponytail:` `MIN_GAP` は 1 本の動画で決めた値。短い相づちまで拾って誤検出 (BGM を声と判定した区間の幻聴) が増えるようなら上げる。

## 8.6 実行キュー・一時停止・優先 (v1.2 追加)

ジョブ一覧を右クリック (またはキーボードのメニューキー / Shift+F10) すると操作メニューが開く。Workflow のヘッダーにも同じボタンを置く。

| 項目 | 決定 |
|---|---|
| 実行のしかた | **1 台で同時に 1 ジョブ**。`SCRIBE_HOME/runner.lock` (OS ロック) を持つプロセスだけがステージを実行し、他は待つ (GPU/8GB 環境でジョブ同士がメモリを取り合わないため) |
| 順番 | `jobs/<id>/control.json` の `order` が小さい順。`process` 開始時に現在時刻 (= 先着順) |
| 待機中の見え方 | 待っているプロセスは毎秒 `progress.json` に `state: queued / paused` を書く (5 秒更新が無ければ死んだとみなす)。`status` の `queue` に `{state, position}` |
| 一時停止 | `control.json` の `paused: true`。実行中の Backend 子プロセスは 0.5 秒以内に kill し、その段階の途中経過は破棄 (再開時にその段階を最初から)。プロセスは終了せず、ランナーを手放して待つ。音声抽出など親プロセス内の短い段階は、終わってから止まる |
| 再開 | `paused: false`。元の順番に戻る |
| 最優先 | `order` を全ジョブの最小値 − 1 に。実行中のジョブは**今の段階が終わった時点**で譲る (途中経過を捨てない)。すぐ止めたいときは実行中のジョブを一時停止する |
| 対象 | プロセスが生きている (実行中・待機中・一時停止中) ジョブだけ。止まったジョブには「再開コマンドをコピー」(`scribe process --job ID`) |
| CLI | 新しく `process` を始めると `paused` は解除され、列の最後に並ぶ |

## 8.7 文字起こしの手修正 (v1.3 追加)

Preview の文字起こし欄で行をダブルクリック (またはフォーカスして Enter / F2) すると編集欄になる。Enter で確定 (日本語入力の変換確定中の Enter は無視)、Esc で取り消し、Shift+Enter で改行。確定した修正は未保存として数えられ、Ctrl+S で話者名と一緒に 1 回で保存する。

| 項目 | 決定 |
|---|---|
| 保存先 | `jobs/<id>/edits.json` = `{"<開始ms>-<終了ms>": "修正後の文"}`。`merged.json` (ASR の結果) は書き換えない |
| 適用 | `job.segments()` が merged に修正を重ねる。書き出し・`sample_text`・抜けチェックはすべてこれを読む |
| 行の削除 | 空にすると書き出しから除く。画面には取り消し線で残り、ダブルクリックで戻せる。元と同じ文に戻すと修正自体が消える |
| 保存処理 | `PUT /api/jobs/{id}/speakers` に `texts` を追加 (`names` と同じロック・同じ書き出し済みファイルの書き直し) |
| 再処理 | 話者分離・文字起こしをやり直すと行の時刻が変わり、修正は適用されなくなる。ファイルには残し、`status` の `warnings` に `text_edits_lost` (件数) を出す。開いたままの画面から古い行を保存しようとすると 409 `stale` |

## 8.8 ジョブの削除 (v1.4 追加)

v1 では誤操作を恐れて置かなかったが、ジョブが溜まるため右クリックメニューに「削除…」を追加した。

| 項目 | 決定 |
|---|---|
| 消えるもの | `SCRIBE_HOME/jobs/<id>` だけ (音声のコピー・文字起こし・話者名・手修正)。元に戻せない |
| 残るもの | 書き出したファイル、元の音声・動画 (どちらもジョブフォルダの外) |
| 確認 | ブラウザの確認ダイアログで、上の 2 点をジョブ名つきで示す |
| 拒否する場合 | `scribe process` が動いている (実行中・待機中・一時停止中) ジョブは 409 `job_busy` (メニューでも押せない)。Windows で中のファイルが開かれている (再生中など) 場合も 409 |
| 手順 | `jobs/<id>` を `SCRIBE_HOME/.deleted/` へ rename (原子的。開いているファイルがあれば失敗して何も消えない) してから中身を削除。削除し損ねた残骸は一覧に出ず、次の削除時に再試行 |
| API | `DELETE /api/jobs/{id}` (他サイトからの `Origin` は 403) |

## 9. 対象外 (追加するタイミング)

| 機能 | 理由 / 追加するとき |
|---|---|
| UI からファイルを選んで処理開始・止まったジョブの再開 | エージェントが CLI で起動する前提。人が単独で使う要望が出たら `POST /api/jobs` で `scribe process` を子プロセス起動 |
| 発言単位の話者付け替え (区間バーを別レーンへドラッグ)・文字の無い区間への文の追加 | 名前の対応付けでは直せない「1 つの ID に 2 人が混ざった」ケース用。`overrides.json` を merge で適用する形で別途設計する。入れるなら Preact へ移行 (§1) |
| 区間の境界編集・分割 | 同上 |
| 処理ログ全文の表示 | `attempts` と `error` で原因は分かる。不足したら pipeline が `jobs/<id>/scribe.log` にも出す |
| 認証・リモート公開 | ローカル 1 人前提 |

## 10. 変更ファイル

| ファイル | 変更 |
|---|---|
| `src/scribe/progress.py` | `SCRIBE_PROGRESS` があれば `progress.json` を書く (1 秒間隔, best-effort) |
| `src/scribe/pipeline.py` | ステージ・試行ごとに `SCRIBE_PROGRESS` / stage / attempt を設定 |
| `src/scribe/job.py` | `write_json` の Windows 向けリトライ |
| `src/scribe/cli.py` | `serve` サブコマンド。共有関数の `_` を外す |
| `src/scribe/server.py` | 新規 (HTTP + SSE + Range) |
| `src/scribe/web/{index.html,app.js,style.css}` | 新規。wheel に含まれることを `uv build` で確認する |
| `skills/scribe/SKILL.md` | §8 の手順 |
| `tests/test_server.py` | 新規 (下記) |

## 11. 検証

- **自動テスト** (`tests/test_server.py`, 一時 `SCRIBE_HOME` + スレッドでサーバー起動):
  - `progress_pct` が重みどおり (完了のみ / 実行中 50% / heartbeat 中)。
  - `PUT /speakers` で `speakers.json` と書き出し済みファイルの両方が新しい名前になる。`If-Match` 不一致で 409、`Origin` が違えば 403。
  - `audio` の Range 要求に 206 と正しいバイト範囲を返す。
- **注意 (検証時に判明)**: `PUT /speakers` は `job.json` の `exports` に記録された**すべての書き出し先**を書き直す。既存ジョブをコピーして試すときは、`exports` を空にしてから使うこと (コピー元の実ファイルを書き換えてしまう)。
- **手動**: 49 分の会議音声で `scribe process` を実行し、Browser pane で「くるくる → 各ノード完了 → 要対応」と進むこと、% が単調に増えること、名前を付けて `Ctrl+S` → `scribe export` の結果に反映されることを確認する。Windows で処理中に UI を開きっぱなしにして `PermissionError` が出ないことも確認する。
