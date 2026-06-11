package com.aigalgame.demo

import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.max
import kotlin.math.roundToInt
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.zIndex
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.aigalgame.demo.live2d.OfficialLive2DRendererStatus
import com.aigalgame.demo.live2d.OfficialLive2DView
import java.util.concurrent.atomic.AtomicReference

private val Live2DStageBaseHeight = 650.dp
private const val Live2DTag = "OfficialLive2DStage"

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
    stageMode: String = "home",
    showCharacter: Boolean = true,
    live2DVisible: Boolean = true,
    onRendererStatus: (OfficialLive2DRendererStatus) -> Unit = {},
    tapBridge: Live2DTapBridge? = null,
    useInternalTapLayer: Boolean = true
) {
    val context = LocalContext.current
    val controller = remember(character) { Live2DController(context, character) }
    val nekoController = remember { Live2DController(context, Live2DCharacterConfigs.DefaultCharacter) }
    val state = controller.state
    val nekoState = nekoController.state
    val density = LocalDensity.current
    val initialPlacement = placement.coerceForStage()
    var gesturePlacement by remember(character, initialPlacement) { mutableStateOf(initialPlacement) }
    val effectivePlacement = if (editable) gesturePlacement else initialPlacement
    var live2dFailed by remember { mutableStateOf(false) }
    var live2dReady by remember { mutableStateOf(false) }
    var lastRendererError by remember { mutableStateOf("") }

    LaunchedEffect(character, line?.id, emotion, pose) {
        if (line != null) {
            controller.applyLine(line)
            if (character != Live2DCharacterConfigs.DefaultCharacter) {
                nekoController.applyLine(line)
            }
        } else {
            controller.applyPreview(emotion, pose)
            if (character != Live2DCharacterConfigs.DefaultCharacter) {
                nekoController.applyPreview(emotion, pose)
            }
        }
    }
    LaunchedEffect(speechState.active, speechState.mouthOpen) {
        controller.updateSpeech(speechState)
        if (character != Live2DCharacterConfigs.DefaultCharacter) {
            nekoController.updateSpeech(speechState)
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
            placement = effectivePlacement,
            relation = relation,
            nowMs = System.currentTimeMillis()
        )
        if (reaction != null) onReaction(reaction)
    }

    DisposableEffect(tapBridge) {
        tapBridge?.setHandler { normalizedX, normalizedY ->
            handleStageTap(normalizedX, normalizedY)
        }
        onDispose {
            tapBridge?.setHandler(null)
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            kotlinx.coroutines.delay(50L)
            val nowMs = System.currentTimeMillis()
            controller.tickLookDecay(nowMs)
            if (character != Live2DCharacterConfigs.DefaultCharacter) {
                nekoController.tickLookDecay(nowMs)
            }
        }
    }

    val hostState = if (state.character == Live2DCharacterConfigs.DefaultCharacter && state.rendererMode == CharacterRendererMode.Live2D) {
        state
    } else {
        nekoState
    }
    val hostPlacement = if (state.character == Live2DCharacterConfigs.DefaultCharacter) {
        effectivePlacement
    } else {
        defaultOutfitPlacement(Live2DCharacterConfigs.DefaultCharacter).coerceForStage()
    }
    val hostCanRenderLive2D = hostState.canRenderLive2D && !live2dFailed
    val showLive2DCharacter = showCharacter && live2DVisible && hostCanRenderLive2D && state.character == Live2DCharacterConfigs.DefaultCharacter
    val fallbackCharacter = if (state.rendererMode == CharacterRendererMode.Live2D) {
        state.staticFallbackCharacter
    } else {
        state.character
    }

    BoxWithConstraints(modifier) {
        SakuraSceneBackground(background)
        if (hostCanRenderLive2D) {
            OfficialLive2DAndroidStage(
                command = hostState.toCommand(hostPlacement, interactive = showLive2DCharacter && !editable),
                visible = showLive2DCharacter,
                onTap = { normalizedX, normalizedY -> handleStageTap(normalizedX, normalizedY) },
                onStatus = { status ->
                    if (status.modelLoaded && status.drawableCount > 0) {
                        live2dReady = true
                    }
                    onRendererStatus(status)
                    Log.d(Live2DTag, "${stageMode}: ${status.summary()}")
                },
                onError = { error ->
                    lastRendererError = error
                    live2dFailed = true
                    Log.e(Live2DTag, error)
                },
                modifier = Modifier
                    .fillMaxSize()
                    .zIndex(1f)
            )
            if (
                useInternalTapLayer &&
                showLive2DCharacter &&
                live2dReady &&
                !editable
            ) {
                StageTapLayer(
                    onTap = { normalizedX, normalizedY -> handleStageTap(normalizedX, normalizedY) },
                    modifier = Modifier
                        .fillMaxSize()
                        .zIndex(1.2f)
                )
            }
        }
        if (showLive2DCharacter && !live2dReady && !live2dFailed) {
            Live2DLoadingLayer(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 110.dp)
                    .zIndex(2f)
            )
        }
        if (showCharacter && (!showLive2DCharacter || live2dFailed)) {
            StaticCharacterStageLayer(
                character = fallbackCharacter,
                emotion = expressionToEmotion(state.expression),
                pose = pose,
                placement = effectivePlacement,
                baseHeight = baseHeight,
                interactive = !editable,
                onTap = { normalizedX, normalizedY -> handleStageTap(normalizedX, normalizedY) },
                modifier = Modifier
                    .fillMaxSize()
                    .zIndex(if (hostCanRenderLive2D) 2f else 1f)
            )
            if (lastRendererError.isNotBlank() && (stageMode == "selftest" || stageMode == "home")) {
                Text(
                    text = lastRendererError,
                    color = Color.White,
                    fontSize = 12.sp,
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(10.dp)
                        .background(Color(0x99000000))
                        .padding(8.dp)
                        .zIndex(3f)
                )
            }
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
    var status by remember { mutableStateOf<OfficialLive2DRendererStatus?>(null) }
    var error by remember { mutableStateOf("") }
    val command = remember {
        Live2DRenderCommand(
            characterId = "neko",
            emotion = "happy",
            motion = "selftest",
            mouthOpen = 0.55f,
            speaking = true,
            lookX = 0.25f,
            lookY = 0.05f,
            placement = OutfitPlacement(scale = 1.08f, offsetY = -10f, bottomInset = 24f),
            interactive = true,
            commandNonce = 1L
        )
    }
    Box(modifier) {
        SakuraSceneBackground("classroom")
        OfficialLive2DAndroidStage(
            command = command,
            visible = true,
            onTap = { _, _ -> },
            onStatus = { status = it },
            onError = { error = it },
            modifier = Modifier
                .fillMaxSize()
                .zIndex(1f)
        )
        Text(
            text = status?.summary() ?: "SDK/Core loaded=false, model loaded=false, drawable count=0, GL lifecycle=booting",
            color = Color.White,
            fontSize = 12.sp,
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(10.dp)
                .background(Color(0x99000000))
                .padding(8.dp)
                .zIndex(2f)
        )
        if (error.isNotBlank()) {
            CharacterStandee(
                character = "atri",
                emotion = "calm",
                pose = "idle",
                placement = OutfitPlacement(scale = 1.0f, bottomInset = 20f),
                baseHeight = Live2DStageBaseHeight,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .zIndex(1.5f)
            )
        }
    }
}

@Composable
private fun OfficialLive2DAndroidStage(
    command: Live2DRenderCommand,
    visible: Boolean,
    onTap: (Float, Float) -> Unit,
    onStatus: (OfficialLive2DRendererStatus) -> Unit,
    onError: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val live2DViewRef = remember { AtomicReference<OfficialLive2DView?>() }

    DisposableEffect(lifecycleOwner) {
        if (lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
            live2DViewRef.get()?.onResume()
        }
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> live2DViewRef.get()?.onResume()
                Lifecycle.Event.ON_PAUSE -> live2DViewRef.get()?.onPause()
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            live2DViewRef.get()?.clearStageListener()
            live2DViewRef.set(null)
        }
    }

    AndroidView(
        factory = { context ->
            OfficialLive2DView(context).apply {
                live2DViewRef.set(this)
                setStageListener(object : OfficialLive2DView.StageListener {
                    override fun onStatus(status: OfficialLive2DRendererStatus) {
                        onStatus(status)
                    }

                    override fun onError(error: String) {
                        onError(error)
                    }
                })
                if (lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
                    onResume()
                }
                setStageVisible(visible)
            }
        },
        update = { view ->
            view.setStageListener(object : OfficialLive2DView.StageListener {
                override fun onStatus(status: OfficialLive2DRendererStatus) {
                    onStatus(status)
                }

                override fun onError(error: String) {
                    onError(error)
                }
            })
            view.isClickable = false
            view.isFocusable = false
            view.setStageVisible(visible)
            view.submitCommand(command)
        },
        modifier = modifier
    )
}

@Composable
private fun Live2DLoadingLayer(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .background(Color(0x66000000), RoundedCornerShape(999.dp))
            .padding(12.dp),
        contentAlignment = Alignment.Center
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(28.dp),
            color = Color.White,
            strokeWidth = 2.dp
        )
    }
}

@Composable
fun CharacterTapZone(
    enabled: Boolean,
    headerBottomPx: Int,
    panelTopPx: Int,
    onTap: (Float, Float) -> Unit,
    modifier: Modifier = Modifier
) {
    if (!enabled || panelTopPx <= headerBottomPx) return

    val density = LocalDensity.current
    val topPx = headerBottomPx.coerceAtLeast(0)
    val heightPx = max(panelTopPx - headerBottomPx, 1)

    BoxWithConstraints(modifier) {
        val screenWidthPx = with(density) { maxWidth.toPx() }
        val screenHeightPx = with(density) { maxHeight.toPx() }
        Box(
            Modifier
                .offset { IntOffset(0, topPx) }
                .fillMaxWidth()
                .height(with(density) { heightPx.toDp() })
                .pointerInput(enabled, topPx, heightPx, screenWidthPx, screenHeightPx) {
                    detectTapGestures { offset ->
                        val screenX = offset.x / screenWidthPx.coerceAtLeast(1f)
                        val screenY = (topPx + offset.y) / screenHeightPx.coerceAtLeast(1f)
                        onTap(screenX.coerceIn(0f, 1f), screenY.coerceIn(0f, 1f))
                    }
                }
        )
    }
}

@Composable
private fun StageTapLayer(
    onTap: (Float, Float) -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier.pointerInput(Unit) {
            detectTapGestures { offset ->
                onTap(
                    offset.x / size.width.coerceAtLeast(1).toFloat(),
                    offset.y / size.height.coerceAtLeast(1).toFloat()
                )
            }
        }
    )
}

@Composable
private fun StaticCharacterStageLayer(
    character: String,
    emotion: String,
    pose: String,
    placement: OutfitPlacement,
    baseHeight: Dp,
    interactive: Boolean,
    onTap: (Float, Float) -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier.then(
            if (interactive) {
                Modifier.pointerInput(character, emotion, pose) {
                    detectTapGestures { offset ->
                        onTap(
                            offset.x / size.width.coerceAtLeast(1).toFloat(),
                            offset.y / size.height.coerceAtLeast(1).toFloat()
                        )
                    }
                }
            } else {
                Modifier
            }
        )
    ) {
        CharacterStandee(
            character = character,
            emotion = emotion,
            pose = pose,
            placement = placement,
            baseHeight = baseHeight,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
        )
    }
}
