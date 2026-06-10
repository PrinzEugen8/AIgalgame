Live2D model assets live here.

Expected layout:

live2d/
  atri/
    atri.model3.json
    atri.moc3
    textures/
    motions/
    expressions/
    physics.json
  murasame/
    murasame.model3.json
    murasame.moc3
    textures/
    motions/
    expressions/
    physics.json

The app first looks for the configured character model. If it is missing, the
current configs fall back to the official Cubism SDK sample model:

live2d/samples/Haru/Haru.model3.json
live2d/models/Haru/Haru.model3.json

The Live2D runtime is packaged as a local WebView/Pixi stage under
live2d-web/. If both the configured model and fallback model are missing, the
app falls back to the PNG standee.
