/**
 * Mirror of android/app/.../Live2DHitTest.kt — keep constants in sync.
 */
const Live2DHitTest = (() => {
  const HorizontalRange = 480;
  const VerticalRange = 900;
  const BottomInsetRange = 650;
  const CharacterCenterX = 0.5;
  const CharacterCenterY = 0.42;
  const ModelCoordMin = -0.5;
  const ModelCoordMax = 1.5;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function mapScreenToModelSpace(screenX, screenY, placement) {
    const scale = clamp(Number(placement?.scale ?? 1), 0.25, 4);
    const offsetXNorm = (Number(placement?.offsetX ?? 0) / HorizontalRange) * 0.14;
    const offsetYNorm = (Number(placement?.offsetY ?? 0) / VerticalRange) * 0.11;
    const bottomLift = (Number(placement?.bottomInset ?? 0) / BottomInsetRange) * 0.07;

    let x = screenX - offsetXNorm;
    let y = screenY + bottomLift - offsetYNorm;
    x = CharacterCenterX + (x - CharacterCenterX) / Math.max(scale, 0.25);
    y = CharacterCenterY + (y - CharacterCenterY) / Math.max(scale, 0.25);
    return { x: clamp(x, ModelCoordMin, ModelCoordMax), y: clamp(y, ModelCoordMin, ModelCoordMax) };
  }

  function projectModelToScreen(modelX, modelY, placement, clampResult) {
    const scale = clamp(Number(placement?.scale ?? 1), 0.25, 4);
    const offsetXNorm = (Number(placement?.offsetX ?? 0) / HorizontalRange) * 0.14;
    const offsetYNorm = (Number(placement?.offsetY ?? 0) / VerticalRange) * 0.11;
    const bottomLift = (Number(placement?.bottomInset ?? 0) / BottomInsetRange) * 0.07;

    let x = CharacterCenterX + (modelX - CharacterCenterX) * Math.max(scale, 0.25);
    let y = CharacterCenterY + (modelY - CharacterCenterY) * Math.max(scale, 0.25);
    x += offsetXNorm;
    y = y + offsetYNorm - bottomLift;
    if (clampResult) {
      return { x: clamp(x, 0, 1), y: clamp(y, 0, 1) };
    }
    return { x, y };
  }

  function mapModelToScreenSpace(modelX, modelY, placement) {
    return projectModelToScreen(modelX, modelY, placement, true);
  }

  function mapModelToScreenSpaceRaw(modelX, modelY, placement) {
    return projectModelToScreen(modelX, modelY, placement, false);
  }

  /** Character bounds for preview — uses unclamped projection so scale/offset track hit math. */
  function characterScreenRect(placement, stageW = HorizontalRange, stageH = 720) {
    const top = mapModelToScreenSpaceRaw(0.5, 0, placement);
    const bottom = mapModelToScreenSpaceRaw(0.5, 1, placement);
    const left = mapModelToScreenSpaceRaw(0, 0.5, placement);
    const right = mapModelToScreenSpaceRaw(1, 0.5, placement);
    return {
      left: left.x * stageW,
      top: top.y * stageH,
      width: (right.x - left.x) * stageW,
      height: (bottom.y - top.y) * stageH,
    };
  }

  function characterBottomCenter(placement, stageW = HorizontalRange, stageH = 720) {
    const point = mapModelToScreenSpaceRaw(0.5, 1, placement);
    return { x: point.x * stageW, y: point.y * stageH };
  }

  return {
    HorizontalRange,
    VerticalRange,
    BottomInsetRange,
    CharacterCenterX,
    CharacterCenterY,
    ModelCoordMin,
    ModelCoordMax,
    mapScreenToModelSpace,
    mapModelToScreenSpace,
    mapModelToScreenSpaceRaw,
    characterScreenRect,
    characterBottomCenter,
  };
})();

if (typeof window !== "undefined") {
  window.Live2DHitTest = Live2DHitTest;
}
