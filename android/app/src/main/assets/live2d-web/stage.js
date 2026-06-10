(function () {
    "use strict";

    var MODEL_BASE_URL = new URL("../", window.location.href).href;
    var DEFAULT_MODEL = MODEL_BASE_URL + "live2d/samples/Haru/Haru.model3.json";
    var DEFAULT_BACKGROUND = MODEL_BASE_URL + "live2d-web/backgrounds/classroom.png";
    var STAGE_VERSION = "pixi-cubism4-runtime-v3";
    var MAX_RESOLUTION = 2;
    var RENDER_BURST_FRAMES = 36;
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
    var backgroundLayer = document.getElementById("stage-background");
    var diagnostics = {
        core: document.getElementById("core"),
        framework: document.getElementById("framework"),
        model: document.getElementById("model"),
        drawables: document.getElementById("drawables"),
        pixelAlpha: document.getElementById("pixel-alpha"),
        version: document.getElementById("version"),
        error: document.getElementById("error")
    };
    var app = null;
    var model = null;
    var backgroundSprite = null;
    var modelSrc = "";
    var backgroundSrc = "";
    var backgroundLoaded = false;
    var modelBaseWidth = 1;
    var modelBaseHeight = 1;
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
    var latestPixelProbe = { supported: false, alphaHits: 0, colorHits: 0 };
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

    function renderDiagnostics() {
        setText(diagnostics.core, status.coreLoaded ? "yes" : "no", status.coreLoaded);
        setText(diagnostics.framework, status.frameworkLoaded ? "yes" : "no", status.frameworkLoaded);
        setText(diagnostics.model, status.modelLoaded ? "yes" : "no", status.modelLoaded);
        setText(diagnostics.drawables, status.drawableCount || 0);
        setText(diagnostics.pixelAlpha, latestPixelProbe.alphaHits || 0, (latestPixelProbe.alphaHits || 0) > 0);
        setText(diagnostics.version, STAGE_VERSION);
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

    function debug(message) {
        try {
            console.log("[AiriLive2D] " + message);
        } catch (_) {
        }
        post("onDebug", message);
    }

    function reportError(error) {
        var message = error && error.message ? error.message : String(error);
        status.lastError = message;
        renderDiagnostics();
        try {
            console.error("[AiriLive2D] " + message);
        } catch (_) {
        }
        post("onError", message);
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
        if (!src) return DEFAULT_BACKGROUND;
        if (/^(file|https?):\/\//i.test(src)) return src;
        return MODEL_BASE_URL + src.replace(/^\/+/, "");
    }

    function setBackground(nextSrc) {
        var resolvedSrc = normalizeBackgroundSrc(nextSrc);
        if (resolvedSrc === backgroundSrc && backgroundLoaded) {
            return;
        }
        backgroundSrc = resolvedSrc;
        backgroundLoaded = false;
        if (backgroundLayer) {
            var cssUrl = resolvedSrc.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
            backgroundLayer.style.backgroundImage = 'url("' + cssUrl + '")';
        }

        var image = new Image();
        image.onload = function () {
            backgroundLoaded = true;
            debug("background loaded " + resolvedSrc);
            setPixiBackground(resolvedSrc);
        };
        image.onerror = function () {
            backgroundLoaded = false;
            debug("background failed " + resolvedSrc);
        };
        image.src = resolvedSrc;
    }

    function setPixiBackground(src) {
        if (!app || !window.PIXI) return;
        if (backgroundSprite) {
            app.stage.removeChild(backgroundSprite);
            try {
                backgroundSprite.destroy({ children: true, texture: false, baseTexture: false });
            } catch (_) {
            }
            backgroundSprite = null;
        }

        var texture = window.PIXI.Texture.from(src);
        backgroundSprite = new window.PIXI.Sprite(texture);
        backgroundSprite.zIndex = -100;
        backgroundSprite.alpha = 1;
        backgroundSprite.visible = true;
        app.stage.addChild(backgroundSprite);

        function markReady() {
            backgroundLoaded = true;
            fitBackground();
            requestRenderBurst(12);
        }

        if (texture.baseTexture.valid) {
            markReady();
        } else {
            texture.baseTexture.once("loaded", markReady);
            texture.baseTexture.once("error", function () {
                backgroundLoaded = false;
                debug("pixi background failed " + src);
            });
        }
    }

    function fitBackground() {
        if (!app || !backgroundSprite) return;
        var size = screenSize();
        var textureWidth = backgroundSprite.texture && backgroundSprite.texture.width || 1;
        var textureHeight = backgroundSprite.texture && backgroundSprite.texture.height || 1;
        var scale = Math.max(size.width / textureWidth, size.height / textureHeight);
        backgroundSprite.scale.set(scale);
        backgroundSprite.x = (size.width - textureWidth * scale) * 0.5;
        backgroundSprite.y = (size.height - textureHeight * scale) * 0.5;
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
        status.coreLoaded = !!window.Live2DCubismCore;
        canvas.style.width = "100vw";
        canvas.style.height = "100vh";
        canvas.style.opacity = "1";
        app = new PIXI.Application({
            view: canvas,
            resizeTo: window,
            autoDensity: true,
            resolution: Math.min(window.devicePixelRatio || 1, MAX_RESOLUTION),
            antialias: true,
            transparent: true,
            backgroundColor: 0x000000,
            backgroundAlpha: 0,
            clearBeforeRender: true,
            preserveDrawingBuffer: true,
            powerPreference: "high-performance"
        });
        app.stage.sortableChildren = true;
        app.stage.visible = true;
        app.stage.alpha = 1;
        if (app.renderer) {
            app.renderer.backgroundAlpha = 0;
        }
        status.frameworkLoaded = !!(window.PIXI.live2d && window.PIXI.live2d.Live2DModel);
        renderDiagnostics();

        var originalRender = app.render.bind(app);
        app.render = function guardedRender() {
            try {
                originalRender();
            } catch (error) {
                reportError(error);
                app.stop();
            }
        };

        app.ticker.add(function () {
            try {
                tick();
            } catch (error) {
                reportError(error);
                app.stop();
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
        });
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
        model.anchor.set(0.5, 0.5);
        model.interactive = interactive;
        model.visible = true;
        model.alpha = 1;
        model.zIndex = 10;
        modelBaseWidth = Math.max(1, model.width || 1);
        modelBaseHeight = Math.max(1, model.height || 1);
        status.modelLoaded = true;
        status.drawableCount = getDrawableCount();
        renderDiagnostics();
        model.on("hit", function (hitAreas) {
            debug("hit:" + JSON.stringify(hitAreas || []));
        });

        app.stage.addChild(model);
        applyFit();
        applyExpression(state.expression, true);
        playMotion(state.motion, true);
        requestRenderBurst(RENDER_BURST_FRAMES);
        debug("model loaded " + resolvedSrc);
        post("onModelLoaded", JSON.stringify({
            modelSrc: resolvedSrc,
            width: modelBaseWidth,
            height: modelBaseHeight
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
        var userScale = Number(placement.scale || 1);
        var offsetX = Number(placement.offsetX || 0);
        var offsetY = Number(placement.offsetY || 0);
        var bottomInset = Number(placement.bottomInset || 0);
        var fitScale = Math.min(
            size.height / modelBaseHeight * 2,
            size.width / modelBaseWidth * 2
        );
        var startingOffsetY = state.stageMode === "dress"
            ? 0.88
            : (size.height >= size.width ? 0.75 : 1.0);

        model.scale.set(fitScale * userScale);
        model.x = size.width * 0.5 + offsetX;
        model.y = size.height * startingOffsetY + offsetY - bottomInset;
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
            } catch (error) {
                reportError(error);
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
            await loadModel(state.modelSrc);
            fitBackground();
            applyFit();
            applyExpression(state.expression, false);
            playMotion(state.motion, false);
            requestRenderBurst(4);
        } catch (error) {
            reportError(error);
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
            var stride = Math.max(1, Math.floor(totalPixels / 4096));
            var sampled = 0;
            var alphaHits = 0;
            var colorHits = 0;
            for (var pixel = 0; pixel < totalPixels; pixel += stride) {
                var offset = pixel * 4;
                sampled += 1;
                if (pixels[offset + 3] > 8) alphaHits += 1;
                if (pixels[offset] > 8 || pixels[offset + 1] > 8 || pixels[offset + 2] > 8) {
                    colorHits += 1;
                }
            }
            latestPixelProbe = {
                supported: true,
                width: width,
                height: height,
                sampled: sampled,
                alphaHits: alphaHits,
                colorHits: colorHits,
                glError: gl.getError()
            };
            renderDiagnostics();
            return latestPixelProbe;
        } catch (error) {
            latestPixelProbe = { supported: false, alphaHits: 0, colorHits: 0, reason: error.message || String(error) };
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
                app: !!app,
                hasModel: !!model,
                modelLoaded: !!model,
                modelSrc: modelSrc,
                backgroundSrc: backgroundSrc,
                backgroundLoaded: backgroundLoaded,
                hasPixiBackground: !!backgroundSprite,
                pixiBackgroundVisible: backgroundSprite ? backgroundSprite.visible : false,
                pixiBackgroundWidth: backgroundSprite ? backgroundSprite.width : 0,
                pixiBackgroundHeight: backgroundSprite ? backgroundSprite.height : 0,
                stageWidth: app ? app.screen.width : 0,
                stageHeight: app ? app.screen.height : 0,
                rendererWidth: app && app.renderer ? app.renderer.width : 0,
                rendererHeight: app && app.renderer ? app.renderer.height : 0,
                canvasWidth: canvas ? canvas.width : 0,
                canvasHeight: canvas ? canvas.height : 0,
                modelWidth: modelBaseWidth,
                modelHeight: modelBaseHeight,
                modelX: model ? model.x : 0,
                modelY: model ? model.y : 0,
                modelScaleX: model ? model.scale.x : 0,
                modelScaleY: model ? model.scale.y : 0,
                modelAlpha: model ? model.alpha : 0,
                modelVisible: model ? model.visible : false,
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

    try {
        debug("boot");
        createApplication();
        setBackground(state.backgroundSrc);
        setState(state);
        post("onReady", "");
    } catch (error) {
        reportError(error);
    }
})();
