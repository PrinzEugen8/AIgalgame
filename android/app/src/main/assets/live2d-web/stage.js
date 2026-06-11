(function () {
    "use strict";

    var MODEL_BASE_URL = new URL("../", window.location.href).href;
    var DEFAULT_MODEL = MODEL_BASE_URL + "live2d/models/Haru/Haru.model3.json";
    var DEFAULT_BACKGROUND = "";
    var STAGE_VERSION = "pixi-cubism-runtime-v10-transparent-dom-fallback";
    var STAGE_BACKGROUND_COLOR = 0x000000;
    var STAGE_BACKGROUND_CSS = "transparent";
    var ENABLE_DOM_PRESENTER = true;
    var MODEL_PIXEL_THRESHOLD = 64;
    var MAX_RESOLUTION = 2;
    var RENDER_BURST_FRAMES = 36;
    var PRESENTER_FRAME_INTERVAL_MS = 33;
    var PRESENT_IMAGE_INTERVAL_MS = 250;
    var motionAliases = {
        Idle: "Idle",
        Happy: "TapBody",
        Think: "TapBody",
        Shy: "TapBody",
        Sad: "TapBody",
        Angry: "TapBody",
        Sleep: "Idle",
        TapHead: "TapBody",
        TapBody: "TapBody",
        TapChest: "TapBody",
        TapHand: "TapBody",
        StepBack: "TapBody"
    };
    var expressionAliases = {
        Neutral: "F01",
        Happy: "F02",
        Curious: "F02",
        Think: "F03",
        Shy: "F04",
        Sad: "F05",
        Angry: "F06",
        Sleep: "F07",
        Awkward: "F08"
    };

    var canvas = document.getElementById("live2d-canvas");
    var fallbackImage = document.getElementById("fallback-image");
    var presentCanvas = document.getElementById("present-canvas");
    var presentContext = ENABLE_DOM_PRESENTER && presentCanvas && presentCanvas.getContext
        ? presentCanvas.getContext("2d", { alpha: true })
        : null;
    var presentImage = document.getElementById("present-image");
    var diagnostics = {
        core: document.getElementById("core"),
        framework: document.getElementById("framework"),
        model: document.getElementById("model"),
        drawables: document.getElementById("drawables"),
        pixelAlpha: document.getElementById("pixel-alpha"),
        modelPixels: document.getElementById("model-pixels"),
        version: document.getElementById("version"),
        bounds: document.getElementById("bounds"),
        fit: document.getElementById("fit"),
        position: document.getElementById("position"),
        presenter: document.getElementById("presenter"),
        error: document.getElementById("error")
    };
    var app = null;
    var model = null;
    var backgroundSprite = null;
    var modelSrc = "";
    var backgroundSrc = "";
    var backgroundLoaded = true;
    var modelBounds = { x: 0, y: 0, width: 1, height: 1 };
    var latestFit = {
        bounds: modelBounds,
        pivotX: 0,
        pivotY: 0,
        scale: 0,
        modelX: 0,
        modelY: 0,
        stageWidth: 0,
        stageHeight: 0
    };
    var state = defaultState();
    var interactive = true;
    var lastMotion = "";
    var lastExpression = "";
    var lastCommandNonce = -1;
    var mouthOpen = 0;
    var targetFocusX = 0;
    var targetFocusY = 0;
    var blinkUntilMs = 0;
    var nextBlinkMs = performance.now() + 1600;
    var latestPixelProbe = { supported: false, alphaHits: 0, colorHits: 0, modelHits: 0, brightHits: 0 };
    var presenterReadBuffer = null;
    var presenterImageData = null;
    var presenterWidth = 0;
    var presenterHeight = 0;
    var presenterFrames = 0;
    var presenterImageFrames = 0;
    var presenterReported = false;
    var presenterLastError = "";
    var presenterLoopId = 0;
    var presenterLastCopyAt = 0;
    var presenterLastImageAt = 0;
    var presenterLastDiagnosticsAt = 0;
    var status = {
        coreLoaded: !!window.Live2DCubismCore,
        frameworkLoaded: false,
        modelLoaded: false,
        drawableCount: 0,
        lastError: "",
        stageMode: "home"
    };

    function defaultState() {
        return {
            stageVersion: STAGE_VERSION,
            modelSrc: DEFAULT_MODEL,
            backgroundSrc: DEFAULT_BACKGROUND,
            expression: "Neutral",
            motion: "Idle",
            commandNonce: 0,
            nowSpeaking: false,
            mouthOpenSize: 0,
            focusAt: { x: 0, y: 0 },
            placement: { scale: 1, offsetX: 0, offsetY: 0, bottomInset: 0 },
            stageMode: "home",
            editable: false
        };
    }

    function bridge() {
        return window.AndroidLive2D || null;
    }

    function post(method, payload) {
        try {
            var target = bridge();
            if (target && target[method]) {
                target[method](payload == null ? "" : String(payload));
            }
        } catch (error) {
            console.warn("Live2D bridge error", error);
        }
    }

    function setText(node, value, ok) {
        if (!node) return;
        node.textContent = String(value);
        node.classList.toggle("status-ok", ok === true);
        node.classList.toggle("status-bad", ok === false);
    }

    function fmt(value) {
        return Math.round((Number(value) || 0) * 100) / 100;
    }

    function renderDiagnostics() {
        setText(diagnostics.core, status.coreLoaded ? "yes" : "no", status.coreLoaded);
        setText(diagnostics.framework, status.frameworkLoaded ? "yes" : "no", status.frameworkLoaded);
        setText(diagnostics.model, status.modelLoaded ? "yes" : "no", status.modelLoaded);
        setText(diagnostics.drawables, status.drawableCount || 0);
        setText(diagnostics.pixelAlpha, latestPixelProbe.alphaHits || 0, (latestPixelProbe.alphaHits || 0) > 0);
        setText(
            diagnostics.modelPixels,
            (latestPixelProbe.modelHits || 0) + " / " + (latestPixelProbe.brightHits || 0),
            (latestPixelProbe.modelHits || 0) >= MODEL_PIXEL_THRESHOLD
        );
        setText(diagnostics.version, STAGE_VERSION);
        setText(
            diagnostics.bounds,
            fmt(modelBounds.x) + "," + fmt(modelBounds.y) + " " + fmt(modelBounds.width) + "x" + fmt(modelBounds.height)
        );
        setText(
            diagnostics.fit,
            fmt(latestFit.pivotX) + "," + fmt(latestFit.pivotY) + " @ " + fmt(latestFit.scale)
        );
        setText(
            diagnostics.position,
            fmt(latestFit.stageWidth) + "x" + fmt(latestFit.stageHeight) + " / " + fmt(latestFit.modelX) + "," + fmt(latestFit.modelY)
        );
        setText(
            diagnostics.presenter,
            ENABLE_DOM_PRESENTER
                ? presenterFrames + "/" + presenterImageFrames + " px " + (latestPixelProbe.modelHits || 0) +
                    (presenterLastError ? " err " + presenterLastError : "")
                : "raw-webgl",
            ENABLE_DOM_PRESENTER
                ? presenterFrames > 0 && presenterImageFrames > 0 && !presenterLastError &&
                    (latestPixelProbe.modelHits || 0) >= MODEL_PIXEL_THRESHOLD
                : true
        );
        if (diagnostics.error) diagnostics.error.textContent = status.lastError || "";
    }

    function applyStageClass(stageMode) {
        status.stageMode = stageMode || "home";
        document.body.classList.toggle("stage-selftest", status.stageMode === "selftest");
        document.body.classList.toggle("stage-home", status.stageMode !== "selftest" && status.stageMode !== "dress");
        document.body.classList.toggle("stage-dress", status.stageMode === "dress");
    }

    function getDrawableCount() {
        try {
            var coreModel = model && model.internalModel && model.internalModel.coreModel;
            if (coreModel && coreModel.drawables && typeof coreModel.drawables.count === "number") {
                return coreModel.drawables.count;
            }
            if (coreModel && typeof coreModel.getDrawableCount === "function") {
                return coreModel.getDrawableCount();
            }
        } catch (_) {
        }
        return 0;
    }

    function resetModelTransform() {
        if (!model) return;
        if (model.anchor && typeof model.anchor.set === "function") model.anchor.set(0, 0);
        if (model.pivot && typeof model.pivot.set === "function") model.pivot.set(0, 0);
        if (model.scale && typeof model.scale.set === "function") model.scale.set(1, 1);
        if (model.position && typeof model.position.set === "function") {
            model.position.set(0, 0);
        } else {
            model.x = 0;
            model.y = 0;
        }
        model.rotation = 0;
        if (model.skew && typeof model.skew.set === "function") model.skew.set(0, 0);
    }

    function refreshModelBounds() {
        if (!model) return modelBounds;
        resetModelTransform();
        try {
            if (typeof model.update === "function") model.update(0);
            var bounds = typeof model.getLocalBounds === "function" ? model.getLocalBounds() : null;
            modelBounds = {
                x: Number(bounds && bounds.x) || 0,
                y: Number(bounds && bounds.y) || 0,
                width: Math.max(1, Number(bounds && bounds.width) || 1),
                height: Math.max(1, Number(bounds && bounds.height) || 1)
            };
        } catch (error) {
            debug("getLocalBounds failed: " + (error && error.message ? error.message : String(error)));
            modelBounds = { x: 0, y: 0, width: 1, height: 1 };
        }
        return modelBounds;
    }

    function debug(message) {
        try {
            console.log("[AiriLive2D] " + message);
        } catch (_) {
        }
        post("onDebug", message);
    }

    function reportError(error, fatal) {
        var message = error && error.message ? error.message : String(error);
        status.lastError = message;
        renderDiagnostics();
        try {
            console.error("[AiriLive2D] " + message);
        } catch (_) {
        }
        if (fatal) {
            post("onError", message);
        } else {
            post("onDebug", "nonfatal error: " + message);
        }
    }

    function canonicalHitArea(name) {
        var value = String(name || "").toLowerCase();
        if (value.indexOf("head") >= 0) return "head";
        if (value.indexOf("body") >= 0) return "body";
        if (value.indexOf("chest") >= 0 || value.indexOf("bust") >= 0) return "chest";
        if (value.indexOf("hand") >= 0 || value.indexOf("arm") >= 0) return "hand";
        return value;
    }

    function normalizeModelSrc(src) {
        if (!src) return DEFAULT_MODEL;
        if (/^(file|https?):\/\//i.test(src)) return src;
        return MODEL_BASE_URL + src.replace(/^\/+/, "");
    }

    function normalizeBackgroundSrc(src) {
        return DEFAULT_BACKGROUND;
    }

    function setBackground(nextSrc) {
        var resolvedSrc = normalizeBackgroundSrc(nextSrc);
        if (!app || !window.PIXI) {
            backgroundSrc = resolvedSrc;
            backgroundLoaded = true;
            renderDiagnostics();
            return;
        }
        if (backgroundSprite && resolvedSrc === backgroundSrc) {
            fitBackground();
            renderDiagnostics();
            return;
        }
        if (backgroundSprite) {
            app.stage.removeChild(backgroundSprite);
            backgroundSprite.destroy({ children: true });
            backgroundSprite = null;
        }
        backgroundSrc = resolvedSrc;
        backgroundLoaded = !resolvedSrc;
        if (resolvedSrc) {
            try {
                backgroundSprite = PIXI.Sprite.from(resolvedSrc);
                backgroundSprite.zIndex = -100;
                backgroundSprite.alpha = 1;
                backgroundSprite.visible = true;
                app.stage.addChild(backgroundSprite);
                if (backgroundSprite.texture && backgroundSprite.texture.baseTexture) {
                    backgroundSprite.texture.baseTexture.on("loaded", function () {
                        backgroundLoaded = true;
                        fitBackground();
                        requestRenderBurst(8);
                    });
                    backgroundSprite.texture.baseTexture.on("error", function (error) {
                        backgroundLoaded = false;
                        debug("background failed: " + (error && error.message ? error.message : String(error)));
                    });
                    backgroundLoaded = !!backgroundSprite.texture.baseTexture.valid;
                }
                fitBackground();
            } catch (error) {
                backgroundLoaded = false;
                debug("background failed: " + (error && error.message ? error.message : String(error)));
            }
        }
        renderDiagnostics();
    }

    function fitBackground() {
        if (!app || !backgroundSprite) return;
        var size = screenSize();
        var texture = backgroundSprite.texture;
        var width = texture && texture.width ? texture.width : 1;
        var height = texture && texture.height ? texture.height : 1;
        var scale = Math.max(size.width / width, size.height / height);
        backgroundSprite.scale.set(scale);
        backgroundSprite.x = (size.width - width * scale) * 0.5;
        backgroundSprite.y = (size.height - height * scale) * 0.5;
    }

    function resolveMotion(name) {
        var motion = String(name || "Idle").trim();
        return motionAliases[motion] || motion || "Idle";
    }

    function resolveExpression(name) {
        var expression = String(name || "Neutral").trim();
        if (/^F\d+$/i.test(expression)) return expression.toUpperCase();
        return expressionAliases[expression] || expression || "F01";
    }

    function createApplication() {
        if (!window.PIXI) {
            throw new Error("PIXI is not available");
        }
        if (window.PIXI.settings && window.PIXI.ENV) {
            window.PIXI.settings.PREFER_ENV = window.PIXI.ENV.WEBGL;
            window.PIXI.settings.FAIL_IF_MAJOR_PERFORMANCE_CAVEAT = false;
        }
        status.coreLoaded = !!window.Live2DCubismCore;
        canvas.style.width = "100vw";
        canvas.style.height = "100vh";
        canvas.style.opacity = "1";
        canvas.style.background = STAGE_BACKGROUND_CSS;
        if (fallbackImage) {
            fallbackImage.style.opacity = "1";
            fallbackImage.style.visibility = "visible";
            fallbackImage.style.background = STAGE_BACKGROUND_CSS;
        }
        if (presentCanvas) {
            presentCanvas.style.width = "100vw";
            presentCanvas.style.height = "100vh";
            presentCanvas.style.opacity = "0";
            presentCanvas.style.background = STAGE_BACKGROUND_CSS;
            presentCanvas.style.display = ENABLE_DOM_PRESENTER ? "block" : "none";
            presentCanvas.style.visibility = "hidden";
        }
        if (presentImage) {
            presentImage.style.width = "100vw";
            presentImage.style.height = "100vh";
            presentImage.style.opacity = "0";
            presentImage.style.background = STAGE_BACKGROUND_CSS;
            presentImage.style.visibility = "hidden";
            presentImage.style.display = ENABLE_DOM_PRESENTER ? "block" : "none";
            presentImage.removeAttribute("src");
        }
        document.documentElement.style.background = STAGE_BACKGROUND_CSS;
        document.body.style.background = STAGE_BACKGROUND_CSS;
        app = new PIXI.Application({
            view: canvas,
            resizeTo: window,
            autoDensity: true,
            resolution: Math.min(window.devicePixelRatio || 1, MAX_RESOLUTION),
            antialias: true,
            transparent: true,
            backgroundColor: STAGE_BACKGROUND_COLOR,
            backgroundAlpha: 0,
            clearBeforeRender: true,
            preserveDrawingBuffer: true,
            powerPreference: "high-performance",
            preferWebGLVersion: 1
        });
        app.stage.sortableChildren = true;
        app.stage.visible = true;
        app.stage.alpha = 1;
        if (app.renderer) {
            app.renderer.backgroundAlpha = 0;
            if (app.renderer.background && app.renderer.background.color) {
                app.renderer.background.color = STAGE_BACKGROUND_COLOR;
            }
        }
        status.frameworkLoaded = !!(window.PIXI.live2d && window.PIXI.live2d.Live2DModel);
        renderDiagnostics();

        var originalRender = app.render.bind(app);
        app.render = function guardedRender() {
            try {
                originalRender();
            } catch (error) {
                reportError(error, false);
            }
        };

        app.ticker.add(function () {
            try {
                tick();
            } catch (error) {
                reportError(error, false);
            }
        });
        if (typeof app.start === "function") app.start();
        if (app.ticker && typeof app.ticker.start === "function") app.ticker.start();
        if (window.PIXI.Ticker && window.PIXI.Ticker.shared) {
            window.PIXI.Ticker.shared.start();
        }

        canvas.addEventListener("pointerup", handlePointerUp, { passive: true });
        window.addEventListener("resize", function () {
            requestAnimationFrame(applyFit);
            requestAnimationFrame(fitBackground);
            requestRenderBurst(8);
            if (ENABLE_DOM_PRESENTER) {
                requestAnimationFrame(function () { copyWebGlFrameToPresenter(true); });
            }
        });
        if (ENABLE_DOM_PRESENTER) startPresenterLoop();
    }

    function notifyPresented(mode) {
        if (presenterReported) return;
        presenterReported = true;
        setFallbackVisible(true);
        post("onPresented", JSON.stringify({
            mode: mode || "raw-webgl",
            presenterFrames: presenterFrames,
            presenterImageFrames: presenterImageFrames
        }));
        renderDiagnostics();
    }

    function isModelPixel(r, g, b, a) {
        return a > 8;
    }

    function analyzePixelBuffer(pixels, width, height, stride) {
        var totalPixels = Math.max(1, width * height);
        var step = Math.max(1, stride || Math.floor(totalPixels / 4096));
        var sampled = 0;
        var alphaHits = 0;
        var colorHits = 0;
        var modelHits = 0;
        var brightHits = 0;
        var minX = width;
        var minY = height;
        var maxX = -1;
        var maxY = -1;
        for (var pixel = 0; pixel < totalPixels; pixel += step) {
            var offset = pixel * 4;
            var r = pixels[offset];
            var g = pixels[offset + 1];
            var b = pixels[offset + 2];
            var a = pixels[offset + 3];
            sampled += 1;
            if (a > 8) alphaHits += 1;
            if (r > 8 || g > 8 || b > 8) colorHits += 1;
            if (r > 80 || g > 80 || b > 80) brightHits += 1;
            if (isModelPixel(r, g, b, a)) {
                var x = pixel % width;
                var y = Math.floor(pixel / width);
                modelHits += 1;
                if (x < minX) minX = x;
                if (y < minY) minY = y;
                if (x > maxX) maxX = x;
                if (y > maxY) maxY = y;
            }
        }
        return {
            supported: true,
            width: width,
            height: height,
            sampled: sampled,
            alphaHits: alphaHits,
            colorHits: colorHits,
            modelHits: modelHits,
            brightHits: brightHits,
            modelPixelThreshold: MODEL_PIXEL_THRESHOLD,
            modelPixelReady: modelHits >= MODEL_PIXEL_THRESHOLD,
            modelBBox: modelHits > 0 ? { minX: minX, minY: minY, maxX: maxX, maxY: maxY } : null
        };
    }

    function setFallbackVisible(visible) {
        if (!fallbackImage) return;
        fallbackImage.style.opacity = visible ? "1" : "0";
        fallbackImage.style.visibility = visible ? "visible" : "hidden";
    }

    function setPresenterVisible(visible) {
        if (presentCanvas) {
            presentCanvas.style.display = "block";
            presentCanvas.style.opacity = visible ? "1" : "0";
            presentCanvas.style.visibility = visible ? "visible" : "hidden";
        }
        if (presentImage) {
            presentImage.style.display = "block";
            presentImage.style.opacity = visible ? "1" : "0";
            presentImage.style.visibility = visible ? "visible" : "hidden";
        }
        setFallbackVisible(true);
    }

    function ensurePresenterBuffers(width, height) {
        if (!presentCanvas || !presentContext || width <= 0 || height <= 0) return false;
        if (presenterWidth === width && presenterHeight === height && presenterReadBuffer && presenterImageData) {
            return true;
        }
        presenterWidth = width;
        presenterHeight = height;
        presentCanvas.width = width;
        presentCanvas.height = height;
        presenterReadBuffer = new Uint8Array(width * height * 4);
        presenterImageData = presentContext.createImageData(width, height);
        return true;
    }

    function copyWebGlFrameToPresenter(force) {
        if (!ENABLE_DOM_PRESENTER || !app || !app.renderer || !app.renderer.gl || !presentContext) return;
        try {
            var now = performance.now();
            if (!force && presenterFrames > 0 && now - presenterLastCopyAt < PRESENTER_FRAME_INTERVAL_MS) return;
            presenterLastCopyAt = now;
            var gl = app.renderer.gl;
            var width = Math.max(1, app.renderer.width || canvas.width || 1);
            var height = Math.max(1, app.renderer.height || canvas.height || 1);
            if (!ensurePresenterBuffers(width, height)) return;
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, presenterReadBuffer);
            latestPixelProbe = Object.assign(
                analyzePixelBuffer(presenterReadBuffer, width, height, Math.max(1, Math.floor(width * height / 4096))),
                { glError: gl.getError() }
            );
            if (!latestPixelProbe.modelPixelReady) {
                presenterLastError = "waiting-model-pixels";
                setPresenterVisible(false);
                renderDiagnostics();
                return;
            }

            var target = presenterImageData.data;
            var rowLength = width * 4;
            for (var y = 0; y < height; y += 1) {
                var sourceOffset = (height - y - 1) * rowLength;
                var targetOffset = y * rowLength;
                target.set(presenterReadBuffer.subarray(sourceOffset, sourceOffset + rowLength), targetOffset);
            }
            presentContext.putImageData(presenterImageData, 0, 0);
            presenterFrames += 1;
            presenterLastError = "";
            if (presentImage && now - presenterLastImageAt >= PRESENT_IMAGE_INTERVAL_MS) {
                presenterLastImageAt = now;
                presentImage.src = presentCanvas.toDataURL("image/png");
                presentImage.style.visibility = "visible";
                setPresenterVisible(true);
                presenterImageFrames += 1;
                notifyPresented("dom-presenter");
            }
            if (now - presenterLastDiagnosticsAt > 500) {
                presenterLastDiagnosticsAt = now;
                renderDiagnostics();
            }
        } catch (error) {
            presenterLastError = error && error.message ? error.message : String(error);
            debug("presenter failed: " + presenterLastError);
            renderDiagnostics();
        }
    }

    function startPresenterLoop() {
        if (presenterLoopId) return;
        function step() {
            copyWebGlFrameToPresenter();
            presenterLoopId = requestAnimationFrame(step);
        }
        presenterLoopId = requestAnimationFrame(step);
    }

    async function loadModel(nextSrc) {
        var resolvedSrc = normalizeModelSrc(nextSrc);
        if (model && resolvedSrc === modelSrc) {
            return;
        }
        debug("loadModel " + resolvedSrc);
        status.modelLoaded = false;
        status.drawableCount = 0;
        status.lastError = "";
        renderDiagnostics();
        var Live2DModel = window.PIXI && window.PIXI.live2d && window.PIXI.live2d.Live2DModel;
        if (!Live2DModel) {
            throw new Error("PIXI.live2d.Live2DModel is not available");
        }
        status.frameworkLoaded = true;
        if (typeof Live2DModel.registerTicker === "function" && window.PIXI.Ticker) {
            Live2DModel.registerTicker(window.PIXI.Ticker);
            window.PIXI.Ticker.shared.start();
        }

        modelSrc = resolvedSrc;
        if (model) {
            app.stage.removeChild(model);
            try {
                model.destroy({ children: true, texture: false, baseTexture: false });
            } catch (error) {
                debug("Model destroy skipped: " + error.message);
            }
            model = null;
        }

        model = await Live2DModel.from(resolvedSrc, {
            autoInteract: true,
            idleMotionGroup: "Idle"
        });
        resetModelTransform();
        model.interactive = interactive;
        model.visible = true;
        model.alpha = 1;
        model.zIndex = 10;
        app.stage.addChild(model);
        refreshModelBounds();
        status.modelLoaded = true;
        status.drawableCount = getDrawableCount();
        renderDiagnostics();
        model.on("hit", function (hitAreas) {
            debug("hit:" + JSON.stringify(hitAreas || []));
        });

        applyFit();
        applyExpression(state.expression, true);
        playMotion(state.motion, true);
        requestRenderBurst(RENDER_BURST_FRAMES);
        requestAnimationFrame(function () {
            try {
                app.render();
                var probe = sampleCanvasPixels();
                if (ENABLE_DOM_PRESENTER) {
                    copyWebGlFrameToPresenter(true);
                } else if (probe && probe.modelPixelReady) {
                    notifyPresented("raw-webgl");
                }
            } catch (error) {
                reportError(error, false);
            }
        });
        debug("model loaded " + resolvedSrc);
        post("onModelLoaded", JSON.stringify({
            modelSrc: resolvedSrc,
            bounds: modelBounds,
            fit: latestFit
        }));
    }

    function screenSize() {
        if (!app) return { width: window.innerWidth || 1, height: window.innerHeight || 1 };
        return {
            width: Math.max(1, app.screen.width || window.innerWidth || 1),
            height: Math.max(1, app.screen.height || window.innerHeight || 1)
        };
    }

    function applyFit() {
        if (!app || !model) return;
        var size = screenSize();
        var placement = state.placement || {};
        var userScale = clamp(Number(placement.scale || 1), 0.7, 1.35);
        var offsetX = clamp(Number(placement.offsetX || 0), -size.width * 0.25, size.width * 0.25);
        var offsetY = clamp(Number(placement.offsetY || 0), -size.height * 0.20, size.height * 0.08);
        var bottomInset = clamp(Number(placement.bottomInset || 0), 0, size.height * 0.28);
        var bounds = modelBounds && modelBounds.width > 1 && modelBounds.height > 1
            ? modelBounds
            : refreshModelBounds();
        var pivotX = bounds.x + bounds.width * 0.5;
        var pivotY = bounds.y + bounds.height;
        var heightRatio = state.stageMode === "dress" ? 0.78 : 0.72;
        var widthRatio = state.stageMode === "dress" ? 0.86 : 0.82;
        var availableHeight = Math.max(1, size.height - bottomInset);
        var fitScale = Math.min(
            availableHeight * heightRatio / bounds.height,
            size.width * widthRatio / bounds.width
        );
        var finalScale = Math.max(0.001, fitScale * userScale);

        if (model.pivot && typeof model.pivot.set === "function") model.pivot.set(pivotX, pivotY);
        model.scale.set(finalScale);
        model.x = size.width * 0.5 + offsetX;
        model.y = size.height - bottomInset + offsetY;
        latestFit = {
            bounds: {
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height
            },
            pivotX: pivotX,
            pivotY: pivotY,
            scale: finalScale,
            modelX: model.x,
            modelY: model.y,
            stageWidth: size.width,
            stageHeight: size.height
        };
        renderDiagnostics();
    }

    function requestRenderBurst(frames) {
        var remaining = frames || 1;
        function renderStep() {
            if (!app || remaining <= 0) return;
            remaining -= 1;
            fitBackground();
            applyFit();
            try {
                if (model && typeof model.update === "function") {
                    model.update(16);
                }
                app.render();
                copyWebGlFrameToPresenter();
            } catch (error) {
                reportError(error, false);
                return;
            }
            requestAnimationFrame(renderStep);
        }
        requestAnimationFrame(renderStep);
    }

    function handlePointerUp(event) {
        if (!interactive || !app) return;
        var rect = canvas.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        var size = screenSize();
        var x = (event.clientX - rect.left) / rect.width * size.width;
        var y = (event.clientY - rect.top) / rect.height * size.height;
        var normalizedX = clamp(x / size.width, 0, 1);
        var normalizedY = clamp(y / size.height, 0, 1);
        var hitAreas = collectHitAreas(x, y);

        targetFocusX = clamp((normalizedX - 0.5) * 2, -1, 1);
        targetFocusY = clamp((0.5 - normalizedY) * 2, -1, 1);

        post("onTap", JSON.stringify({
            x: x,
            y: y,
            normalizedX: normalizedX,
            normalizedY: normalizedY,
            hitAreas: hitAreas
        }));
    }

    function collectHitAreas(x, y) {
        if (!model || typeof model.hitTest !== "function") return [];
        try {
            var raw = model.hitTest(x, y) || [];
            var result = [];
            raw.forEach(function (name) {
                if (result.indexOf(name) < 0) result.push(name);
                var canonical = canonicalHitArea(name);
                if (canonical && result.indexOf(canonical) < 0) result.push(canonical);
            });
            return result;
        } catch (error) {
            debug("hitTest failed: " + error.message);
            return [];
        }
    }

    async function playMotion(name, force) {
        if (!model) return;
        var requested = String(name || "Idle");
        var resolved = resolveMotion(requested);
        if (!force && requested === lastMotion && state.commandNonce === lastCommandNonce) {
            return;
        }
        lastMotion = requested;
        lastCommandNonce = state.commandNonce;

        try {
            var ok = await model.motion(resolved);
            if (!ok && resolved !== "TapBody") {
                await model.motion("TapBody");
            }
        } catch (error) {
            if (resolved !== "TapBody") {
                try {
                    await model.motion("TapBody");
                } catch (fallbackError) {
                    debug("motion failed: " + fallbackError.message);
                }
            } else {
                debug("motion failed: " + error.message);
            }
        }
    }

    function applyExpression(name, force) {
        if (!model || typeof model.expression !== "function") return;
        var requested = String(name || "Neutral");
        var resolved = resolveExpression(requested);
        if (!force && requested === lastExpression) return;
        lastExpression = requested;
        try {
            model.expression(resolved);
        } catch (error) {
            if (resolved !== "F01") {
                try {
                    model.expression("F01");
                } catch (fallbackError) {
                    debug("expression failed: " + fallbackError.message);
                }
            } else {
                debug("expression failed: " + error.message);
            }
        }
    }

    function setParam(id, value) {
        if (!model || !model.internalModel || !model.internalModel.coreModel) return;
        var coreModel = model.internalModel.coreModel;
        try {
            if (typeof coreModel.setParameterValueById === "function") {
                coreModel.setParameterValueById(id, value);
            }
        } catch (error) {
            debug("param failed " + id + ": " + error.message);
        }
    }

    function tick() {
        if (!model) return;
        var now = performance.now();
        var t = now / 1000;
        var speakingTarget = state.nowSpeaking
            ? Math.max(Number(state.mouthOpenSize || 0), 0.18 + Math.sin(t * 18) * 0.08)
            : 0;

        mouthOpen += (clamp(speakingTarget, 0, 1) - mouthOpen) * 0.35;
        var idleX = Math.sin(t * 0.9) * 2.5;
        var idleY = Math.sin(t * 0.7) * 1.5;
        var focusX = Number(state.focusAt && state.focusAt.x || targetFocusX || 0);
        var focusY = Number(state.focusAt && state.focusAt.y || targetFocusY || 0);

        setParam("ParamMouthOpenY", mouthOpen);
        setParam("ParamAngleX", focusX * 18 + idleX);
        setParam("ParamAngleY", focusY * 10 + idleY);
        setParam("ParamAngleZ", focusX * -8);
        setParam("ParamBodyAngleX", focusX * 6 + Math.sin(t * 0.45) * 1.5);
        setParam("ParamBreath", 0.5 + Math.sin(t * 2.2) * 0.5);

        if (now >= nextBlinkMs) {
            blinkUntilMs = now + 160;
            nextBlinkMs = now + 2600 + Math.random() * 1800;
        }
        var eyeOpen = 1;
        if (blinkUntilMs > now) {
            var progress = 1 - (blinkUntilMs - now) / 160;
            eyeOpen = progress < 0.5 ? 1 - progress * 2 : (progress - 0.5) * 2;
        }
        setParam("ParamEyeLOpen", eyeOpen);
        setParam("ParamEyeROpen", eyeOpen);
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function setInteractive(enabled) {
        interactive = !!enabled;
        canvas.style.pointerEvents = interactive ? "auto" : "none";
        if (model) {
            model.interactive = interactive;
        }
    }

    async function setState(nextState) {
        try {
            state = Object.assign({}, state, nextState || {});
            state.placement = Object.assign({}, defaultState().placement, state.placement || {});
            state.focusAt = Object.assign({}, defaultState().focusAt, state.focusAt || {});
            applyStageClass(state.stageMode || "home");
            if (state.stageVersion && state.stageVersion !== STAGE_VERSION) {
                debug("stage version mismatch android=" + state.stageVersion + " web=" + STAGE_VERSION);
            }
            setBackground(state.backgroundSrc);
            setInteractive(!state.editable);
        } catch (error) {
            reportError(error, false);
        }

        try {
            await loadModel(state.modelSrc);
        } catch (error) {
            reportError(error, true);
            return;
        }

        try {
            fitBackground();
            applyFit();
            applyExpression(state.expression, false);
            playMotion(state.motion, false);
            requestRenderBurst(4);
        } catch (error) {
            reportError(error, false);
        }
    }

    function sampleCanvasPixels() {
        if (!app || !app.renderer || !app.renderer.gl) {
            return { supported: false, reason: "no-webgl-renderer" };
        }
        try {
            app.render();
            var gl = app.renderer.gl;
            var width = Math.max(1, app.renderer.width || canvas.width || 1);
            var height = Math.max(1, app.renderer.height || canvas.height || 1);
            var pixels = new Uint8Array(width * height * 4);
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            var totalPixels = width * height;
            latestPixelProbe = Object.assign(
                analyzePixelBuffer(pixels, width, height, Math.max(1, Math.floor(totalPixels / 4096))),
                { glError: gl.getError() }
            );
            renderDiagnostics();
            return latestPixelProbe;
        } catch (error) {
            latestPixelProbe = {
                supported: false,
                alphaHits: 0,
                colorHits: 0,
                modelHits: 0,
                brightHits: 0,
                reason: error.message || String(error)
            };
            renderDiagnostics();
            return latestPixelProbe;
        }
    }

    window.AiriLive2D = {
        setState: setState,
        playReaction: function (reaction) {
            reaction = reaction || {};
            if (reaction.expression) applyExpression(reaction.expression, true);
            if (reaction.motion) playMotion(reaction.motion, true);
        },
        setInteractive: setInteractive,
        getDebugState: function () {
            var probe = sampleCanvasPixels();
            return {
                stageVersion: STAGE_VERSION,
                requestedStageVersion: state.stageVersion || "",
                coreLoaded: status.coreLoaded,
                frameworkLoaded: status.frameworkLoaded,
                modelLoaded: status.modelLoaded,
                drawableCount: status.drawableCount,
                lastError: status.lastError,
                app: !!app,
                hasModel: !!model,
                modelSrc: modelSrc,
                backgroundSrc: backgroundSrc,
                backgroundLoaded: backgroundLoaded,
                webBackgroundDisabled: true,
                opaqueWebGlSurface: false,
                stageWidth: app ? app.screen.width : 0,
                stageHeight: app ? app.screen.height : 0,
                rendererWidth: app && app.renderer ? app.renderer.width : 0,
                rendererHeight: app && app.renderer ? app.renderer.height : 0,
                canvasWidth: canvas ? canvas.width : 0,
                canvasHeight: canvas ? canvas.height : 0,
                modelBounds: modelBounds,
                fit: latestFit,
                modelX: model ? model.x : 0,
                modelY: model ? model.y : 0,
                modelScaleX: model ? model.scale.x : 0,
                modelScaleY: model ? model.scale.y : 0,
                modelPivotX: model && model.pivot ? model.pivot.x : 0,
                modelPivotY: model && model.pivot ? model.pivot.y : 0,
                modelAlpha: model ? model.alpha : 0,
                modelVisible: model ? model.visible : false,
                domPresenterEnabled: ENABLE_DOM_PRESENTER,
                presenterCanvas: !!presentCanvas,
                presenterImage: !!presentImage,
                presenterFrames: presenterFrames,
                presenterImageFrames: presenterImageFrames,
                presenterLastError: presenterLastError,
                presenterWidth: presenterWidth,
                presenterHeight: presenterHeight,
                modelTransform: {
                    x: model ? model.x : 0,
                    y: model ? model.y : 0,
                    scaleX: model ? model.scale.x : 0,
                    scaleY: model ? model.scale.y : 0
                },
                stageChildren: app ? app.stage.children.length : 0,
                pixelProbe: probe,
                canvasPixelProbe: probe
            };
        }
    };

    renderDiagnostics();
    applyStageClass(state.stageMode);

    try {
        debug("boot");
        createApplication();
        setBackground(state.backgroundSrc);
        setState(state);
        post("onReady", "");
    } catch (error) {
        reportError(error, true);
    }
})();
