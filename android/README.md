# Android App

Build:

```powershell
cd E:\AIgalgame\android
.\gradlew.bat :app:assembleDebug
```

APK:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

The app is designed for a real Android device connecting through an HTTPS tunnel or trusted HTTPS endpoint. Cleartext HTTP is disabled in the Android app. Online service keys are configured only in the backend admin page.

## Tunnel Troubleshooting

The backend defaults to local port `8899`. If an intranet tunnel reports `dial tcp 127.0.0.1:8898: connectex: No connection could be made`, the tunnel is pointing at a port where the backend is not listening. Set the tunnel's local target to `127.0.0.1:8899`, or start the backend on `8898`:

```powershell
$env:AIGALGAME_PORT = "8898"
.\backend\run.ps1
```

The phone-facing address must be HTTPS, for example `https://your-tunnel-domain.example`. The tunnel can still forward privately to `127.0.0.1:8899` on the PC.

Debug APKs trust all HTTPS certificates to make personal tunnel testing painless, while still blocking cleartext HTTP. If the debug app reports a certificate error, rebuild/reinstall the latest debug APK. Release builds do not bypass certificate checks and should use a public trusted HTTPS certificate.

Implemented surfaces:

- Galgame home screen with top status, Sakura character scene, dialogue box, normal/key replies, custom input, and TTS playback when backend provides audio.
- Bottom navigation with only Home, Dress Up, and Settings.
- Dress Up screen with local PNG character standees and scene selection.
- Moments feed with like/comment feedback.
- Calendar panel backed by 15-minute schedule slots.
- Journal/memory view.
- 2x4 home-screen AppWidget and periodic notification/widget polling.
