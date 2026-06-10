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

The app is designed for a real Android device on the same LAN as the backend. Cleartext HTTP is enabled for local development, so `http://192.168.x.x:8899` works. Online service keys are configured only in the backend admin page.

## Tunnel Troubleshooting

The backend defaults to port `8899`. If an intranet tunnel reports `dial tcp 127.0.0.1:8898: connectex: No connection could be made`, the tunnel is pointing at a port where the backend is not listening. Set the tunnel's local target to `127.0.0.1:8899`, or start the backend on `8898`:

```powershell
$env:AIGALGAME_PORT = "8898"
.\backend\run.ps1
```

If the app reports `Trust anchor for certification path not found`, Android does not trust the HTTPS certificate served by the tunnel. Prefer the tunnel's `http://` URL for local development, or use an HTTPS tunnel/domain with a public trusted certificate. A self-signed certificate requires installing its CA on the phone and configuring the debug app to trust it.

Implemented surfaces:

- Galgame home screen with top status, Sakura character scene, dialogue box, normal/key replies, custom input, and TTS playback when backend provides audio.
- Bottom navigation with only Home, Dress Up, and Settings.
- Dress Up screen with local PNG character standees and scene selection.
- Moments feed with like/comment feedback.
- Calendar panel backed by 15-minute schedule slots.
- Journal/memory view.
- 2x4 home-screen AppWidget and periodic notification/widget polling.
