package com.aigalgame.demo

import android.Manifest
import android.app.Application
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.work.ExistingWorkPolicy
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()
    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "sakura_widget_poll",
            ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<NotificationWorker>(15, TimeUnit.MINUTES).build()
        )
        WorkManager.getInstance(this).enqueueUniqueWork(
            "sakura_widget_startup_refresh",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<NotificationWorker>().build()
        )
        viewModel.consumeLaunchIntent(intent)
        setContent {
            GalgameTheme {
                AiGalgameApp(viewModel)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        viewModel.consumeLaunchIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        viewModel.resumeFromForeground()
    }
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = SettingsStore(application)
    private var api: ApiClient? = null
    private var bootstrappedBaseUrl = ""
    private var bootstrapInFlight = false
    private var appOpenedBaseUrl = ""
    private var dialogueEventInFlight = false
    private var placementDraftCharacter = ""
    private var pendingProactiveEventId = ""

    var baseUrl by mutableStateOf("")
        private set
    var connectionMessage by mutableStateOf("请先连接电脑后端")
        private set
    var screen by mutableStateOf(AppScreen.Home)
    var relation by mutableStateOf(RelationState())
    var currentLineIndex by mutableIntStateOf(0)
    var storyIndex by mutableIntStateOf(0)
    var isBusy by mutableStateOf(false)
    var errorMessage by mutableStateOf("")
    var storyCompleted by mutableStateOf(false)
    var selectedCharacter by mutableStateOf("atri")
    var selectedBackground by mutableStateOf("classroom")
    var previewEmotion by mutableStateOf("calm")
    var ttsEnabled by mutableStateOf(true)
    var notificationsEnabled by mutableStateOf(true)
    var outfitPlacements by mutableStateOf(defaultOutfitPlacements())
    var placementDraft by mutableStateOf<OutfitPlacement?>(null)
        private set

    val lines = mutableStateListOf<DialogueLine>()
    val dialogueHistory = mutableStateListOf<DialogueLine>()
    val normalReplies = mutableStateListOf<ReplyOption>()
    val keyReplies = mutableStateListOf<ReplyOption>()
    val moments = mutableStateListOf<MomentItem>()
    val memories = mutableStateListOf<MemoryItem>()
    val calendar = mutableStateListOf<CalendarItem>()

    init {
        viewModelScope.launch {
            settings.baseUrl.collect { saved ->
                val changed = saved != baseUrl
                baseUrl = saved
                if (changed) {
                    bootstrappedBaseUrl = ""
                    appOpenedBaseUrl = ""
                }
                if (saved.isNotBlank()) {
                    api = ApiClient(saved)
                    refreshBootstrap(openAfterBootstrap = pendingProactiveEventId.isBlank())
                    openPendingProactive()
                } else {
                    api = null
                }
            }
        }
        viewModelScope.launch {
            settings.uiSettings.collect { saved ->
                selectedCharacter = saved.selectedCharacter
                selectedBackground = saved.selectedBackground
                previewEmotion = saved.previewEmotion
                ttsEnabled = saved.ttsEnabled
                notificationsEnabled = saved.notificationsEnabled
                outfitPlacements = saved.placements
            }
        }
    }

    fun currentLine(): DialogueLine? = lines.getOrNull(currentLineIndex)
    fun currentPlacement(): OutfitPlacement = (outfitPlacements[selectedCharacter] ?: defaultOutfitPlacement(selectedCharacter)).coerceForStage()
    fun visiblePlacement(): OutfitPlacement = placementDraft ?: currentPlacement()

    fun saveBaseUrl(value: String) {
        val cleaned = value.trim().trimEnd('/')
        viewModelScope.launch {
            settings.saveBaseUrl(cleaned)
            baseUrl = cleaned
            bootstrappedBaseUrl = ""
            appOpenedBaseUrl = ""
            api = if (cleaned.isBlank()) null else ApiClient(cleaned)
            if (cleaned.isNotBlank()) {
                testHealth()
            }
        }
    }

    fun resolveUrl(path: String): String {
        val client = api ?: return path
        return client.absoluteUrl(path)
    }

    fun testHealth() {
        val client = api ?: return
        launchBusy {
            val health = client.health()
            connectionMessage = if (health.optBoolean("ok")) "电脑后端已连接" else "电脑后端返回异常"
            refreshBootstrap()
        }
    }

    fun refreshBootstrap(openAfterBootstrap: Boolean = true) {
        val client = api ?: return
        val urlKey = baseUrl
        if (urlKey.isBlank() || bootstrapInFlight || bootstrappedBaseUrl == urlKey) return
        bootstrapInFlight = true
        launchBusy {
            try {
                val boot = client.bootstrap()
                val rel = boot.optObject("relation")
                relation = RelationState(
                    affection = rel.optInt("affection", relation.affection),
                    trust = rel.optInt("trust", relation.trust),
                    dependency = rel.optInt("dependency", relation.dependency),
                    mood = rel.optInt("mood", relation.mood),
                    stage = rel.optString("stage", relation.stage)
                )
                storyCompleted = boot.optObject("user").optBoolean("story_completed", storyCompleted)
                bootstrappedBaseUrl = urlKey
                if (openAfterBootstrap && lines.isEmpty()) {
                    openApp()
                }
            } finally {
                bootstrapInFlight = false
            }
        }
    }

    fun consumeLaunchIntent(intent: Intent?) {
        val eventId = intent?.getStringExtra("proactive_event_id").orEmpty()
        if (eventId.isBlank()) return
        pendingProactiveEventId = eventId
        openPendingProactive()
    }

    private fun openPendingProactive() {
        val client = api ?: return
        val eventId = pendingProactiveEventId
        if (eventId.isBlank()) return
        pendingProactiveEventId = ""
        launchDialogueEvent {
            SakuraWidgetProvider.clearUnread(getApplication())
            try {
                client.consumeProactive(eventId)
            } catch (_: Exception) {
            }
            applyEvent(client.openingReady(eventId))
            appOpenedBaseUrl = baseUrl
        }
    }

    fun openApp(force: Boolean = false) {
        val client = api ?: return
        val urlKey = baseUrl
        if (!force && appOpenedBaseUrl == urlKey) return
        launchDialogueEvent {
            if (storyCompleted) {
                applyEvent(client.openingReady())
            } else {
                applyEvent(client.postEvent("app_opened", storyIndex = storyIndex))
            }
            appOpenedBaseUrl = urlKey
        }
    }

    fun resumeFromForeground() {
        if (baseUrl.isBlank()) return
        val waitingForUser = currentLine() == null && (normalReplies.isNotEmpty() || keyReplies.isNotEmpty())
        if (storyCompleted && waitingForUser) {
            normalReplies.clear()
            keyReplies.clear()
            openApp(force = true)
        } else {
            openApp()
        }
    }

    fun advanceLine() {
        if (currentLineIndex < lines.lastIndex) {
            currentLineIndex += 1
        } else if (lines.isNotEmpty()) {
            currentLineIndex = lines.size
        }
    }

    fun selectReply(option: ReplyOption) {
        val client = api ?: return
        launchDialogueEvent {
            val type = if (option.type == "key") "option_selected" else "user_message"
            applyEvent(client.postEvent(type, text = option.text, replyId = option.id, storyIndex = storyIndex))
        }
    }

    fun sendUserMessage(text: String) {
        if (text.isBlank()) return
        val client = api ?: return
        launchDialogueEvent {
            applyEvent(client.postEvent("user_message", text = text.trim()))
        }
    }

    fun loadMoments() {
        val client = api ?: return
        launchBusy {
            val rows = client.moments().optArray("items")
            moments.clear()
            for (i in 0 until rows.length()) {
                val item = rows.optJSONObject(i) ?: continue
                val commentsJson = item.optArray("comments")
                val comments = buildList {
                    for (j in 0 until commentsJson.length()) {
                        val comment = commentsJson.optJSONObject(j) ?: continue
                        add(
                            MomentComment(
                                actorName = comment.optString("actor_name", comment.optString("actor_id", "AI")),
                                content = comment.optString("content")
                            )
                        )
                    }
                }
                val likesJson = item.optArray("like_actors")
                val likeActors = buildList {
                    for (j in 0 until likesJson.length()) {
                        val like = likesJson.optJSONObject(j) ?: continue
                        add(like.optString("actor_name", like.optString("actor_id")))
                    }
                }
                moments.add(
                    MomentItem(
                        id = item.optString("moment_id"),
                        authorName = item.optString("author_name", "小樱"),
                        text = item.optString("text"),
                        mediaUrl = item.optString("media_url"),
                        likes = item.optInt("likes"),
                        likeActors = likeActors,
                        comments = comments,
                        createdAt = item.optString("created_at")
                    )
                )
            }
        }
    }

    fun likeMoment(id: String) {
        val client = api ?: return
        launchBusy {
            client.likeMoment(id)
            loadMoments()
        }
    }

    fun commentMoment(id: String, content: String) {
        val client = api ?: return
        if (content.isBlank()) return
        launchBusy {
            client.commentMoment(id, content.trim())
            loadMoments()
        }
    }

    fun loadJournal() {
        val client = api ?: return
        launchBusy {
            val rows = client.journal().optArray("memories")
            memories.clear()
            for (i in 0 until rows.length()) {
                val item = rows.optJSONObject(i) ?: continue
                memories.add(
                    MemoryItem(
                        id = item.optString("memory_id"),
                        layer = item.optString("layer"),
                        content = item.optString("content"),
                        confidence = item.optDouble("confidence"),
                        importance = item.optDouble("importance"),
                        createdAt = item.optString("created_at")
                    )
                )
            }
        }
    }

    fun loadCalendar(month: String = "") {
        val client = api ?: return
        launchBusy {
            val rows = client.calendar(month).optArray("days")
            calendar.clear()
            for (i in 0 until rows.length()) {
                val item = rows.optJSONObject(i) ?: continue
                calendar.add(
                    CalendarItem(
                        date = item.optString("date"),
                        startAt = item.optString("start_at"),
                        title = item.optString("activity_title"),
                        status = item.optString("status"),
                        salience = item.optInt("salience")
                    )
                )
            }
        }
    }

    fun chooseCharacter(value: String) {
        selectedCharacter = value
        viewModelScope.launch { settings.saveSelectedCharacter(value) }
    }

    fun chooseBackground(value: String) {
        selectedBackground = value
        viewModelScope.launch { settings.saveSelectedBackground(value) }
    }

    fun choosePreviewEmotion(value: String) {
        previewEmotion = value
        viewModelScope.launch { settings.savePreviewEmotion(value) }
    }

    fun beginPlacementEdit() {
        placementDraftCharacter = selectedCharacter
        placementDraft = currentPlacement()
    }

    fun updatePlacementDraft(value: OutfitPlacement) {
        val character = placementDraftCharacter.ifBlank { selectedCharacter }
        val next = value.coerceForStage()
        placementDraft = next
        outfitPlacements = outfitPlacements.toMutableMap().also { it[character] = next }
    }

    fun commitPlacementEdit() {
        val value = placementDraft?.coerceForStage() ?: return
        val character = placementDraftCharacter.ifBlank { selectedCharacter }
        placementDraft = null
        placementDraftCharacter = ""
        outfitPlacements = outfitPlacements.toMutableMap().also { it[character] = value }
        viewModelScope.launch { settings.savePlacement(character, value) }
    }

    fun resetPlacement() {
        val character = placementDraftCharacter.ifBlank { selectedCharacter }
        updatePlacementDraft(defaultOutfitPlacement(character))
    }

    fun updateTtsEnabled(value: Boolean) {
        ttsEnabled = value
        viewModelScope.launch { settings.saveTtsEnabled(value) }
    }

    fun updateNotificationsEnabled(value: Boolean) {
        notificationsEnabled = value
        viewModelScope.launch { settings.saveNotificationsEnabled(value) }
    }

    fun clearLocalState() {
        lines.clear()
        dialogueHistory.clear()
        normalReplies.clear()
        keyReplies.clear()
        moments.clear()
        memories.clear()
        calendar.clear()
        storyIndex = 0
        currentLineIndex = 0
        errorMessage = ""
    }

    private fun applyEvent(event: JSONObject) {
        val type = event.optString("event_type")
        if (type == "error") {
            errorMessage = event.optObject("payload").optString("message")
            return
        }
        if (type == "no_reply") {
            normalReplies.clear()
            keyReplies.clear()
            return
        }
        val payload = event.optObject("payload")
        if (payload.has("story_completed")) {
            storyCompleted = payload.optBoolean("story_completed", storyCompleted)
        }
        storyIndex = payload.optInt("story_index", storyIndex)
        val relationJson = payload.optObject("relation_delta")
        if (relationJson.length() > 0) {
            relation = relation.copy(
                affection = relation.affection + relationJson.optInt("affection"),
                trust = relation.trust + relationJson.optInt("trust"),
                dependency = relation.dependency + relationJson.optInt("dependency"),
                mood = relation.mood + relationJson.optInt("mood")
            )
        }
        val newLines = payload.optArray("lines")
        lines.clear()
        for (i in 0 until newLines.length()) {
            val item = newLines.optJSONObject(i) ?: continue
            val line = DialogueLine(
                id = item.optString("line_id"),
                text = item.optString("text"),
                emotion = item.optString("emotion", "calm"),
                pose = item.optString("pose", "idle"),
                ttsUrl = item.optString("tts_audio_url"),
                ttsError = item.optString("tts_error")
            )
            lines.add(line)
            dialogueHistory.add(line)
        }
        if (ttsEnabled && lines.size > 1 && lines.any { it.ttsUrl.isBlank() }) {
            errorMessage = "本次回复有台词缺少语音，已记录诊断。"
        }
        currentLineIndex = 0
        normalReplies.clear()
        parseReplies(payload.optArray("normal_replies"), "normal").forEach { normalReplies.add(it) }
        keyReplies.clear()
        parseReplies(payload.optArray("key_replies"), "key").forEach { keyReplies.add(it) }
    }

    private fun parseReplies(array: org.json.JSONArray, fallbackType: String): List<ReplyOption> {
        return buildList {
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                val delta = item.optObject("preview_delta")
                add(
                    ReplyOption(
                        id = item.optString("reply_id"),
                        text = item.optString("text"),
                        type = item.optString("type", fallbackType),
                        affection = delta.optInt("affection"),
                        trust = delta.optInt("trust"),
                        dependency = delta.optInt("dependency"),
                        mood = delta.optInt("mood")
                    )
                )
            }
        }
    }

    private fun launchBusy(block: suspend () -> Unit) {
        viewModelScope.launch {
            isBusy = true
            errorMessage = ""
            try {
                block()
            } catch (e: Exception) {
                errorMessage = e.message ?: e.javaClass.simpleName
            } finally {
                isBusy = false
            }
        }
    }

    private fun launchDialogueEvent(block: suspend () -> Unit) {
        if (dialogueEventInFlight) return
        dialogueEventInFlight = true
        launchBusy {
            try {
                block()
            } finally {
                dialogueEventInFlight = false
            }
        }
    }
}

@Composable
fun GalgameTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Color(0xFFE86B8D),
            secondary = Color(0xFF5E8DB8),
            surface = Color(0xFFFFFBFA),
            background = Color(0xFFFFF7F4)
        ),
        content = content
    )
}

fun defaultOutfitPlacement(character: String): OutfitPlacement {
    return when (character) {
        "murasame" -> OutfitPlacement(scale = 1.08f, offsetY = -6f, bottomInset = 42f)
        else -> OutfitPlacement(scale = 1.14f, offsetY = -12f, bottomInset = 34f)
    }
}

fun defaultOutfitPlacements(): Map<String, OutfitPlacement> {
    return mapOf("atri" to defaultOutfitPlacement("atri"), "murasame" to defaultOutfitPlacement("murasame"))
}

private val CharacterStageBaseHeight = 650.dp

@Composable
fun AiGalgameApp(vm: MainViewModel) {
    AudioLinePlayer(vm)
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        if (vm.baseUrl.isBlank()) {
            ConnectionScreen(vm)
        } else {
            Scaffold(bottomBar = { AppBottomBar(vm) }) { padding ->
                Box(Modifier.padding(padding)) {
                    when (vm.screen) {
                        AppScreen.Home -> HomeScreen(vm)
                        AppScreen.DressUp -> DressUpScreen(vm)
                        AppScreen.Settings -> SettingsScreen(vm)
                        AppScreen.Moments -> MomentsScreen(vm)
                        AppScreen.Calendar -> CalendarScreen(vm)
                        AppScreen.Journal -> JournalScreen(vm)
                    }
                    if (vm.errorMessage.isNotBlank()) {
                        Card(
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .padding(12.dp),
                            colors = CardDefaults.cardColors(containerColor = Color(0xFFFFECEF))
                        ) {
                            Text(vm.errorMessage, Modifier.padding(12.dp), color = Color(0xFF7B2535))
                        }
                    }
                    if (vm.isBusy) {
                        LinearProgressIndicator(Modifier.fillMaxWidth().align(Alignment.TopCenter))
                    }
                }
            }
        }
    }
}

@Composable
fun AudioLinePlayer(vm: MainViewModel) {
    val context = LocalContext.current
    val player = remember { ExoPlayer.Builder(context).build() }
    val line = vm.currentLine()
    LaunchedEffect(line?.id, line?.ttsUrl, vm.ttsEnabled) {
        val url = line?.ttsUrl.orEmpty()
        if (vm.ttsEnabled && url.isNotBlank()) {
            player.stop()
            player.clearMediaItems()
            player.setMediaItem(MediaItem.fromUri(vm.resolveUrl(url)))
            player.prepare()
            player.play()
        } else {
            player.stop()
        }
    }
    DisposableEffect(Unit) {
        onDispose { player.release() }
    }
}

@Composable
fun ConnectionScreen(vm: MainViewModel) {
    var value by remember { mutableStateOf("http://192.168.1.2:8899") }
    Column(
        Modifier
            .fillMaxSize()
            .background(Brush.verticalGradient(listOf(Color(0xFFFFF7F4), Color(0xFFDDF2FF))))
            .padding(24.dp),
        verticalArrangement = Arrangement.Center
    ) {
        Text("恋爱物语 Demo", fontSize = 30.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4A2A2B))
        Spacer(Modifier.height(10.dp))
        Text("先在电脑浏览器打开管理台，复制其中显示的手机连接地址。手机和电脑需要在同一网络。", color = Color(0xFF79545B), lineHeight = 22.sp)
        Spacer(Modifier.height(18.dp))
        OutlinedTextField(value = value, onValueChange = { value = it }, label = { Text("电脑后端地址") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(12.dp))
        Button(onClick = { vm.saveBaseUrl(value) }, modifier = Modifier.fillMaxWidth()) {
            Text("连接电脑后端")
        }
        Spacer(Modifier.height(12.dp))
        Text(vm.connectionMessage, color = Color(0xFF79545B))
    }
}

@Composable
fun AppBottomBar(vm: MainViewModel) {
    NavigationBar(containerColor = Color(0xF8FFFFFF), tonalElevation = 10.dp) {
        val items = listOf(
            Triple(AppScreen.Home, "首页", R.drawable.ic_nav_home),
            Triple(AppScreen.DressUp, "时装", R.drawable.ic_nav_dress),
            Triple(AppScreen.Settings, "设置", R.drawable.ic_nav_settings)
        )
        items.forEach { (screen, label, iconRes) ->
            val selected = vm.screen == screen
            NavigationBarItem(
                selected = selected,
                onClick = { vm.screen = screen },
                icon = {
                    Icon(
                        painter = painterResource(iconRes),
                        contentDescription = label,
                        tint = if (selected) Color(0xFFE86B8D) else Color(0xFF6F87AD),
                        modifier = Modifier.size(28.dp)
                    )
                },
                label = { Text(label, maxLines = 1, fontWeight = FontWeight.Bold) }
            )
        }
    }
}

@Composable
fun HomeScreen(vm: MainViewModel) {
    var input by remember { mutableStateOf("") }
    var historyExpanded by remember { mutableStateOf(false) }
    var standeeEditMode by remember { mutableStateOf(false) }
    val line = vm.currentLine()
    val lastLine = line ?: vm.lines.lastOrNull()
    val density = LocalDensity.current
    val keyboardLift = with(density) {
        (WindowInsets.ime.getBottom(this) - WindowInsets.navigationBars.getBottom(this)).coerceAtLeast(0).toDp()
    }
    Box(Modifier.fillMaxSize()) {
        CharacterStage(
            background = vm.selectedBackground,
            character = vm.selectedCharacter,
            emotion = lastLine?.emotion ?: "calm",
            pose = lastLine?.pose ?: "idle",
            placement = vm.visiblePlacement(),
            editable = standeeEditMode,
            onPlacementChange = { vm.updatePlacementDraft(it) },
            modifier = Modifier.fillMaxSize()
        )

        HomeHeader(
            vm = vm,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(horizontal = 18.dp, vertical = 18.dp)
        )
        HomeStandeeEditBar(
            editing = standeeEditMode,
            onToggle = {
                if (standeeEditMode) {
                    vm.commitPlacementEdit()
                    standeeEditMode = false
                } else {
                    vm.beginPlacementEdit()
                    standeeEditMode = true
                }
            },
            onReset = { vm.resetPlacement() },
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(top = 104.dp, end = 18.dp)
        )

        HomeInteractionPanel(
            vm = vm,
            line = line,
            input = input,
            onInputChange = { input = it },
            onSend = {
                vm.sendUserMessage(input)
                input = ""
            },
            onShowHistory = { historyExpanded = true },
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(start = 14.dp, top = 12.dp, end = 14.dp, bottom = 12.dp + keyboardLift)
        )

        if (historyExpanded) {
            DialogueHistoryOverlay(
                history = vm.dialogueHistory,
                onDismiss = { historyExpanded = false },
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(horizontal = 14.dp, vertical = 12.dp)
            )
        }
    }
}

@Composable
fun HomeInteractionPanel(
    vm: MainViewModel,
    line: DialogueLine?,
    input: String,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onShowHistory: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (line != null) {
            DialogueBox(
                line = line,
                history = vm.dialogueHistory,
                canAdvance = vm.lines.isNotEmpty(),
                onAdvance = { vm.advanceLine() },
                onShowHistory = onShowHistory
            )
        } else {
            if (vm.keyReplies.isNotEmpty()) {
                KeyReplies(vm)
            } else {
                ReplyStrip(vm)
            }
            ReplyInputRow(input = input, onInputChange = onInputChange, onSend = onSend)
        }
    }
}

@Composable
fun HomeStandeeEditBar(editing: Boolean, onToggle: () -> Unit, onReset: () -> Unit, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (editing) {
            OutlinedButton(
                onClick = onReset,
                colors = ButtonDefaults.outlinedButtonColors(containerColor = Color(0xEFFFFFFF), contentColor = Color(0xFF5C2E24)),
                border = BorderStroke(1.dp, Color(0xFFE4B28D)),
                shape = RoundedCornerShape(12.dp)
            ) {
                Text("重置", fontWeight = FontWeight.Bold)
            }
        }
        Button(
            onClick = onToggle,
            colors = ButtonDefaults.buttonColors(containerColor = if (editing) Color(0xFF5E8DB8) else Color(0xFFE86B8D)),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(if (editing) "完成" else "调整立绘", fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
fun HomeHeader(vm: MainViewModel, modifier: Modifier = Modifier) {
    var now by remember { mutableStateOf(LocalTime.now()) }
    val today = LocalDate.now()
    val clock = now.format(DateTimeFormatter.ofPattern("HH:mm"))
    LaunchedEffect(Unit) {
        while (true) {
            now = LocalTime.now()
            delay(30_000)
        }
    }
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Column(
            Modifier
                .weight(1f)
                .clickable {
                    vm.screen = AppScreen.Calendar
                    vm.loadCalendar()
                }
        ) {
            Text(clock, color = Color.White, fontSize = 31.sp, fontWeight = FontWeight.ExtraBold)
            Text(today.format(DateTimeFormatter.ofPattern("yyyy.MM.dd")), color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Row(horizontalArrangement = Arrangement.spacedBy(5.dp), modifier = Modifier.padding(top = 5.dp)) {
                HeaderPill(weekdayLabel(today))
                HeaderPill(festivalLabel(today))
            }
        }
        CharacterInfoCard(
            vm = vm,
            modifier = Modifier
                .weight(0.85f)
                .padding(top = 6.dp)
        )
        Card(
            modifier = Modifier
                .size(72.dp)
                .shadow(8.dp, CircleShape)
                .clickable {
                    vm.screen = AppScreen.Moments
                    vm.loadMoments()
                },
            colors = CardDefaults.cardColors(containerColor = Color(0xFFE84F91)),
            shape = CircleShape,
            border = BorderStroke(4.dp, Color(0xEEFFFFFF))
        ) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("朋友圈", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, lineHeight = 18.sp)
            }
        }
    }
}

@Composable
fun HeaderPill(text: String) {
    Surface(color = Color(0xDDF8F3F1), shape = RoundedCornerShape(18.dp)) {
        Text(
            text,
            color = Color(0xFF563238),
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp)
        )
    }
}

@Composable
fun CharacterInfoCard(vm: MainViewModel, modifier: Modifier = Modifier) {
    val progress = (vm.relation.affection / 100f).coerceIn(0f, 1f)
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xE7A8D9FF)),
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(2.dp, Color(0xEEFFFFFF)),
        modifier = modifier
            .height(70.dp)
            .clickable {
                vm.screen = AppScreen.Journal
                vm.loadJournal()
            }
    ) {
        Row(Modifier.fillMaxSize().padding(horizontal = 10.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("小樱", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 18.sp, maxLines = 1)
                Box(
                    Modifier
                        .fillMaxWidth()
                        .height(8.dp)
                        .clip(RoundedCornerShape(99.dp))
                        .background(Color(0x66FFFFFF))
                ) {
                    Box(
                        Modifier
                            .fillMaxWidth(progress)
                            .fillMaxHeight()
                            .background(Color(0xFFFF5D7A))
                    )
                }
            }
            Text(vm.relation.affection.toString(), color = Color(0xFF563238), fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(start = 6.dp))
        }
    }
}

@Composable
fun DialogueBox(
    line: DialogueLine,
    history: List<DialogueLine>,
    canAdvance: Boolean,
    onAdvance: () -> Unit,
    onShowHistory: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .height(164.dp)
            .pointerInput(history.size) {
                detectVerticalDragGestures { _, dragAmount ->
                    if (dragAmount < -10f && history.isNotEmpty()) onShowHistory()
                }
            }
            .clickable(enabled = canAdvance) { onAdvance() },
        colors = CardDefaults.cardColors(containerColor = Color(0xF2FFF1DF)),
        shape = RoundedCornerShape(18.dp),
        border = BorderStroke(2.dp, Color(0xFFDDA884))
    ) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Surface(color = Color(0xFFEFA178), shape = RoundedCornerShape(16.dp), modifier = Modifier.width(112.dp)) {
                Text("小樱", color = Color(0xFF5C2E24), fontWeight = FontWeight.Bold, fontSize = 17.sp, textAlign = TextAlign.Center, modifier = Modifier.padding(vertical = 3.dp))
            }
            Text(
                line.text,
                color = Color(0xFF4A2A2B),
                fontSize = 21.sp,
                lineHeight = 29.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .weight(1f)
                    .padding(top = 8.dp)
            )
            if (line.ttsError.isNotBlank()) {
                Text(line.ttsError, color = Color(0xFF9A5A62), fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
fun DialogueHistoryOverlay(history: List<DialogueLine>, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier
            .fillMaxWidth()
            .fillMaxHeight(0.68f),
        colors = CardDefaults.cardColors(containerColor = Color(0xF4FFF1DF)),
        shape = RoundedCornerShape(20.dp),
        border = BorderStroke(2.dp, Color(0xFFDDA884))
    ) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("历史", color = Color(0xFF5C2E24), fontSize = 18.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                TextButton(onClick = onDismiss) { Text("收起", color = Color(0xFF5C2E24)) }
            }
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
                reverseLayout = true
            ) {
                items(history.asReversed()) { item ->
                    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text("小樱", color = Color(0xFFE86B8D), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        Text(item.text, color = Color(0xFF3D211D), fontSize = 17.sp, lineHeight = 24.sp)
                    }
                }
            }
        }
    }
}

@Composable
fun ReplyStrip(vm: MainViewModel) {
    if (vm.normalReplies.isEmpty()) return
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        vm.normalReplies.take(2).forEach { option ->
            OutlinedButton(
                onClick = { vm.selectReply(option) },
                modifier = Modifier
                    .weight(1f)
                    .height(56.dp),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = Color(0xEFFFFFFF), contentColor = Color(0xFF5C2E24)),
                border = BorderStroke(1.5.dp, Color(0xFFE4B28D)),
                shape = RoundedCornerShape(16.dp)
            ) {
                Text(option.text, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
fun KeyReplies(vm: MainViewModel) {
    if (vm.keyReplies.isEmpty()) return
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        vm.keyReplies.take(2).forEach { option ->
            Button(
                onClick = { vm.selectReply(option) },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(54.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF7A2C), contentColor = Color.White),
                shape = RoundedCornerShape(13.dp),
                border = BorderStroke(2.dp, Color(0xFFFFC08A))
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(option.text, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                    Text(replyDeltaLabel(option), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 12.sp, color = Color(0xFFFFF0D8))
                }
            }
        }
    }
}

@Composable
fun ReplyInputRow(input: String, onInputChange: (String) -> Unit, onSend: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xF4FFF6E8)),
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(2.dp, Color(0xFFDDA884))
    ) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = input,
                onValueChange = onInputChange,
                placeholder = { Text("输入回复") },
                modifier = Modifier.weight(1f),
                maxLines = 2
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = onSend,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE86B8D)),
                shape = RoundedCornerShape(12.dp)
            ) {
                Text("发送", fontWeight = FontWeight.Bold)
            }
        }
    }
}

fun signed(value: Int): String = if (value >= 0) "+$value" else value.toString()

fun replyDeltaLabel(option: ReplyOption): String {
    val deltas = listOf(
        "好感${signed(option.affection)}",
        "信任${signed(option.trust)}",
        "依赖${signed(option.dependency)}",
        "心情${signed(option.mood)}"
    )
    return deltas.joinToString("  ")
}

fun heartMeter(value: Int): String {
    val filled = (value / 20).coerceIn(0, 5)
    return "♥".repeat(filled) + "♡".repeat(5 - filled)
}

fun weekdayLabel(date: LocalDate): String {
    return when (date.dayOfWeek.value) {
        1 -> "星期一"
        2 -> "星期二"
        3 -> "星期三"
        4 -> "星期四"
        5 -> "星期五"
        6 -> "星期六"
        else -> "星期日"
    }
}

fun festivalLabel(date: LocalDate): String {
    return when (date.format(DateTimeFormatter.ofPattern("MM-dd"))) {
        "01-01" -> "元旦"
        "02-14" -> "情人节"
        "03-14" -> "白色情人节"
        "05-20" -> "告白日"
        "12-25" -> "圣诞节"
        else -> "樱花季"
    }
}

@Composable
fun SakuraSceneBackground(style: String) {
    Box(Modifier.fillMaxSize()) {
        Image(
            painter = painterResource(R.drawable.bg_classroom_sakura),
            contentDescription = "场景背景",
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.Crop
        )
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        listOf(Color(0x33000000), Color.Transparent, Color(0x66000000))
                    )
                )
        )
    }
}

@Composable
fun CharacterStage(
    background: String,
    character: String,
    emotion: String,
    pose: String,
    placement: OutfitPlacement,
    editable: Boolean = false,
    onPlacementChange: ((OutfitPlacement) -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    val density = LocalDensity.current
    val stagePlacement = placement.coerceForStage()
    var gesturePlacement by remember(stagePlacement) { mutableStateOf(stagePlacement) }
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
    val stageGestureModifier = if (editable && onPlacementChange != null) {
        Modifier.transformable(
            state = transformState,
            lockRotationOnZoomPan = true,
            enabled = true
        )
    } else {
        Modifier
    }
    Box(modifier.then(stageGestureModifier)) {
        SakuraSceneBackground(background)
        CharacterStandee(
            character = character,
            emotion = emotion,
            pose = pose,
            placement = stagePlacement,
            baseHeight = CharacterStageBaseHeight,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
        )
    }
}

@Composable
fun CharacterStandee(
    character: String,
    emotion: String,
    pose: String,
    placement: OutfitPlacement,
    baseHeight: Dp,
    modifier: Modifier = Modifier
) {
    Image(
        painter = painterResource(characterImageRes(character, emotion, pose)),
        contentDescription = "角色立绘",
        modifier = modifier
            .offset(x = placement.offsetX.dp, y = placement.offsetY.dp)
            .height(baseHeight * placement.scale)
            .padding(bottom = placement.bottomInset.dp),
        alignment = Alignment.BottomCenter,
        contentScale = ContentScale.Fit
    )
}

fun characterImageRes(character: String, emotion: String, pose: String): Int {
    val state = when {
        emotion == "happy" || pose == "happy" -> "happy"
        emotion == "thinking" || pose == "thinking" -> "thinking"
        emotion == "shy" || pose == "shy" -> "shy"
        emotion == "sad" || pose == "sad" -> "sad"
        emotion == "angry" || pose == "angry" -> "angry"
        emotion == "sleep" || pose == "sleep" -> "sleep"
        else -> "idle"
    }
    return when (character) {
        "murasame" -> when (state) {
            "happy" -> R.drawable.character_murasame_happy
            "thinking" -> R.drawable.character_murasame_thinking
            "shy" -> R.drawable.character_murasame_shy
            "angry" -> R.drawable.character_murasame_angry
            else -> R.drawable.character_murasame_idle
        }
        else -> when (state) {
            "happy" -> R.drawable.character_atri_happy
            "thinking" -> R.drawable.character_atri_thinking
            "shy" -> R.drawable.character_atri_shy
            "sad" -> R.drawable.character_atri_sad
            "angry" -> R.drawable.character_atri_angry
            "sleep" -> R.drawable.character_atri_sleep
            else -> R.drawable.character_atri_idle
        }
    }
}

@Composable
fun DressUpScreen(vm: MainViewModel) {
    val placement = vm.currentPlacement()
    Column(
        Modifier
            .fillMaxSize()
            .background(Color(0xFFFFF7F4))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Column {
            Text("装扮", fontSize = 26.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4A2A2B))
            Text("切换立绘、表情和场景预览。", color = Color(0xFF79545B))
        }
        Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBFA))) {
            CharacterStage(
                background = vm.selectedBackground,
                character = vm.selectedCharacter,
                emotion = vm.previewEmotion,
                pose = vm.previewEmotion,
                placement = placement,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(220.dp)
                    .clip(RoundedCornerShape(12.dp))
            )
        }
        LazyColumn(
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            item {
                Text("角色", fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SelectablePill("小樱 校园装", vm.selectedCharacter == "atri") { vm.chooseCharacter("atri") }
                    SelectablePill("美月 和风", vm.selectedCharacter == "murasame") { vm.chooseCharacter("murasame") }
                }
            }
            item {
                Text("表情", fontWeight = FontWeight.Bold)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(
                        "calm" to "平静",
                        "happy" to "开心",
                        "thinking" to "思考",
                        "shy" to "害羞"
                    ).forEach { (value, label) ->
                        SelectablePill(label, vm.previewEmotion == value) { vm.choosePreviewEmotion(value) }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("sad" to "低落", "angry" to "生气", "sleep" to "休息").forEach { (value, label) ->
                        SelectablePill(label, vm.previewEmotion == value) { vm.choosePreviewEmotion(value) }
                    }
                }
            }
            item {
                Text("场景", fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SelectablePill("教室", vm.selectedBackground == "classroom") { vm.chooseBackground("classroom") }
                    SelectablePill("樱花街", vm.selectedBackground == "street") { vm.chooseBackground("street") }
                    SelectablePill("房间", vm.selectedBackground == "room") { vm.chooseBackground("room") }
                }
            }
        }
    }
}

@Composable
fun SelectablePill(label: String, selected: Boolean, onClick: () -> Unit) {
    val colors = if (selected) {
        ButtonDefaults.buttonColors(containerColor = Color(0xFFE86B8D), contentColor = Color.White)
    } else {
        ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFF5D403B))
    }
    if (selected) {
        Button(onClick = onClick, colors = colors) { Text(label, maxLines = 1) }
    } else {
        OutlinedButton(onClick = onClick, colors = colors) { Text(label, maxLines = 1) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MomentsScreen(vm: MainViewModel) {
    Box(Modifier.fillMaxSize()) {
        SakuraSceneBackground("classroom")
        Column(Modifier.fillMaxSize()) {
            SocialTitleBar(title = "朋友圈", onBack = { vm.screen = AppScreen.Home })
            if (vm.moments.isEmpty()) {
                EmptyPanel("还没有真实朋友圈动态")
            } else {
                LazyColumn(
                    Modifier
                        .fillMaxSize()
                        .padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    items(vm.moments) { item ->
                        MomentCard(vm, item)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarScreen(vm: MainViewModel) {
    var month by remember { mutableStateOf(YearMonth.now()) }
    var selectedDate by remember { mutableStateOf(LocalDate.now()) }
    LaunchedEffect(month) {
        vm.loadCalendar(month.toString())
    }
    val eventsByDay = remember(vm.calendar.toList()) {
        vm.calendar.groupBy { parseLocalDate(it.date) }
    }
    Box(Modifier.fillMaxSize()) {
        SakuraSceneBackground("classroom")
        Column(Modifier.fillMaxSize()) {
            SocialTitleBar(title = "日历", onBack = { vm.screen = AppScreen.Home })
            CalendarMonthPanel(
                month = month,
                selectedDate = selectedDate,
                eventsByDay = eventsByDay,
                onPreviousMonth = {
                    month = month.minusMonths(1)
                    selectedDate = month.atDay(1)
                },
                onNextMonth = {
                    month = month.plusMonths(1)
                    selectedDate = month.atDay(1)
                },
                onSelectDate = { selectedDate = it },
                modifier = Modifier.padding(14.dp)
            )
            DayScheduleCard(
                date = selectedDate,
                events = eventsByDay[selectedDate].orEmpty(),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 14.dp)
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JournalScreen(vm: MainViewModel) {
    Box(Modifier.fillMaxSize()) {
        Image(
            painter = painterResource(R.drawable.journal_paper_sakura),
            contentDescription = "手账纸张",
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.Crop
        )
        LazyColumn(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            item {
                JournalHeader(onBack = { vm.screen = AppScreen.Home })
            }
            item {
                JournalProfileSection(vm.relation)
            }
            item {
                JournalPerspectiveSection(vm.relation)
            }
            item {
                Text("我们的回忆", color = Color(0xFF5C2E24), fontSize = 26.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            }
            if (vm.memories.isEmpty()) {
                item { EmptyPanel("还没有真实记忆记录") }
            } else {
                items(vm.memories.take(12)) { memory ->
                    JournalMemoryRow(memory)
                }
            }
            item { Spacer(Modifier.height(18.dp)) }
        }
    }
}

@Composable
fun EmptyPanel(text: String) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xEFFFF8ED)),
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.5.dp, Color(0xFFD7B491)),
        modifier = Modifier
            .fillMaxWidth()
            .padding(14.dp)
    ) {
        Text(text, color = Color(0xFF5C2E24), fontSize = 18.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(22.dp))
    }
}

@Composable
fun SocialTitleBar(title: String, onBack: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(Color(0xEFFFF0DD))
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        TextButton(onClick = onBack) { Text("‹ 返回", color = Color(0xFF5C2E24), fontSize = 18.sp) }
        Text(
            title,
            color = Color(0xFF3D211D),
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
            modifier = Modifier.weight(1f)
        )
        Spacer(Modifier.width(72.dp))
    }
}

@Composable
fun MomentCard(vm: MainViewModel, item: MomentItem) {
    var comment by remember(item.id) { mutableStateOf("") }
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xF8FFF8ED)),
        shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.5.dp, Color(0xFFD7B491))
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Image(
                    painter = painterResource(R.drawable.character_atri_happy),
                    contentDescription = "小樱头像",
                    modifier = Modifier
                        .size(54.dp)
                        .clip(CircleShape)
                        .background(Color(0xFFFFE4EA)),
                    contentScale = ContentScale.Crop
                )
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(item.authorName, fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Color(0xFF3D211D))
                    Text(formatMomentTime(item.createdAt), color = Color(0xFF8F6B62), fontSize = 14.sp)
                }
            }
            Text(item.text, fontSize = 21.sp, lineHeight = 29.sp, fontWeight = FontWeight.Bold, color = Color(0xFF2F1D1A))
            MomentImage(mediaUrl = if (item.mediaUrl.isNotBlank()) vm.resolveUrl(item.mediaUrl) else "")
            Surface(color = Color(0xFFFFE6D6), shape = RoundedCornerShape(10.dp), border = BorderStroke(1.dp, Color(0xFFDDB08B))) {
                Column(Modifier.fillMaxWidth().padding(10.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    if (item.likes > 0) {
                        val names = item.likeActors.take(4).joinToString("、").ifBlank { "${item.likes} 人" }
                        Text("♥ $names 觉得很赞", color = Color(0xFF5C2E24), fontSize = 16.sp, fontWeight = FontWeight.Bold)
                    }
                    item.comments.take(5).forEach {
                        Text("${it.actorName}：${it.content}", color = Color(0xFF3D211D), fontSize = 16.sp, lineHeight = 22.sp)
                    }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(
                    onClick = { vm.likeMoment(item.id) },
                    border = BorderStroke(1.dp, Color(0xFFE86B8D))
                ) { Text("点赞") }
                Spacer(Modifier.width(8.dp))
                OutlinedTextField(
                    value = comment,
                    onValueChange = { comment = it },
                    placeholder = { Text("评论") },
                    modifier = Modifier.weight(1f),
                    singleLine = true
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        vm.commentMoment(item.id, comment)
                        comment = ""
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE86B8D))
                ) { Text("发") }
            }
        }
    }
}

@Composable
fun MomentImage(mediaUrl: String) {
    val bitmap by produceState<Bitmap?>(initialValue = null, mediaUrl) {
        value = if (mediaUrl.isBlank()) {
            null
        } else {
            withContext(Dispatchers.IO) {
                runCatching {
                    val bytes = HttpDownloader.bytes(mediaUrl)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                }.getOrNull()
            }
        }
    }
    val shape = RoundedCornerShape(12.dp)
    if (bitmap != null) {
        Image(
            bitmap = bitmap!!.asImageBitmap(),
            contentDescription = "朋友圈配图",
            modifier = Modifier
                .fillMaxWidth()
                .height(170.dp)
                .clip(shape),
            contentScale = ContentScale.Crop
        )
    } else {
        Image(
            painter = painterResource(R.drawable.moment_walk_sakura),
            contentDescription = "朋友圈配图",
            modifier = Modifier
                .fillMaxWidth()
                .height(170.dp)
                .clip(shape),
            contentScale = ContentScale.Crop
        )
    }
}

@Composable
fun CalendarMonthPanel(
    month: YearMonth,
    selectedDate: LocalDate,
    eventsByDay: Map<LocalDate?, List<CalendarItem>>,
    onPreviousMonth: () -> Unit,
    onNextMonth: () -> Unit,
    onSelectDate: (LocalDate) -> Unit,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xF9FFF2DE)),
        shape = RoundedCornerShape(18.dp),
        border = BorderStroke(2.dp, Color(0xFFD7B491))
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onPreviousMonth) { Text("‹", fontSize = 34.sp, color = Color(0xFFC6765B)) }
                Text(
                    "${month.year}年${month.monthValue}月",
                    modifier = Modifier.weight(1f),
                    textAlign = TextAlign.Center,
                    fontSize = 27.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF4A2A2B)
                )
                TextButton(onClick = onNextMonth) { Text("›", fontSize = 34.sp, color = Color(0xFFC6765B)) }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                listOf("日", "一", "二", "三", "四", "五", "六").forEach { label ->
                    Surface(
                        color = Color(0xFFF4B9AD),
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.weight(1f)
                    ) {
                        Text(label, textAlign = TextAlign.Center, color = Color(0xFF5C2E24), fontSize = 18.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(vertical = 8.dp))
                    }
                }
            }
            calendarCells(month).chunked(7).forEach { week ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    week.forEach { day ->
                        CalendarDayCell(
                            date = day,
                            events = day?.let { eventsByDay[it].orEmpty() }.orEmpty(),
                            selected = day == selectedDate,
                            onSelect = onSelectDate,
                            modifier = Modifier
                                .weight(1f)
                                .aspectRatio(0.82f)
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun CalendarDayCell(date: LocalDate?, events: List<CalendarItem>, selected: Boolean, onSelect: (LocalDate) -> Unit, modifier: Modifier = Modifier) {
    if (date == null) {
        Box(modifier)
        return
    }
    val today = date == LocalDate.now()
    val important = events.maxByOrNull { it.salience }
    val festival = festivalLabel(date).takeIf { it != "樱花季" }
    val bg = when {
        selected -> Color(0xFFFFC8A8)
        today -> Color(0xFFFFEEE2)
        date.dayOfWeek.value == 6 || date.dayOfWeek.value == 7 -> Color(0xFFFFE3E8)
        else -> Color(0xFFFFF8ED)
    }
    Surface(
        modifier = modifier.clickable { onSelect(date) },
        color = bg,
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(if (selected) 2.dp else 1.dp, if (selected) Color(0xFFE86B8D) else Color(0xFFD7B491))
    ) {
        Column(Modifier.padding(5.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            Text(date.dayOfMonth.toString(), color = Color(0xFF4A2A2B), fontSize = 22.sp, fontWeight = FontWeight.Bold)
            val label = festival ?: important?.title.orEmpty()
            if (label.isNotBlank()) {
                Text(label, color = if ((important?.salience ?: 0) >= 60 || festival != null) Color(0xFFE86B8D) else Color(0xFF8F6B62), fontSize = 10.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
            }
        }
    }
}

@Composable
fun DayScheduleCard(date: LocalDate, events: List<CalendarItem>, modifier: Modifier = Modifier) {
    val summary = events
        .distinctBy { it.title }
        .sortedBy { it.startAt }
        .take(6)
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = Color(0xF9FFF8ED)),
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.5.dp, Color(0xFFD7B491))
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${date.monthValue}月${date.dayOfMonth}日 ${weekdayLabel(date)}", color = Color(0xFF5C2E24), fontSize = 20.sp, fontWeight = FontWeight.Bold)
            if (summary.isEmpty()) {
                Text("今天暂时没有特别安排。", color = Color(0xFF8F6B62))
            } else {
                summary.forEach { item ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(formatEventTime(item.startAt), color = Color(0xFFE86B8D), fontWeight = FontWeight.Bold, modifier = Modifier.width(54.dp))
                        Column(Modifier.weight(1f)) {
                            Text(item.title, color = Color(0xFF3D211D), fontWeight = FontWeight.Bold)
                            Text(statusLabel(item.status), color = Color(0xFF8F6B62), fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }
}

fun calendarCells(month: YearMonth): List<LocalDate?> {
    val first = month.atDay(1)
    val leading = first.dayOfWeek.value % 7
    val days = (1..month.lengthOfMonth()).map { month.atDay(it) }
    val trailing = (7 - ((leading + days.size) % 7)) % 7
    return List(leading) { null } + days + List(trailing) { null }
}

fun parseLocalDate(value: String): LocalDate? {
    return runCatching { LocalDate.parse(value.take(10)) }.getOrNull()
}

fun formatEventTime(value: String): String {
    val time = value.substringAfter("T", "").take(5)
    return time.ifBlank { "--:--" }
}

fun statusLabel(value: String): String {
    return when (value) {
        "completed" -> "已完成"
        "interrupted" -> "被打断"
        "pending" -> "待进行"
        else -> value.ifBlank { "待进行" }
    }
}

@Composable
fun JournalHeader(onBack: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = onBack) { Text("‹ 返回", color = Color(0xFF5C2E24), fontSize = 22.sp) }
        Text(
            "我们的日记本 ♥",
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
            color = Color(0xFF5C2E24),
            fontSize = 29.sp,
            fontWeight = FontWeight.Bold
        )
        Spacer(Modifier.width(72.dp))
    }
    Divider(color = Color(0x995C2E24), thickness = 2.dp)
}

@Composable
fun JournalProfileSection(relation: RelationState) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBF2)),
            shape = RoundedCornerShape(2.dp),
            modifier = Modifier
                .width(145.dp)
                .shadow(5.dp)
        ) {
            Column(Modifier.padding(8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Image(
                    painter = painterResource(R.drawable.character_atri_happy),
                    contentDescription = "小樱",
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(116.dp)
                        .background(Color(0xFFFFE4EA)),
                    contentScale = ContentScale.Crop
                )
                Text("小樱", color = Color(0xFF5C2E24), fontSize = 18.sp, fontWeight = FontWeight.Bold)
            }
        }
        Spacer(Modifier.width(18.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text("我眼中的小樱 ♥", color = Color(0xFF5C2E24), fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Text("生日：3月15日", color = Color(0xFF3D211D), fontSize = 18.sp)
            Text("喜欢：草莓蛋糕、读书、钢琴", color = Color(0xFF3D211D), fontSize = 18.sp)
            Text("性格：温柔、害羞、善良", color = Color(0xFF3D211D), fontSize = 18.sp)
            Text("关系：${relation.stage}  ${heartMeter(relation.affection)}", color = Color(0xFF3D211D), fontSize = 18.sp)
        }
    }
}

@Composable
fun JournalPerspectiveSection(relation: RelationState) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xAAFFFFFF)),
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, Color(0x55B08A6A))
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("☺", color = Color(0xFF5C2E24), fontSize = 72.sp, modifier = Modifier.width(92.dp), textAlign = TextAlign.Center)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("小樱眼中的我 ♥", color = Color(0xFF5C2E24), fontSize = 24.sp, fontWeight = FontWeight.Bold)
                Text("总是很温柔地对我，会认真听我说话。陪我散步的时候很开心。", color = Color(0xFF3D211D), fontSize = 18.sp, lineHeight = 26.sp)
                Text("印象：可靠、体贴  信任 ${relation.trust}", color = Color(0xFF3D211D), fontSize = 17.sp)
            }
        }
    }
}

@Composable
fun JournalMemoryRow(memory: MemoryItem) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xCCFFFDF8)),
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, Color(0x55B08A6A))
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text("${formatMemoryDate(memory.createdAt)}  ${memory.layer}", color = Color(0xFFE86B8D), fontWeight = FontWeight.Bold, fontSize = 15.sp)
            Text(memory.content, color = Color(0xFF2F1D1A), fontSize = 18.sp, lineHeight = 26.sp)
            Text("重要度 ${"%.2f".format(memory.importance)}  置信度 ${"%.2f".format(memory.confidence)}", fontSize = 12.sp, color = Color(0xFF8F6B62))
        }
    }
}

fun formatMomentTime(value: String): String {
    if (value.isBlank()) return "刚刚"
    if (!value.first().isDigit()) return value
    return value.replace("T", " ").take(16)
}

fun formatMemoryDate(value: String): String {
    if (value.isBlank()) return "未记录日期"
    return value.replace("T", " ").take(10)
}

@Composable
fun RelationPanel(relation: RelationState) {
    Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("关系阶段：${relation.stage}", fontWeight = FontWeight.Bold)
        Text("好感 ${relation.affection}  信任 ${relation.trust}  依赖 ${relation.dependency}  心情 ${relation.mood}")
        Divider()
    }
}

@Composable
fun SettingsScreen(vm: MainViewModel) {
    var backend by remember(vm.baseUrl) { mutableStateOf(vm.baseUrl) }
    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        item {
            Text("设置", fontSize = 26.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4A2A2B))
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBFA))) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("电脑连接", fontWeight = FontWeight.Bold)
                    OutlinedTextField(backend, { backend = it }, label = { Text("电脑后端地址") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    Button(onClick = { vm.saveBaseUrl(backend) }, modifier = Modifier.fillMaxWidth()) { Text("保存并检测连接") }
                    Text(vm.connectionMessage, color = Color(0xFF79545B))
                    Text("电脑管理台地址：${vm.baseUrl}/admin", color = Color(0xFF98717A), fontSize = 12.sp)
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBFA))) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("陪伴体验", fontWeight = FontWeight.Bold)
                    SettingSwitch("语音播放", "开启后会自动播放小樱回复的语音。", vm.ttsEnabled) { vm.updateTtsEnabled(it) }
                    SettingSwitch("主动提醒", "开启后桌面组件和本地通知会显示新消息。", vm.notificationsEnabled) { vm.updateNotificationsEnabled(it) }
                }
            }
        }
        item {
            OutlinedButton(onClick = { vm.clearLocalState() }, modifier = Modifier.fillMaxWidth()) {
                Text("清理本地临时状态")
            }
        }
    }
}

@Composable
fun SettingSwitch(label: String, description: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, fontWeight = FontWeight.Bold)
            Text(description, color = Color(0xFF98717A), fontSize = 12.sp)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
