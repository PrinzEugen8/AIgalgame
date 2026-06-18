using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-75)]
    public sealed class AIGalgameRelaxRoomUI : MonoBehaviour
    {
        [Header("Backend")]
        [SerializeField] private string backendBaseUrl = "http://127.0.0.1:8899";
        [SerializeField] private string userId = "demo_user";
        [SerializeField] private string characterId = "atri";
        [SerializeField] private string sessionId = "relaxroom_unity";

        [Header("Targets")]
        [SerializeField] private AIGalgameChatdollController controller;
        [SerializeField] private AIGalgameRelaxRoomMotionDirector motionDirector;
        [SerializeField] private AIGalgameRelaxRoomExpressionDirector expressionDirector;
        [SerializeField] private AIGalgameRelaxRoomGazeDirector gazeDirector;
        [SerializeField] private AIGalgameRelaxRoomTouchController touchController;
        [SerializeField] private AIGalgameMotionTestUI motionTestUI;
        [SerializeField] private AIGalgameChatdollDemoUI controllerTestUI;
        [SerializeField] private AudioSource audioSource;

        [Header("RelaxRoom behavior")]
        [SerializeField] private float replyArrivalLeadSeconds = 1.1f;
        [SerializeField] private float idleActionAfterSeconds = 55f;
        [SerializeField] private float idleActionRetrySeconds = 25f;

        [Header("Local ASR")]
        [SerializeField] private bool sendVoiceAfterRecognition = true;
        [SerializeField] private string androidAsrLanguage = "zh";
        [SerializeField, Range(1, 4)] private int androidAsrThreads = 2;
        [SerializeField] private int voiceRecordMaxSeconds = 10;
        [SerializeField] private int voiceRecordSampleRate = 16000;

        [Header("Scene UI")]
        [SerializeField] private GameObject canvasRoot;
        [SerializeField] private GameObject homeLayer;
        [SerializeField] private GameObject socialPanel;
        [SerializeField] private GameObject settingsPanel;
        [SerializeField] private RectTransform chatPanel;
        [SerializeField] private GameObject chatCollapsedGroup;
        [SerializeField] private GameObject chatExpandedGroup;
        [SerializeField] private GameObject historyViewport;
        [SerializeField] private ScrollRect historyScrollRect;
        [SerializeField] private Text timeText;
        [SerializeField] private Text dateText;
        [SerializeField] private Text dayText;
        [SerializeField] private Text statusText;
        [SerializeField] private Text chatStateText;
        [SerializeField] private Text testStatusText;
        [SerializeField] private Text voiceButtonText;
        [SerializeField] private Text voiceAutoSendButtonText;
        [SerializeField] private Text promptText;
        [SerializeField] private InputField inputField;
        [SerializeField] private InputField backendUrlField;
        [SerializeField] private Button socialButton;
        [SerializeField] private Button socialBackButton;
        [SerializeField] private Button socialComposeButton;
        [SerializeField] private Button settingsButton;
        [SerializeField] private Button settingsCloseButton;
        [SerializeField] private Button promptToggleButton;
        [SerializeField] private Button voiceButton;
        [SerializeField] private Button sendButton;
        [SerializeField] private Button saveBackendButton;
        [SerializeField] private Button healthButton;
        [SerializeField] private Button voiceAutoSendButton;
        [SerializeField] private Button stateTestButton;
        [SerializeField] private Button expressionTestButton;
        [SerializeField] private Button gazeTestButton;
        [SerializeField] private Button choiceBackButton;
        [SerializeField] private Button choicePrimaryButton;
        [SerializeField] private Button choiceSecondaryButton;
        [SerializeField] private ChatMessageSlot[] chatMessageSlots = Array.Empty<ChatMessageSlot>();
        [SerializeField] private int relationshipDay = 128;
        [SerializeField] private string companionDisplayName = "小白";
        [SerializeField] private string userDisplayName = "你";
        [SerializeField] private string collapsedPrompt = "今日提示 · 她刚看完消息";
        [SerializeField] private string chatStateLabel = "夜雨空间 · 和小白聊天中";
        [SerializeField] private bool seedSceneConversation = false;

        [Header("Backend Data Endpoints")]
        [SerializeField] private bool fetchBackendDataOnStart = true;
        [SerializeField] private string homeBootstrapPath = "/api/relaxroom/home";
        [SerializeField] private string momentsPath = "/api/relaxroom/moments";
        [SerializeField] private string momentLikePathTemplate = "/api/moments/{moment_id}/like";
        [SerializeField] private string momentCommentPathTemplate = "/api/moments/{moment_id}/comments";

        [Header("Quick Replies (backend-driven)")]
        [SerializeField] private ScrollRect quickRepliesScroll;
        [SerializeField] private RectTransform quickRepliesContent;
        [SerializeField] private Button quickReplyChipTemplate;

        [Header("Moments / Friend Circle (backend-driven)")]
        [SerializeField] private RectTransform momentsContent;
        [SerializeField] private GameObject momentCardTemplate;
        [SerializeField] private Text momentsProfileName;
        [SerializeField] private Text momentsProfileStatus;
        [SerializeField] private Text momentsUpdateNote;
        [SerializeField] private Image momentsProfileAvatar;
        [SerializeField] private float momentsRefreshIntervalSeconds = 5f;
        [SerializeField] private string momentsStatusSuffixFallback = "今天也是好好地过";

        private const float CollapsedChatHeight = 316f;
        private const float ExpandedChatHeight = 1068f;
        private const int MaxHistoryEntries = 120;

        private BackendReplyOption[] pendingQuickReplies = Array.Empty<BackendReplyOption>();
        private MomentsResponse momentsData;
        private bool momentsFetchInFlight;
        private bool momentInteractionInFlight;
        private string pendingMomentCommentId = "";
        private string pendingMomentCommentAuthor = "";
        private float nextMomentsRefreshAt;
        private readonly Dictionary<string, Sprite> spriteCache = new();
        private readonly List<GameObject> spawnedHistorySlots = new();
        private readonly List<GameObject> spawnedQuickReplyChips = new();
        private readonly List<GameObject> spawnedMomentCards = new();

        private RectTransform historyContent;
        private ScrollRect momentsScrollRect;
        private Font font;
        private bool chatExpanded;
        private bool requestInFlight;
        private bool idleActionInFlight;
        private bool asrInFlight;
        private bool recordingVoice;
        private float lastInteractionAt;
        private float nextIdleActionCheckAt;
        private AudioClip recordedClip;
        private string activeMicrophoneDevice;
        private int recordedSamplePosition;
        private Coroutine startVoiceRoutine;
        private AIGalgameLocalAsrClient localAsrClient;
        private readonly List<ChatHistoryEntry> history = new();
        private int stateTestIndex;
        private int expressionTestIndex;
        private int gazeTestIndex;

        private void Start()
        {
            ResolveReferences();
            localAsrClient = new AIGalgameLocalAsrClient();
            localAsrClient.Configure(androidAsrLanguage, androidAsrThreads);
            BindSceneInterface();
            SetChatExpanded(false);
            SetHomeVisible(true);
            SetSocialVisible(false);
            SetSettingsVisible(false);
            RefreshClock();
            SeedInitialConversation();
            RefreshHistorySlots();
            ApplyMockMoments();
            lastInteractionAt = Time.time;
            ConfigureTouchController();
            SetStatus("准备好了");

            if (fetchBackendDataOnStart)
            {
                StartCoroutine(FetchHomeBootstrap());
                RequestMomentsRefresh(true);
            }
        }

        private void Update()
        {
            RefreshClock();

            if (Input.anyKeyDown)
            {
                RecordInteraction();
            }

            if (recordingVoice && recordedClip != null && !Microphone.IsRecording(activeMicrophoneDevice))
            {
                StopVoiceRecording();
            }

            TryStartIdleAction();
            TryRefreshMomentsWhileVisible();
        }

        private void OnDestroy()
        {
            localAsrClient?.Dispose();
            localAsrClient = null;
        }

        public void SetTargets(
            AIGalgameChatdollController targetController,
            AIGalgameMotionTestUI targetMotionTestUI,
            AIGalgameChatdollDemoUI targetControllerTestUI,
            AudioSource targetAudioSource,
            AIGalgameRelaxRoomMotionDirector targetMotionDirector = null,
            AIGalgameRelaxRoomTouchController targetTouchController = null)
        {
            controller = targetController;
            motionDirector = targetMotionDirector;
            touchController = targetTouchController;
            motionTestUI = targetMotionTestUI;
            controllerTestUI = targetControllerTestUI;
            audioSource = targetAudioSource;
            ConfigureTouchController();
        }

        private void ResolveReferences()
        {
            if (controller == null)
            {
                controller = FindFirstObjectByType<AIGalgameChatdollController>();
            }

            if (motionTestUI == null)
            {
                motionTestUI = FindFirstObjectByType<AIGalgameMotionTestUI>();
            }

            if (motionDirector == null)
            {
                motionDirector = FindFirstObjectByType<AIGalgameRelaxRoomMotionDirector>();
            }

            if (expressionDirector == null)
            {
                expressionDirector = controller != null
                    ? controller.GetComponent<AIGalgameRelaxRoomExpressionDirector>() ??
                        controller.GetComponentInChildren<AIGalgameRelaxRoomExpressionDirector>() ??
                        controller.GetComponentInParent<AIGalgameRelaxRoomExpressionDirector>()
                    : FindFirstObjectByType<AIGalgameRelaxRoomExpressionDirector>();
            }

            if (gazeDirector == null)
            {
                gazeDirector = controller != null
                    ? controller.GetComponent<AIGalgameRelaxRoomGazeDirector>() ??
                        controller.GetComponentInChildren<AIGalgameRelaxRoomGazeDirector>() ??
                        controller.GetComponentInParent<AIGalgameRelaxRoomGazeDirector>()
                    : FindFirstObjectByType<AIGalgameRelaxRoomGazeDirector>();
            }

            if (touchController == null)
            {
                touchController = FindFirstObjectByType<AIGalgameRelaxRoomTouchController>();
            }

            if (controllerTestUI == null)
            {
                controllerTestUI = FindFirstObjectByType<AIGalgameChatdollDemoUI>();
            }

            if (audioSource == null)
            {
                audioSource = GetComponent<AudioSource>() ?? FindFirstObjectByType<AudioSource>();
            }
        }

        private void BindSceneInterface()
        {
            font = LoadFont();

            if (canvasRoot == null)
            {
                var canvas = GetComponentInChildren<Canvas>(true);
                canvasRoot = canvas != null ? canvas.gameObject : gameObject;
            }

            if (historyScrollRect != null && historyViewport == null)
            {
                historyViewport = historyScrollRect.gameObject;
            }

            if (historyScrollRect != null)
            {
                historyContent = historyScrollRect.content;
            }

            if (momentsScrollRect == null && momentsContent != null)
            {
                momentsScrollRect = momentsContent.GetComponentInParent<ScrollRect>(true);
            }

            RegisterButton(socialButton, ShowSocialCircle);
            RegisterButton(socialBackButton, ShowHome);
            RegisterButton(socialComposeButton, () => SetStatus("朋友圈编辑入口已预留"));
            RegisterButton(settingsButton, () => SetSettingsVisible(settingsPanel == null || !settingsPanel.activeSelf));
            RegisterButton(settingsCloseButton, () => SetSettingsVisible(false));
            RegisterButton(promptToggleButton, () => SetChatExpanded(!chatExpanded));
            RegisterButton(voiceButton, ToggleVoiceRecording);
            RegisterButton(sendButton, SendCurrentInput);
            RegisterButton(saveBackendButton, SaveBackendUrl);
            RegisterButton(healthButton, () => StartCoroutine(CheckHealth()));
            RegisterButton(voiceAutoSendButton, ToggleVoiceAutoSend);
            RegisterButton(stateTestButton, TestNextStateStep);
            RegisterButton(expressionTestButton, TestNextExpressionStep);
            RegisterButton(gazeTestButton, TestNextGazeStep);
            RegisterButton(choiceBackButton, () => SetChatExpanded(false));
            HideLegacyChoiceButton(choicePrimaryButton);
            HideLegacyChoiceButton(choiceSecondaryButton);

            BindInputField(inputField);

            if (backendUrlField != null)
            {
                backendUrlField.text = backendBaseUrl;
            }

            if (promptText != null)
            {
                promptText.text = collapsedPrompt;
            }

            if (chatStateText != null)
            {
                chatStateText.text = chatStateLabel;
            }

            if (voiceAutoSendButtonText != null)
            {
                voiceAutoSendButtonText.text = AutoSendLabel();
            }
        }

        private static void RegisterButton(Button button, UnityEngine.Events.UnityAction action)
        {
            if (button == null || action == null)
            {
                return;
            }

            button.onClick.AddListener(action);
        }

        private void BindInputField(InputField field)
        {
            if (field == null)
            {
                return;
            }

            field.onValueChanged.AddListener(value =>
            {
                RecordInteraction();
            });
        }

        private static void HideLegacyChoiceButton(Button button)
        {
            if (button != null)
            {
                button.onClick.RemoveAllListeners();
                button.gameObject.SetActive(false);
            }
        }

        private string CurrentInputText()
        {
            return inputField != null ? inputField.text ?? "" : "";
        }

        private void SetInputText(string text)
        {
            if (inputField != null)
            {
                inputField.text = text ?? "";
            }
        }

        private void SaveBackendUrl()
        {
            backendBaseUrl = backendUrlField != null && !string.IsNullOrWhiteSpace(backendUrlField.text)
                ? backendUrlField.text.Trim()
                : backendBaseUrl;
            ConfigureTouchController();
            SetStatus("后端地址已保存");
        }

        private void ToggleVoiceAutoSend()
        {
            sendVoiceAfterRecognition = !sendVoiceAfterRecognition;
            if (voiceAutoSendButtonText != null)
            {
                voiceAutoSendButtonText.text = AutoSendLabel();
            }

            SetStatus(sendVoiceAfterRecognition ? "语音识别后自动发送" : "语音识别后停在输入框");
        }

        private void ApplyChoiceText(string text)
        {
            SetInputText(text);
            SetChatExpanded(true);
            RecordInteraction();
        }

        private void ShowSocialCircle()
        {
            SetHomeVisible(false);
            SetSettingsVisible(false);
            SetSocialVisible(true);
            if (momentsData != null)
            {
                RefreshMomentsProfile();
                PopulateMoments();
            }
            RequestMomentsRefresh(true);
            RecordInteraction();
        }

        private void ShowHome()
        {
            SetSocialVisible(false);
            SetHomeVisible(true);
            ClearPendingMomentComment();
            RecordInteraction();
        }

        #if UNITY_EDITOR
                // Editor-only verification hooks: drive the real parse -> apply -> render path with sample JSON.
                public void DebugApplyHomeJson(string json)
                {
                    ApplyHomeBootstrap(JsonUtility.FromJson<HomeBootstrapResponse>(json));
                }

                public void DebugApplyMomentsJson(string json)
                {
                    momentsData = JsonUtility.FromJson<MomentsResponse>(json);
                    RefreshMomentsProfile();
                    PopulateMoments();
                }
        #endif

                private IEnumerator FetchHomeBootstrap()
                {
            var url = AppendQuery(CombineUrl(backendBaseUrl, homeBootstrapPath));
            using var request = UnityWebRequest.Get(url);
            request.SetRequestHeader("Accept", "application/json");
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                SetStatus("主页数据获取失败，使用本地占位");
                yield break;
            }

            HomeBootstrapResponse data = null;
            try
            {
                data = JsonUtility.FromJson<HomeBootstrapResponse>(request.downloadHandler.text);
            }
            catch (Exception)
            {
                data = null;
            }

            if (data == null)
            {
                SetStatus("主页数据解析失败");
                yield break;
            }

            ApplyHomeBootstrap(data);
        }

        private void ApplyHomeBootstrap(HomeBootstrapResponse data)
        {
            if (data == null)
            {
                return;
            }

            if (TryComputeRelationshipDay(data.relationship_start_date, out var days))
            {
                relationshipDay = days;
            }

            if (!string.IsNullOrWhiteSpace(data.daily_tip))
            {
                collapsedPrompt = data.daily_tip;
                if (promptText != null)
                {
                    promptText.text = collapsedPrompt;
                }
            }

            if (!string.IsNullOrWhiteSpace(data.companion_name))
            {
                companionDisplayName = data.companion_name;
            }

            if (!string.IsNullOrWhiteSpace(data.user_name))
            {
                userDisplayName = data.user_name;
            }

            if (!string.IsNullOrWhiteSpace(data.chat_state_label))
            {
                chatStateLabel = data.chat_state_label;
                if (chatStateText != null)
                {
                    chatStateText.text = chatStateLabel;
                }
            }

            SetQuickReplyTexts(data.quick_replies);
            RefreshClock();
            RefreshHistorySlots();
            RefreshMomentsProfile();
        }

        private static bool TryComputeRelationshipDay(string startDateText, out int days)
        {
            days = 0;
            if (string.IsNullOrWhiteSpace(startDateText))
            {
                return false;
            }

            if (!DateTime.TryParse(startDateText, System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AssumeLocal, out var start))
            {
                return false;
            }

            days = Mathf.Max(1, (DateTime.Now.Date - start.Date).Days + 1);
            return true;
        }

        private void PopulateQuickReplies()
        {
            if (quickReplyChipTemplate == null || quickRepliesContent == null)
            {
                return;
            }

            foreach (var chip in spawnedQuickReplyChips)
            {
                if (chip != null)
                {
                    Destroy(chip);
                }
            }
            spawnedQuickReplyChips.Clear();

            quickReplyChipTemplate.gameObject.SetActive(false);

            if (pendingQuickReplies == null || pendingQuickReplies.Length == 0)
            {
                return;
            }

            foreach (var replyOption in pendingQuickReplies)
            {
                var replyText = replyOption?.text ?? "";
                if (string.IsNullOrWhiteSpace(replyText))
                {
                    continue;
                }

                var chipObj = Instantiate(quickReplyChipTemplate.gameObject, quickRepliesContent);
                chipObj.SetActive(true);
                var label = chipObj.GetComponentInChildren<Text>(true);
                if (label != null)
                {
                    label.text = replyText;
                }

                var captured = replyOption;
                var chipButton = chipObj.GetComponent<Button>();
                if (chipButton != null)
                {
                    chipButton.onClick.RemoveAllListeners();
                    chipButton.onClick.AddListener(() => OnQuickReplyChip(captured));
                }

                spawnedQuickReplyChips.Add(chipObj);
            }

            if (quickRepliesContent != null)
            {
                LayoutRebuilder.ForceRebuildLayoutImmediate(quickRepliesContent);
            }

            if (quickRepliesScroll != null)
            {
                quickRepliesScroll.horizontalNormalizedPosition = 0f;
            }
        }

        private void SetQuickReplyTexts(string[] replies)
        {
            if (replies == null || replies.Length == 0)
            {
                SetQuickReplyOptions(Array.Empty<BackendReplyOption>());
                return;
            }

            var options = new List<BackendReplyOption>();
            foreach (var text in replies)
            {
                if (string.IsNullOrWhiteSpace(text))
                {
                    continue;
                }

                options.Add(new BackendReplyOption { text = text.Trim(), type = "normal" });
            }

            SetQuickReplyOptions(options.ToArray());
        }

        private void SetQuickReplyOptions(BackendReplyOption[] options)
        {
            pendingQuickReplies = options ?? Array.Empty<BackendReplyOption>();
            PopulateQuickReplies();
        }

        private void ApplyBackendReplyOptions(BackendPayload payload)
        {
            if (payload == null)
            {
                SetQuickReplyOptions(Array.Empty<BackendReplyOption>());
                return;
            }

            var options = new List<BackendReplyOption>();
            AddReplyOptions(options, payload.key_replies, "key");
            AddReplyOptions(options, payload.normal_replies, "normal");
            SetQuickReplyOptions(options.ToArray());
        }

        private static void AddReplyOptions(List<BackendReplyOption> target, BackendReplyOption[] source, string fallbackType)
        {
            if (target == null || source == null)
            {
                return;
            }

            foreach (var option in source)
            {
                if (option == null || string.IsNullOrWhiteSpace(option.text))
                {
                    continue;
                }

                if (string.IsNullOrWhiteSpace(option.type))
                {
                    option.type = fallbackType;
                }

                target.Add(option);
            }
        }

        private void OnQuickReplyChip(BackendReplyOption option)
        {
            if (option == null)
            {
                return;
            }

            RecordInteraction();
            SendReplyOption(option);
        }

        private void TryRefreshMomentsWhileVisible()
        {
            if (!fetchBackendDataOnStart || socialPanel == null || !socialPanel.activeInHierarchy)
            {
                return;
            }

            if (Time.unscaledTime < nextMomentsRefreshAt)
            {
                return;
            }

            RequestMomentsRefresh(false);
        }

        private void RequestMomentsRefresh(bool force)
        {
            if (!fetchBackendDataOnStart || momentsFetchInFlight)
            {
                return;
            }

            if (!force && Time.unscaledTime < nextMomentsRefreshAt)
            {
                return;
            }

            nextMomentsRefreshAt = Time.unscaledTime + Mathf.Max(1f, momentsRefreshIntervalSeconds);
            StartCoroutine(FetchMoments());
        }

        private IEnumerator FetchMoments()
        {
            momentsFetchInFlight = true;
            var url = AppendQuery(CombineUrl(backendBaseUrl, momentsPath));
            using var request = UnityWebRequest.Get(url);
            request.SetRequestHeader("Accept", "application/json");
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                momentsFetchInFlight = false;
                ApplyMockMoments("朋友圈数据获取失败，显示本地 mock");
                yield break;
            }

            MomentsResponse data = null;
            try
            {
                data = JsonUtility.FromJson<MomentsResponse>(request.downloadHandler.text);
            }
            catch (Exception)
            {
                data = null;
            }

            if (data == null)
            {
                momentsFetchInFlight = false;
                ApplyMockMoments("朋友圈数据解析失败，显示本地 mock");
                yield break;
            }

            if (data.posts == null || data.posts.Length == 0)
            {
                momentsFetchInFlight = false;
                ApplyMockMoments("朋友圈暂时无后端数据，显示本地 mock");
                yield break;
            }

            momentsData = data;
            RefreshMomentsProfile();
            PopulateMoments();
            momentsFetchInFlight = false;
        }

        private void ApplyMockMoments(string status = null)
        {
            momentsData = CreateMockMomentsData();
            RefreshMomentsProfile();
            PopulateMoments();
            if (!string.IsNullOrWhiteSpace(status))
            {
                SetStatus(status);
            }
        }

        private static MomentsResponse CreateMockMomentsData()
        {
            string LocalAsset(string name) => "local://RelaxRoomUI/" + name;
            string MinutesAgo(int minutes) => DateTimeOffset.Now.AddMinutes(-minutes).ToString("O");
            string HoursAgo(int hours) => DateTimeOffset.Now.AddHours(-hours).ToString("O");

            return new MomentsResponse
            {
                profile = new MomentsProfile
                {
                    name = "小白",
                    status_suffix = "今天也是好好地过",
                    update_note = "本地 mock · 后端接入后自动覆盖",
                    avatar_url = LocalAsset("avatar_xiaobai")
                },
                posts = new[]
                {
                    new MomentPost
                    {
                        id = "mock_moment_curry",
                        username = "小白",
                        created_at = MinutesAgo(12),
                        visibility_label = "仅好友可见",
                        type = "image",
                        text = "咖喱饭比想象中更好吃。要是你也在就好了，我会把最大的土豆留给你。",
                        avatar_url = LocalAsset("avatar_xiaobai"),
                        images = new[]
                        {
                            LocalAsset("moment_curry_01"),
                            LocalAsset("moment_curry_02"),
                            LocalAsset("moment_curry_03")
                        },
                        likes = new[] { "你", "凛", "真白" },
                        comments = new[]
                        {
                            new MomentComment { user = "你", text = "那我下次一定去。", created_at = MinutesAgo(9) },
                            new MomentComment { user = "小白", text = "说好了，不许鸽。", created_at = MinutesAgo(7) }
                        }
                    },
                    new MomentPost
                    {
                        id = "mock_moment_rain",
                        username = "小白",
                        created_at = HoursAgo(2),
                        visibility_label = "仅好友可见",
                        type = "image",
                        text = "雨停了一小会儿。窗外的灯像被擦亮了一样。",
                        avatar_url = LocalAsset("avatar_xiaobai"),
                        images = new[] { LocalAsset("moment_rain_city") },
                        likes = new[] { "你", "由希" },
                        comments = new[]
                        {
                            new MomentComment { user = "你", text = "这个角度很好看。", created_at = HoursAgo(1) }
                        }
                    },
                    new MomentPost
                    {
                        id = "mock_moment_room",
                        username = "小白",
                        created_at = HoursAgo(6),
                        visibility_label = "仅好友可见",
                        type = "text",
                        text = "整理房间的时候翻到了旧票根。突然觉得，那天回家的路也挺短的。",
                        avatar_url = LocalAsset("avatar_xiaobai"),
                        likes = new[] { "你" },
                        comments = new[]
                        {
                            new MomentComment { user = "你", text = "因为有人陪你走。", created_at = HoursAgo(5) }
                        }
                    },
                    new MomentPost
                    {
                        id = "mock_moment_video",
                        username = "小白",
                        created_at = HoursAgo(20),
                        visibility_label = "仅好友可见",
                        type = "video",
                        text = "今天练习的时候录到一小段，还不太熟。",
                        avatar_url = LocalAsset("avatar_xiaobai"),
                        video_thumbnail = LocalAsset("moment_video_thumb"),
                        video_title = "夜晚练习片段",
                        video_duration = "00:18",
                        likes = new[] { "你", "凛" },
                        comments = new[]
                        {
                            new MomentComment { user = "你", text = "已经很好听了。", created_at = HoursAgo(18) }
                        }
                    }
                }
            };
        }

        private void RefreshMomentsProfile()
        {
            var profile = momentsData?.profile;
            if (momentsProfileName != null && profile != null && !string.IsNullOrWhiteSpace(profile.name))
            {
                momentsProfileName.text = profile.name;
            }

            if (momentsProfileStatus != null)
            {
                var suffix = profile != null && !string.IsNullOrWhiteSpace(profile.status_suffix)
                    ? profile.status_suffix
                    : momentsStatusSuffixFallback;
                momentsProfileStatus.text = $"第{Mathf.Max(1, relationshipDay)}天 · {suffix}";
            }

            if (momentsUpdateNote != null && profile != null && !string.IsNullOrWhiteSpace(profile.update_note))
            {
                momentsUpdateNote.text = profile.update_note;
            }

            if (momentsProfileAvatar != null && profile != null && !string.IsNullOrWhiteSpace(profile.avatar_url))
            {
                StartCoroutine(DownloadSprite(profile.avatar_url, sprite =>
                {
                    if (sprite != null && momentsProfileAvatar != null)
                    {
                        momentsProfileAvatar.sprite = sprite;
                    }
                }));
            }
        }

        private void PopulateMoments()
        {
            if (momentCardTemplate == null || momentsContent == null)
            {
                return;
            }

            foreach (var card in spawnedMomentCards)
            {
                if (card != null)
                {
                    Destroy(card);
                }
            }
            spawnedMomentCards.Clear();

            momentCardTemplate.SetActive(false);

            var posts = momentsData?.posts ?? Array.Empty<MomentPost>();
            foreach (var post in posts)
            {
                if (post == null)
                {
                    continue;
                }

                var cardObj = Instantiate(momentCardTemplate, momentsContent);
                cardObj.SetActive(true);
                BindMomentCard(cardObj.transform, post);
                ConfigureMomentCardLayout(cardObj, post);
                spawnedMomentCards.Add(cardObj);
            }

            RebuildMomentsLayout(resetScroll: true);
            StartCoroutine(RebuildMomentsLayoutNextFrame());
        }

        private void BindMomentCard(Transform card, MomentPost post)
        {
            SetChildText(card, "Name", post.username);
            SetChildText(card, "Meta", FormatMomentMeta(post));
            SetChildText(card, "Body", post.text);

            var likesText = post.likes != null && post.likes.Length > 0
                ? "♥ " + string.Join("，", post.likes)
                : "";
            SetChildText(card, "Likes/Text", likesText);
            SetChildActive(card, "Likes", !string.IsNullOrWhiteSpace(likesText));
            BindMomentActionButtons(card, post);

            var comments = post.comments ?? Array.Empty<MomentComment>();
            var commentsBuilder = new StringBuilder();
            foreach (var comment in comments)
            {
                if (comment == null || string.IsNullOrWhiteSpace(comment.text))
                {
                    continue;
                }

                if (commentsBuilder.Length > 0)
                {
                    commentsBuilder.Append('\n');
                }
                commentsBuilder.Append(string.IsNullOrWhiteSpace(comment.user) ? "" : comment.user + "：").Append(comment.text);
            }
            SetChildText(card, "Comments/Text", commentsBuilder.Length > 0 ? commentsBuilder.ToString() : "还没有评论");
            SetChildActive(card, "Comments", true);

            var avatar = FindChildImage(card, "Avatar");
            if (avatar != null && !string.IsNullOrWhiteSpace(post.avatar_url))
            {
                StartCoroutine(DownloadSprite(post.avatar_url, sprite =>
                {
                    if (sprite != null && avatar != null)
                    {
                        avatar.sprite = sprite;
                    }
                }));
            }

            var isVideo = string.Equals(post.type, "video", StringComparison.OrdinalIgnoreCase);
            var isImage = string.Equals(post.type, "image", StringComparison.OrdinalIgnoreCase) &&
                post.images != null && post.images.Length > 0;

            var imageGrid = FindChild(card, "ImageGrid");
            var videoForward = FindChild(card, "VideoForward");

            if (imageGrid != null)
            {
                imageGrid.gameObject.SetActive(isImage);
                if (isImage)
                {
                    BindImageGrid(imageGrid, post.images);
                }
            }

            if (videoForward != null)
            {
                videoForward.gameObject.SetActive(isVideo);
                if (isVideo)
                {
                    SetChildText(videoForward, "VideoTitle", post.video_title);
                    SetChildText(videoForward, "VideoSub", post.video_duration);
                    var videoThumb = FindChildImage(videoForward, "VideoThumb");
                    if (videoThumb != null && !string.IsNullOrWhiteSpace(post.video_thumbnail))
                    {
                        StartCoroutine(DownloadSprite(post.video_thumbnail, sprite =>
                        {
                            if (sprite != null && videoThumb != null)
                            {
                                videoThumb.sprite = sprite;
                            }
                        }));
                    }
                }
            }
        }

        private void BindMomentActionButtons(Transform card, MomentPost post)
        {
            if (card == null || post == null)
            {
                return;
            }

            var hasMomentId = !string.IsNullOrWhiteSpace(post.id);
            var likeButton = FindChild(card, "LikeButton")?.GetComponent<Button>();
            if (likeButton != null)
            {
                likeButton.onClick.RemoveAllListeners();
                likeButton.interactable = hasMomentId;
                SetChildText(likeButton.transform, "Text", HasUserLikedMoment(post) ? "♥ 已赞" : "♥ 点赞");
                var capturedId = post.id;
                likeButton.onClick.AddListener(() => OnMomentLikeClicked(capturedId));
            }

            var commentButton = FindChild(card, "CommentButton")?.GetComponent<Button>();
            if (commentButton != null)
            {
                commentButton.onClick.RemoveAllListeners();
                commentButton.interactable = hasMomentId;
                SetChildText(commentButton.transform, "Text", "评论");
                var capturedId = post.id;
                var capturedAuthor = post.username;
                commentButton.onClick.AddListener(() => OnMomentCommentClicked(capturedId, capturedAuthor));
            }
        }

        private bool HasUserLikedMoment(MomentPost post)
        {
            if (post?.likes == null)
            {
                return false;
            }

            foreach (var actor in post.likes)
            {
                if (string.Equals(actor, "你", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(actor, userDisplayName, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        private void OnMomentLikeClicked(string momentId)
        {
            if (string.IsNullOrWhiteSpace(momentId) || momentInteractionInFlight)
            {
                return;
            }

            StartCoroutine(PostMomentLike(momentId));
        }

        private void OnMomentCommentClicked(string momentId, string authorName)
        {
            if (string.IsNullOrWhiteSpace(momentId))
            {
                return;
            }

            pendingMomentCommentId = momentId;
            pendingMomentCommentAuthor = string.IsNullOrWhiteSpace(authorName) ? "这条动态" : authorName;
            SetInputText("");
            SetChatExpanded(true);
            SetStatus($"评论 {pendingMomentCommentAuthor} 的动态");
            RecordInteraction();
        }

        private void ClearPendingMomentComment()
        {
            pendingMomentCommentId = "";
            pendingMomentCommentAuthor = "";
        }

        private void ConfigureMomentCardLayout(GameObject cardObj, MomentPost post)
        {
            if (cardObj == null)
            {
                return;
            }

            var rect = cardObj.GetComponent<RectTransform>();
            var preferredHeight = PreferredMomentCardHeight(post);
            if (rect != null)
            {
                rect.anchorMin = new Vector2(0f, 1f);
                rect.anchorMax = new Vector2(1f, 1f);
                rect.pivot = new Vector2(0.5f, 1f);
                rect.anchoredPosition = Vector2.zero;
                rect.sizeDelta = new Vector2(0f, preferredHeight);
            }

            var layout = cardObj.GetComponent<LayoutElement>() ?? cardObj.AddComponent<LayoutElement>();
            layout.ignoreLayout = false;
            layout.minHeight = 240f;
            layout.preferredHeight = preferredHeight;
            layout.flexibleWidth = 1f;
            layout.flexibleHeight = 0f;
            layout.layoutPriority = 20;
        }

        private static float PreferredMomentCardHeight(MomentPost post)
        {
            var textLength = Mathf.Max(0, post?.text?.Length ?? 0);
            var textLines = Mathf.Clamp(Mathf.CeilToInt(textLength / 22f), 1, 5);
            var height = 154f + textLines * 34f;

            var imageCount = post?.images?.Length ?? 0;
            var isImage = string.Equals(post?.type, "image", StringComparison.OrdinalIgnoreCase) && imageCount > 0;
            if (isImage)
            {
                var rows = Mathf.CeilToInt(Mathf.Min(9, imageCount) / 3f);
                height += rows * 148f + Mathf.Max(0, rows - 1) * 8f + 16f;
            }

            if (string.Equals(post?.type, "video", StringComparison.OrdinalIgnoreCase))
            {
                height += 170f;
            }

            if (post?.likes != null && post.likes.Length > 0)
            {
                height += 48f;
            }

            height += 58f;

            var commentCount = post?.comments?.Length ?? 0;
            if (commentCount > 0)
            {
                height += Mathf.Min(150f, 28f + commentCount * 36f);
            }
            else
            {
                height += 50f;
            }

            return Mathf.Max(300f, height);
        }

        private IEnumerator RebuildMomentsLayoutNextFrame()
        {
            yield return null;
            RebuildMomentsLayout(resetScroll: true);
        }

        private void RebuildMomentsLayout(bool resetScroll)
        {
            if (momentsContent == null)
            {
                return;
            }

            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate(momentsContent);

            var height = PreferredMomentsContentHeight();
            momentsContent.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical, height);
            LayoutRebuilder.ForceRebuildLayoutImmediate(momentsContent);

            if (momentsScrollRect == null)
            {
                momentsScrollRect = momentsContent.GetComponentInParent<ScrollRect>(true);
            }

            if (momentsScrollRect != null)
            {
                momentsScrollRect.content = momentsContent;
                Canvas.ForceUpdateCanvases();
                if (resetScroll)
                {
                    momentsScrollRect.verticalNormalizedPosition = 1f;
                }
            }

        }

        private float PreferredMomentsContentHeight()
        {
            if (momentsContent == null)
            {
                return 0f;
            }

            var group = momentsContent.GetComponent<VerticalLayoutGroup>();
            var height = group != null ? group.padding.top + group.padding.bottom : 0f;
            var activeChildren = 0;

            for (var i = 0; i < momentsContent.childCount; i++)
            {
                var child = momentsContent.GetChild(i) as RectTransform;
                if (child == null || !child.gameObject.activeSelf)
                {
                    continue;
                }

                if (activeChildren > 0 && group != null)
                {
                    height += group.spacing;
                }

                height += Mathf.Max(1f, LayoutUtility.GetPreferredHeight(child));
                activeChildren++;
            }

            var viewportHeight = momentsScrollRect != null && momentsScrollRect.viewport != null
                ? momentsScrollRect.viewport.rect.height
                : 0f;
            return Mathf.Max(height, viewportHeight);
        }

        private void BindImageGrid(Transform imageGrid, string[] images)
        {
            var existing = new List<Transform>();
            for (var i = 0; i < imageGrid.childCount; i++)
            {
                existing.Add(imageGrid.GetChild(i));
            }

            if (existing.Count == 0)
            {
                return;
            }

            var template = existing[0];
            template.gameObject.SetActive(false);

            for (var i = 1; i < existing.Count; i++)
            {
                Destroy(existing[i].gameObject);
            }

            var shown = 0;
            for (var i = 0; i < images.Length; i++)
            {
                var url = images[i];
                if (string.IsNullOrWhiteSpace(url))
                {
                    continue;
                }

                var thumbObj = Instantiate(template.gameObject, imageGrid);
                thumbObj.SetActive(true);
                var innerImage = thumbObj.transform.Find("Image");
                var thumbImage = innerImage != null ? innerImage.GetComponent<Image>() : thumbObj.GetComponent<Image>();
                if (thumbImage != null)
                {
                    StartCoroutine(DownloadSprite(url, sprite =>
                    {
                        if (sprite != null && thumbImage != null)
                        {
                            thumbImage.sprite = sprite;
                        }
                    }));
                }
                shown++;
            }

            // Size the grid height to fit the rows so the card's vertical layout reserves enough space.
            var grid = imageGrid.GetComponent<GridLayoutGroup>();
            var gridLayout = imageGrid.GetComponent<LayoutElement>();
            if (grid != null && gridLayout != null && shown > 0)
            {
                var columns = Mathf.Max(1, grid.constraint == GridLayoutGroup.Constraint.FixedColumnCount ? grid.constraintCount : 3);
                var rows = Mathf.CeilToInt(shown / (float)columns);
                gridLayout.preferredHeight = rows * grid.cellSize.y + Mathf.Max(0, rows - 1) * grid.spacing.y;
            }

            LayoutRebuilder.ForceRebuildLayoutImmediate(imageGrid as RectTransform);
        }

        private IEnumerator DownloadSprite(string url, Action<Sprite> onComplete)
        {
            if (string.IsNullOrWhiteSpace(url))
            {
                onComplete?.Invoke(null);
                yield break;
            }

            if (spriteCache.TryGetValue(url, out var cached))
            {
                onComplete?.Invoke(cached);
                yield break;
            }

            var localSprite = LoadLocalSprite(url);
            if (localSprite != null)
            {
                spriteCache[url] = localSprite;
                onComplete?.Invoke(localSprite);
                yield break;
            }

            var fullUrl = CombineUrl(backendBaseUrl, url);
            using var request = UnityWebRequestTexture.GetTexture(fullUrl);
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                onComplete?.Invoke(null);
                yield break;
            }

            var texture = DownloadHandlerTexture.GetContent(request);
            if (texture == null)
            {
                onComplete?.Invoke(null);
                yield break;
            }

            var sprite = Sprite.Create(texture,
                new Rect(0f, 0f, texture.width, texture.height),
                new Vector2(0.5f, 0.5f), 100f);
            spriteCache[url] = sprite;
            onComplete?.Invoke(sprite);
        }

        private Sprite LoadLocalSprite(string url)
        {
            var path = LocalResourcePath(url);
            if (string.IsNullOrWhiteSpace(path))
            {
                return null;
            }

            var sprite = Resources.Load<Sprite>(path);
            if (sprite != null)
            {
                return sprite;
            }

            var texture = Resources.Load<Texture2D>(path);
            if (texture == null)
            {
                return null;
            }

            return Sprite.Create(texture,
                new Rect(0f, 0f, texture.width, texture.height),
                new Vector2(0.5f, 0.5f), 100f);
        }

        private static string LocalResourcePath(string url)
        {
            const string localPrefix = "local://";
            const string resourcesPrefix = "resources://";
            const string resourcePrefix = "resource://";

            string path;
            if (url.StartsWith(localPrefix, StringComparison.OrdinalIgnoreCase))
            {
                path = url.Substring(localPrefix.Length);
            }
            else if (url.StartsWith(resourcesPrefix, StringComparison.OrdinalIgnoreCase))
            {
                path = url.Substring(resourcesPrefix.Length);
            }
            else if (url.StartsWith(resourcePrefix, StringComparison.OrdinalIgnoreCase))
            {
                path = url.Substring(resourcePrefix.Length);
            }
            else
            {
                return "";
            }

            path = path.Trim().TrimStart('/', '\\').Replace("\\", "/");
            var extension = Path.GetExtension(path);
            return string.IsNullOrEmpty(extension) ? path : path.Substring(0, path.Length - extension.Length);
        }

        private string AppendQuery(string url)
        {
            var separator = url.Contains("?") ? "&" : "?";
            return url + separator +
                "user_id=" + UnityWebRequest.EscapeURL(userId) +
                "&character_id=" + UnityWebRequest.EscapeURL(characterId) +
                "&session_id=" + UnityWebRequest.EscapeURL(sessionId);
        }

        private static Transform FindChild(Transform parent, string path)
        {
            if (parent == null || string.IsNullOrWhiteSpace(path))
            {
                return null;
            }

            var child = parent.Find(path);
            if (child != null)
            {
                return child;
            }

            var segments = path.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            return segments.Length == 0 ? null : FindDescendantPath(parent, segments, 0);
        }

        private static Transform FindDescendantPath(Transform parent, string[] segments, int segmentIndex)
        {
            for (var i = 0; i < parent.childCount; i++)
            {
                var child = parent.GetChild(i);
                if (string.Equals(child.name, segments[segmentIndex], StringComparison.Ordinal))
                {
                    if (segmentIndex == segments.Length - 1)
                    {
                        return child;
                    }

                    var nested = FindDescendantPath(child, segments, segmentIndex + 1);
                    if (nested != null)
                    {
                        return nested;
                    }
                }

                var descendant = FindDescendantPath(child, segments, segmentIndex);
                if (descendant != null)
                {
                    return descendant;
                }
            }

            return null;
        }

        private static Image FindChildImage(Transform parent, string path)
        {
            var child = FindChild(parent, path);
            return child != null ? child.GetComponent<Image>() : null;
        }

        private static void SetChildText(Transform parent, string path, string value)
        {
            var child = FindChild(parent, path);
            if (child == null)
            {
                return;
            }

            var text = child.GetComponent<Text>();
            if (text != null)
            {
                text.text = value ?? "";
            }
        }

        private static void SetChildActive(Transform parent, string path, bool active)
        {
            var child = FindChild(parent, path);
            if (child != null)
            {
                child.gameObject.SetActive(active);
            }
        }

        private static string FormatMomentMeta(MomentPost post)
        {
            if (post == null)
            {
                return "";
            }

            var timeText = FormatRelativeTime(post.created_at);
            if (string.IsNullOrWhiteSpace(timeText))
            {
                timeText = post.time;
            }

            var visibility = string.IsNullOrWhiteSpace(post.visibility_label) ? "仅好友可见" : post.visibility_label;
            if (string.IsNullOrWhiteSpace(timeText))
            {
                return visibility;
            }

            return string.IsNullOrWhiteSpace(visibility) ? timeText : $"{timeText} · {visibility}";
        }

        private static string FormatRelativeTime(string timestamp)
        {
            if (string.IsNullOrWhiteSpace(timestamp))
            {
                return "";
            }

            if (!DateTimeOffset.TryParse(timestamp, System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AssumeUniversal, out var createdAt))
            {
                return "";
            }

            var elapsed = DateTimeOffset.Now - createdAt.ToLocalTime();
            if (elapsed.TotalSeconds < 0)
            {
                elapsed = TimeSpan.Zero;
            }

            if (elapsed.TotalMinutes < 1)
            {
                return "刚刚";
            }

            if (elapsed.TotalHours < 1)
            {
                return $"{Mathf.Max(1, Mathf.FloorToInt((float)elapsed.TotalMinutes))} 分钟前";
            }

            if (elapsed.TotalDays < 1)
            {
                return $"{Mathf.Max(1, Mathf.FloorToInt((float)elapsed.TotalHours))} 小时前";
            }

            if (elapsed.TotalDays < 2)
            {
                return "昨天";
            }

            if (elapsed.TotalDays < 7)
            {
                return $"{Mathf.FloorToInt((float)elapsed.TotalDays)} 天前";
            }

            return createdAt.ToLocalTime().ToString("MM / dd");
        }

        private void RefreshClock()
        {
            var now = DateTime.Now;
            if (timeText != null)
            {
                timeText.text = now.ToString("HH:mm");
            }

            if (dateText != null)
            {
                dateText.text = now.ToString("MM / dd");
            }

            if (dayText != null)
            {
                dayText.text = $"第 {Mathf.Max(1, relationshipDay)} 天";
            }
        }

        private void SendCurrentInput()
        {
            if (requestInFlight)
            {
                return;
            }

            var text = (CurrentInputText() ?? "").Trim();
            if (string.IsNullOrWhiteSpace(text))
            {
                SetStatus("先说点什么吧");
                return;
            }

            if (!string.IsNullOrWhiteSpace(pendingMomentCommentId))
            {
                var momentId = pendingMomentCommentId;
                SetInputText("");
                RecordInteraction();
                StartCoroutine(PostMomentComment(momentId, text));
                return;
            }

            SetInputText("");
            RecordInteraction();
            SetQuickReplyOptions(Array.Empty<BackendReplyOption>());
            BeginReplyRequestMotion();
            PlayPhoneNotificationSound();
            AddHistory("You", text, new Color(0.78f, 0.9f, 1f, 1f));
            StartCoroutine(PostUserMessage(text));
        }

        private void SendReplyOption(BackendReplyOption option)
        {
            if (requestInFlight || option == null)
            {
                return;
            }

            var text = (option.text ?? "").Trim();
            if (string.IsNullOrWhiteSpace(text))
            {
                return;
            }

            SetInputText("");
            SetQuickReplyOptions(Array.Empty<BackendReplyOption>());
            RecordInteraction();
            BeginReplyRequestMotion();
            PlayPhoneNotificationSound();
            AddHistory("You", text, new Color(0.78f, 0.9f, 1f, 1f));
            StartCoroutine(PostUserMessage(text, option.reply_id, IsKeyReplyOption(option)));
        }

        private static bool IsKeyReplyOption(BackendReplyOption option)
        {
            return option != null && string.Equals(option.type, "key", StringComparison.OrdinalIgnoreCase);
        }

        private IEnumerator PostMomentLike(string momentId)
        {
            if (momentInteractionInFlight)
            {
                yield break;
            }

            momentInteractionInFlight = true;
            SetStatus("点赞中...");

            var url = AppendQuery(CombineUrl(backendBaseUrl, BuildMomentPath(momentLikePathTemplate, momentId, "/api/moments/{moment_id}/like")));
            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes("{}"));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.SetRequestHeader("Accept", "application/json");

            yield return request.SendWebRequest();

            momentInteractionInFlight = false;
            if (request.result != UnityWebRequest.Result.Success)
            {
                SetStatus("点赞失败");
                yield break;
            }

            SetStatus("已点赞");
            RequestMomentsRefresh(true);
        }

        private IEnumerator PostMomentComment(string momentId, string text)
        {
            if (momentInteractionInFlight)
            {
                yield break;
            }

            momentInteractionInFlight = true;
            SetStatus("评论中...");

            var url = AppendQuery(CombineUrl(backendBaseUrl, BuildMomentPath(momentCommentPathTemplate, momentId, "/api/moments/{moment_id}/comments")));
            var body = $"{{\"content\":\"{EscapeJson(text)}\"}}";
            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.SetRequestHeader("Accept", "application/json");

            yield return request.SendWebRequest();

            momentInteractionInFlight = false;
            if (request.result != UnityWebRequest.Result.Success)
            {
                SetStatus("评论失败");
                yield break;
            }

            ClearPendingMomentComment();
            SetStatus("评论已发送");
            RequestMomentsRefresh(true);
        }

        private IEnumerator PostUserMessage(string text, string replyId = "", bool keyReply = false)
        {
            requestInFlight = true;
            SetStatus("发送中...");

            var url = CombineUrl(backendBaseUrl, "/api/events");
            var body = BuildUserMessageRequest(text, replyId, keyReply);
            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.SetRequestHeader("Accept", "application/json");

            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                requestInFlight = false;
                EndReplyWithoutMessageMotion();
                AddHistory("System", request.error, new Color(1f, 0.68f, 0.6f, 1f));
                SetStatus("后端请求失败");
                yield break;
            }

            var response = JsonUtility.FromJson<BackendEventResponse>(request.downloadHandler.text);
            if (response == null)
            {
                requestInFlight = false;
                EndReplyWithoutMessageMotion();
                AddHistory("System", "Invalid backend response", new Color(1f, 0.68f, 0.6f, 1f));
                SetStatus("后端返回异常");
                yield break;
            }

            if (string.Equals(response.event_type, "error", StringComparison.OrdinalIgnoreCase))
            {
                requestInFlight = false;
                EndReplyWithoutMessageMotion();
                AddHistory("Backend", response.payload != null ? response.payload.message : "Provider error", new Color(1f, 0.68f, 0.6f, 1f));
                SetStatus("后端返回错误");
                yield break;
            }

            var lines = response.payload?.lines ?? Array.Empty<AIGalgameDialogueLine>();
            if (lines.Length == 0)
            {
                requestInFlight = false;
                EndReplyWithoutMessageMotion();
                SetStatus("她暂时没有回复");
                yield break;
            }

            BeginReplyReceivedMotion(true);
            ApplyBackendReplyOptions(response.payload);

            SetStatus("她正在回复");
            if (replyArrivalLeadSeconds > 0f)
            {
                yield return new WaitForSeconds(replyArrivalLeadSeconds);
            }
            yield return PlayLines(
                lines,
                driveController: true,
                returnControllerIdleWhenDone: false,
                onLineStarted: line =>
                {
                    if (!string.IsNullOrWhiteSpace(line.text))
                    {
                        AddHistory("Atri", line.text, new Color(1f, 0.86f, 0.72f, 1f));
                    }
                });
            EndReplyWithoutMessageMotion();
            requestInFlight = false;
            RecordInteraction();
            SetStatus("准备好了");
        }

        private void TryStartIdleAction()
        {
            if (idleActionInFlight || requestInFlight || recordingVoice || asrInFlight || Time.time < nextIdleActionCheckAt)
            {
                return;
            }

            if (Time.time - lastInteractionAt < idleActionAfterSeconds)
            {
                return;
            }

            var target = GetLiveController();
            if (target != null && target.State != AIGalgameAvatarState.Idle)
            {
                return;
            }

            if (audioSource != null && audioSource.isPlaying)
            {
                return;
            }

            StartCoroutine(PostIdleAction());
        }

        private IEnumerator PostIdleAction()
        {
            idleActionInFlight = true;
            nextIdleActionCheckAt = Time.time + idleActionRetrySeconds;

            var url = CombineUrl(backendBaseUrl, "/api/relaxroom/idle");
            var body = BuildIdleActionRequest();
            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.SetRequestHeader("Accept", "application/json");
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                idleActionInFlight = false;
                nextIdleActionCheckAt = Time.time + idleActionRetrySeconds;
                yield break;
            }

            var response = JsonUtility.FromJson<BackendIdleActionResponse>(request.downloadHandler.text);
            var payload = response?.payload;
            if (payload == null || !payload.ok)
            {
                idleActionInFlight = false;
                nextIdleActionCheckAt = Time.time + idleActionRetrySeconds;
                yield break;
            }

            nextIdleActionCheckAt = Time.time + Mathf.Max(idleActionRetrySeconds, payload.cooldown_seconds);
            if (!string.IsNullOrWhiteSpace(payload.motion))
            {
                motionDirector?.PlayIdleAction(payload.motion, payload.action_duration_seconds);
            }

            var lines = payload.lines ?? Array.Empty<AIGalgameDialogueLine>();
            foreach (var line in lines)
            {
                if (!string.IsNullOrWhiteSpace(line.text))
                {
                    AddHistory("Atri", line.text, new Color(1f, 0.86f, 0.72f, 1f));
                }
            }

            if (lines.Length > 0)
            {
                yield return PlayLines(lines, driveController: true);
            }

            idleActionInFlight = false;
            RecordInteraction();
        }

        private IEnumerator PlayLines(
            AIGalgameDialogueLine[] lines,
            bool driveController,
            bool returnControllerIdleWhenDone = true,
            Action<AIGalgameDialogueLine> onLineStarted = null)
        {
            RecordInteraction();
            foreach (var line in lines)
            {
                AudioClip clip = null;
                if (!string.IsNullOrWhiteSpace(line.tts_audio_url))
                {
                    yield return DownloadAudio(line.tts_audio_url, value => clip = value);
                }

                if (clip != null && audioSource != null)
                {
                    audioSource.Stop();
                    audioSource.clip = clip;
                    audioSource.Play();
                }

                onLineStarted?.Invoke(line);

                if (driveController)
                {
                    PlayControllerLine(line, returnControllerIdleWhenDone);
                }

                var deadline = Time.time + EstimateLineSeconds(line);
                while ((audioSource != null && audioSource.isPlaying) || Time.time < deadline)
                {
                    yield return null;
                }
            }
        }

        private IEnumerator DownloadAudio(string url, Action<AudioClip> onComplete)
        {
            var fullUrl = CombineUrl(backendBaseUrl, url);
            using var request = UnityWebRequestMultimedia.GetAudioClip(fullUrl, AudioTypeForUrl(fullUrl));
            yield return request.SendWebRequest();

            if (request.result != UnityWebRequest.Result.Success)
            {
                SetStatus("语音下载失败");
                onComplete?.Invoke(null);
                yield break;
            }

            onComplete?.Invoke(DownloadHandlerAudioClip.GetContent(request));
        }

        private IEnumerator CheckHealth()
        {
            SetStatus("正在检查后端...");
            using var request = UnityWebRequest.Get(CombineUrl(backendBaseUrl, "/api/health"));
            yield return request.SendWebRequest();
            SetStatus(request.result == UnityWebRequest.Result.Success ? "后端连接正常" : $"后端检查失败：{request.error}");
        }

        private void ToggleVoiceRecording()
        {
            if (recordingVoice)
            {
                StopVoiceRecording();
                return;
            }

            if (asrInFlight)
            {
                SetStatus("语音识别还在进行");
                return;
            }

            if (startVoiceRoutine != null)
            {
                return;
            }

#if !UNITY_ANDROID || UNITY_EDITOR
            if (!AIGalgameLocalAsrClient.IsStandaloneRuntimeAvailable())
            {
                SetStatus("Windows 本地 ASR 运行库未安装");
                return;
            }
#endif

            startVoiceRoutine = StartCoroutine(StartVoiceRecording());
        }

        private IEnumerator StartVoiceRecording()
        {
#if UNITY_ANDROID || UNITY_IOS
            if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
            {
                SetStatus("正在请求麦克风权限...");
                yield return Application.RequestUserAuthorization(UserAuthorization.Microphone);
                if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
                {
                    startVoiceRoutine = null;
                    SetStatus("麦克风权限被拒绝");
                    yield break;
                }
            }
#endif
            if (Microphone.devices == null || Microphone.devices.Length == 0)
            {
                startVoiceRoutine = null;
                SetStatus("没有找到麦克风");
                yield break;
            }

            var maxSeconds = Mathf.Clamp(voiceRecordMaxSeconds, 1, 60);
            var sampleRate = Mathf.Clamp(voiceRecordSampleRate, 8000, 48000);
            activeMicrophoneDevice = null;
            recordedSamplePosition = 0;
            recordedClip = Microphone.Start(activeMicrophoneDevice, false, maxSeconds, sampleRate);
            if (recordedClip == null)
            {
                startVoiceRoutine = null;
                SetStatus("麦克风启动失败");
                yield break;
            }

            recordingVoice = true;
            startVoiceRoutine = null;
            SetStatus("正在录音...");
        }

        private void StopVoiceRecording()
        {
            if (!recordingVoice)
            {
                return;
            }

            recordedSamplePosition = recordedClip != null ? Microphone.GetPosition(activeMicrophoneDevice) : 0;
            Microphone.End(activeMicrophoneDevice);
            recordingVoice = false;

            var samples = ExtractRecordedMonoSamples(recordedClip, recordedSamplePosition);
            if (samples.Length == 0)
            {
                SetStatus("没有录到声音");
                return;
            }

            if (!HasAudibleVoice(samples))
            {
                SetStatus("没有检测到语音");
                return;
            }

            StartCoroutine(RecognizeVoiceSamples(samples, Mathf.Clamp(voiceRecordSampleRate, 8000, 48000)));
        }

        private IEnumerator RecognizeVoiceSamples(float[] monoSamples, int sampleRate)
        {
            asrInFlight = true;
            RecordInteraction();
            SetStatus("正在识别语音...");

            AIGalgameLocalAsrResult result;
#if UNITY_ANDROID && !UNITY_EDITOR
            localAsrClient ??= new AIGalgameLocalAsrClient();
            localAsrClient.Configure(androidAsrLanguage, androidAsrThreads);
            var recognizeTask = localAsrClient.RecognizeAndroidAsync(monoSamples, sampleRate);
            while (!recognizeTask.IsCompleted)
            {
                yield return null;
            }

            result = recognizeTask.IsFaulted
                ? AIGalgameLocalAsrResult.Fail(recognizeTask.Exception?.GetBaseException().Message ?? "Android ASR failed")
                : recognizeTask.Result;
#elif UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
            localAsrClient ??= new AIGalgameLocalAsrClient();
            localAsrClient.Configure(androidAsrLanguage, androidAsrThreads);
            var recognizeTask = localAsrClient.RecognizeStandaloneAsync(monoSamples, sampleRate);
            while (!recognizeTask.IsCompleted)
            {
                yield return null;
            }

            result = recognizeTask.IsFaulted
                ? AIGalgameLocalAsrResult.Fail(recognizeTask.Exception?.GetBaseException().Message ?? "Windows ASR failed")
                : recognizeTask.Result;
#else
            yield return null;
            result = AIGalgameLocalAsrResult.Fail("Bundled local ASR is not available on this platform yet");
#endif

            asrInFlight = false;
            var text = (result.Text ?? "").Trim();
            if (!result.Ok || string.IsNullOrWhiteSpace(text))
            {
                SetStatus(result.Ok ? "没有识别出内容" : $"语音识别失败：{result.Error}");
                yield break;
            }

            SetChatExpanded(true);
            AddHistory("You (voice)", text, new Color(0.78f, 0.9f, 1f, 1f));

            if (sendVoiceAfterRecognition && !requestInFlight)
            {
                SetInputText("");
                SetQuickReplyOptions(Array.Empty<BackendReplyOption>());
                BeginReplyRequestMotion();
                PlayPhoneNotificationSound();
                StartCoroutine(PostUserMessage(text));
            }
            else
            {
                SetInputText(text);
                SetStatus(sendVoiceAfterRecognition ? "语音已识别，等待上一条回复" : "语音已放入输入框");
            }
        }

        private void AddHistory(string speaker, string text, Color color)
        {
            var normalizedSpeaker = NormalizeSpeaker(speaker);
            history.Add(new ChatHistoryEntry(normalizedSpeaker, text ?? "", color, IsUserSpeaker(speaker), IsSystemSpeaker(speaker)));
            if (history.Count > MaxHistoryEntries)
            {
                history.RemoveAt(0);
            }

            RefreshHistorySlots();
            SetChatExpanded(true);
        }

        private void RefreshHistorySlots()
        {
            if (chatMessageSlots == null || chatMessageSlots.Length == 0)
            {
                return;
            }

            var templateRoot = chatMessageSlots[0]?.Root;
            if (historyContent != null && templateRoot != null)
            {
                RefreshDynamicHistorySlots(templateRoot);
                return;
            }

            var first = Mathf.Max(0, history.Count - chatMessageSlots.Length);
            for (var i = 0; i < chatMessageSlots.Length; i++)
            {
                var slot = chatMessageSlots[i];
                if (slot == null)
                {
                    continue;
                }

                var historyIndex = first + i;
                if (historyIndex >= history.Count)
                {
                    slot.Clear();
                    continue;
                }

                slot.Set(history[historyIndex], companionDisplayName, userDisplayName);
            }

            Canvas.ForceUpdateCanvases();
            if (historyContent != null)
            {
                LayoutRebuilder.ForceRebuildLayoutImmediate(historyContent);
            }

            if (historyScrollRect != null)
            {
                historyScrollRect.verticalNormalizedPosition = 0f;
            }
        }

        private void RefreshDynamicHistorySlots(GameObject templateRoot)
        {
            foreach (var slot in chatMessageSlots)
            {
                slot?.Clear();
            }

            foreach (var spawned in spawnedHistorySlots)
            {
                if (spawned != null)
                {
                    Destroy(spawned);
                }
            }
            spawnedHistorySlots.Clear();

            var first = Mathf.Max(0, history.Count - MaxHistoryEntries);
            for (var i = first; i < history.Count; i++)
            {
                var slotObj = Instantiate(templateRoot, historyContent, false);
                slotObj.name = $"ChatSlot_Runtime_{i - first:00}";
                var slot = ChatMessageSlot.FromRoot(slotObj);
                slot.Set(history[i], companionDisplayName, userDisplayName);
                spawnedHistorySlots.Add(slotObj);
            }

            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate(historyContent);
            if (historyScrollRect != null)
            {
                historyScrollRect.verticalNormalizedPosition = 0f;
            }
        }

        private void SeedInitialConversation()
        {
            if (!seedSceneConversation || history.Count > 0)
            {
                return;
            }

            history.Add(new ChatHistoryEntry(companionDisplayName, "你是在问晚餐吗？食堂今天有咖喱饭哦。", new Color(1f, 0.86f, 0.72f, 1f), false, false));
            history.Add(new ChatHistoryEntry(companionDisplayName, "你刚忙完，记得好好吃一顿，别太累了。", new Color(1f, 0.86f, 0.72f, 1f), false, false));
            history.Add(new ChatHistoryEntry(userDisplayName, "今天晚上吃什么", new Color(0.78f, 0.9f, 1f, 1f), true, false));
            history.Add(new ChatHistoryEntry(companionDisplayName, "要一起去吗？我可以等你。", new Color(1f, 0.86f, 0.72f, 1f), false, false));
        }

        private void SetChatExpanded(bool expanded)
        {
            chatExpanded = expanded;
            if (chatPanel != null)
            {
                chatPanel.sizeDelta = new Vector2(chatPanel.sizeDelta.x, expanded ? ExpandedChatHeight : CollapsedChatHeight);
            }

            if (historyViewport != null)
            {
                historyViewport.SetActive(expanded);
            }

            if (chatCollapsedGroup != null)
            {
                chatCollapsedGroup.SetActive(!expanded);
            }

            if (chatExpandedGroup != null)
            {
                chatExpandedGroup.SetActive(expanded);
            }
        }

        private void SetSettingsVisible(bool visible)
        {
            if (settingsPanel != null)
            {
                settingsPanel.SetActive(visible);
            }
        }

        private void SetHomeVisible(bool visible)
        {
            if (homeLayer != null)
            {
                homeLayer.SetActive(visible);
            }
        }

        private void SetSocialVisible(bool visible)
        {
            if (socialPanel != null)
            {
                socialPanel.SetActive(visible);
            }
        }

        private void SetStatus(string text)
        {
            if (statusText != null)
            {
                statusText.text = text;
            }
        }

        private void RecordInteraction()
        {
            lastInteractionAt = Time.time;
        }

        private string NormalizeSpeaker(string speaker)
        {
            if (IsUserSpeaker(speaker))
            {
                return userDisplayName;
            }

            if (IsSystemSpeaker(speaker))
            {
                return "系统";
            }

            return companionDisplayName;
        }

        private static bool IsUserSpeaker(string speaker)
        {
            return !string.IsNullOrWhiteSpace(speaker) &&
                (speaker.StartsWith("You", StringComparison.OrdinalIgnoreCase) ||
                    speaker.StartsWith("User", StringComparison.OrdinalIgnoreCase) ||
                    speaker.StartsWith("你", StringComparison.Ordinal));
        }

        private static bool IsSystemSpeaker(string speaker)
        {
            return !string.IsNullOrWhiteSpace(speaker) &&
                (speaker.StartsWith("System", StringComparison.OrdinalIgnoreCase) ||
                    speaker.StartsWith("Backend", StringComparison.OrdinalIgnoreCase) ||
                    speaker.StartsWith("系统", StringComparison.Ordinal));
        }

        private void ConfigureTouchController()
        {
            ResolveReferences();
            if (touchController == null)
            {
                return;
            }

            touchController.ConfigureBackend(backendBaseUrl, userId, characterId);
            touchController.TouchStarted -= OnTouchStarted;
            touchController.TouchStarted += OnTouchStarted;
            var liveController = GetLiveController();
            var liveAnimator = liveController != null
                ? liveController.GetComponent<Animator>() ?? liveController.GetComponentInChildren<Animator>() ?? liveController.GetComponentInParent<Animator>()
                : null;
            touchController.SetTargets(liveController, motionDirector, liveAnimator, audioSource);
        }

        private void OnTouchStarted(string hitArea)
        {
            RecordInteraction();
            SetStatus($"Touch: {hitArea}");
        }

        private AIGalgameChatdollController GetLiveController()
        {
            if (controller == null)
            {
                controller = FindFirstObjectByType<AIGalgameChatdollController>();
            }

            return controller;
        }

        private void BeginReplyRequestMotion()
        {
            var target = GetLiveController();
            if (target != null)
            {
                target.BeginReplyRequest();
            }
            else
            {
                motionDirector?.BeginReplyRequest();
            }
        }

        private void BeginReplyReceivedMotion(bool hasDialogue)
        {
            var target = GetLiveController();
            if (target != null)
            {
                target.BeginReplyReceived(hasDialogue);
            }
            else
            {
                motionDirector?.BeginReplyReceived(hasDialogue);
            }
        }

        private void EndReplyWithoutMessageMotion()
        {
            var target = GetLiveController();
            if (target != null)
            {
                target.EndReplyWithoutMessage();
            }
            else
            {
                motionDirector?.EndReplyWithoutMessage();
            }
        }

        private void PlayPhoneNotificationSound()
        {
            ResolveReferences();
            if (motionDirector == null)
            {
                var target = GetLiveController();
                motionDirector = target != null
                    ? target.GetComponent<AIGalgameRelaxRoomMotionDirector>() ??
                        target.GetComponentInChildren<AIGalgameRelaxRoomMotionDirector>() ??
                        target.GetComponentInParent<AIGalgameRelaxRoomMotionDirector>()
                    : null;
            }

            motionDirector?.PlayPhoneNotificationSfx();
        }

        private void TestNextStateStep()
        {
            ResolveReferences();
            const int totalSteps = 12;
            var step = stateTestIndex++ % totalSteps;
            var label = "";
            switch (step)
            {
                case 0:
                    label = "Idle";
                    var target = GetLiveController();
                    if (target != null)
                    {
                        target.SetIdle();
                    }
                    else
                    {
                        motionDirector?.SetIdle();
                    }
                    break;
                case 1:
                    label = "Reply request / pick up phone";
                    BeginReplyRequestMotion();
                    PlayPhoneNotificationSound();
                    break;
                case 2:
                    label = "Reply received / phone focus";
                    BeginReplyReceivedMotion(true);
                    break;
                case 3:
                    label = "Speaking while reply is active";
                    PlayControllerLine(BuildStructuredExpressionTestLine(), returnIdleWhenDone: false);
                    break;
                case 4:
                    label = "Reply end / put down phone";
                    EndReplyWithoutMessageMotion();
                    break;
                case 5:
                    label = "Idle action: tablet";
                    motionDirector?.PlayIdleAction("idle_tablet", 45f);
                    break;
                case 6:
                    label = "Idle action: texting";
                    motionDirector?.PlayIdleAction("idle_texting", 45f);
                    break;
                case 7:
                    label = "Idle action: typing";
                    motionDirector?.PlayIdleAction("idle_typing", 45f);
                    break;
                case 8:
                    label = "Idle action: yawn";
                    motionDirector?.PlayIdleAction("idle_yawn", 3f);
                    break;
                case 9:
                    label = "Idle action: sleeping / dozing";
                    motionDirector?.PlayIdleAction("idle_sleeping", 45f);
                    break;
                case 10:
                    label = "Idle action: waking";
                    motionDirector?.PlayIdleAction("idle_waking", 45f);
                    break;
                default:
                    label = "Touch reaction: head";
                    motionDirector?.PlayTouch("head", "bashful");
                    break;
            }

            RecordInteraction();
            SetTestStatus($"State {step + 1}/{totalSteps} - {label}");
        }

        private void TestNextExpressionStep()
        {
            ResolveReferences();
            var step = expressionTestIndex++ % 9;
            switch (step)
            {
                case 0:
                    ApplyDirectExpressionTest("neutral", 0.42f, step + 1, "Direct neutral");
                    break;
                case 1:
                    ApplyDirectExpressionTest("thinking", 0.72f, step + 1, "Direct thinking");
                    break;
                case 2:
                    ApplyDirectExpressionTest("happy", 0.78f, step + 1, "Direct happy");
                    break;
                case 3:
                    ApplyDirectExpressionTest("shy", 0.72f, step + 1, "Direct shy");
                    break;
                case 4:
                    ApplyDirectExpressionTest("angry", 0.68f, step + 1, "Direct angry");
                    break;
                case 5:
                    ApplyDirectExpressionTest("surprised", 0.78f, step + 1, "Direct surprised");
                    break;
                case 6:
                    PlayTaggedExpressionTest();
                    break;
                case 7:
                    PlayControllerLine(BuildStructuredExpressionTestLine(), returnIdleWhenDone: true);
                    RecordInteraction();
                    SetTestStatus("Face 8/9 - structured visual_cues");
                    break;
                default:
                    expressionDirector?.AddTransient("shy", 0.46f, 1.8f);
                    gazeDirector?.SetFocus("user", 1.8f);
                    RecordInteraction();
                    SetTestStatus("Face 9/9 - transient shy micro-expression");
                    break;
            }
        }

        private void TestNextGazeStep()
        {
            ResolveReferences();
            var step = gazeTestIndex++ % 5;
            var focus = step switch
            {
                0 => "user",
                1 => "computer",
                2 => "phone",
                3 => "down",
                _ => "away"
            };

            gazeDirector?.SetFocus(focus, 3.2f);
            expressionDirector?.SetEmotion(focus == "phone" || focus == "computer" ? "thinking" : "neutral", 0.46f, 3.2f);
            RecordInteraction();
            SetTestStatus($"Gaze {step + 1}/5 - {focus}");
        }

        private void ApplyDirectExpressionTest(string face, float weight, int displayIndex, string label)
        {
            var target = GetLiveController();
            target?.SetFace(face);
            expressionDirector?.SetEmotion(face, weight, 2.4f);
            RecordInteraction();
            SetTestStatus($"Face {displayIndex}/9 - {label}");
        }

        private void PlayTaggedExpressionTest()
        {
            var target = GetLiveController();
            if (target != null)
            {
                target.SayTagged("[face:thinking]I am checking the cue timing, [face:happy]then easing into a smile, [face:shy]and softening at the end.");
            }
            else
            {
                expressionDirector?.SetEmotion("happy", 0.72f, 2.4f);
            }

            RecordInteraction();
            SetTestStatus("Face 7/9 - tagged [face:] timeline");
        }

        private AIGalgameDialogueLine BuildStructuredExpressionTestLine()
        {
            return new AIGalgameDialogueLine
            {
                text = "I noticed the message, thought for a moment, then answered a little more warmly.",
                emotion = "thinking",
                expression = "thinking",
                motion = "speaking",
                controller = new AIGalgameDialogueControllerCommand
                {
                    state = "speaking",
                    face = "thinking",
                    animation = "speaking",
                    focus = "user",
                    mouth = "auto",
                    lipsync = true
                },
                visual_cues = new[]
                {
                    new AIGalgameDialogueVisualCue
                    {
                        text = "I noticed the message",
                        face = "thinking",
                        focus = "phone",
                        weight = 0.72f
                    },
                    new AIGalgameDialogueVisualCue
                    {
                        text = "thought for a moment",
                        face = "neutral",
                        focus = "computer",
                        weight = 0.48f
                    },
                    new AIGalgameDialogueVisualCue
                    {
                        text = "answered a little more warmly",
                        face = "happy",
                        focus = "user",
                        weight = 0.78f
                    }
                }
            };
        }

        private void SetTestStatus(string text)
        {
            if (testStatusText != null)
            {
                testStatusText.text = $"Test: {text}";
            }

            SetStatus(text);
        }

        private void PlayControllerLine(AIGalgameDialogueLine line, bool returnIdleWhenDone = true)
        {
            var target = GetLiveController();
            if (target != null)
            {
                target.PlayLine(line, returnIdleWhenDone);
            }
        }

        private string BuildUserMessageRequest(string text, string replyId = "", bool keyReply = false)
        {
            var localTime = DateTime.Now.ToString("O");
            var eventType = keyReply ? "option_selected" : "user_message";
            var payload = keyReply
                ? $"\"reply_text\":\"{EscapeJson(text)}\""
                : $"\"text\":\"{EscapeJson(text)}\"";
            if (!string.IsNullOrWhiteSpace(replyId))
            {
                payload += $",\"reply_id\":\"{EscapeJson(replyId)}\"";
            }

            return
                "{" +
                $"\"event_type\":\"{eventType}\"," +
                $"\"event_id\":\"unity_{DateTime.UtcNow.Ticks}\"," +
                $"\"user_id\":\"{EscapeJson(userId)}\"," +
                $"\"character_id\":\"{EscapeJson(characterId)}\"," +
                $"\"session_id\":\"{EscapeJson(sessionId)}\"," +
                $"\"payload\":{{{payload}}}," +
                $"\"client_context\":{{\"source\":\"RelaxRoomAI\",\"local_time\":\"{EscapeJson(localTime)}\"}}" +
                "}"; 
        }

        private string BuildIdleActionRequest()
        {
            var localTime = DateTime.Now.ToString("O");
            var idleSeconds = Mathf.Max(0, Mathf.FloorToInt(Time.time - lastInteractionAt));
            return
                "{" +
                $"\"user_id\":\"{EscapeJson(userId)}\"," +
                $"\"character_id\":\"{EscapeJson(characterId)}\"," +
                $"\"session_id\":\"{EscapeJson(sessionId)}\"," +
                $"\"scene\":\"Scene_01\"," +
                $"\"idle_seconds\":{idleSeconds}," +
                $"\"local_time\":\"{EscapeJson(localTime)}\"" +
                "}";
        }

        private string AutoSendLabel()
        {
            return sendVoiceAfterRecognition ? "语音自动发送：开" : "语音自动发送：关";
        }

        private static float[] ExtractRecordedMonoSamples(AudioClip clip, int samplePosition)
        {
            if (clip == null || clip.samples <= 0 || clip.channels <= 0)
            {
                return Array.Empty<float>();
            }

            var frameCount = samplePosition > 0 ? samplePosition : clip.samples;
            frameCount = Mathf.Clamp(frameCount, 0, clip.samples);
            if (frameCount == 0)
            {
                return Array.Empty<float>();
            }

            var channels = clip.channels;
            var data = new float[clip.samples * channels];
            if (!clip.GetData(data, 0))
            {
                return Array.Empty<float>();
            }

            var mono = new float[frameCount];
            for (var frame = 0; frame < frameCount; frame++)
            {
                var offset = frame * channels;
                var sum = 0f;
                for (var channel = 0; channel < channels; channel++)
                {
                    sum += data[offset + channel];
                }
                mono[frame] = sum / channels;
            }

            return mono;
        }

        private static bool HasAudibleVoice(float[] samples)
        {
            if (samples == null || samples.Length < 1600)
            {
                return false;
            }

            double sumSquares = 0d;
            var peak = 0f;
            for (var i = 0; i < samples.Length; i++)
            {
                var value = Mathf.Abs(samples[i]);
                peak = Mathf.Max(peak, value);
                sumSquares += value * value;
            }

            var rms = Math.Sqrt(sumSquares / samples.Length);
            return rms >= 0.004d || peak >= 0.035f;
        }

        private static float EstimateLineSeconds(AIGalgameDialogueLine line)
        {
            var textLength = string.IsNullOrWhiteSpace(line?.text) ? 12 : line.text.Length;
            var pause = line?.controller != null ? Mathf.Clamp(line.controller.pause, 0f, 10f) : 0f;
            return pause + Mathf.Clamp(textLength * 0.075f, 1.1f, 7.5f);
        }

        private static AudioType AudioTypeForUrl(string url)
        {
            var lower = (url ?? "").ToLowerInvariant();
            if (lower.EndsWith(".wav"))
            {
                return AudioType.WAV;
            }

            if (lower.EndsWith(".ogg"))
            {
                return AudioType.OGGVORBIS;
            }

            return AudioType.MPEG;
        }

        private static string CombineUrl(string baseUrl, string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return baseUrl;
            }

            if (path.StartsWith("http://", StringComparison.OrdinalIgnoreCase) ||
                path.StartsWith("https://", StringComparison.OrdinalIgnoreCase) ||
                path.StartsWith("file://", StringComparison.OrdinalIgnoreCase))
            {
                return path;
            }

            var trimmedBase = (baseUrl ?? "").TrimEnd('/');
            var trimmedPath = path.StartsWith("/") ? path : "/" + path;
            return trimmedBase + trimmedPath;
        }

        private static string BuildMomentPath(string template, string momentId, string fallback)
        {
            var path = string.IsNullOrWhiteSpace(template) ? fallback : template;
            return path.Replace("{moment_id}", UnityWebRequest.EscapeURL(momentId ?? ""));
        }

        private static string EscapeJson(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "";
            }

            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\b", "\\b")
                .Replace("\f", "\\f")
                .Replace("\n", "\\n")
                .Replace("\r", "\\r")
                .Replace("\t", "\\t");
        }

        private static Font LoadFont()
        {
            return Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf") ??
                Resources.GetBuiltinResource<Font>("Arial.ttf");
        }

        private readonly struct ChatHistoryEntry
        {
            public ChatHistoryEntry(string speaker, string text, Color accent, bool isUser, bool isSystem)
            {
                Speaker = speaker ?? "";
                Text = text ?? "";
                Accent = accent;
                IsUser = isUser;
                IsSystem = isSystem;
            }

            public string Speaker { get; }
            public string Text { get; }
            public Color Accent { get; }
            public bool IsUser { get; }
            public bool IsSystem { get; }
        }

        [Serializable]
        private sealed class ChatMessageSlot
        {
            [SerializeField] private GameObject root;
            [SerializeField] private GameObject companionSide;
            [SerializeField] private GameObject userSide;
            [SerializeField] private GameObject systemSide;
            [SerializeField] private Text companionSpeakerText;
            [SerializeField] private Text companionBodyText;
            [SerializeField] private Text userSpeakerText;
            [SerializeField] private Text userBodyText;
            [SerializeField] private Text systemBodyText;
            [SerializeField] private Image companionBubble;
            [SerializeField] private Image userBubble;
            [SerializeField] private Image systemBubble;
            [SerializeField] private LayoutElement layoutElement;

            private static readonly Color CompanionBubbleColor = new(0.16f, 0.2f, 0.28f, 0.9f);
            private static readonly Color UserBubbleColor = new(0.02f, 0.48f, 0.48f, 0.9f);
            private static readonly Color SystemBubbleColor = new(0.15f, 0.16f, 0.2f, 0.74f);

            public GameObject Root => root;

            public static ChatMessageSlot FromRoot(GameObject slotRoot)
            {
                var slot = new ChatMessageSlot
                {
                    root = slotRoot,
                    layoutElement = slotRoot != null ? slotRoot.GetComponent<LayoutElement>() : null
                };

                var rootTransform = slotRoot != null ? slotRoot.transform : null;
                var companion = FindChildTransform(rootTransform, "CompanionSide");
                var user = FindChildTransform(rootTransform, "UserSide");
                var system = FindChildTransform(rootTransform, "SystemSide");

                slot.companionSide = companion != null ? companion.gameObject : null;
                slot.userSide = user != null ? user.gameObject : null;
                slot.systemSide = system != null ? system.gameObject : null;
                slot.companionSpeakerText = FindNamedComponent<Text>(companion, "Speaker");
                slot.companionBodyText = FindNamedComponent<Text>(companion, "Body");
                slot.userSpeakerText = FindNamedComponent<Text>(user, "Speaker");
                slot.userBodyText = FindNamedComponent<Text>(user, "Body");
                slot.systemBodyText = FindNamedComponent<Text>(system, "Body");
                slot.companionBubble = FindNamedComponent<Image>(companion, "Bubble");
                slot.userBubble = FindNamedComponent<Image>(user, "Bubble");
                slot.systemBubble = FindNamedComponent<Image>(system, "Bubble");
                return slot;
            }

            public void Set(ChatHistoryEntry entry, string companionName, string userName)
            {
                if (root != null)
                {
                    root.SetActive(true);
                }

                var showUser = entry.IsUser;
                var showSystem = entry.IsSystem;
                SetActive(companionSide, !showUser && !showSystem);
                SetActive(userSide, showUser);
                SetActive(systemSide, showSystem);

                Text activeBodyText;
                if (showUser)
                {
                    SetText(userSpeakerText, string.IsNullOrWhiteSpace(entry.Speaker) ? userName : entry.Speaker);
                    SetBodyText(userBodyText, entry.Text);
                    SetBubbleColor(userBubble, UserBubbleColor);
                    activeBodyText = userBodyText;
                }
                else if (showSystem)
                {
                    SetBodyText(systemBodyText, entry.Text);
                    SetBubbleColor(systemBubble, SystemBubbleColor);
                    activeBodyText = systemBodyText;
                }
                else
                {
                    SetText(companionSpeakerText, string.IsNullOrWhiteSpace(entry.Speaker) ? companionName : entry.Speaker);
                    SetBodyText(companionBodyText, entry.Text);
                    SetBubbleColor(companionBubble, CompanionBubbleColor);
                    activeBodyText = companionBodyText;
                }

                if (layoutElement != null)
                {
                    var bodyHeight = PreferredBodyHeight(activeBodyText, entry.Text);
                    layoutElement.minHeight = showSystem ? 56f : 72f;
                    layoutElement.preferredHeight = showSystem
                        ? Mathf.Max(56f, bodyHeight + 28f)
                        : Mathf.Max(72f, bodyHeight + 52f);
                }
            }

            public void Clear()
            {
                if (root != null)
                {
                    root.SetActive(false);
                }
            }

            private static void SetActive(GameObject target, bool visible)
            {
                if (target != null)
                {
                    target.SetActive(visible);
                }
            }

            private static void SetText(Text target, string value)
            {
                if (target != null)
                {
                    target.text = value ?? "";
                }
            }

            private static void SetBodyText(Text target, string value)
            {
                if (target == null)
                {
                    return;
                }

                target.horizontalOverflow = HorizontalWrapMode.Wrap;
                target.verticalOverflow = VerticalWrapMode.Overflow;
                var fitter = target.GetComponent<ContentSizeFitter>();
                if (fitter != null)
                {
                    fitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
                    fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
                }

                target.text = value ?? "";
            }

            private static float PreferredBodyHeight(Text target, string value)
            {
                if (target == null)
                {
                    return Mathf.Clamp(Mathf.CeilToInt((value?.Length ?? 0) / 18f) * 24f, 24f, 220f);
                }

                var preferred = target.preferredHeight;
                if (preferred <= 0f)
                {
                    preferred = Mathf.CeilToInt((value?.Length ?? 0) / 18f) * 24f;
                }

                return Mathf.Clamp(preferred, 24f, 260f);
            }

            private static void SetBubbleColor(Image target, Color value)
            {
                if (target != null)
                {
                    target.color = value;
                }
            }

            private static Transform FindChildTransform(Transform rootTransform, string childName)
            {
                if (rootTransform == null || string.IsNullOrWhiteSpace(childName))
                {
                    return null;
                }

                for (var i = 0; i < rootTransform.childCount; i++)
                {
                    var child = rootTransform.GetChild(i);
                    if (string.Equals(child.name, childName, StringComparison.OrdinalIgnoreCase))
                    {
                        return child;
                    }

                    var nested = FindChildTransform(child, childName);
                    if (nested != null)
                    {
                        return nested;
                    }
                }

                return null;
            }

            private static T FindNamedComponent<T>(Transform rootTransform, string childName) where T : Component
            {
                var child = FindChildTransform(rootTransform, childName);
                return child != null ? child.GetComponent<T>() : null;
            }
        }

        [Serializable]
        private sealed class HomeBootstrapResponse
        {
            public string relationship_start_date = "";
            public string daily_tip = "";
            public string[] quick_replies = Array.Empty<string>();
            public string companion_name = "";
            public string user_name = "";
            public string chat_state_label = "";
        }

        [Serializable]
        private sealed class MomentsResponse
        {
            public MomentsProfile profile = new();
            public MomentPost[] posts = Array.Empty<MomentPost>();
        }

        [Serializable]
        private sealed class MomentsProfile
        {
            public string name = "";
            public string status_suffix = "";
            public string update_note = "";
            public string avatar_url = "";
        }

        [Serializable]
        private sealed class MomentPost
        {
            public string id = "";
            public string username = "";
            public string time = "";
            public string created_at = "";
            public string visibility_label = "";
            public string type = "text";
            public string text = "";
            public string avatar_url = "";
            public string[] images = Array.Empty<string>();
            public string video_thumbnail = "";
            public string video_title = "";
            public string video_duration = "";
            public string[] likes = Array.Empty<string>();
            public MomentComment[] comments = Array.Empty<MomentComment>();
        }

        [Serializable]
        private sealed class MomentComment
        {
            public string user = "";
            public string text = "";
            public string created_at = "";
        }

        [Serializable]
        private sealed class BackendEventResponse
        {
            public string event_type = "";
            public string event_id = "";
            public string session_id = "";
            public BackendPayload payload = new();
        }

        [Serializable]
        private sealed class BackendPayload
        {
            public AIGalgameDialogueLine[] lines = Array.Empty<AIGalgameDialogueLine>();
            public BackendReplyOption[] normal_replies = Array.Empty<BackendReplyOption>();
            public BackendReplyOption[] key_replies = Array.Empty<BackendReplyOption>();
            public string message = "";
            public string reply_mode = "";
            public string pace_reason = "";
        }

        [Serializable]
        private sealed class BackendReplyOption
        {
            public string reply_id = "";
            public string text = "";
            public string type = "normal";
        }

        [Serializable]
        private sealed class BackendIdleActionResponse
        {
            public string event_type = "";
            public string event_id = "";
            public string session_id = "";
            public BackendIdlePayload payload = new();
        }

        [Serializable]
        private sealed class BackendIdlePayload
        {
            public bool ok;
            public string action = "";
            public string motion = "";
            public AIGalgameDialogueLine[] lines = Array.Empty<AIGalgameDialogueLine>();
            public float cooldown_seconds = 45f;
            public float action_duration_seconds = 75f;
        }

    }
}
