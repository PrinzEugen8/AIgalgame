# RelaxRoom Mobile

React Native shell for RelaxRoomAI. Unity renders the 3D character and scene; this app owns chat UI, moments, settings, and backend HTTP.

## Prerequisites

- Node.js 18+
- Android SDK + JDK 17
- Unity 6000.0.77f1 with Android Build Support
- Python backend running at `http://127.0.0.1:8899`

Android emulator uses `http://10.0.2.2:8899` by default.

## First-time setup

```powershell
cd E:\AIgalgame\relaxroom-mobile
npm install --no-package-lock --legacy-peer-deps
```

## Export Unity Android library

1. Open `E:\AIgalgame\RelaxRoomAI` in Unity
2. Run `Tools > AIgalgame > RN > Export Android Library`
3. Confirm output exists at `relaxroom-mobile/unity/builds/android/unityLibrary`

If export uses Gradle output instead of full player export, use Unity's `File > Build Settings > Android > Export Project` and point the output folder to `relaxroom-mobile/unity/builds/android`.

After export, remove the standalone launcher intent from:

`unity/builds/android/unityLibrary/src/main/AndroidManifest.xml`

## Node.js

React Native 0.76 请使用 **Node 20 LTS**，不要用 Node 22/24。

首次或升级 Node 后：

```powershell
node E:\AIgalgame\scripts\download-node20.cjs
powershell -ExecutionPolicy Bypass -File E:\AIgalgame\scripts\use-node20.ps1
```

然后**重新打开终端**，确认：

```powershell
node -v   # 应为 v20.18.3
```

## Run on Android

```powershell
cd E:\AIgalgame\relaxroom-mobile
npm start
```

另开一个终端：

```powershell
cd E:\AIgalgame\relaxroom-mobile
npm run android
```

真机调试若红屏 `Unable to load script`，先确认 Metro 在跑，再执行：

```powershell
adb reverse tcp:8081 tcp:8081
```

或在 Android 模拟器里直接 `npm run android`（会自动连 Metro）。

Or use the helper script from repo root:

```powershell
.\scripts\build_relaxroom_apk.ps1
```

## Architecture

- `src/screens/RelaxRoomScreen.tsx` - Unity background + RN overlays
- `src/bridge/unityBridge.ts` - JSON commands to `RelaxRoomBridge`
- `src/api/*` - FastAPI client for `/api/relaxroom/*` and `/api/events`
- Unity bridge: `RelaxRoomAI/Assets/_AIgalgame/Scripts/Bridge/RelaxRoomPresentationBridge.cs`

## Notes

- `unity/builds/` is gitignored because export artifacts are large
- Windows EXE remains a separate Unity Standalone build for now
- Existing `android/` Compose demo is unchanged
