/**
 * Admin-only preview: character pose follows Live2DHitTest projection so hit boxes stay aligned.
 */
const Live2DPreview = (() => {
  const STAGE_W = 480;
  const STAGE_H = 720;

  let app = null;
  let model = null;
  let glCanvas = null;
  let placement = { scale: 1.1, offsetX: 0, offsetY: -10, bottomInset: 30 };
  let rendererMode = "live2d";
  let cachedModelHeight = null;

  function requireHitTest() {
    if (typeof window.Live2DHitTest === "undefined") {
      throw new Error("Live2DHitTest is not loaded");
    }
    return window.Live2DHitTest;
  }

  function requirePixi() {
    if (typeof window.PIXI === "undefined") {
      throw new Error("PIXI is not loaded");
    }
    return window.PIXI;
  }

  async function init(canvas) {
    glCanvas = canvas;
    if (!glCanvas) return;
    requirePixi();
    if (app) {
      app.destroy(true, { children: true, texture: true, baseTexture: true });
      app = null;
      model = null;
    }
    app = new PIXI.Application({
      view: glCanvas,
      width: STAGE_W,
      height: STAGE_H,
      backgroundColor: 0x1a1a22,
      backgroundAlpha: 1,
      antialias: true,
      autoDensity: true,
      resolution: window.devicePixelRatio || 1,
    });
  }

  function applyPlacement() {
    if (!model || !app) return;
    const hitTest = requireHitTest();
    const rect = hitTest.characterScreenRect(placement, STAGE_W, STAGE_H);
    const bottom = hitTest.characterBottomCenter(placement, STAGE_W, STAGE_H);
    const modelHeight = Math.max(cachedModelHeight || model.height || 1, 1);
    const scale = rect.height / modelHeight;
    model.anchor.set(0.5, 1.0);
    model.scale.set(scale);
    model.x = bottom.x;
    model.y = bottom.y;
  }

  async function loadModel(modelUrl) {
    if (!app) {
      throw new Error("Live2D preview is not initialized");
    }
    const PIXI = requirePixi();
    if (!PIXI.live2d?.Live2DModel) {
      throw new Error("pixi-live2d-display is not loaded");
    }
    if (model) {
      app.stage.removeChild(model);
      model.destroy({ children: true });
      model = null;
      cachedModelHeight = null;
    }
    model = await PIXI.live2d.Live2DModel.from(modelUrl, { autoInteract: false });
    app.stage.addChild(model);
    try {
      model.motion("idle");
    } catch (_error) {
      /* optional */
    }
    try {
      model.expression("calm");
    } catch (_error) {
      /* optional */
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const local = model.getLocalBounds?.() || { height: model.height };
    cachedModelHeight = Math.max(local.height || model.height || 1, 1);
    applyPlacement();
    rendererMode = "live2d";
    if (glCanvas) glCanvas.style.display = "block";
  }

  function setPlacement(nextPlacement) {
    placement = {
      scale: Number(nextPlacement?.scale ?? placement.scale),
      offsetX: Number(nextPlacement?.offsetX ?? placement.offsetX),
      offsetY: Number(nextPlacement?.offsetY ?? placement.offsetY),
      bottomInset: Number(nextPlacement?.bottomInset ?? placement.bottomInset),
    };
    if (rendererMode === "live2d") {
      applyPlacement();
    }
    return { ...placement };
  }

  function getPlacement() {
    return { ...placement };
  }

  function drawStaticStandee(ctx, image, nextPlacement, width = STAGE_W, height = STAGE_H) {
    if (!ctx || !image) return;
    const hitTest = requireHitTest();
    placement = {
      scale: Number(nextPlacement?.scale ?? placement.scale),
      offsetX: Number(nextPlacement?.offsetX ?? placement.offsetX),
      offsetY: Number(nextPlacement?.offsetY ?? placement.offsetY),
      bottomInset: Number(nextPlacement?.bottomInset ?? placement.bottomInset),
    };
    rendererMode = "static_png";
    if (glCanvas) glCanvas.style.display = "none";
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#1a1a22";
    ctx.fillRect(0, 0, width, height);
    const rect = hitTest.characterScreenRect(placement, width, height);
    const aspect = image.width / Math.max(image.height, 1);
    let drawWidth = rect.width;
    let drawHeight = rect.height;
    const rectAspect = rect.width / Math.max(rect.height, 1);
    if (aspect > rectAspect) {
      drawWidth = rect.width;
      drawHeight = drawWidth / aspect;
    } else {
      drawHeight = rect.height;
      drawWidth = drawHeight * aspect;
    }
    const bottom = hitTest.characterBottomCenter(placement, width, height);
    const x = bottom.x - drawWidth / 2;
    const y = bottom.y - drawHeight;
    ctx.drawImage(image, x, y, drawWidth, drawHeight);
  }

  function destroy() {
    cachedModelHeight = null;
    if (model) {
      model.destroy({ children: true });
      model = null;
    }
    if (app) {
      app.destroy(true, { children: true, texture: true, baseTexture: true });
      app = null;
    }
    glCanvas = null;
  }

  function setRendererMode(mode) {
    rendererMode = mode;
    if (glCanvas) {
      glCanvas.style.display = mode === "live2d" ? "block" : "none";
    }
  }

  return {
    STAGE_W,
    STAGE_H,
    init,
    loadModel,
    setPlacement,
    getPlacement,
    drawStaticStandee,
    destroy,
    setRendererMode,
  };
})();

if (typeof window !== "undefined") {
  window.Live2DPreview = Live2DPreview;
}
