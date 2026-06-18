# Project Overview
- Game Title: RelaxRoomAI
- High-Level Concept: An interactive AI companion relaxation room game featuring "Hayaseyuuka" (a 3D VRM 1.0 character) with automated motion play, audio triggers, phone attachment, touch interactions, and an immersive, multi-layered homepage and chat interface.
- Players: Single player
- Inspiration / Reference Games: Character-focused interaction and companion games (e.g., Galgame, virtual companion simulators).
- Tone / Art Direction: Stylized 3D Anime / Galgame
- Target Platform: Standalone Windows 64-bit
- Screen Orientation / Resolution: Landscape (1920x1080)
- Render Pipeline: Universal Render Pipeline (URP)

# Game Mechanics
## Core Gameplay Loop
Players interact with Hayaseyuuka in her apartment room. The experience is augmented by a realistic virtual smartphone interface overlay which serves as the "Homepage and Chat Interface". Through this UI, players can text her, record voice messages, view her automated moments feed (social circle), and configure AI connection settings, driving interactive motion and dialogue responses.

## Controls and Input Methods
- **Mouse Clicks / UI Interactions**: Clicking smartphone overlay buttons (Home, Moments, Settings, Chat).
- **Text Typing**: Typing text replies in the Input Field and sending via the Send button.
- **Voice Recording**: Recording audio messages using microphone input via the Voice button.
- **Swipe / Drag Transitions**: Scrolling chat history scroll rects and moments feeds.

# UI
The UI is a sophisticated mobile smartphone interface overlay built with Unity **uGUI**. It features a modern, clean design inspired by the Figma "Homepage and Chat Interface" template, utilizing smooth rounded corners, semi-transparent overlays, and custom icons.

### Hierarchy & Component Wireframe
- **AIgalgame_RelaxRoomSceneUI** (Root Canvas: 1080x810, CanvasScaler, GraphicRaycaster)
  - **HomeLayer** (Active: Primary phone screen displaying date, day, digital clock)
    - *HomeTime / HomeDate / HomeDay*: Dynamic clock text elements.
    - *MomentsButton*: Top-right button with notification badge (`MomentsDot`) to open social circle feed.
    - *SettingsButton*: Top-right gear button to toggle the settings menu.
    - *ChatPanel*: Positioned at the bottom, supporting two states:
      - *CollapsedChat* (Default, active): Shows brief prompt bar (`PromptToggle`), prompt icon, and simplified Input Row (`VoiceButton`, `ChatInput`, `SendButton`).
      - *ExpandedChat* (Inactive, expands on text input / click): Extends the panel upward, revealing the full chat container.
        - *ChatStateBar*: Header with companion display name and server connection indicators.
        - *HistoryViewport*: `ScrollRect` container holding 6 pre-instantiated message slots (`ChatSlot_00` to `ChatSlot_05`) that dynamically display Speaker name, Text bubble (`ui_rounded_rect` / `ui_pill`), and Avatar headshots.
        - *ChoiceRow*: Prompt options container with multiple choice buttons (`ChoiceBack`, `ChoicePrimary`, `ChoiceSecondary`).
  - **MomentsPanel** (Inactive, overlays screen on Moments click)
    - *MomentsTopBar*: Header with back button (`MomentsBack`) and Title.
    - *MomentsHero*: Features rain city background (`moment_rain_city`), avatar headshot, companion name, status badge, and custom profile details.
    - *MomentsViewport*: `ScrollRect` listing high-fidelity content cards:
      - *MomentCard (01)*: Image gallery card containing a room lamp photo and rain city photo, with mock likes and comments list.
      - *MomentCard (02)*: Image gallery card displaying three curry cooking photos, with mock likes and comments list.
      - *MomentVideoCard*: Video player teaser previewing a workout video clip.
  - **SettingsPanel** (Inactive, overlays screen on Settings click)
    - *SettingsCard*: Centered modal popup.
    - *Connection details*: IP field (`BackendUrlInput`) to edit the API host address, along with "Save" and "Health Check" status buttons.
    - *Voice / ASR Config*: Button to toggle voice auto-send and ASR details.
    - *Test Bench*: Inline debug triggers to test animations, facial expressions, and gaze logic directly.

# Key Asset & Context
- **UI Root GameObject**: `AIgalgame_RelaxRoomSceneUI` in `Scene_01.unity`.
- **UI Controller Script**: `Assets/_AIgalgame/Scripts/Animation/AIGalgameRelaxRoomUI.cs`.
  - Belongs to `AIgalgame.Motion` namespace.
  - Dynamically registers all button click listeners inside `BindSceneInterface()`.
  - Updates clock display (`RefreshClock`) and manages state transitions between Home, Moments, Settings, and Expanded Chat.
- **Custom Sprites** (located in `Assets/_AIgalgame/UI/RelaxRoom/`):
  - `avatar_user.png` / `avatar_xiaobai.png`: Chat and Hero headshots.
  - `moment_rain_city.png` / `moment_room_lamp.png` / `moment_curry_01/02/03.png` / `moment_video_thumb.png`: Feed mock contents.
  - `ui_circle.png` / `ui_pill.png` / `ui_rounded_rect.png` / `ui_soft_rect.png`: UI background shapes and border frames.

# Implementation Steps
Since the complete high-fidelity UI layout and C# Controller script are already beautifully implemented in the project and fully integrated in `Scene_01.unity`, the remaining steps focus on testing, verifying, and validating that all interactions match the Figma specification and execute flawlessly in play mode.

### Step 1: Verify Scene UI References and Wireups
- **Description**: Validate that all serialized fields in the `AIGalgameRelaxRoomUI` component on `AIgalgame_RelaxRoomSceneUI` in `Scene_01.unity` are properly bound to the existing hierarchy, and no sprite references are missing.
- **Assigned role**: developer
- **Dependencies**: None
- **Parallelizable**: No

### Step 2: Validate Play Mode Transitions and Interface Logic
- **Description**: Run the game in Play Mode, open the console to verify successful initialization (no runtime errors), and interact with each UI element:
  1. Confirm the clock matches the system time and updates second-by-second.
  2. Click the Moments Button. Verify the `HomeLayer` hides, `MomentsPanel` becomes visible, and we can scroll the Moments card list. Click "Back" to return.
  3. Click the Settings Button. Verify the `SettingsPanel` appears as a overlay, and closing it works.
  4. Click the Prompt Bar or click in the input text area. Verify the bottom panel smoothly expands into `ExpandedChat` state, displaying historical text message slots and interactive choice selection buttons.
- **Assigned role**: developer
- **Dependencies**: Step 1
- **Parallelizable**: No

### Step 3: Verify Dynamic Chat and Button Listener Bindings
- **Description**: Test entering a message in the chat input field and pressing "Send". Confirm that the input binds correctly, triggers log outputs, and updates the Chat History slot list. Verify that interactive "Choice" buttons correctly populate pre-selected reply text.
- **Assigned role**: developer
- **Dependencies**: Step 2
- **Parallelizable**: No

# Verification & Testing
1. **Compilation Check**: Verify the project compiles cleanly with no CS warnings or errors.
2. **Dynamic UI Binding Verification**: Check that all 20+ persistent button actions are registered at runtime with no unresolved listeners.
3. **Playmode Functional Verification**: Walkthrough of Home -> Moments -> Settings -> Expanded Chat -> Text input to confirm zero layout breakage, correct pixel-perfect layering, and successful interface workflow execution.
