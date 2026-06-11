Live2D model assets live here.

Expected runtime layout:

live2d/
  models/
    neko/
      neko.model3.json
      neko.moc3
      neko.physics3.json
      neko.cdi3.json
      neko.4096/
        texture_00.png
        texture_01.png
        texture_02.png
        texture_03.png
        texture_04.png
        texture_05.png
  atri/
    .gitkeep
  murasame/
    .gitkeep

NEKO is rendered through the official Cubism SDK for Java Android renderer.
ATRI and Murasame stay as PNG standees in res/drawable-nodpi and are also used
as the fallback path when the Live2D renderer or model fails to initialize.
