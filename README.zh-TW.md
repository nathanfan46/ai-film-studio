# ai-film

其他語言版本：[English](README.md)

`ai-film` 是一套獨立、不綁定特定 AI 供應商的 CLI 工具與引擎，用於 AI 電影製作流程。
它會建立專案目錄，透過具備成本審核（cost-gated approval）機制的 JSON shot store
追蹤每個鏡頭（shot）的生成生命週期（圖像、影片、語音、音效、音樂），並使用 ffmpeg
將完成的鏡頭渲染成最終影片。

`/create-film`（Claude Code 的斜線指令）透過對話引導你完成故事、角色、場景與鏡頭的
建立，並自動幫你寫好 `shot.json`——詳見下方的 Roadmap。這個 CLI 就是該對話層底下
實際運作的生成引擎，若你偏好手動撰寫鏡頭，也可以直接單獨使用它。

## 1. 安裝

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

以下所有指令都假設 `.venv/bin` 已加入你的 `PATH`，或者你也可以直接呼叫
`.venv/bin/ai-film`。

## 2. 先用 mock provider 免費試用

不需要任何 API key——這能在你花錢之前，先驗證整條 pipeline 是否正常運作。

```bash
ai-film init "The Last Ship" --path ~/my-film
```

編輯 `~/my-film/config.json`，把 `providers` 底下每一個 `"provider": "fal"` 都改成
`"provider": "mock"`。

在 `~/my-film/03_shots/S01_SH01.json` 建立一個鏡頭：

```json
{
  "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 3,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "a lone engineer walks through a dim corridor",
  "visual": {"style": "cinematic sci-fi", "lighting": "blue emergency light"},
  "camera": {"shot": "wide", "movement": "slow_push_in"},
  "characters": [],
  "generation": {
    "image": {"status": "pending", "attempts": 0},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"},
    "sfx": {"status": "not_required"},
    "music": {"status": "not_required"}
  }
}
```

接著依序執行整個流程：

```bash
# 1. 依 schema 驗證這個鏡頭
ai-film validate --path ~/my-film

# 2. 記錄一次連戲檢查（角色/世界觀在各鏡頭間的一致性）
ai-film check-continuity --shot S01_SH01 --status passed --path ~/my-film

# 3. 核准這個鏡頭進入生成階段——沒有核准就不會生成任何東西（成本閘門）
ai-film approve-generation --scope storyboard --targets S01_SH01 --path ~/my-film

# 4. 生成分鏡圖與影片
ai-film generate-image --shot S01_SH01 --path ~/my-film
ai-film generate-video --shot S01_SH01 --path ~/my-film

# 5. 檢查整體狀態
ai-film status --path ~/my-film

# 6. 渲染最終影片（需要安裝 ffmpeg：`brew install ffmpeg`）
ai-film render --path ~/my-film
```

備註：`generate-image`／`generate-video`（如上方直接生成的指令）*的確*會檢查這裡
記錄的核准狀態，但比對的是單純的 shot id（`S01_SH01`）——下方第 3 節的候選圖迴圈
指令（`generate-candidates`／`edit-candidate`）比對規則更嚴格，是比對完整的 target
字串，所以 `approve-generation --targets` 必須和你之後要傳給 `generate-candidates`
的 `--target` 完全一致（一個單純的 shot id 如 `S01_SH01` 並不能授權
`shot:S01_SH01:image`——詳見第 3 節）。

`ai-film status --path ~/my-film` 應該會回報 `S01_SH01  completed`，而且
`~/my-film/final/reel_001.mp4` 應該已經存在且可以播放。

用同樣的方式新增更多鏡頭（`S01_SH02.json` 等等），然後一次生成某個階段的所有鏡頭，
並行數量受 `config.json` 的 `generation.max_parallel_jobs` 限制：

```bash
ai-film generate-all --stage image --path ~/my-film
ai-film generate-all --stage video --path ~/my-film
```

語音／音效／音樂需要 `--prompt`（因為不是每個鏡頭都有對白或音效提示）：

```bash
ai-film generate-voice --shot S01_SH01 --path ~/my-film
ai-film generate-sfx --shot S01_SH01 --prompt "distant metal groaning" --path ~/my-film
ai-film generate-music --shot S01_SH01 --prompt "tense ambient drone" --path ~/my-film
```

## 3. 生成前先審核與調整（候選圖迴圈）

與其直接生成一張最終圖片然後賭它是對的，你可以一次生成多個候選版本，在瀏覽器裡逐一
檢視、討論修改方向，再鎖定你要的那一張。這套流程同時適用於角色／場景參考圖，以及
鏡頭分鏡圖。

```bash
# 1. 核准這個 target 進入生成階段（bibles scope，適用於角色／場景）
ai-film approve-generation --scope bibles --targets character:girl --path ~/my-film

# 2. 一次生成 4 個候選版本（單次成本審核）
ai-film generate-candidates --target character:girl --count 4 \
  --prompt "a girl, cinematic sci-fi style, black jacket" --path ~/my-film

# 3. 在瀏覽器開啟所有候選圖的畫廊頁面
ai-film review --target character:girl --path ~/my-film

# 4. 不太滿意？用一段指示修改某個候選版本——
#    若該 provider 支援真正的圖片編輯就直接編輯，
#    否則會把這段指示合併進原始 prompt 後重新生成
ai-film edit-candidate --target character:girl --id 002 \
  --instruction "make the jacket shorter" --path ~/my-film

# 5. 再檢視一次——新的候選版本會顯示它的來源（"edit of 002"）
ai-film review --target character:girl --path ~/my-film

# 6. 鎖定你要的那一張。這會把檔案複製到 assets/characters/girl/reference.png
ai-film select-candidate --target character:girl --id 005 --path ~/my-film
```

改變主意了？只要用不同的 `--id` 再跑一次 `select-candidate` 就好——`selected`
永遠代表*目前*的選擇，不是永久鎖定。

同樣這四個指令也適用於鏡頭分鏡圖——把 `character:girl` 換成
`--target shot:S01_SH01:image`。如果你在鏡頭 target 上省略 `--prompt`，系統會自動
從該鏡頭的 `action`／`visual`／`camera` 欄位推導出提示詞（跟 `generate-image` 的
行為相同）；但對 `character:`／`env:` target 來說 `--prompt` 是必填的，因為沒有
對應的鏡頭可以推導。對鏡頭 target 執行 `select-candidate` 會直接寫入該鏡頭
`shot.json` 裡的 `generation.image.artifact`，跟 `generate-image` 的結果完全
一樣——後續流程（`status`、`render`）並不知道、也不在乎一張圖片是來自候選圖迴圈
還是直接呼叫 `generate-image`。

場景（environments）也是同樣的模式，使用 `--target env:<name>`。

影片／音訊的候選版本以及整部片的預覽功能目前還沒做——詳見下方 Roadmap。

## 4. 切換到正式生成

```bash
export FAL_KEY="your-fal-api-key"
```

把 `config.json` 的 provider 改回 `"fal"`。查看各項能力目前有哪些可用模型：

```bash
ai-film models --capability image
ai-film models --capability video
```

其餘所有指令（`validate`、`check-continuity`、`approve-generation`、`generate-*`、
`status`、`render`）行為完全相同——只有 config 裡的 `provider` 值不一樣。

## 指令參考

執行 `ai-film --help` 或 `ai-film <command> --help` 可查看完整指令列表與參數。完整
指令集：`init`、`models`、`status`、`validate`、`generate-image`、`generate-video`、
`generate-voice`、`generate-lipsync`、`generate-sfx`、`generate-music`、
`generate-all`、`check-continuity`、`approve-generation`、`render`、
`generate-candidates`、`review`、`select-candidate`、`edit-candidate`、
`add-feedback`、`resolve-feedback`、`apply-audio-offset`、`review-media`。

`generate-lipsync` 會對一個鏡頭已生成的影片與語音執行一次音訊驅動的對嘴（lip-sync）
處理，並用同步後的結果取代原本的影片 artifact（沿用跟其他任何影片重新生成一樣的
版本／歷史紀錄機制——`render` 以及下游所有流程都不需要任何改動就能自動採用同步後
的片段）。這個指令要求兩個階段都已經是 `completed` 狀態；它沒有 `--force` 參數，
因為設計上它每次執行就是會直接取代原本的結果。另外要注意：`generate-video` 在
生成有對白的鏡頭時，會自動把影片長度對齊該鏡頭語音實際測量出的長度（前提是語音
已經生成過），而不是使用鏡頭固定的 `duration_seconds`——如果你希望影片跟語音長度
一致，對白鏡頭要先生成語音再生成影片。

`generate-video --continue-from-previous` 會把前一個鏡頭影片的最後一幀（透過 ffmpeg
擷取）當作這個鏡頭影片的起始畫面，並搭配這個鏡頭自己鎖定的分鏡圖作為結尾畫面——
形成真正的雙關鍵影格（dual-keyframe）連戲效果，適合那些動作應該直接接續前一鏡、
而不是在剪接點重新開始的鏡頭。目前只有 `MODELS_WITH_END_IMAGE_URL` 裡列出的模型
（目前是 `h3-max`，已對照 fal.ai 官方 OpenAPI schema 驗證過）真的會採用結尾畫面；
其他模型仍然會把擷取到的最後一幀當作唯一的起始參考圖。這個功能會優雅降級——如果
沒有前一個鏡頭、前一個鏡頭還沒有影片，或這個鏡頭還沒鎖定分鏡圖，就會自動退回一般
的單張參考圖生成方式，並印出原因，不會出錯。

`shot.json` 可以宣告一個選填的 `format` 物件（例如
`{"resolution": "1280x720", "fps": 24}`）——用來明確覆寫這個鏡頭的專案輸出格式。
大多數鏡頭其實不需要這麼做：`config.json` 的 `render.resolution`／`render.fps`
（預設 `1280x720`／`24`）就是專案預設值，任何沒有自己 `format` 的鏡頭都會繼承
這個預設值，而 `render` 在最終渲染時也一律會把每個片段都正規化成這個值，不管
個別鏡頭原本設定的目標或某個 provider 實際回傳的結果是什麼。`render.strict_format`
（預設 `false`）開啟後，如果實際 artifact 的長寬比跟目標偏差超過 2%，生成就會
失敗——但解析度與影格率（fps）的差異在任一種模式下都不會導致生成失敗，因為這個
專案用到的任何 fal 模型都無法精確命中目標像素尺寸或影格率（已對照各模型自己的
schema 驗證過）；真正強制套用精確最終尺寸的是 `render` 這一步。

`generate-motion-transfer --shot <id> [--force]` 會把一段驅動參考影片的動作，重新
套用到這個鏡頭已鎖定的角色參考圖上（`kling-video/v2.6/motion-control`），藉此生成
鏡頭影片，而不是靠文字提示驅動的文字轉影片——適合用在文字描述無法穩定重現特定、
精確、需要精準計數的編舞動作時（fal.ai 自家的文字轉影片模型常常會把重複的細微
動作，塌縮成一種籠統、大概的近似效果）。這個指令會直接從鏡頭自己的 `shot.json`
欄位讀取兩個輸入，不需要任何 CLI 參數：`driving_video.path`
（`{"driving_video": {"path": "05_video/reference_clips/dance.mp4"}}`，跟其他所有
參考路徑一樣是相對於專案的路徑）以及 `characters[0].reference`（這個鏡頭第一位
角色鎖定的參考圖——motion-transfer 只支援單一驅動角色）。生成結果會寫進
`generate-video` 使用的同一個 `generation.video` artifact 欄位，並沿用相同的
idempotent／`--force` 語意——`render`、`generate-lipsync`、`mux-audio` 都會自動
採用，不需要額外改動。生成出來的片段一律是無聲的（`keep_original_sound` 會被
強制關閉）——之後請照一般方式，透過 `generate-lipsync`／`mux-audio` 補上對白、
音效、音樂。

`analyze-reference-video --source <path> [--force]`會分析本地端的參考影片——場景切點、關鍵影格，以及粗略的「這裡變化多少」訊號——完全透過本地 ffmpeg 運算，不花費任何 fal.ai 額度。會寫入
`assets/reference-video/video_analysis_brief.json`；`/analyze-reference` 指令會派遣一個
agent 讀取這份分析，用自己的視覺能力查看關鍵影格，填入每個場景的描述/主體/運鏡，標記出適合
`MOTION_TRANSFER` 的場景，並在這份分析被 `/create-film` 的 Director 與 Storyboard agent
當作參考依據使用之前，先取得你的核准。若分析結果已被核准，重新執行會被拒絕——想重新分析的話，
請先移動或改名既有的檔案。

**影片模型可以依鏡頭特徵個別設定**，透過 `config.json` 的
`providers.video.model_by_feature`，這樣你就不用為了某些需要不同模型能力的鏡頭，
反覆切換整個專案的預設模型：

```json
"video": {
  "model": "veo-3",
  "model_by_feature": {
    "dialogue": "hailuo-2.3",
    "continue_from_previous": "h3-max"
  }
}
```

目前支援的標籤：`"dialogue"` / `"silent"`（這個鏡頭是否有台詞——有些模型在沒有
台詞的鏡頭裡，還是會自己生出不受控的說話動作），以及 `"continue_from_previous"`
（這次呼叫有帶 `--continue-from-previous`——讓你只在真正需要雙關鍵影格生成能力
時，才指定支援的模型）。比對規則是最精確的標籤優先命中（`continue_from_previous`
會比 `dialogue`／`silent` 先被檢查）；如果鏡頭沒有對應到任何已設定的標籤，就會
退回使用 `providers.video.model`。如果你要新增標籤，可以參考 `src/ai_film/cli.py`
裡的 `_shot_features`／`_video_model`。

最後四個指令屬於媒體審閱層，用來檢視已生成的影片／音訊，並且不需要呼叫 provider
就能修正一些低成本的時間軸問題：

```bash
# 針對某個鏡頭的影片/語音/音效/音樂/對嘴同步，記錄一筆審閱意見，
# 可選擇指定單一時間點（--at）或範圍（--range-start/--range-end）
ai-film add-feedback --shot S01_SH01 --target video --note "too dark" --at 3.3 --path ~/my-film

# 將一筆審閱意見標記為已處理，可選擇附上處理方式的說明
ai-film resolve-feedback --shot S01_SH01 --id fb001 --resolution "regenerated" --path ~/my-film

# 透過 ffmpeg 微調某條音軌（語音/音效/音樂）的起始時間——不需要呼叫 provider
ai-film apply-audio-offset --shot S01_SH01 --track voice --offset-ms 400 --path ~/my-film

# 建立（或重建）某個鏡頭的影片/音訊審閱頁面，並在瀏覽器中開啟
ai-film review-media --shot S01_SH01 --path ~/my-film
```

## 專案目錄結構

`ai-film init` 會建立以下結構：

```
config.json                 # provider/model 選擇、核准狀態、生成相關設定，
                             #   以及 render.resolution/render.fps/render.strict_format（輸出格式）
assets/                     # 參考圖（角色、場景、道具、字型）
  characters/<name>/          # candidates.json + candidates/*.png + 鎖定後的 reference.png
  environments/<name>/        # 結構與 characters/ 相同
00_story/ 01_bibles/ 02_scenes/
03_shots/                    # SH*.json —— shot.json 的內容規格，目前為手動撰寫
04_storyboard/                # 生成的分鏡圖
  candidates/<shot_id>/candidates/  # 該鏡頭分鏡圖選定前的候選圖——注意這裡的 "candidates/" 出現兩次（target_dir 本身已經包含一層，候選圖生成時又會在上面再加一層子目錄）
05_video/                     # 生成的影片片段
06_audio/{dialogue,sfx,music}/
07_review/                    # 由 `review-media` 建立的靜態單鏡頭審閱頁（<shot_id>.html），以及生成的波形 PNG
final/                        # 渲染完成的 reel_001.mp4 會放在這裡
99_logs/                      # 每個鏡頭的生成嘗試記錄與核准紀錄
```

一旦某個鏡頭的 image/video/voice/sfx/music artifact 被重新生成過（`--force`）或
做過時間軸修正（`apply-audio-offset`）至少一次，對應階段的輸出目錄旁邊就會出現一個
`history/` 子目錄（例如 `05_video/history/`、`06_audio/dialogue/history/`），裡面
存放被取代掉的舊版本——舊的 artifact 檔案會被移過去，而不是刪除，而且 `shot.json`
的 `generation.<stage>.history` 會記錄每一次變更。

## 已知限制（v1）

- **`render` 只會包含已經內嵌在各鏡頭影片檔裡的音訊。** 獨立存放在 `06_audio/`
  的音效／音樂音軌，以及還沒跑過 `generate-lipsync` 的鏡頭語音，目前都不會混進
  最終成片。
- **對正在執行中的生成工作沒有崩潰保護。** 如果在生成過程中強制中止程式，系統會
  失去對那筆已付費工作的追蹤；重新執行時會重新送出並再付一次費。
- **provider 輪詢沒有退避（backoff）或逾時機制**——一個卡住的工作可能會讓
  `generate-*` 或 `generate-candidates`／`edit-candidate` 指令無限期卡住。
- **候選圖功能目前只支援圖片。** 影片／音訊的候選版本審閱，以及整部片的預覽功能
  都還沒做——詳見下方 Roadmap。
- **`generate-all --stage image` 不會串連同一場景內的連戲關係。** 因為每個鏡頭的
  參考圖清單是在任何鏡頭開始生成之前就先全部建好，所以 `generate-image`／
  `generate-candidates` 在依場景順序逐鏡頭執行時會套用的「同場景沿用前一鏡頭
  圖片」機制，在批次模式下並不會生效。如果你需要跨批次維持連戲關係，請依場景
  順序逐鏡頭生成。

以上都是這個專案自己在最終審閱階段就已經記錄下來的已知落差，不是你之後才會意外
發現的問題——完整的實作歷程請參考
`docs/superpowers/plans/2026-08-18-ai-film-studio-core-engine.md` 與
`docs/superpowers/plans/2026-08-22-human-interaction-model.md`。

## Roadmap

Claude Code 的 Agent 層已經完成：`/ai-film-setup` 負責設定 provider，
`/create-film "Title"` 會建立專案，並透過對話引導你走完整條流程——故事 -> 角色 ->
場景 -> 鏡頭 -> 審閱過的分鏡圖 -> 審閱過的影片，依序派遣 `ai-film-director`、
`ai-film-character`、`ai-film-environment`、`ai-film-storyboard`、`ai-film-media`
這幾個 subagent。場景（locations）跟角色一樣，會採用鎖定參考圖的處理方式——
Environment agent 會針對 Director 所寫場景中出現的每個獨特地點各執行一次，而且是
在 Storyboard 撰寫任何鏡頭之前執行；之後每個鏡頭的生成，除了原本就會依賴角色之外，
也同樣會依賴該場景已鎖定的地點。Media agent 會先生成每個鏡頭的影片，如果該鏡頭有
對白就先生成語音——並依語音實際測量出的長度來決定影片長度，而不是用固定猜測值，
接著執行一次對嘴處理（`generate-lipsync`），讓影片裡的嘴型是真的由音訊驅動，而不是
巧合對上。它會開啟靜態審閱頁面並套用修正——可能是低成本的音訊時間微調、針對性的
`shot.json` 欄位修改加上重新生成（會視需要重新觸發 voice/video/lipsync 整條鏈路），
或是提出釐清問題——直到你確認這個鏡頭為止；音效／音樂只有在你明確要求時才會生成。
詳見 `docs/superpowers/specs/2026-08-23-agent-layer-design.md`、
`docs/superpowers/plans/2026-08-23-agent-layer.md`、
`docs/superpowers/specs/2026-08-28-media-agent-design.md`、
`docs/superpowers/plans/2026-08-28-media-agent.md`、
`docs/superpowers/specs/2026-08-30-environment-locking-design.md`，以及
`docs/superpowers/plans/2026-08-30-environment-locking.md`，裡面有完整的設計與
實作歷程。

**使用方式：** `/ai-film-setup`、`/create-film` 與 `/analyze-reference` 這三個
指令，以及它們用到的六個 agent，都放在這個 repo 自己的 `.claude/commands/` 與
`.claude/agents/` 底下——Claude Code 只有在你從一個 `.claude/` 目錄底下包含這些
檔案的資料夾執行 `claude` 時，才會偵測到這些專案層級的指令／agent。請從這個
repo 的 checkout 目錄執行 `claude` 來使用它們（實際的電影專案不需要放在這裡——
`/create-film "Title" [path]` 可以接受目的地路徑作為參數，預設會建立在你執行
`claude` 當下所在目錄底下的 `./<slugified-title>`）。如果你想從其他工作目錄，
或是一個獨立的、只放電影專案的 repo 使用這些指令，可以把
`.claude/commands/ai-film-setup.md`、`.claude/commands/create-film.md`、
`.claude/commands/analyze-reference.md`，以及 `.claude/agents/` 底下的六個檔案，
複製或建立符號連結到該目錄自己的 `.claude/`（或是放到 `~/.claude/commands/` 與
`~/.claude/agents/`，讓它們在任何地方都能使用）。

依照這些設計文件裡的 Future Extensions：如果鏡頭數量多到需要獨立一個 context 才
處理得完，可能會加入專屬的 Continuity agent；針對已完成的電影，提供一個不必重跑
整個 `/create-film` 就能單獨進行媒體審閱的獨立入口；跟 Media agent 現有的「依序
生成 -> 審閱 -> 修正」流程不同的、多分支（N-way）影片候選探索；不需要明確要求、
系統自動判斷是否該建議音效／音樂；一個負責最終剪輯組裝的 Editor agent；以及在
`ai_film` 裡實作一套可程式化的成本估算引擎，取代目前 agent 用的靜態每模型價格
知識表。
