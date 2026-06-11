package com.aigalgame.demo

import android.annotation.SuppressLint
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.zIndex
import androidx.webkit.WebViewAssetLoader
import kotlinx.coroutines.delay
import org.json.JSONObject

private val Live2DStageBaseHeight = 650.dp
private const val Live2DWebTag = "Live2DWebStage"
private const val Live2DWebStageVersion = "pixi-cubism-runtime-v11-presenter-primary"
private const val Live2DWebStageUrl = "https://appassets.androidplatform.net/assets/live2d-web/index.html?v=pixi-cubism-runtime-v11-presenter-primary"
private const val Live2DWebClassroomBackground = "live2d-web/backgrounds/classroom.png"
private const val Live2DDefaultModelAssetPath = "live2d/models/Haru/Haru.model3.json"
private const val Live2DWebSurfaceColor = 0x00000000
private const val Live2DPresenterModelPixelThreshold = 64

@Composable
fun Live2DStage(
    background: String,
    character: String,
    emotion: String,
    pose: String,
    placement: OutfitPlacement,
    speechState: Live2DSpeechState = Live2DSpeechState(),
    line: DialogueLine? = null,
    editable: Boolean = false,
    onPlacementChange: ((OutfitPlacement) -> Unit)? = null,
    onReaction: (Live2DReaction) -> Unit = {},
    relation: RelationState = RelationState(),
    modifier: Modifier = Modifier,
    baseHeight: Dp = Live2DStageBaseHeight,
    stageMode: String = "home"
) {
    val context = LocalContext.current
    val controller = remember(character) { Live2DController(context, character) }
    val state = controller.state
    val density = LocalDensity.current
    val stagePlacement = placement.coerceForStage()
    var gesturePlacement by remember(stagePlacement) { mutableStateOf(stagePlacement) }
    var webStageFailed by remember(character) { mutableStateOf(false) }
    var webStagePresented by remember(character) { mutableStateOf(false) }

    LaunchedEffect(character, line?.id, emotion, pose) {
        if (line != null) {
            controller.applyLine(line)
        } else {
            controller.applyPreview(emotion, pose)
        }
    }
    LaunchedEffect(speechState.active, speechState.mouthOpen) {
        controller.updateSpeech(speechState)
    }
    LaunchedEffect(state.modelAssetPath) {
        webStageFailed = false
        webStagePresented = false
    }
    LaunchedEffect(state.canRenderLive2D, state.modelAssetPath, webStagePresented) {
        if (state.canRenderLive2D && !webStagePresented) {
            delay(4500L)
            if (!webStagePresented) {
                Log.e(Live2DWebTag, "Live2D presenter trust timed out; keeping WebView visible")
            }
        }
    }
    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        if (!editable || onPlacementChange == null) return@rememberTransformableState
        val dx = with(density) { panChange.x.toDp().value }
        val dy = with(density) { panChange.y.toDp().value }
        val next = gesturePlacement.copy(
            scale = gesturePlacement.scale * zoomChange,
            offsetX = gesturePlacement.offsetX + dx,
            offsetY = gesturePlacement.offsetY + dy
        ).coerceForStage()
        gesturePlacement = next
        onPlacementChange(next)
    }

    fun handleStageTap(normalizedX: Float, normalizedY: Float) {
        val reaction = controller.handleTap(
            normalizedX = normalizedX.coerceIn(0f, 1f),
            normalizedY = normalizedY.coerceIn(0f, 1f),
            relation = relation,
            nowMs = System.currentTimeMillis()
        )
        if (reaction != null) onReaction(reaction)
    }

    val useWebStage = state.canRenderLive2D && !webStageFailed

    Box(modifier) {
        SakuraSceneBackground(background)
        if (useWebStage) {
            Live2DWebStage(
                state = state,
                background = background,
                placement = stagePlacement,
                editable = editable,
                stageMode = stageMode,
                onTap = { normalizedX, normalizedY -> handleStageTap(normalizedX, normalizedY) },
                onError = {
                    Log.e(Live2DWebTag, it)
                    webStageFailed = true
                },
                onPresented = { report ->
                    webStagePresented = true
                    Log.d(
                        Live2DWebTag,
                        "presented ${report.mode} frames=${report.presenterCanvasFrames} pixels=${report.modelPixelHits}"
                    )
                },
                modifier = Modifier
                    .fillMaxSize()
                    .zIndex(1f)
            )
        }
        if (editable && onPlacementChange != null) {
            Box(
                Modifier
                    .fillMaxSize()
                    .zIndex(4f)
                    .transformable(
                        state = transformState,
                        lockRotationOnZoomPan = true,
                        enabled = true
                    )
            )
        }
    }
}

@Composable
fun Live2DSelfTestStage(modifier: Modifier = Modifier) {
    val state = remember {
        Live2DRenderState(
            character = "selftest",
            modelAssetPath = Live2DDefaultModelAssetPath,
            modelAssetPresent = true,
            rendererAvailable = true,
            statusMessage = "Live2D Pixi runtime self test"
        )
    }
    Box(modifier) {
        Live2DWebStage(
            state = state,
            background = "classroom",
            placement = OutfitPlacement(scale = 1.0f, offsetY = 0f, bottomInset = 0f),
            editable = false,
            stageMode = "selftest",
            onTap = { _, _ -> },
            onError = { Log.e(Live2DWebTag, it) },
            onPresented = { report ->
                Log.d(
                    Live2DWebTag,
                    "selftest presented ${report.mode} frames=${report.presenterCanvasFrames} pixels=${report.modelPixelHits}"
                )
            },
            modifier = Modifier
                .fillMaxSize()
                .zIndex(1f)
        )
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun Live2DWebStage(
    state: Live2DRenderState,
    background: String,
    placement: OutfitPlacement,
    editable: Boolean,
    stageMode: String,
    onTap: (Float, Float) -> Unit,
    onError: (String) -> Unit,
    onPresented: (Live2DPresentationReport) -> Unit,
    modifier: Modifier = Modifier
) {
    var ready by remember { mutableStateOf(false) }
    val latestOnTap = rememberUpdatedState(onTap)
    val latestOnError = rememberUpdatedState(onError)
    val latestOnPresented = rememberUpdatedState(onPresented)
    val backgroundAssetPath = remember(background) { live2DWebBackgroundAssetPath(background) }
    val webState = remember(state, placement, editable, stageMode, backgroundAssetPath) {
        state.toLive2DWebStateJson(
            placement = placement,
            editable = editable,
            stageMode = stageMode,
            backgroundAssetPath = backgroundAssetPath
        )
    }

    AndroidView(
        factory = { context ->
            if (BuildConfig.DEBUG) {
                WebView.setWebContentsDebuggingEnabled(true)
            }
            val assetLoader = WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context))
                .build()
            Live2DStageWebView(context).apply {
                setBackgroundColor(Live2DWebSurfaceColor)
                this.background?.alpha = 0
                setLayerType(View.LAYER_TYPE_HARDWARE, null)
                alpha = 1f
                visibility = View.VISIBLE
                setWillNotDraw(false)
                clipToOutline = false
                clearCache(true)
                isVerticalScrollBarEnabled = false
                isHorizontalScrollBarEnabled = false
                overScrollMode = View.OVER_SCROLL_NEVER
                isLongClickable = false
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.allowFileAccess = true
                settings.allowContentAccess = true
                settings.cacheMode = WebSettings.LOAD_NO_CACHE
                webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(
                        view: WebView?,
                        request: WebResourceRequest?
                    ): WebResourceResponse? {
                        return request?.url?.let { assetLoader.shouldInterceptRequest(it) }
                    }

                    override fun onPageFinished(view: WebView?, url: String?) {
                        Log.i(Live2DWebTag, "page finished: ${url.orEmpty()}")
                        (view as? Live2DStageWebView)?.retryLastStateAfterPageFinished()
                    }

                    override fun onReceivedError(
                        view: WebView?,
                        request: WebResourceRequest?,
                        error: WebResourceError?
                    ) {
                        if (request?.isForMainFrame == true) {
                            latestOnError.value(
                                error?.description?.toString().orEmpty()
                                    .ifBlank { "WebView load error" }
                            )
                        }
                    }
                }
                webChromeClient = object : WebChromeClient() {
                    override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
                        consoleMessage?.let {
                            Log.d(
                                Live2DWebTag,
                                "${it.message()} @ ${it.sourceId()}:${it.lineNumber()}"
                            )
                        }
                        return true
                    }
                }
                addJavascriptInterface(
                    Live2DWebBridge(
                        onReady = { ready = true },
                        onTap = { normalizedX, normalizedY -> latestOnTap.value(normalizedX, normalizedY) },
                        onError = { latestOnError.value(it) },
                        onPresented = { report -> latestOnPresented.value(report) },
                        onDebug = { Log.d(Live2DWebTag, it) }
                    ),
                    "AndroidLive2D"
                )
                loadUrl(Live2DWebStageUrl)
            }
        },
        update = { webView ->
            webView.alpha = 1f
            webView.visibility = View.VISIBLE
            webView.setBackgroundColor(Live2DWebSurfaceColor)
            webView.background?.alpha = 0
            webView.invalidate()
            webView.isClickable = !editable
            val setStateScript = "window.AiriLive2D && window.AiriLive2D.setState($webState);"
            (webView as Live2DStageWebView).submitLive2DState(setStateScript)
            if (ready) {
                webView.evaluateJavascript(
                    "window.AiriLive2D && window.AiriLive2D.setInteractive(${(!editable).toString()});",
                    null
                )
            }
        },
        modifier = modifier
    )
}

private class Live2DStageWebView(context: Context) : WebView(context) {
    private var lastStateScript: String = ""
    private var bootRetriesScheduled = false

    fun submitLive2DState(script: String) {
        lastStateScript = script
        evaluateJavascript(script, null)
        if (!bootRetriesScheduled) {
            bootRetriesScheduled = true
            scheduleStateRetry(250L)
            scheduleStateRetry(1000L)
            scheduleStateRetry(2000L)
            scheduleStateRetry(4000L)
        }
    }

    fun retryLastStateAfterPageFinished() {
        scheduleStateRetry(0L)
        scheduleStateRetry(250L)
        scheduleStateRetry(1000L)
        scheduleDebugStateProbe(1500L)
        scheduleDebugStateProbe(5000L)
        scheduleDebugStateProbe(9000L)
    }

    private fun scheduleStateRetry(delayMs: Long) {
        postDelayed(
            {
                if (lastStateScript.isNotBlank()) {
                    evaluateJavascript(lastStateScript, null)
                }
            },
            delayMs
        )
    }

    private fun scheduleDebugStateProbe(delayMs: Long) {
        postDelayed(
            {
                evaluateJavascript(
                    "window.AiriLive2D && JSON.stringify(window.AiriLive2D.getDebugState && window.AiriLive2D.getDebugState());"
                ) { result ->
                    Log.i(Live2DWebTag, "debug state ${delayMs}ms: $result")
                }
            },
            delayMs
        )
    }
}

private class Live2DWebBridge(
    private val onReady: () -> Unit,
    private val onTap: (Float, Float) -> Unit,
    private val onError: (String) -> Unit,
    private val onPresented: (Live2DPresentationReport) -> Unit,
    private val onDebug: (String) -> Unit
) {
    private val mainHandler = Handler(Looper.getMainLooper())

    @JavascriptInterface
    fun onReady(payload: String?) {
        mainHandler.post {
            onDebug("ready")
            onReady()
        }
    }

    @JavascriptInterface
    fun onModelLoaded(payload: String?) {
        mainHandler.post { onDebug("model loaded: ${payload.orEmpty()}") }
    }

    @JavascriptInterface
    fun onError(message: String?) {
        mainHandler.post { onError(message.orEmpty().ifBlank { "Live2D Web stage error" }) }
    }

    @JavascriptInterface
    fun onPresented(payload: String?) {
        mainHandler.post {
            onDebug("presented: ${payload.orEmpty()}")
            val report = Live2DPresentationReport.fromPayload(payload)
            if (report.isTrusted) {
                onPresented(report)
            } else {
                onDebug("presented ignored: ${report.reason}")
            }
        }
    }

    @JavascriptInterface
    fun onDebug(message: String?) {
        mainHandler.post { onDebug(message.orEmpty()) }
    }

    @JavascriptInterface
    fun onTap(payload: String?) {
        val json = runCatching { JSONObject(payload.orEmpty()) }.getOrNull() ?: return
        val normalizedX = json.optDouble("normalizedX", 0.5).toFloat()
        val normalizedY = json.optDouble("normalizedY", 0.5).toFloat()
        mainHandler.post { onTap(normalizedX, normalizedY) }
    }
}

private data class Live2DPresentationReport(
    val mode: String,
    val stageVersion: String,
    val presenterCanvasFrames: Int,
    val presenterImageFrames: Int,
    val modelPixelHits: Int,
    val modelPixelReady: Boolean,
    val presenterWidth: Int,
    val presenterHeight: Int,
    val stageWidth: Int,
    val stageHeight: Int,
    val reason: String
) {
    val isTrusted: Boolean
        get() = reason.isBlank()

    companion object {
        fun fromPayload(payload: String?): Live2DPresentationReport {
            val json = runCatching { JSONObject(payload.orEmpty()) }.getOrNull()
                ?: return invalid("invalid-json")
            val pixelProbe = json.optJSONObject("pixelProbe")
            val report = Live2DPresentationReport(
                mode = json.optString("mode"),
                stageVersion = json.optString("stageVersion"),
                presenterCanvasFrames = json.optInt(
                    "presenterCanvasFrames",
                    json.optInt("presenterFrames", 0)
                ),
                presenterImageFrames = json.optInt("presenterImageFrames", 0),
                modelPixelHits = json.optInt(
                    "modelPixelHits",
                    pixelProbe?.optInt("modelHits", 0) ?: 0
                ),
                modelPixelReady = json.optBoolean(
                    "modelPixelReady",
                    pixelProbe?.optBoolean("modelPixelReady", false) ?: false
                ),
                presenterWidth = json.optInt("presenterWidth", 0),
                presenterHeight = json.optInt("presenterHeight", 0),
                stageWidth = json.optInt("stageWidth", 0),
                stageHeight = json.optInt("stageHeight", 0),
                reason = ""
            )
            return report.copy(reason = report.validationFailure())
        }

        private fun invalid(reason: String): Live2DPresentationReport {
            return Live2DPresentationReport(
                mode = "",
                stageVersion = "",
                presenterCanvasFrames = 0,
                presenterImageFrames = 0,
                modelPixelHits = 0,
                modelPixelReady = false,
                presenterWidth = 0,
                presenterHeight = 0,
                stageWidth = 0,
                stageHeight = 0,
                reason = reason
            )
        }
    }

    private fun validationFailure(): String {
        return when {
            stageVersion != Live2DWebStageVersion -> "stage-version-$stageVersion"
            mode != "dom-presenter" -> "mode-$mode"
            presenterCanvasFrames <= 0 -> "no-presenter-canvas-frame"
            !modelPixelReady -> "model-pixels-not-ready"
            modelPixelHits < Live2DPresenterModelPixelThreshold -> "model-pixels-$modelPixelHits"
            presenterWidth <= 0 || presenterHeight <= 0 -> "presenter-size-${presenterWidth}x$presenterHeight"
            stageWidth <= 0 || stageHeight <= 0 -> "stage-size-${stageWidth}x$stageHeight"
            else -> ""
        }
    }
}

private fun Live2DRenderState.toLive2DWebStateJson(
    placement: OutfitPlacement,
    editable: Boolean,
    stageMode: String,
    backgroundAssetPath: String
): String {
    val placementJson = JSONObject()
        .put("scale", placement.scale.toDouble())
        .put("offsetX", placement.offsetX.toDouble())
        .put("offsetY", placement.offsetY.toDouble())
        .put("bottomInset", placement.bottomInset.toDouble())
    val focusJson = JSONObject()
        .put("x", lookX.toDouble())
        .put("y", lookY.toDouble())
    return JSONObject()
        .put("stageVersion", Live2DWebStageVersion)
        .put("modelSrc", modelAssetPath)
        .put("backgroundSrc", backgroundAssetPath)
        .put("expression", expression)
        .put("motion", motion)
        .put("commandNonce", commandNonce)
        .put("idleMotionEnabled", false)
        .put("nowSpeaking", nowSpeaking)
        .put("mouthOpenSize", mouthOpen.toDouble())
        .put("focusAt", focusJson)
        .put("placement", placementJson)
        .put("stageMode", stageMode)
        .put("editable", editable)
        .toString()
}

private fun live2DWebBackgroundAssetPath(background: String): String {
    return ""
}
