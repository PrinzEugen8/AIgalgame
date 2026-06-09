package com.aigalgame.demo

import android.Manifest
import android.app.Application
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.launch
import org.json.JSONObject
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
        setContent {
            GalgameTheme {
                AiGalgameApp(viewModel)
            }
        }
    }
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = SettingsStore(application)
    private var api: ApiClient? = null

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
    var selectedCharacter by mutableStateOf("atri")
    var selectedBackground by mutableStateOf("classroom")
    var previewEmotion by mutableStateOf("calm")
    var ttsEnabled by mutableStateOf(true)
    var notificationsEnabled by mutableStateOf(true)

    val lines = mutableStateListOf<DialogueLine>()
    val normalReplies = mutableStateListOf<ReplyOption>()
    val keyReplies = mutableStateListOf<ReplyOption>()
    val moments = mutableStateListOf<MomentItem>()
    val memories = mutableStateListOf<MemoryItem>()
    val calendar = mutableStateListOf<CalendarItem>()

    init {
        viewModelScope.launch {
            settings.baseUrl.collect { saved ->
                baseUrl = saved
                if (saved.isNotBlank()) {
                    api = ApiClient(saved)
                    refreshBootstrap()
                }
            }
        }
    }

    fun currentLine(): DialogueLine? = lines.getOrNull(currentLineIndex)

    fun saveBaseUrl(value: String) {
        val cleaned = value.trim().trimEnd('/')
        viewModelScope.launch {
            settings.saveBaseUrl(cleaned)
            baseUrl = cleaned
            api = ApiClient(cleaned)
            testHealth()
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

    fun refreshBootstrap() {
        val client = api ?: return
        launchBusy {
            val boot = client.bootstrap()
            val rel = boot.optObject("relation")
            relation = RelationState(
                affection = rel.optInt("affection", relation.affection),
                trust = rel.optInt("trust", relation.trust),
                dependency = rel.optInt("dependency", relation.dependency),
                mood = rel.optInt("mood", relation.mood),
                stage = rel.optString("stage", relation.stage)
            )
            if (lines.isEmpty()) {
                openApp()
            }
        }
    }

    fun openApp() {
        val client = api ?: return
        launchBusy {
            applyEvent(client.postEvent("app_opened", storyIndex = storyIndex))
        }
    }

    fun advanceLine() {
        if (currentLineIndex < lines.lastIndex) {
            currentLineIndex += 1
        } else if (keyReplies.isEmpty() && normalReplies.isEmpty()) {
            openApp()
        }
    }

    fun selectReply(option: ReplyOption) {
        val client = api ?: return
        launchBusy {
            val type = if (option.type == "key") "option_selected" else "user_message"
            applyEvent(client.postEvent(type, text = option.text, replyId = option.id, storyIndex = storyIndex))
        }
    }

    fun sendUserMessage(text: String) {
        if (text.isBlank()) return
        val client = api ?: return
        launchBusy {
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
                        add(comment.optString("content"))
                    }
                }
                moments.add(
                    MomentItem(
                        id = item.optString("moment_id"),
                        authorName = item.optString("author_name", "小樱"),
                        text = item.optString("text"),
                        mediaUrl = item.optString("media_url"),
                        likes = item.optInt("likes"),
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
                        confidence = item.optDouble("confidence")
                    )
                )
            }
        }
    }

    fun loadCalendar() {
        val client = api ?: return
        launchBusy {
            val rows = client.calendar().optArray("days")
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
    }

    fun chooseBackground(value: String) {
        selectedBackground = value
    }

    fun choosePreviewEmotion(value: String) {
        previewEmotion = value
    }

    fun clearLocalState() {
        lines.clear()
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
        val payload = event.optObject("payload")
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
            lines.add(
                DialogueLine(
                    id = item.optString("line_id"),
                    text = item.optString("text"),
                    emotion = item.optString("emotion", "calm"),
                    pose = item.optString("pose", "idle"),
                    ttsUrl = item.optString("tts_audio_url"),
                    ttsError = item.optString("tts_error")
                )
            )
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
    LaunchedEffect(line?.ttsUrl, vm.ttsEnabled) {
        val url = line?.ttsUrl.orEmpty()
        if (vm.ttsEnabled && url.isNotBlank()) {
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
    NavigationBar(containerColor = Color(0xFFFFFBFA)) {
        val items = listOf(
            AppScreen.Home to "首页",
            AppScreen.DressUp to "装扮",
            AppScreen.Settings to "设置"
        )
        items.forEach { (screen, label) ->
            NavigationBarItem(
                selected = vm.screen == screen,
                onClick = { vm.screen = screen },
                icon = { Text(label.take(1), fontWeight = FontWeight.Bold) },
                label = { Text(label, maxLines = 1) }
            )
        }
    }
}

@Composable
fun HomeScreen(vm: MainViewModel) {
    var input by remember { mutableStateOf("") }
    val line = vm.currentLine()
    Box(Modifier.fillMaxSize()) {
        SakuraSceneBackground(vm.selectedBackground)
        Column(Modifier.fillMaxSize().padding(16.dp).navigationBarsPadding().imePadding()) {
            HomeHeader(vm)
            Box(Modifier.weight(1f).fillMaxWidth()) {
                CharacterStandee(
                    character = vm.selectedCharacter,
                    emotion = line?.emotion ?: "calm",
                    pose = line?.pose ?: "idle",
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .height(520.dp)
                )
            }
            KeyReplies(vm)
            DialogueBox(
                line = line,
                canAdvance = vm.lines.isNotEmpty(),
                onAdvance = { vm.advanceLine() }
            )
            ReplyStrip(vm)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = input,
                    onValueChange = { input = it },
                    label = { Text("输入回复") },
                    modifier = Modifier.weight(1f),
                    maxLines = 2
                )
                Spacer(Modifier.width(8.dp))
                Button(onClick = {
                    vm.sendUserMessage(input)
                    input = ""
                }) {
                    Text("发送")
                }
            }
        }
    }
}

@Composable
fun HomeHeader(vm: MainViewModel) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Column(
            Modifier
                .weight(1f)
                .clickable {
                    vm.screen = AppScreen.Calendar
                    vm.loadCalendar()
                }
        ) {
            Text("14:30", color = Color.White, fontSize = 30.sp, fontWeight = FontWeight.Bold)
            Text("第 1 天", color = Color.White, fontSize = 16.sp)
        }
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xCCFFFFFF)),
            shape = RoundedCornerShape(18.dp),
            modifier = Modifier.clickable {
                vm.screen = AppScreen.Journal
                vm.loadJournal()
            }
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("小樱", fontWeight = FontWeight.Bold, color = Color(0xFF563238))
                Text("好感 ${vm.relation.affection}", color = Color(0xFFE86B8D), fontSize = 13.sp)
            }
        }
        Spacer(Modifier.width(10.dp))
        Button(onClick = {
            vm.screen = AppScreen.Moments
            vm.loadMoments()
        }, colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE86B8D))) {
            Text("朋友圈")
        }
    }
}

@Composable
fun DialogueBox(line: DialogueLine?, canAdvance: Boolean, onAdvance: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .height(128.dp)
            .clickable(enabled = canAdvance) { onAdvance() },
        colors = CardDefaults.cardColors(containerColor = Color(0xEEFFF8F0)),
        shape = RoundedCornerShape(18.dp)
    ) {
        Column(Modifier.padding(16.dp)) {
            Text("小樱", color = Color(0xFFC86278), fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text(
                line?.text ?: "连接电脑后端后，小樱会在这里和你说话。",
                color = Color(0xFF4A2A2B),
                fontSize = 20.sp,
                lineHeight = 27.sp,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis
            )
            if (!line?.ttsError.isNullOrBlank()) {
                Text("语音暂不可用：${line?.ttsError}", color = Color(0xFF9A5A62), fontSize = 11.sp, maxLines = 1)
            }
        }
    }
}

@Composable
fun ReplyStrip(vm: MainViewModel) {
    if (vm.normalReplies.isEmpty()) return
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        vm.normalReplies.take(2).forEach { option ->
            OutlinedButton(onClick = { vm.selectReply(option) }, modifier = Modifier.weight(1f)) {
                Text(option.text, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
fun KeyReplies(vm: MainViewModel) {
    if (vm.keyReplies.isEmpty()) return
    Column(Modifier.fillMaxWidth().padding(bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        vm.keyReplies.forEach { option ->
            Button(
                onClick = { vm.selectReply(option) },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFF7A2C))
            ) {
                Text("${option.text}  好感${signed(option.affection)} 信任${signed(option.trust)}", maxLines = 2)
            }
        }
    }
}

fun signed(value: Int): String = if (value >= 0) "+$value" else value.toString()

@Composable
fun SakuraSceneBackground(style: String) {
    Canvas(Modifier.fillMaxSize()) {
        val palette = when (style) {
            "street" -> listOf(Color(0xFF9BD4F6), Color(0xFFFFDCE8), Color(0xFFF6E7D0))
            "room" -> listOf(Color(0xFFFFD6C4), Color(0xFFFFF3E6), Color(0xFFDDE9F4))
            else -> listOf(Color(0xFF86CFF7), Color(0xFFFFE4EA), Color(0xFFFFF8F0))
        }
        drawRect(Brush.verticalGradient(palette))
        repeat(18) { i ->
            val x = (size.width / 18f) * i
            val y = 70f + (i % 5) * 48f
            drawCircle(Color(0x55FFFFFF), radius = 36f + (i % 3) * 14f, center = Offset(x, y))
            drawCircle(Color(0x55F6A8B9), radius = 20f + (i % 4) * 8f, center = Offset(x + 25f, y + 18f))
        }
        drawRect(Color(0x66FFFFFF), topLeft = Offset(0f, size.height * 0.28f), size = Size(size.width, size.height * 0.35f))
        for (i in 0..4) {
            val left = i * size.width / 4f
            drawRect(Color(0x80AFC8D8), topLeft = Offset(left, size.height * 0.31f), size = Size(8f, size.height * 0.3f))
        }
        drawRect(Color(0x55C68A50), topLeft = Offset(0f, size.height * 0.67f), size = Size(size.width, size.height * 0.33f))
    }
}

@Composable
fun CharacterStandee(character: String, emotion: String, pose: String, modifier: Modifier = Modifier) {
    Image(
        painter = painterResource(characterImageRes(character, emotion, pose)),
        contentDescription = "角色立绘",
        modifier = modifier,
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
    LazyColumn(
        Modifier
            .fillMaxSize()
            .background(Color(0xFFFFF7F4))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        item {
            Text("装扮", fontSize = 26.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4A2A2B))
            Text("切换立绘、表情和场景预览。", color = Color(0xFF79545B))
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBFA))) {
                Box(Modifier.fillMaxWidth().height(460.dp)) {
                    SakuraSceneBackground(vm.selectedBackground)
                    CharacterStandee(
                        character = vm.selectedCharacter,
                        emotion = vm.previewEmotion,
                        pose = vm.previewEmotion,
                        modifier = Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(440.dp)
                    )
                }
            }
        }
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
    var comment by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().background(Color(0xFFFFF7F4))) {
        TopAppBar(
            title = { Text("朋友圈") },
            navigationIcon = { TextButton(onClick = { vm.screen = AppScreen.Home }) { Text("返回") } }
        )
        LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(vm.moments) { item ->
                Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFFBFA))) {
                    Column(Modifier.padding(14.dp)) {
                        Text(item.authorName, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text(item.createdAt.take(16), color = Color(0xFF98717A), fontSize = 12.sp)
                        Spacer(Modifier.height(8.dp))
                        Text(item.text, fontSize = 18.sp, lineHeight = 25.sp)
                        if (item.mediaUrl.isNotBlank()) {
                            Text("图片资源：${vm.resolveUrl(item.mediaUrl)}", color = Color(0xFF5E8DB8), fontSize = 12.sp)
                        }
                        Spacer(Modifier.height(10.dp))
                        Text("点赞 ${item.likes}", color = Color(0xFFE86B8D))
                        item.comments.forEach { Text("评论：$it", color = Color(0xFF563238)) }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            OutlinedButton(onClick = { vm.likeMoment(item.id) }) { Text("点赞") }
                            Spacer(Modifier.width(8.dp))
                            OutlinedTextField(value = comment, onValueChange = { comment = it }, label = { Text("评论") }, modifier = Modifier.weight(1f), singleLine = true)
                            Spacer(Modifier.width(8.dp))
                            Button(onClick = { vm.commentMoment(item.id, comment); comment = "" }) { Text("发") }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarScreen(vm: MainViewModel) {
    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("日历") },
            navigationIcon = { TextButton(onClick = { vm.screen = AppScreen.Home }) { Text("返回") } }
        )
        LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(vm.calendar.take(96)) { item ->
                Card(colors = CardDefaults.cardColors(containerColor = if (item.salience >= 60) Color(0xFFFFECEF) else Color.White)) {
                    Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(item.title, fontWeight = FontWeight.Bold)
                            Text(item.startAt.take(16), color = Color(0xFF98717A), fontSize = 12.sp)
                        }
                        Text(item.status, color = Color(0xFF5E8DB8))
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JournalScreen(vm: MainViewModel) {
    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("手账") },
            navigationIcon = { TextButton(onClick = { vm.screen = AppScreen.Home }) { Text("返回") } }
        )
        RelationPanel(vm.relation)
        LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(vm.memories) { memory ->
                Card {
                    Column(Modifier.padding(12.dp)) {
                        Text(memory.layer, color = Color(0xFFE86B8D), fontWeight = FontWeight.Bold)
                        Text(memory.content)
                        Text("置信度 ${"%.2f".format(memory.confidence)}", fontSize = 12.sp, color = Color(0xFF98717A))
                    }
                }
            }
        }
    }
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
                    SettingSwitch("语音播放", "开启后会自动播放小樱回复的语音。", vm.ttsEnabled) { vm.ttsEnabled = it }
                    SettingSwitch("主动提醒", "开启后桌面组件和本地通知会显示新消息。", vm.notificationsEnabled) { vm.notificationsEnabled = it }
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
