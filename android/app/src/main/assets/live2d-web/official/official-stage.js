(function () {
  "use strict";

  var STAGE_VERSION = "official-core-direct-v1";
  var DEFAULT_MODEL = "live2d/models/Haru/Haru.model3.json";
  var canvas = document.getElementById("stage");
  var diagnostics = {
    core: document.getElementById("core"),
    framework: document.getElementById("framework"),
    model: document.getElementById("model"),
    drawables: document.getElementById("drawables"),
    frame: document.getElementById("frame"),
    version: document.getElementById("version"),
    error: document.getElementById("error")
  };

  var gl = null;
  var program = null;
  var attribPosition = -1;
  var attribUv = -1;
  var uniformMatrix = null;
  var uniformTexture = null;
  var uniformOpacity = null;
  var vertexBuffer = null;
  var uvBuffer = null;
  var indexBuffer = null;
  var currentModel = null;
  var currentMoc = null;
  var currentModelSrc = "";
  var textures = [];
  var parameterIndex = {};
  var drawableBounds = null;
  var pendingState = {};
  var interactive = true;
  var loadingPromise = null;
  var lastFrameStartedAt = 0;
  var lastFrameMs = 0;
  var lastFrameAt = 0;
  var pixelProbe = { alphaHits: 0, colorHits: 0 };
  var hadFirstFrame = false;
  var status = {
    coreLoaded: false,
    frameworkLoaded: false,
    modelLoaded: false,
    drawableCount: 0,
    lastError: "",
    modelSrc: "",
    stageMode: "selftest"
  };

  function bridge() {
    return window.AndroidLive2D || null;
  }

  function callBridge(name, payload) {
    try {
      var target = bridge();
      if (target && typeof target[name] === "function") {
        target[name](payload == null ? "" : String(payload));
      }
    } catch (error) {
      console.warn("[Live2DOfficialStage] bridge error", error);
    }
  }

  function debug(message) {
    console.log("[Live2DOfficialStage] " + message);
    callBridge("onDebug", message);
  }

  function fail(message, error) {
    var detail = message;
    if (error && error.message) {
      detail += ": " + error.message;
    }
    status.lastError = detail;
    renderDiagnostics();
    console.error("[Live2DOfficialStage] " + detail, error || "");
    callBridge("onError", detail);
  }

  function setText(node, value, ok) {
    if (!node) return;
    node.textContent = value;
    node.classList.toggle("status-ok", ok === true);
    node.classList.toggle("status-bad", ok === false);
  }

  function renderDiagnostics() {
    setText(diagnostics.core, status.coreLoaded ? "yes" : "no", status.coreLoaded);
    setText(diagnostics.framework, status.frameworkLoaded ? "yes" : "no", status.frameworkLoaded);
    setText(diagnostics.model, status.modelLoaded ? "yes" : "no", status.modelLoaded);
    setText(diagnostics.drawables, String(status.drawableCount || 0));
    setText(diagnostics.frame, lastFrameMs.toFixed(1) + " ms");
    setText(diagnostics.version, STAGE_VERSION);
    if (diagnostics.error) diagnostics.error.textContent = status.lastError || "";
  }

  function assetUrl(path) {
    if (!path) return "";
    if (/^(https?:|file:|data:|blob:)/i.test(path)) return path;
    var cleaned = String(path).replace(/^\/+/, "").replace(/^assets\//, "");
    return "/assets/" + cleaned;
  }

  function dirname(path) {
    var index = path.lastIndexOf("/");
    return index >= 0 ? path.slice(0, index + 1) : "";
  }

  function resizeCanvas() {
    var ratio = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    var width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
    var height = Math.max(1, Math.floor(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    if (gl) {
      gl.viewport(0, 0, canvas.width, canvas.height);
    }
  }

  function waitForCore() {
    return new Promise(function (resolve, reject) {
      var attempts = 0;
      function tick() {
        attempts += 1;
        try {
          if (
            window.Live2DCubismCore &&
            window.Live2DCubismCore.Version &&
            typeof window.Live2DCubismCore.Version.csmGetVersion === "function"
          ) {
            window.Live2DCubismCore.Version.csmGetVersion();
            status.coreLoaded = true;
            renderDiagnostics();
            resolve(window.Live2DCubismCore);
            return;
          }
        } catch (error) {
          if (attempts > 120) {
            reject(error);
            return;
          }
        }
        if (attempts > 120) {
          reject(new Error("Live2D Cubism Core did not become ready"));
          return;
        }
        window.setTimeout(tick, 50);
      }
      tick();
    });
  }

  function compileShader(type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      var message = gl.getShaderInfoLog(shader) || "shader compile failed";
      gl.deleteShader(shader);
      throw new Error(message);
    }
    return shader;
  }

  function initProgram() {
    if (program) return;
    var vertex = compileShader(
      gl.VERTEX_SHADER,
      [
        "attribute vec2 aPosition;",
        "attribute vec2 aUv;",
        "uniform mat4 uMatrix;",
        "varying vec2 vUv;",
        "void main() {",
        "  gl_Position = uMatrix * vec4(aPosition, 0.0, 1.0);",
        "  vUv = aUv;",
        "}"
      ].join("\n")
    );
    var fragment = compileShader(
      gl.FRAGMENT_SHADER,
      [
        "precision mediump float;",
        "uniform sampler2D uTexture;",
        "uniform float uOpacity;",
        "varying vec2 vUv;",
        "void main() {",
        "  vec4 color = texture2D(uTexture, vUv);",
        "  gl_FragColor = color * uOpacity;",
        "}"
      ].join("\n")
    );
    program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) || "program link failed");
    }

    attribPosition = gl.getAttribLocation(program, "aPosition");
    attribUv = gl.getAttribLocation(program, "aUv");
    uniformMatrix = gl.getUniformLocation(program, "uMatrix");
    uniformTexture = gl.getUniformLocation(program, "uTexture");
    uniformOpacity = gl.getUniformLocation(program, "uOpacity");
    vertexBuffer = gl.createBuffer();
    uvBuffer = gl.createBuffer();
    indexBuffer = gl.createBuffer();
    status.frameworkLoaded = true;
    renderDiagnostics();
  }

  function initGl() {
    if (gl) return;
    gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      preserveDrawingBuffer: true
    });
    if (!gl) {
      throw new Error("WebGL is not available in this WebView");
    }
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.STENCIL_TEST);
    gl.enable(gl.BLEND);
    gl.clearColor(0, 0, 0, 0);
    resizeCanvas();
    initProgram();
  }

  function fetchJson(path) {
    return fetch(assetUrl(path), { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status + " " + path);
      return response.json();
    });
  }

  function fetchArrayBuffer(path) {
    return fetch(assetUrl(path), { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status + " " + path);
      return response.arrayBuffer();
    });
  }

  function loadImage(path) {
    return new Promise(function (resolve, reject) {
      var image = new Image();
      image.onload = function () { resolve(image); };
      image.onerror = function () { reject(new Error("Image load failed: " + path)); };
      image.src = assetUrl(path);
    });
  }

  function createTexture(image) {
    var texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    gl.bindTexture(gl.TEXTURE_2D, null);
    return texture;
  }

  function releaseModel() {
    for (var i = 0; i < textures.length; i += 1) {
      if (textures[i]) gl.deleteTexture(textures[i]);
    }
    textures = [];
    if (currentModel && typeof currentModel.release === "function") {
      currentModel.release();
    }
    if (currentMoc && typeof currentMoc._release === "function") {
      currentMoc._release();
    }
    currentModel = null;
    currentMoc = null;
    parameterIndex = {};
    drawableBounds = null;
    status.modelLoaded = false;
    status.drawableCount = 0;
  }

  function indexParameters(model) {
    parameterIndex = {};
    var ids = model.parameters.ids || [];
    for (var i = 0; i < ids.length; i += 1) {
      parameterIndex[ids[i]] = i;
    }
  }

  function setParam(name, value) {
    if (!currentModel) return;
    var index = parameterIndex[name];
    if (index == null) return;
    var params = currentModel.parameters;
    var min = params.minimumValues[index];
    var max = params.maximumValues[index];
    params.values[index] = Math.max(min, Math.min(max, value));
  }

  function calculateBounds(model) {
    var drawables = model.drawables;
    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    for (var i = 0; i < drawables.count; i += 1) {
      var vertices = drawables.vertexPositions[i];
      for (var j = 0; j < vertices.length; j += 2) {
        minX = Math.min(minX, vertices[j]);
        maxX = Math.max(maxX, vertices[j]);
        minY = Math.min(minY, vertices[j + 1]);
        maxY = Math.max(maxY, vertices[j + 1]);
      }
    }
    if (!isFinite(minX) || !isFinite(minY) || !isFinite(maxX) || !isFinite(maxY)) {
      return { minX: -0.5, maxX: 0.5, minY: -0.9, maxY: 0.9, width: 1, height: 1.8 };
    }
    return {
      minX: minX,
      maxX: maxX,
      minY: minY,
      maxY: maxY,
      width: Math.max(0.001, maxX - minX),
      height: Math.max(0.001, maxY - minY)
    };
  }

  function applyStageClass(stageMode) {
    var mode = stageMode || "home";
    document.body.classList.toggle("stage-selftest", mode === "selftest");
    document.body.classList.toggle("stage-home", mode !== "selftest" && mode !== "dress");
    document.body.classList.toggle("stage-dress", mode === "dress");
  }

  function loadModel(modelSrc) {
    var nextSrc = modelSrc || DEFAULT_MODEL;
    if (loadingPromise && nextSrc === currentModelSrc) return loadingPromise;
    currentModelSrc = nextSrc;
    status.modelSrc = nextSrc;
    status.lastError = "";
    renderDiagnostics();

    loadingPromise = Promise.all([waitForCore(), Promise.resolve().then(initGl)])
      .then(function (values) {
        var core = values[0];
        return fetchJson(nextSrc).then(function (modelJson) {
          var base = dirname(nextSrc);
          var refs = modelJson.FileReferences || {};
          if (!refs.Moc) throw new Error("model3.json missing FileReferences.Moc");
          return Promise.all([
            fetchArrayBuffer(base + refs.Moc),
            Promise.all((refs.Textures || []).map(function (texturePath) {
              return loadImage(base + texturePath);
            }))
          ]).then(function (parts) {
            releaseModel();
            currentMoc = core.Moc.fromArrayBuffer(parts[0]);
            if (!currentMoc) throw new Error("Cubism Core failed to create Moc");
            currentModel = core.Model.fromMoc(currentMoc);
            if (!currentModel) throw new Error("Cubism Core failed to create Model");
            textures = parts[1].map(createTexture);
            indexParameters(currentModel);
            currentModel.update();
            drawableBounds = calculateBounds(currentModel);
            status.modelLoaded = true;
            status.drawableCount = currentModel.drawables.count || 0;
            renderDiagnostics();
            callBridge("onModelLoaded", JSON.stringify({
              stageVersion: STAGE_VERSION,
              modelSrc: nextSrc,
              drawableCount: status.drawableCount,
              textureCount: textures.length,
              canvas: currentModel.canvasinfo || {}
            }));
          });
        });
      })
      .catch(function (error) {
        releaseModel();
        fail("Model load failed", error);
      });
    return loadingPromise;
  }

  function eyeOpenAt(timeSeconds) {
    var period = 4.2;
    var phase = timeSeconds % period;
    if (phase < 3.75) return 1;
    if (phase < 3.86) return 1 - ((phase - 3.75) / 0.11);
    if (phase < 3.96) return 0;
    if (phase < 4.12) return (phase - 3.96) / 0.16;
    return 1;
  }

  function applyIdleParameters(now) {
    if (!currentModel) return;
    var seconds = now / 1000;
    var state = pendingState || {};
    var focus = state.focusAt || {};
    var lookX = typeof focus.x === "number" ? focus.x : 0;
    var lookY = typeof focus.y === "number" ? focus.y : 0;
    var mouth = state.nowSpeaking ? Number(state.mouthOpenSize || 0) : 0;
    var eye = eyeOpenAt(seconds);

    setParam("ParamAngleX", lookX * 18 + Math.sin(seconds * 0.85) * 3.5);
    setParam("ParamAngleY", lookY * 12 + Math.sin(seconds * 0.72) * 2.5);
    setParam("ParamAngleZ", Math.sin(seconds * 0.55) * 2.5);
    setParam("ParamBodyAngleX", lookX * 5 + Math.sin(seconds * 0.48) * 1.5);
    setParam("ParamBodyAngleY", lookY * 3);
    setParam("ParamEyeBallX", lookX * 0.65 + Math.sin(seconds * 0.45) * 0.08);
    setParam("ParamEyeBallY", lookY * 0.45 + Math.sin(seconds * 0.50) * 0.05);
    setParam("ParamEyeLOpen", eye);
    setParam("ParamEyeROpen", eye);
    setParam("ParamBreath", 0.5 + Math.sin(seconds * 2.2) * 0.35);
    setParam("ParamMouthOpenY", Math.max(0, Math.min(1, mouth)));
  }

  function matrixForPlacement() {
    var bounds = drawableBounds || { minX: -0.5, maxX: 0.5, minY: -0.9, maxY: 0.9, width: 1, height: 1.8 };
    var placement = pendingState.placement || {};
    var stageScale = Number(placement.scale || 1);
    var aspect = Math.max(0.2, canvas.width / Math.max(1, canvas.height));
    var fitByHeight = 1.72 / bounds.height;
    var fitByWidth = (1.62 * aspect) / bounds.width;
    var scaleY = Math.min(fitByHeight, fitByWidth) * stageScale;
    var scaleX = scaleY / aspect;
    var centerX = (bounds.minX + bounds.maxX) * 0.5;
    var centerY = (bounds.minY + bounds.maxY) * 0.5;
    var offsetX = (Number(placement.offsetX || 0) / Math.max(1, canvas.clientWidth)) * 2;
    var offsetY = (-Number(placement.offsetY || 0) / Math.max(1, canvas.clientHeight)) * 2;
    var bottomInset = (Number(placement.bottomInset || 0) / Math.max(1, canvas.clientHeight)) * 2;
    return new Float32Array([
      scaleX, 0, 0, 0,
      0, scaleY, 0, 0,
      0, 0, 1, 0,
      -centerX * scaleX + offsetX,
      -centerY * scaleY + offsetY + bottomInset,
      0,
      1
    ]);
  }

  function drawModel(now) {
    if (!gl || !program || !currentModel) return;
    var core = window.Live2DCubismCore;
    var drawables = currentModel.drawables;
    var order = [];
    for (var i = 0; i < drawables.count; i += 1) order.push(i);
    order.sort(function (a, b) {
      return drawables.renderOrders[a] - drawables.renderOrders[b];
    });

    applyIdleParameters(now);
    currentModel.update();

    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(program);
    gl.uniformMatrix4fv(uniformMatrix, false, matrixForPlacement());
    gl.uniform1i(uniformTexture, 0);
    gl.enableVertexAttribArray(attribPosition);
    gl.enableVertexAttribArray(attribUv);
    gl.activeTexture(gl.TEXTURE0);

    for (var oi = 0; oi < order.length; oi += 1) {
      var index = order[oi];
      var dynamicFlags = drawables.dynamicFlags[index];
      if (!core.Utils.hasIsVisibleBit(dynamicFlags)) continue;
      var textureIndex = drawables.textureIndices[index];
      var texture = textures[textureIndex];
      if (!texture) continue;

      var flags = drawables.constantFlags[index];
      if (core.Utils.hasBlendAdditiveBit(flags)) {
        gl.blendFunc(gl.ONE, gl.ONE);
      } else if (core.Utils.hasBlendMultiplicativeBit(flags)) {
        gl.blendFunc(gl.DST_COLOR, gl.ONE_MINUS_SRC_ALPHA);
      } else {
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      }

      gl.disable(gl.CULL_FACE);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.uniform1f(uniformOpacity, drawables.opacities[index]);

      gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, drawables.vertexPositions[index], gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(attribPosition, 2, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, uvBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, drawables.vertexUvs[index], gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(attribUv, 2, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, drawables.indices[index], gl.DYNAMIC_DRAW);
      gl.drawElements(gl.TRIANGLES, drawables.indexCounts[index], gl.UNSIGNED_SHORT, 0);
    }

    drawables.resetDynamicFlags();
  }

  function updatePixelProbe() {
    if (!gl || !hadFirstFrame) return;
    try {
      var pixels = new Uint8Array(4 * 16 * 16);
      var x = Math.max(0, Math.floor(canvas.width / 2) - 8);
      var y = Math.max(0, Math.floor(canvas.height / 2) - 8);
      gl.readPixels(x, y, 16, 16, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      var alphaHits = 0;
      var colorHits = 0;
      for (var i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] > 0) alphaHits += 1;
        if (pixels[i] + pixels[i + 1] + pixels[i + 2] > 8) colorHits += 1;
      }
      pixelProbe = { alphaHits: alphaHits, colorHits: colorHits };
    } catch (error) {
      pixelProbe = { alphaHits: -1, colorHits: -1 };
    }
  }

  function frame(now) {
    window.requestAnimationFrame(frame);
    resizeCanvas();
    lastFrameStartedAt = performance.now();
    try {
      if (currentModel) {
        drawModel(now);
        hadFirstFrame = true;
      } else if (gl) {
        gl.clear(gl.COLOR_BUFFER_BIT);
      }
      lastFrameAt = now;
      lastFrameMs = performance.now() - lastFrameStartedAt;
      if (Math.floor(now / 1000) !== Math.floor((now - 16) / 1000)) {
        updatePixelProbe();
      }
      renderDiagnostics();
    } catch (error) {
      fail("Render failed", error);
    }
  }

  function setState(nextState) {
    pendingState = Object.assign({}, pendingState, nextState || {});
    status.stageMode = pendingState.stageMode || "home";
    applyStageClass(status.stageMode);
    if (pendingState.modelSrc && pendingState.modelSrc !== currentModelSrc) {
      loadModel(pendingState.modelSrc);
    }
  }

  function playReaction(reaction) {
    if (!reaction) return;
    if (reaction.expression) pendingState.expression = reaction.expression;
    if (reaction.motion) pendingState.motion = reaction.motion;
  }

  function onPointerDown(event) {
    if (!interactive) return;
    var rect = canvas.getBoundingClientRect();
    var x = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0.5;
    var y = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5;
    var hitAreas = [];
    if (x >= 0.36 && x <= 0.64 && y >= 0.10 && y <= 0.30) hitAreas.push("head");
    if (x >= 0.38 && x <= 0.62 && y >= 0.34 && y <= 0.52) hitAreas.push("chest");
    if (x >= 0.18 && x <= 0.82 && y >= 0.42 && y <= 0.70) hitAreas.push("hand");
    if (x >= 0.30 && x <= 0.70 && y >= 0.30 && y <= 0.82) hitAreas.push("body");
    pendingState.focusAt = { x: (x - 0.5) * 2, y: (0.5 - y) * 2 };
    callBridge("onTap", JSON.stringify({
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      normalizedX: Math.max(0, Math.min(1, x)),
      normalizedY: Math.max(0, Math.min(1, y)),
      hitAreas: hitAreas
    }));
  }

  function boot() {
    diagnostics.version.textContent = STAGE_VERSION;
    window.addEventListener("resize", resizeCanvas);
    canvas.addEventListener("pointerdown", onPointerDown);

    window.Live2DOfficialStage = {
      setState: setState,
      playReaction: playReaction,
      setInteractive: function (enabled) { interactive = !!enabled; },
      getDebugState: function () {
        return {
          stageVersion: STAGE_VERSION,
          coreLoaded: status.coreLoaded,
          frameworkLoaded: status.frameworkLoaded,
          modelLoaded: status.modelLoaded,
          drawableCount: status.drawableCount,
          modelSrc: status.modelSrc,
          stageMode: status.stageMode,
          canvasWidth: canvas.width,
          canvasHeight: canvas.height,
          lastFrameAt: lastFrameAt,
          lastFrameMs: lastFrameMs,
          pixelProbe: pixelProbe,
          lastError: status.lastError
        };
      }
    };
    window.AiriLive2D = window.Live2DOfficialStage;

    waitForCore()
      .then(function () {
        initGl();
        callBridge("onReady", JSON.stringify({ stageVersion: STAGE_VERSION }));
        return loadModel((pendingState && pendingState.modelSrc) || DEFAULT_MODEL);
      })
      .catch(function (error) {
        fail("Stage boot failed", error);
      });
    window.requestAnimationFrame(frame);
  }

  renderDiagnostics();
  boot();
})();
