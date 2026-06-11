package com.aigalgame.demo

import android.util.Log
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
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
    useInternalTapLayer: Boolean = true,
    remoteHitAreas: List<Live2DHitArea> = emptyList(),
    remoteReactions: List<Live2DReactionConfig> = emptyList(),
    touchCooldownRequest: TouchCooldownRequest? = null,
    touchReactionsEnabled: Boolean = true
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

    LaunchedEffect(remoteHitAreas, remoteReactions) {
        if (remoteHitAreas.isNotEmpty()) {
            controller.applyRemoteConfig(remoteHitAreas, remoteReactions)
        }
    }

    LaunchedEffect(touchCooldownRequest) {
        val request = touchCooldownRequest ?: return@LaunchedEffect
        controller.applyTouchCooldown(request.hitArea, request.cooldownMs, System.currentTimeMillis())
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
        if (!touchReactionsEnabled) {
            controller.updateGazeFromTap(
                normalizedX = normalizedX.coerceIn(0f, 1f),
                normalizedY = normalizedY.coerceIn(0f, 1f),
                placement = effectivePlacement,
                nowMs = System.currentTimeMillis()
            )
            return
        }
        val reaction = controller.handleTap(
            normalizedX = normalizedX.coerceIn(0f, 1f),
            normalizedY = normalizedY.coerceIn(0f, 1f),
            placement = effectivePlacement,
            relation = relation,
            nowMs = System.currentTimeMillis()
        )
        if (reaction != null) onReaction(reaction)
    }

    DisposableEffect(tapBridge, effectivePlacement, touchReactionsEnabled) {
        tapBridge?.setGazeHandler { normalizedX, normalizedY ->
            controller.updateGazeFromTap(
                normalizedX = normalizedX.coerceIn(0f, 1f),
                normalizedY = normalizedY.coerceIn(0f, 1f),
                placement = effectivePlacement,
                nowMs = System.currentTimeMillis()
            )
        }
        tapBridge?.setHandler { normalizedX, normalizedY ->
            handleStageTap(normalizedX, normalizedY)
        }
        onDispose {
            tapBridge?.setGazeHandler(null)
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
fun Live2DSelfTestStage(vm: MainViewModel, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val controller = remember { Live2DController(context, Live2DCharacterConfigs.DefaultCharacter) }
    var status by remember { mutableStateOf<OfficialLive2DRendererStatus?>(null) }
    var error by remember { mutableStateOf("") }
    var hitDebug by remember { mutableStateOf("Tap model to test hit areas") }
    var hitAreas by remember { mutableStateOf<List<Live2DHitArea>>(emptyList()) }
    var hitAreaMeta by remember { mutableStateOf("Loading hit areas from server…") }
    val placement = (vm.outfitPlacements["neko"] ?: defaultOutfitPlacement("neko")).coerceForStage()
    val command = remember(placement) {
        Live2DRenderCommand(
            characterId = "neko",
            emotion = "happy",
            motion = "selftest",
            mouthOpen = 0.55f,
            speaking = true,
            lookX = 0.25f,
            lookY = 0.05f,
            placement = placement,
            interactive = true,
            commandNonce = 1L
        )
    }
    val effectiveHitAreas = hitAreas.ifEmpty { Live2DCharacterConfigs.forCharacter("neko").hitAreas }
    LaunchedEffect(vm.baseUrl) {
        if (vm.baseUrl.isBlank()) {
            hitAreaMeta = "未连接后端，使用 APK 内置默认区域"
            return@LaunchedEffect
        }
        val (remoteAreas, configVersion) = vm.fetchNekoSelfTestHitAreas()
        if (remoteAreas.isNotEmpty()) {
            hitAreas = remoteAreas
            hitAreaMeta = "appearance=neko · config_version=$configVersion · ${remoteAreas.size} areas (server)"
        } else {
            hitAreaMeta = "拉取失败或无数据，使用 APK 内置默认区域"
        }
    }
    LaunchedEffect(effectiveHitAreas) {
        if (effectiveHitAreas.isNotEmpty()) {
            controller.applyRemoteConfig(effectiveHitAreas, emptyList())
        }
    }
    Box(modifier) {
        SakuraSceneBackground("classroom")
        OfficialLive2DAndroidStage(
            command = command,
            visible = true,
            onTap = { normalizedX, normalizedY ->
                val (modelX, modelY) = Live2DHitTest.mapScreenToModelSpace(normalizedX, normalizedY, placement)
                val hit = effectiveHitAreas.firstOrNull { it.contains(modelX, modelY) }?.id ?: "miss"
                hitDebug = "screen(${String.format("%.2f", normalizedX)}, ${String.format("%.2f", normalizedY)}) " +
                    "→ model(${String.format("%.2f", modelX)}, ${String.format("%.2f", modelY)}) → $hit"
                controller.handleTap(normalizedX, normalizedY, placement, RelationState(), System.currentTimeMillis())
            },
            onStatus = { status = it },
            onError = { error = it },
            modifier = Modifier
                .fillMaxSize()
                .zIndex(1f)
        )
        HitAreaDebugOverlay(
            hitAreas = effectiveHitAreas,
            placement = placement,
            modifier = Modifier
                .fillMaxSize()
                .zIndex(3f)
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
                .zIndex(4f)
        )
        Text(
            text = hitAreaMeta,
            color = Color(0xFF9AD0FF),
            fontSize = 10.sp,
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(10.dp)
                .background(Color(0x99000000))
                .padding(6.dp)
                .zIndex(4f)
        )
        Text(
            text = hitDebug,
            color = Color.White,
            fontSize = 11.sp,
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(10.dp)
                .background(Color(0x99000000))
                .padding(8.dp)
                .zIndex(4f)
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
fun HitAreaDebugOverlay(
    hitAreas: List<Live2DHitArea>,
    placement: OutfitPlacement,
    modifier: Modifier = Modifier
) {
    BoxWithConstraints(modifier) {
        val widthPx = constraints.maxWidth.toFloat().coerceAtLeast(1f)
        val heightPx = constraints.maxHeight.toFloat().coerceAtLeast(1f)
        Canvas(Modifier.fillMaxSize()) {
            for (area in hitAreas) {
                val (leftX, topY) = Live2DHitTest.mapModelToScreenSpaceRaw(area.left, area.top, placement)
                val (rightX, bottomY) = Live2DHitTest.mapModelToScreenSpaceRaw(area.right, area.bottom, placement)
                val left = minOf(leftX, rightX) * widthPx
                val top = minOf(topY, bottomY) * heightPx
                val rectWidth = kotlin.math.abs(rightX - leftX) * widthPx
                val rectHeight = kotlin.math.abs(bottomY - topY) * heightPx
                drawRect(
                    color = Color.Red,
                    topLeft = Offset(left, top),
                    size = Size(rectWidth, rectHeight),
                    style = Stroke(width = 3f)
                )
            }
        }
        for (area in hitAreas) {
            val (centerX, topY) = Live2DHitTest.mapModelToScreenSpace(
                (area.left + area.right) / 2f,
                area.top,
                placement
            )
            Text(
                text = area.label,
                color = Color.White,
                fontSize = 11.sp,
                modifier = Modifier
                    .offset {
                        IntOffset(
                            (centerX * widthPx - 24f).roundToInt(),
                            (topY * heightPx - 18f).roundToInt()
                        )
                    }
                    .background(Color(0xCCB00020), RoundedCornerShape(4.dp))
                    .padding(horizontal = 4.dp, vertical = 2.dp)
            )
        }
    }
}

@Composable
fun StandeeGestureZone(
    headerBottomPx: Int,
    panelTopPx: Int,
    placement: OutfitPlacement,
    onPlacementChange: (OutfitPlacement) -> Unit,
    modifier: Modifier = Modifier
) {
    if (panelTopPx <= headerBottomPx) return
    val density = LocalDensity.current
    val topPx = headerBottomPx.coerceAtLeast(0)
    val heightPx = max(panelTopPx - headerBottomPx, 1)
    var gesturePlacement by remember(placement) { mutableStateOf(placement.coerceForStage()) }
    LaunchedEffect(placement) {
        gesturePlacement = placement.coerceForStage()
    }
    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
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
    BoxWithConstraints(modifier) {
        Box(
            Modifier
                .offset { IntOffset(0, topPx) }
                .fillMaxWidth()
                .height(with(density) { heightPx.toDp() })
                .transformable(
                    state = transformState,
                    lockRotationOnZoomPan = true,
                    enabled = true
                )
        )
    }
}

@Composable
fun GazeDragZone(
    enabled: Boolean,
    onGaze: (Float, Float) -> Unit,
    modifier: Modifier = Modifier
) {
    if (!enabled) return
    Box(
        modifier
            .fillMaxSize()
            .pointerInput(enabled) {
                fun emitGaze(x: Float, y: Float) {
                    onGaze(
                        x / size.width.coerceAtLeast(1).toFloat(),
                        y / size.height.coerceAtLeast(1).toFloat()
                    )
                }
                detectDragGestures(
                    onDragStart = { offset -> emitGaze(offset.x, offset.y) },
                    onDrag = { change, _ -> emitGaze(change.position.x, change.position.y) }
                )
            }
            .pointerInput(enabled) {
                detectTapGestures { offset ->
                    onGaze(
                        offset.x / size.width.coerceAtLeast(1).toFloat(),
                        offset.y / size.height.coerceAtLeast(1).toFloat()
                    )
                }
            }
    )
}

@Composable
fun CharacterTapZone(
    enabled: Boolean,
    headerBottomPx: Int,
    panelTopPx: Int,
    onTap: (Float, Float) -> Unit,
    onGaze: (Float, Float) -> Unit = { _, _ -> },
    modifier: Modifier = Modifier
) {
    if (!enabled || panelTopPx <= headerBottomPx) return

    val density = LocalDensity.current
    val topPx = headerBottomPx.coerceAtLeast(0)
    val heightPx = max(panelTopPx - headerBottomPx, 1)

    BoxWithConstraints(modifier) {
        val screenWidthPx = with(density) { maxWidth.toPx() }
        val screenHeightPx = with(density) { maxHeight.toPx() }
        fun toScreenCoords(localX: Float, localY: Float): Pair<Float, Float> {
            val screenX = localX / screenWidthPx.coerceAtLeast(1f)
            val screenY = (topPx + localY) / screenHeightPx.coerceAtLeast(1f)
            return screenX.coerceIn(0f, 1f) to screenY.coerceIn(0f, 1f)
        }
        Box(
            Modifier
                .offset { IntOffset(0, topPx) }
                .fillMaxWidth()
                .height(with(density) { heightPx.toDp() })
                .pointerInput(enabled, topPx, heightPx, screenWidthPx, screenHeightPx) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val (x, y) = toScreenCoords(offset.x, offset.y)
                            onGaze(x, y)
                        },
                        onDrag = { change, _ ->
                            val (x, y) = toScreenCoords(change.position.x, change.position.y)
                            onGaze(x, y)
                        }
                    )
                }
                .pointerInput(enabled, topPx, heightPx, screenWidthPx, screenHeightPx) {
                    detectTapGestures { offset ->
                        val (x, y) = toScreenCoords(offset.x, offset.y)
                        onTap(x, y)
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
