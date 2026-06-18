# Project Overview
- Game Title: RelaxRoomAI
- High-Level Concept: An interactive AI companion relaxation room game featuring "Hayaseyuuka" (a 3D VRM 1.0 character). Rebuild the UI into a high-fidelity vertical mobile phone mockup overlaid on the 3D room, matching the GitHub/Figma web reference. **All display data is backend-driven — nothing is hardcoded.**
- Players: Single player
- Inspiration / Reference Games: Virtual companion and simulation games with active mobile interface elements.
- Tone / Art Direction: Stylized 3D Anime / Modern Mobile App UI
- Target Platform: Standalone Windows 64-bit (Landscape 1920x1080 viewport)
- Screen Orientation / Resolution: Landscape (1920x1080), with a centered Portrait smartphone mockup (9:16) on a 1080x1920 logical canvas.
- Render Pipeline: Universal Render Pipeline (URP)

# Core Architecture Principle: Backend-Driven Data
**No display values are hardcoded.** The UI fetches all content from the backend and only renders what it receives. On startup the UI requests bootstrap + moments data; until data arrives it shows neutral loading/empty states. The reference strings from the web repo (tips, quick replies, posts) are ONLY example payloads the backend will return — they live on the backend, not in Unity.

## API Contract (designed here; backend implements later)

### `GET /api/relaxroom/home?user_id=&character_id=&session_id=` → home bootstrap
```json
{
  "relationship_start_date": "2026-01-11",   // ISO date; Unity computes elapsed days locally
  "daily_tip": "今日提示：今天也要好好吃饭哦～",
  "quick_replies": ["一起去食堂吧", "今晚想吃什么", "最近有点累了", "陪我聊聊天吧", "帮我想个计划"],
  "companion_name": "小白",
  "user_name": "你",
  "chat_state_label": "夜雨空间 · 和小白聊天中"
}
```
- Day count rule: if `relationship_start_date` parses, `relationshipDay = max(1, (Now.Date - start.Date).Days + 1)`. Auto-increments daily without backend updates.

### `GET /api/relaxroom/moments?user_id=&character_id=` → friend-circle feed
```json
{
  "profile": { "name": "夜雨空间", "status_suffix": "今天也是好好地过", "update_note": "小白更新了 3 动态", "avatar_url": "" },
  "posts": [
    {
      "id": "p1", "username": "小白", "time": "10分钟前", "type": "image",
      "text": "今天做了咖喱饭～", "avatar_url": "http://.../avatar.png",
      "images": ["http://.../curry_01.png", "http://.../curry_02.png"],
      "video_thumbnail": "", "video_title": "", "video_duration": "",
      "likes": ["你", "小白"],
      "comments": [ { "user": "你", "text": "看起来好好吃" } ]
    }
  ]
}
```
- `type` ∈ `"text" | "image" | "video"`. Images are full network URLs downloaded via `UnityWebRequestTexture`.
- Profile banner status is composed at runtime: `"第" + relationshipDay + "天 · " + status_suffix`.

### Existing endpoints (keep as-is)
- `POST /api/events` — user message (text or recognized voice).
- `POST /api/relaxroom/idle` — idle actions.
- `GET /api/health` — health check.

## Voice / ASR (keep existing wiring)
Voice button continues to drive the existing local-ASR pipeline (`AIGalgameLocalAsrClient`): record mic → extract mono samples → local recognize (Android/Windows native) → on success, if auto-send is on, POST the recognized text to `/api/events`; otherwise place it in the input field. No change to ASR logic; only ensure the rebuilt voice button stays bound to `ToggleVoiceRecording`.

# UI Rebuilding Specifications
Rebuild the outdated horizontal-stretched canvas into a vertical **smartphone mockup** centered on screen.

### Layout Hierarchy Specs
- **Canvas Root**: `AIgalgame_RelaxRoomSceneUI` (ScaleWithScreenSize, reference 1080x1920, Match=0 already set).
- **PhoneContainer** (NEW): a centered vertical container:
  - Anchors: Min `(0.5, 0)`, Max `(0.5, 1)`; controls width via `AspectRatioFitter` (ratio `0.5625` = 9:16, mode `FitInParent`).
  - Holds `HomeLayer`, `MomentsPanel`, `SettingsPanel` (all stretch to fill the phone).
  - **HomeLayer** (active): clock/date, day-count text, MomentsButton (with badge), SettingsButton, and bottom `ChatPanel` half-drawer.
    - *CollapsedChat*: hint strip bound to backend `daily_tip` (empty until fetched), prompt toggle, input row (Voice / Input / Send).
    - *ExpandedChat*: chat history `ScrollRect` (slots), chat state bar (backend `chat_state_label`), and **QuickRepliesBar**.
    - *QuickRepliesBar* (NEW): horizontal `ScrollRect` (horizontal only, **no visible Scrollbar**, viewport masked) + `Content` with `HorizontalLayoutGroup` + `ContentSizeFitter`. Chips are instantiated at runtime from backend `quick_replies`; tapping a chip fills/sends that text.
  - **MomentsPanel** (inactive): top bar (back/title/compose), profile banner (backend `profile`), and a `ScrollRect` whose `Content` is populated at runtime from backend `posts` using a card template that supports text / image-grid / video layouts, plus likes row and comments list.
  - **SettingsPanel** (inactive): backend URL field, ASR/voice controls, debug test bench (unchanged).

# Key Asset & Context
- **Script**: `Assets/_AIgalgame/Scripts/Animation/AIGalgameRelaxRoomUI.cs` (namespace `AIgalgame.Motion`). Modify to: add home/moments data models + fetch coroutines, compute day from start date, populate quick replies dynamically, populate moments dynamically with async image download, keep ASR wiring. Remove hardcoded display defaults (use them only as inert fallbacks shown on fetch failure, not as authored content).
- **Sprites** under `Assets/_AIgalgame/UI/RelaxRoom/`: `ui_circle/ui_pill/ui_rounded_rect/ui_soft_rect` (shapes/frames), `avatar_*` and `moment_*` exist but moment images now come from backend URLs.

# Implementation Steps

### Step 1: Add backend data models + fetch layer to `AIGalgameRelaxRoomUI.cs`
- **Description**:
  1. Add `[Serializable]` classes: `HomeBootstrapResponse`, `MomentsResponse`, `MomentsProfile`, `MomentPost`, `MomentComment`.
  2. Add coroutines `FetchHomeBootstrap()` (GET `/api/relaxroom/home`) and `FetchMoments()` (GET `/api/relaxroom/moments`), parsed via `JsonUtility`, called from `Start()` after `BindSceneInterface()`.
  3. Add day computation from `relationship_start_date`.
  4. Add an async image loader coroutine `DownloadSprite(url, onComplete)` using `UnityWebRequestTexture` with a small in-memory cache.
  5. Make `daily_tip`, `quick_replies`, `companion_name`, `user_name`, `chat_state_label`, and moments content apply from fetched data. Keep current hardcoded strings only as private fallbacks used when a request fails.
- **Assigned role**: developer
- **Dependencies**: None
- **Parallelizable**: No

### Step 2: Restructure Canvas into centered portrait PhoneContainer
- **Description**:
  1. Create `PhoneContainer` under `AIgalgame_RelaxRoomSceneUI`, anchors Min `(0.5,0)` Max `(0.5,1)`, add `AspectRatioFitter` (ratio 0.5625, FitInParent).
  2. Reparent `HomeLayer`, `MomentsPanel`, `SettingsPanel` under it and set them to stretch-fill.
  3. Verify nothing clips and existing serialized references on the UI component remain valid after reparenting.
- **Assigned role**: developer
- **Dependencies**: None
- **Parallelizable**: Yes (with Step 1)

### Step 3: Build dynamic Quick Replies horizontal bar (no scrollbar)
- **Description**:
  1. Under `ExpandedChat`, replace fixed `ChoiceRow` (`ChoicePrimary`/`ChoiceSecondary`) with `QuickRepliesScroll`: `ScrollRect` (horizontal=true, vertical=false, no Scrollbar assigned), masked `Viewport`, `Content` with `HorizontalLayoutGroup` + `ContentSizeFitter` (horizontal=PreferredSize).
  2. Create an inactive chip template (pill `Button`+`Text`) used to clone chips at runtime.
  3. Add serialized fields for the scroll content + chip template; bind in component.
  4. Wire `AIGalgameRelaxRoomUI` to spawn one chip per backend `quick_replies` entry; tap → fill input and send.
- **Assigned role**: developer
- **Dependencies**: Step 1, Step 2
- **Parallelizable**: No

### Step 4: Build dynamic Moments feed from backend
- **Description**:
  1. Convert `MomentsContent` to a runtime-populated container; create card templates for text / image-grid / video types (reuse existing `MomentCard`/`MomentVideoCard` structure as templates, set inactive).
  2. On `FetchMoments` success, instantiate one card per `post`, fill username/time/text, build image grid (async download per URL), likes row, and comments list; set profile banner from `profile` + computed day.
  3. Bind new serialized references (moments content, card templates, profile texts) in the component.
- **Assigned role**: developer
- **Dependencies**: Step 1, Step 2
- **Parallelizable**: No

### Step 5: Verify voice/ASR wiring intact after rebuild
- **Description**: Confirm the rebuilt Voice button stays bound to `ToggleVoiceRecording` and the local-ASR path (record → recognize → auto-send/fill) is unaffected. Confirm `voiceButtonText` toggles 麦/停.
- **Assigned role**: developer
- **Dependencies**: Step 2, Step 3
- **Parallelizable**: No

### Step 6: Play-mode verification with a local mock backend
- **Description**: Since the real backend is not yet implemented, verify rendering by feeding the parser sample JSON (matching the contract) through the fetch handlers (e.g., a temporary local stub or directly invoking the apply methods with deserialized sample payloads). Confirm: portrait phone centered at 16:9; day count computed from start date; quick reply chips spawn and scroll horizontally with no visible scrollbar; moments cards populate (text/image/video) with async images; chat expand/collapse and Moments/Settings transitions work; no console errors. Never leave the editor in Play Mode.
- **Assigned role**: developer
- **Dependencies**: Step 3, Step 4, Step 5
- **Parallelizable**: No

# Verification & Testing
1. **No hardcoded content**: Grep confirms display strings/posts/quick-replies are not authored as final values in the scene; they populate from parsed payloads (fallbacks only on failure).
2. **Backend contract parse**: Sample JSON for both endpoints deserializes cleanly via `JsonUtility` and applies to the UI.
3. **Dynamic scaling**: At 16:9/16:10/21:9 the phone stays a centered 9:16 rect.
4. **Quick replies**: Spawned from array, horizontally scrollable, no visible scrollbar, tap sends/fills.
5. **Moments**: Cards built from posts; network images load async; likes/comments render.
6. **ASR intact**: Voice button still records and routes through local ASR.
7. **Compilation**: Project compiles with no errors.
