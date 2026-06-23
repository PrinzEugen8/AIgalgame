using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Serialization;

#if UNITY_EDITOR
using UnityEditor;
#endif

namespace AIgalgame.Motion
{
    public enum RelaxRoomMotionMode
    {
        Idle,
        Reply,
        IdleAction,
        Touch,
        Speaking,
        VideoCall,
        Sleeping
    }

    [DefaultExecutionOrder(-110)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomMotionDirector : MonoBehaviour
    {
        [Header("Targets")]
        [SerializeField] private Animator animator;
        [SerializeField] private AIGalgamePlayableMotionPlayer motionPlayer;
        [SerializeField] private AIGalgameRelaxRoomPhoneAttachmentController phoneAttachment;
        [SerializeField] private AIGalgameRelaxRoomPhonePickupController phonePickup;
        [SerializeField] private AIGalgameRelaxRoomPhoneScreenController phoneScreen;
        [SerializeField] private AIGalgameRelaxRoomGazeDirector gazeDirector;
        [SerializeField] private AIGalgameRelaxRoomExpressionDirector expressionDirector;

        [Header("Runtime State Machine")]
        [SerializeField] private bool useAnimatorControllerStateMachine = true;
        [SerializeField] private bool enableAnimatorRootMotion;

        [Header("Sitting base")]
        [SerializeField] private string sittingBaseMotionHint = "Sitting Idle";
        [SerializeField] private string sittingBaseFolderHint = "\u5f85\u673a";

        [Header("Texting segments")]
        [SerializeField] private Vector2 textingPickupRange = new(0f, 0.28f);
        [SerializeField] private Vector2 textingLoopRange = new(0.28f, 0.78f);
        [SerializeField] private Vector2 textingPutDownRange = new(0.78f, 1f);
        [SerializeField] private float replyReceivedSettleSeconds = 3.5f;
        [SerializeField] private float replyHoldAfterVoiceSeconds = 6.5f;

        [Header("Dedicated phone clips")]
        [SerializeField] private bool preferDedicatedPhoneClips = true;
        [SerializeField] private string phonePickupMotionHint = "K_sitting_up_phone";
        [SerializeField] private string phonePutDownMotionHint = "K_sitting_down_phone_Reverse";
        [SerializeField] private string phoneTextingLoopMotionHint = "K_Texting";
        [SerializeField] private string phoneTextingStopMotionHint = "K_Texting_stop_hand";

        [Header("Video call")]
        [SerializeField] private float videoCallStateReleaseSeconds = 3.05f;

        [Header("Sleep")]
        [SerializeField] private Transform bedSleepPoint;
        [SerializeField] private Transform seatPoint;
        [SerializeField] private Transform sleepMoveRoot;
        [FormerlySerializedAs("bedSleepingStateName")]
        [SerializeField] private string bedSleepingParameterName = "bed_sleeping";
        [SerializeField] private bool resetBedSleepingParameterOnExit = true;
        [SerializeField] private float sleepingNotificationScreenSeconds = 4.5f;

        [Header("Playback")]
        [SerializeField] private float fallbackOneShotSeconds = 2.4f;
        [SerializeField] private float motionTransitionSeconds = 0.65f;
        [SerializeField] private Vector2 loopIdleActionDurationRange = new(30f, 120f);
        [SerializeField] private float defaultLoopIdleActionSeconds = 75f;
        [SerializeField] private bool applyFootIk = true;

        [Header("Audio")]
        [SerializeField] private AIGalgameRelaxRoomAudioConfig audioConfig;
        [SerializeField] private AudioClip bgmClip;
        [SerializeField] private AudioClip phoneNotificationClip;
        [SerializeField] private AudioClip[] phoneTypingClips = new AudioClip[0];
        [SerializeField] private AudioClip[] keyboardTypingClips = new AudioClip[0];
        [SerializeField] private Transform phoneAudioTarget;
        [SerializeField] private Transform keyboardAudioTarget;
        [SerializeField] private AudioSource bgmAudioSource;
        [SerializeField] private AudioSource phoneAudioSource;
        [SerializeField] private AudioSource keyboardAudioSource;
        [SerializeField] private bool playBgmOnStart = true;
        [SerializeField, Range(0f, 1f)] private float bgmVolume = 0.32f;
        [SerializeField, Range(0f, 1f)] private float sfxVolume = 0.85f;
        [SerializeField] private Vector2 typingPitchRange = new(0.96f, 1.04f);
        [SerializeField] private float minTypingSfxInterval = 0.045f;

        private const string ReplyFolderHint = "回复";
        private const string IdleFolderHint = "待机";
        private const string TouchFolderHint = "触摸";
        private const string IdleActionFolderHint = "闲时动画";

        private const string AudioConfigResource = "RelaxRoom/RelaxRoomAudioConfig";

        private const string TriggerToIdle = "ToIdle";
        private const string TriggerReplyRequest = "Reply_Request";
        private const string TriggerReplyWaitingPose = "Reply_WaitingPose";
        private const string TriggerReplyDoubleTyping = "Reply_DoubleTyping";
        private const string TriggerReplyPutDown = "Reply_PutDown";
        private const string TriggerReplyTextingStop = "Reply_TextingStop";
        private const string TriggerIdleTablet = "Idle_Tablet";
        private const string TriggerIdleTexting = "Idle_Texting";
        private const string TriggerIdleTyping = "Idle_Typing";
        private const string TriggerIdleYawn = "Idle_Yawn";
        private const string TriggerIdleSleeping = "Idle_Sleeping";
        private const string TriggerIdleWaking = "Idle_Waking";
        private const string TriggerIdleSitting = "Idle_Sitting";
        private const string TriggerTouchHead = "Touch_Head";
        private const string TriggerTouchChest = "Touch_Chest";
        private const string TriggerTouchHand = "Touch_Hand";
        private const string TriggerVideoCallBegin = "VideoCall_Begin";
        private const string TriggerVideoCallEnd = "VideoCall_End";
        private const string BoolVideoCallActive = "VideoCall_Active";
        private const string BoolVideoCallAiSpeaking = "VideoCall_AiSpeaking";
        private const string BoolVideoCallUserSpeaking = "VideoCall_UserSpeaking";
        private const float MinimumVideoCallPutDownSeconds = 3.05f;

        private static readonly string[] ControllerTriggers =
        {
            TriggerToIdle,
            TriggerReplyRequest,
            TriggerReplyWaitingPose,
            TriggerReplyDoubleTyping,
            TriggerReplyPutDown,
            TriggerReplyTextingStop,
            TriggerIdleTablet,
            TriggerIdleTexting,
            TriggerIdleTyping,
            TriggerIdleYawn,
            TriggerIdleSleeping,
            TriggerIdleWaking,
            TriggerIdleSitting,
            TriggerTouchHead,
            TriggerTouchChest,
            TriggerTouchHand,
            TriggerVideoCallBegin,
            TriggerVideoCallEnd,
        };

        private Coroutine managedRoutine;
        private Coroutine delayedPutDownRoutine;
        private RelaxRoomMotionMode mode = RelaxRoomMotionMode.Idle;
        private float busyUntil;
        private bool replyPhoneHeld;
        private bool replyWaitingPoseScheduled;
        private bool replyWaitingPoseActive;
        private bool replyVoiceEnded;
        private bool videoCallActive;
        private bool videoCallEnding;
        private bool videoCallAiSpeaking;
        private bool videoCallUserSpeaking;
        private bool loggedMissingVideoCallAnimatorContract;
        private bool loggedMissingPhoneNotificationAudioSource;
        private bool loggedMissingPhoneNotificationClip;
        private readonly HashSet<string> loggedControllerOnlyFallbackBlocks = new();
        private Vector3 preSleepPosition;
        private Quaternion preSleepRotation = Quaternion.identity;
        private bool hasPreSleepPose;
        private bool loggedMissingPhoneAttachment;
        private int lastPhoneTypingClipIndex = -1;
        private int lastKeyboardTypingClipIndex = -1;
        private float lastPhoneTypingAt = -999f;
        private float lastKeyboardTypingAt = -999f;

        public RelaxRoomMotionMode Mode => mode;
        public bool CanAcceptTouch => mode == RelaxRoomMotionMode.Idle && Time.time >= busyUntil;

        private void Reset()
        {
            ResolveReferences();
        }

#if UNITY_EDITOR
        private void OnValidate()
        {
            MigrateSerializedDefaults();
            ConfigureAssignedAudioSources();
            if (Application.isPlaying && phoneAttachment != null && phoneAttachment.IsVisible)
            {
                phoneAttachment.RefreshAttachment();
            }
        }
#endif

        private void Awake()
        {
            MigrateSerializedDefaults();
            ResolveReferences();
            AIGalgameStartupDiagnostics.Log("motion_director_awake");
            LoadAudioConfigIfNeeded();
            AIGalgameStartupDiagnostics.Log("motion_director_audio_loaded");
            ConfigureAnimatorRootMotion();
            if (useAnimatorControllerStateMachine)
            {
                motionPlayer?.ConfigureForAnimatorControllerDriver();
            }
        }

        private void Start()
        {
            if (motionPlayer != null && motionPlayer.Clips.Count == 0)
            {
                motionPlayer.RefreshClipCatalog();
            }

            AIGalgameStartupDiagnostics.Log("motion_director_start_begin");
#if RELAXROOM_RN
            SetIdle(0f);
#else
            PlayBgmIfNeeded();
            SetIdle(0f);
#endif
            AIGalgameStartupDiagnostics.Log("motion_director_idle_set");
        }

        private void Update()
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                StopGraph();
                return;
            }
#endif

            if (phoneAttachment != null && phoneAttachment.IsVisible)
            {
                phoneAttachment.RefreshAttachment();
            }
        }

        private void OnDestroy()
        {
            StopDelayedPutDownRoutine();
            StopGraph();
        }

        public void SetTargets(Animator targetAnimator, AIGalgamePlayableMotionPlayer targetMotionPlayer)
        {
            animator = targetAnimator;
            motionPlayer = targetMotionPlayer;
            if (phoneAttachment != null)
            {
                phoneAttachment.SetAnimator(animator);
            }

            if (phonePickup != null)
            {
                phonePickup.SetTargets(animator, motionPlayer, phoneAttachment);
            }

            if (motionPlayer != null && animator != null)
            {
                motionPlayer.SetAnimator(animator);
                if (useAnimatorControllerStateMachine)
                {
                    motionPlayer.ConfigureForAnimatorControllerDriver();
                }
            }

            ConfigureAnimatorRootMotion();
            gazeDirector?.SetTargets(null, animator, this);
        }

        public void SetVisualDirectors(AIGalgameRelaxRoomGazeDirector gaze, AIGalgameRelaxRoomExpressionDirector expression)
        {
            gazeDirector = gaze != null ? gaze : gazeDirector;
            expressionDirector = expression != null ? expression : expressionDirector;
            gazeDirector?.SetTargets(null, animator, this);
        }

        public void BeginReplyRequest()
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                NotifyWhileSleeping();
                return;
            }

            var continueWithPhone = replyPhoneHeld || replyWaitingPoseActive || replyWaitingPoseScheduled;
            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            if (continueWithPhone)
            {
                EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyDoubleTyping);
            }
            else
            {
                PreparePhoneOnTable();
                phonePickup?.SetActiveGrip(RelaxRoomPhoneGrip.ReplyTexting);
            }
            mode = RelaxRoomMotionMode.Reply;
            busyUntil = Time.time + 2f;
            gazeDirector?.SetMotionMode(mode, "phone_texting");
            phoneScreen?.ShowMessage(0f, true);
            PlayPhoneNotificationAudioOnly();
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Typing);
            expressionDirector?.SetEmotion("thinking", 0.48f);
            replyPhoneHeld = true;
            replyWaitingPoseScheduled = false;
            replyWaitingPoseActive = false;
            replyVoiceEnded = false;

            var replyTrigger = continueWithPhone ? TriggerReplyDoubleTyping : TriggerReplyRequest;
            if (TryTriggerController(replyTrigger))
            {
                return;
            }

            replyPhoneHeld = continueWithPhone;
            LogControllerOnlyFallbackBlocked("BeginReplyRequest", replyTrigger);
        }

        public void BeginReplyReceived(bool hasDialogue)
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                NotifyWhileSleeping();
                return;
            }

            if (!hasDialogue)
            {
                EndReplyWithoutMessage();
                return;
            }

            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            phoneScreen?.ShowMessage(0f, true);
            replyPhoneHeld = true;
            replyWaitingPoseScheduled = false;
            replyWaitingPoseActive = false;
            replyVoiceEnded = false;
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.Reply, "phone_texting");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Typing);
            StartManagedRoutine(ReplyReceivedRoutine());
        }

        public void EndReplyWithoutMessage()
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                return;
            }

            replyVoiceEnded = true;
            gazeDirector?.SetFocus("user", 1.5f);
            if (!replyPhoneHeld)
            {
                SetIdle();
                return;
            }

            if (replyWaitingPoseActive)
            {
                ScheduleReplyPutDownAfterHold();
                return;
            }

            if (replyWaitingPoseScheduled)
            {
                return;
            }

            StartReplyPutDownNow();
        }

        public void BeginVideoCall()
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                NotifyWhileSleeping();
                return;
            }

            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            ClearReplyState();
            videoCallActive = true;
            videoCallEnding = false;
            videoCallAiSpeaking = false;
            videoCallUserSpeaking = false;
            mode = RelaxRoomMotionMode.VideoCall;
            busyUntil = Time.time + 3600f;
            phonePickup?.SetActiveGrip(RelaxRoomPhoneGrip.VideoCallSelfie);
            EnsurePhoneInHand(RelaxRoomPhoneGrip.VideoCallSelfie);
            phoneScreen?.ShowActiveScreen(0f, true);
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.VideoCall, "video_call");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);
            expressionDirector?.SetEmotion("shy", 0.52f);
            ApplyVideoCallAnimatorState(triggerBegin: true, triggerEnd: false);
        }

        public void EndVideoCall()
        {
            if (!videoCallActive && mode != RelaxRoomMotionMode.VideoCall)
            {
                return;
            }

            videoCallActive = false;
            videoCallEnding = true;
            videoCallAiSpeaking = false;
            videoCallUserSpeaking = false;
            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            ApplyVideoCallAnimatorState(triggerBegin: false, triggerEnd: true);
            StartManagedRoutine(VideoCallStateReleaseRoutine());
        }

        public void SetVideoCallAiSpeaking(bool active)
        {
            if (videoCallEnding)
            {
                return;
            }

            if (!videoCallActive && active)
            {
                BeginVideoCall();
            }

            if (!videoCallActive)
            {
                return;
            }

            videoCallAiSpeaking = active;
            expressionDirector?.SetAvatarState(active ? AIGalgameAvatarState.Speaking : AIGalgameAvatarState.Idle);
            expressionDirector?.SetEmotion(active ? "happy" : "shy", active ? 0.72f : 0.52f);
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.VideoCall, "video_call");
            ApplyVideoCallAnimatorState(triggerBegin: false, triggerEnd: false);
        }

        public void SetVideoCallUserSpeaking(bool active)
        {
            if (videoCallEnding)
            {
                return;
            }

            if (!videoCallActive)
            {
                return;
            }

            videoCallUserSpeaking = active;
            if (active)
            {
                expressionDirector?.AddTransient("thinking", 0.34f, 1.2f);
            }

            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.VideoCall, "video_call");
            ApplyVideoCallAnimatorState(triggerBegin: false, triggerEnd: false);
        }

        public void EnterSleep()
        {
            ResolveReferences();
            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            ClearReplyState();
            ClearVideoCallState();

            if (phonePickup != null && phonePickup.enabled)
            {
                phonePickup.PlacePhoneOnTable();
            }

            PrepareAnimatorControllerDriver();

            var root = GetCharacterRoot();
            if (root != null)
            {
                preSleepPosition = root.position;
                preSleepRotation = root.rotation;
                hasPreSleepPose = true;
            }

            MoveCharacterToSleepPoint();

            phoneAttachment?.PlaceOnBedroomSlot(true);
            phoneScreen?.SetScreenOn(false);

            mode = RelaxRoomMotionMode.Sleeping;
            busyUntil = float.MaxValue;
            gazeDirector?.SetMotionMode(mode, "sleeping");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);
            expressionDirector?.SetEmotion("relaxed", 0.55f);

            if (!SetSleepAnimatorParameters(true))
            {
                Debug.LogError($"RelaxRoom sleep requires Animator Controller parameter '{bedSleepingParameterName}'. Add a Bool or Trigger parameter with that name and transition to the bed_sleeping state from the Controller.", this);
            }
        }

        public void ExitSleep()
        {
            if (mode != RelaxRoomMotionMode.Sleeping)
            {
                SetIdle();
                return;
            }

            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            PrepareAnimatorControllerDriver();
            phoneScreen?.SetScreenOn(false);

            if (resetBedSleepingParameterOnExit)
            {
                SetSleepAnimatorParameters(false);
            }

            if (!MoveCharacterToSeatPoint() && hasPreSleepPose)
            {
                var root = GetCharacterRoot();
                if (root != null)
                {
                    root.SetPositionAndRotation(preSleepPosition, preSleepRotation);
                }
            }

            hasPreSleepPose = false;

            SetPhoneVisible(false);
            mode = RelaxRoomMotionMode.Idle;
            busyUntil = 0f;
            gazeDirector?.SetMotionMode(mode, "idle");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);
        }

        public void NotifyWhileSleeping(float screenSeconds = -1f)
        {
            ResolveReferences();
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                phoneAttachment?.PlaceOnBedroomSlot(true);
            }

            phoneScreen?.ShowMessage(
                screenSeconds > 0f ? screenSeconds : sleepingNotificationScreenSeconds,
                false);
            PlayPhoneNotificationAudioOnly();
        }

        public bool PlaySemanticMotion(string motion, AIGalgameAvatarState state, float transitionSeconds)
        {
            var key = Normalize(motion);
            if (state == AIGalgameAvatarState.Idle && IsIdleKey(key))
            {
                SetIdle(transitionSeconds);
                return true;
            }

            if (state == AIGalgameAvatarState.Typing || key == "typing" || key == "type" || key == "input")
            {
                PlayTypingLoopForDialogue();
                return true;
            }

            if (state == AIGalgameAvatarState.Speaking && (string.IsNullOrWhiteSpace(key) || key == "speaking" || key == "speak" || key == "talk" || key == "talking" || key == "happy" || key == "shy"))
            {
                return true;
            }

            if (key == "taphead" || key == "head" || key == "bashful")
            {
                PlayTouch("head", motion);
                return true;
            }

            if (key == "tapchest" || key == "chest" || key == "disapproval" || key == "angry" || key == "sad")
            {
                PlayTouch("chest", motion);
                return true;
            }

            if (key == "taphand" || key == "hand" || key == "rubbingarm")
            {
                PlayTouch("hand", motion);
                return true;
            }

            if (key.StartsWith("idle"))
            {
                PlayIdleAction(motion);
                return true;
            }

            if (key == "thinking" || key == "think")
            {
                SetIdle(transitionSeconds);
                return true;
            }

            return false;
        }

        public void PlayPhoneNotificationSfx()
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                NotifyWhileSleeping();
                return;
            }

            ResolveReferences();
            phoneScreen?.ShowMessage(4.5f, false);
            PlayPhoneNotificationAudioOnly();
        }

        private void PlayPhoneNotificationAudioOnly()
        {
            var source = EnsurePhoneAudioSource();
            if (source == null)
            {
                if (!loggedMissingPhoneNotificationAudioSource)
                {
                    Debug.LogWarning("RelaxRoom phone notification sound has no AudioSource. Assign phoneAudioSource in AIGalgameRelaxRoomMotionDirector.", this);
                    loggedMissingPhoneNotificationAudioSource = true;
                }
                return;
            }

            if (phoneNotificationClip == null)
            {
                if (!loggedMissingPhoneNotificationClip)
                {
                    Debug.LogWarning("RelaxRoom phone notification sound has no AudioClip. Assign phoneNotificationClip in AIGalgameRelaxRoomMotionDirector.", this);
                    loggedMissingPhoneNotificationClip = true;
                }
                return;
            }

            PlayOneShot(source, phoneNotificationClip, sfxVolume, randomizePitch: false);
        }

        public void PlayRelaxRoomPhoneTypingSfx()
        {
            PlayPhoneTypingSfx();
        }

        public void PlayPhoneTypingSfx()
        {
            if (Time.time - lastPhoneTypingAt < Mathf.Max(0f, minTypingSfxInterval))
            {
                return;
            }

            var clip = PickRandomClip(phoneTypingClips, ref lastPhoneTypingClipIndex);
            var source = EnsurePhoneAudioSource();
            if (clip == null || source == null)
            {
                return;
            }

            lastPhoneTypingAt = Time.time;
            PlayOneShot(source, clip, sfxVolume, randomizePitch: true);
        }

        public void PlayRelaxRoomKeyboardTypingSfx()
        {
            PlayKeyboardTypingSfx();
        }

        public void PlayKeyboardTypingSfx()
        {
            if (Time.time - lastKeyboardTypingAt < Mathf.Max(0f, minTypingSfxInterval))
            {
                return;
            }

            var clip = PickRandomClip(keyboardTypingClips, ref lastKeyboardTypingClipIndex);
            var source = EnsureKeyboardAudioSource();
            if (clip == null || source == null)
            {
                return;
            }

            lastKeyboardTypingAt = Time.time;
            PlayOneShot(source, clip, sfxVolume, randomizePitch: true);
        }

        public void SetIdle(float transitionSeconds = -1f)
        {
            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            ClearReplyState();
            ClearVideoCallState();
            var deferPhoneReleaseToAnimator = ShouldDeferPhoneReleaseToAnimator();
            if (!deferPhoneReleaseToAnimator)
            {
                SetPhoneVisible(false);
            }

            mode = RelaxRoomMotionMode.Idle;
            busyUntil = 0f;
            gazeDirector?.SetMotionMode(mode, "idle");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);

            if (TryTriggerController(TriggerToIdle))
            {
                return;
            }

            if (deferPhoneReleaseToAnimator)
            {
                SetPhoneVisible(false);
            }

            LogControllerOnlyFallbackBlocked("SetIdle", TriggerToIdle);
        }

        public void PlayTouch(string hitArea, string motionHint = "")
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                return;
            }

            StopManagedRoutine();
            SetPhoneVisible(false);
            mode = RelaxRoomMotionMode.Touch;
            busyUntil = Time.time + 1.2f;
            gazeDirector?.SetMotionMode(mode, motionHint);

            var key = Normalize(FirstNonEmpty(hitArea, motionHint));
            if (key == "head" || key == "taphead" || Normalize(motionHint) == "bashful")
            {
                expressionDirector?.AddTransient("shy", 0.38f, fallbackOneShotSeconds);
                if (TryTriggerController(TriggerTouchHead))
                {
                    ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Bashful"));
                    return;
                }

                LogControllerOnlyFallbackBlocked("PlayTouch(head)", TriggerTouchHead);
                ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Bashful"));
                return;
            }

            if (key == "hand" || key == "taphand" || Normalize(motionHint) == "rubbingarm")
            {
                expressionDirector?.AddTransient("shy", 0.28f, fallbackOneShotSeconds);
                if (TryTriggerController(TriggerTouchHand))
                {
                    ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Sitting Rubbing Arm", "Rubbing Arm"));
                    return;
                }

                LogControllerOnlyFallbackBlocked("PlayTouch(hand)", TriggerTouchHand);
                ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Sitting Rubbing Arm", "Rubbing Arm"));
                return;
            }

            if (TryTriggerController(TriggerTouchChest))
            {
                expressionDirector?.AddTransient("angry", 0.36f, fallbackOneShotSeconds);
                ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Sitting Disapproval", "Disapproval"));
                return;
            }

            expressionDirector?.AddTransient("angry", 0.36f, fallbackOneShotSeconds);
            LogControllerOnlyFallbackBlocked("PlayTouch(chest)", TriggerTouchChest);
            ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Sitting Disapproval", "Disapproval"));
        }

        public void PlayIdleAction(string motion)
        {
            PlayIdleAction(motion, -1f);
        }

        public void PlayIdleAction(string motion, float requestedDurationSeconds)
        {
            if (mode == RelaxRoomMotionMode.Sleeping)
            {
                return;
            }

            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            ClearReplyState();
            var key = Normalize(motion);
            var holdSeconds = ResolveLoopIdleActionDuration(requestedDurationSeconds);
            var isPhoneIdleAction = IsPhoneIdleActionKey(key);
            var idleGrip = GetIdleActionPhoneGrip(key);
            if (isPhoneIdleAction && (useAnimatorControllerStateMachine || ShouldUsePhonePickupFlow()))
            {
                PreparePhoneOnTable();
                phonePickup?.SetActiveGrip(idleGrip);
            }
            else
            {
                SetPhoneVisible(isPhoneIdleAction, idleGrip);
            }
            mode = RelaxRoomMotionMode.IdleAction;
            busyUntil = Time.time + holdSeconds;
            gazeDirector?.SetMotionMode(mode, motion);
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);
            expressionDirector?.SetEmotion(ExpressionForIdleAction(key), 0.48f);

            if (TryPlayIdleActionOnController(key, holdSeconds))
            {
                return;
            }

            LogControllerOnlyFallbackBlocked($"PlayIdleAction({motion})", ControllerTriggerForIdleActionKey(key));
            SetIdle();
        }

        private float ResolveLoopIdleActionDuration(float requestedDurationSeconds)
        {
            var min = Mathf.Max(5f, loopIdleActionDurationRange.x);
            var max = Mathf.Max(min, loopIdleActionDurationRange.y);
            if (requestedDurationSeconds > 0f)
            {
                return Mathf.Clamp(requestedDurationSeconds, min, max);
            }

            var fallback = defaultLoopIdleActionSeconds > 0f
                ? Mathf.Clamp(defaultLoopIdleActionSeconds, min, max)
                : UnityEngine.Random.Range(min, max);
            return fallback;
        }

        private float ResolveReplySettleWaitSeconds()
        {
            var stopClip = GetPhoneTextingStopClip(ReplyFolderHint);
            if (CanUseAnimatorController() && stopClip != null && HasControllerTrigger(TriggerReplyTextingStop))
            {
                return ClipDuration(stopClip, Mathf.Max(0.75f, replyReceivedSettleSeconds));
            }

            if (stopClip != null)
            {
                return ClipDuration(stopClip, Mathf.Clamp(replyReceivedSettleSeconds, 1f, 6f));
            }

            return Mathf.Clamp(replyReceivedSettleSeconds, 3f, 4f);
        }

        private float ResolveOneShotDurationSeconds(string key, float requestedDurationSeconds, params string[] clipHints)
        {
            if (requestedDurationSeconds > 0f)
            {
                return requestedDurationSeconds;
            }

            if (clipHints != null)
            {
                for (var i = 0; i < clipHints.Length; i++)
                {
                    var hint = clipHints[i];
                    if (string.IsNullOrWhiteSpace(hint))
                    {
                        continue;
                    }

                    var clip = GetClip(hint, IdleActionFolderHint) ??
                        GetClip(hint, TouchFolderHint) ??
                        GetClip(hint, ReplyFolderHint) ??
                        GetClip(hint, "");
                    if (clip != null && clip.length > 0.01f)
                    {
                        return clip.length;
                    }
                }
            }

            if (!string.IsNullOrWhiteSpace(key))
            {
                var clip = GetClip(key, IdleActionFolderHint) ??
                    GetClip(key, TouchFolderHint) ??
                    GetClip(key, "");
                if (clip != null && clip.length > 0.01f)
                {
                    return clip.length;
                }
            }

            return fallbackOneShotSeconds;
        }

        private static string ExpressionForIdleAction(string key)
        {
            if (key.Contains("typing") || key.Contains("tablet") || key.Contains("phone") || key.Contains("texting"))
            {
                return "thinking";
            }

            if (key.Contains("waking") || key.Contains("sleep") || key.Contains("doz") || key.Contains("yawn"))
            {
                return "relaxed";
            }

            return "neutral";
        }

        private static string ControllerTriggerForIdleActionKey(string key)
        {
            switch (key)
            {
                case "phone":
                case "phonetablet":
                case "idletablet":
                case "standingusingtouchscreentablet":
                    return TriggerIdleTablet;
                case "phonetexting":
                case "idletexting":
                case "texting":
                    return TriggerIdleTexting;
                case "typing":
                case "idletyping":
                    return TriggerIdleTyping;
                case "yawn":
                case "idleyawn":
                    return TriggerIdleYawn;
                case "waking":
                case "wakeup":
                case "idlewaking":
                    return TriggerIdleWaking;
                case "sleeping":
                case "sleepy":
                case "dozing":
                case "doze":
                case "idlesleeping":
                case "idledozing":
                    return TriggerIdleSleeping;
                case "sitting":
                    return TriggerIdleSitting;
                default:
                    return "idle action trigger";
            }
        }

        private IEnumerator ReplyRequestRoutine()
        {
            LogControllerOnlyFallbackBlocked(nameof(ReplyRequestRoutine), TriggerReplyRequest);
            yield break;
        }

        private IEnumerator ReplyReceivedRoutine()
        {
            mode = RelaxRoomMotionMode.Reply;
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            replyPhoneHeld = true;
            replyWaitingPoseActive = true;
            replyWaitingPoseScheduled = false;
            PlayReplyWaitingPoseState();
            if (replyVoiceEnded)
            {
                ScheduleReplyPutDownAfterHold();
            }

            yield break;
        }

        private bool TryPlayIdleActionOnController(string key, float holdSeconds)
        {
            if (!CanUseAnimatorController())
            {
                return false;
            }

            switch (key)
            {
                case "phone":
                case "phonetablet":
                case "idletablet":
                case "standingusingtouchscreentablet":
                    if (TryTriggerController(TriggerIdleTablet))
                    {
                        ScheduleIdleReturn(holdSeconds);
                        return true;
                    }
                    return false;
                case "phonetexting":
                case "idletexting":
                case "texting":
                    if (TryTriggerController(TriggerIdleTexting))
                    {
                        ScheduleIdleReturn(holdSeconds);
                        return true;
                    }
                    return false;
                case "typing":
                case "idletyping":
                    if (TryTriggerController(TriggerIdleTyping))
                    {
                        ScheduleIdleReturn(holdSeconds);
                        return true;
                    }
                    return false;
                case "yawn":
                case "idleyawn":
                    if (TryTriggerController(TriggerIdleYawn))
                    {
                        return true;
                    }
                    return false;
                case "waking":
                case "wakeup":
                case "idlewaking":
                    if (TryTriggerController(TriggerIdleWaking))
                    {
                        ScheduleIdleReturn(Mathf.Max(30f, holdSeconds * 0.6f));
                        return true;
                    }
                    return false;
                case "sleeping":
                case "sleepy":
                case "dozing":
                case "doze":
                case "idlesleeping":
                case "idledozing":
                    if (TryTriggerController(TriggerIdleSleeping))
                    {
                        ScheduleIdleReturn(holdSeconds);
                        return true;
                    }
                    return false;
                case "sitting":
                    if (TryTriggerController(TriggerIdleSitting))
                    {
                        ScheduleIdleReturn(holdSeconds);
                        return true;
                    }
                    return false;
                default:
                    return false;
            }
        }

        private void PlayReplyBaseOnly()
        {
            mode = RelaxRoomMotionMode.Reply;
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            if (CanUseAnimatorController())
            {
                if (TryTriggerController(TriggerReplyTextingStop))
                {
                    return;
                }

                if (TryTriggerController(TriggerToIdle))
                {
                    return;
                }

                return;
            }

            LogControllerOnlyFallbackBlocked("PlayReplyBaseOnly", $"{TriggerReplyTextingStop}/{TriggerToIdle}");
        }

        private void PlayReplyWaitingPoseState()
        {
            if (TryTriggerController(TriggerReplyWaitingPose))
            {
                return;
            }

            LogControllerOnlyFallbackBlocked("PlayReplyWaitingPoseState", TriggerReplyWaitingPose);
        }

        private void StartReplyPutDownNow()
        {
            StopManagedRoutine();
            StopDelayedPutDownRoutine();
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            StartManagedRoutine(PutPhoneDownRoutine(ReplyFolderHint));
        }

        private void ScheduleReplyPutDownAfterHold()
        {
            StopDelayedPutDownRoutine();
            delayedPutDownRoutine = StartCoroutine(DelayedReplyPutDownRoutine());
        }

        private IEnumerator DelayedReplyPutDownRoutine()
        {
            yield return new WaitForSeconds(Mathf.Clamp(replyHoldAfterVoiceSeconds, 0.5f, 15f));
            replyWaitingPoseActive = false;
            replyWaitingPoseScheduled = false;
            mode = RelaxRoomMotionMode.Reply;
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.Reply, "phone_texting");
            var putDownDuration = PhonePutDownDuration(ReplyFolderHint);

            if (TryTriggerController(TriggerReplyPutDown))
            {
                yield return new WaitForSeconds(putDownDuration);
            }
            else
            {
                LogControllerOnlyFallbackBlocked("DelayedReplyPutDownRoutine", TriggerReplyPutDown);
            }

            delayedPutDownRoutine = null;
            ClearReplyState();
            SetIdle();
        }

        private IEnumerator PutPhoneDownRoutine(string folderHint)
        {
            mode = RelaxRoomMotionMode.Reply;
            replyWaitingPoseActive = false;
            replyWaitingPoseScheduled = false;
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.Reply, "phone_texting");
            var putDownDuration = PhonePutDownDuration(folderHint);

            if (TryTriggerController(TriggerReplyPutDown))
            {
                yield return new WaitForSeconds(putDownDuration);
                ClearReplyState();
                SetIdle();
                yield break;
            }

            LogControllerOnlyFallbackBlocked("PutPhoneDownRoutine", TriggerReplyPutDown);
            ClearReplyState();
            SetIdle();
        }

        private IEnumerator IdleTextingRoutine(float holdSeconds)
        {
            LogControllerOnlyFallbackBlocked(nameof(IdleTextingRoutine), TriggerIdleTexting);
            yield return null;
            SetIdle();
        }

        private IEnumerator IdleTypingRoutine(float holdSeconds)
        {
            LogControllerOnlyFallbackBlocked(nameof(IdleTypingRoutine), TriggerIdleTyping);
            yield return null;
            SetIdle();
        }

        private IEnumerator LongPhoneLoopRoutine(string clipHint, RelaxRoomPhoneGrip grip, float holdSeconds)
        {
            LogControllerOnlyFallbackBlocked($"{nameof(LongPhoneLoopRoutine)}({clipHint})", TriggerIdleTablet);
            yield return null;
            SetIdle();
        }

        private IEnumerator DozingRoutine(float holdSeconds)
        {
            LogControllerOnlyFallbackBlocked(nameof(DozingRoutine), TriggerIdleSleeping);
            yield return null;
            SetIdle();
        }

        private IEnumerator LongSingleLoopRoutine(string clipHint, float holdSeconds)
        {
            LogControllerOnlyFallbackBlocked($"{nameof(LongSingleLoopRoutine)}({clipHint})", TriggerIdleSitting);
            yield return null;
            SetIdle();
        }

        private void PlayTypingLoopForDialogue()
        {
            StopManagedRoutine();
            SetPhoneVisible(true, RelaxRoomPhoneGrip.ReplyTexting);
            phoneScreen?.ShowMessage(0f, true);
            mode = RelaxRoomMotionMode.Reply;
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Typing);
            if (TryTriggerController(TriggerReplyRequest))
            {
                return;
            }

            LogControllerOnlyFallbackBlocked("PlayTypingLoopForDialogue", TriggerReplyRequest);
        }

        private void PlaySittingSpeaking()
        {
            StopManagedRoutine();
            mode = RelaxRoomMotionMode.Speaking;
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Speaking);
            LogControllerOnlyFallbackBlocked("PlaySittingSpeaking", "Animator Controller speaking state");
        }

        private IEnumerator VideoCallStateReleaseRoutine()
        {
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);
            expressionDirector?.SetEmotion("calm", 0.45f, 1.2f);
            yield return new WaitForSeconds(ResolveVideoCallReleaseSeconds());

            ClearVideoCallState();
            if (mode == RelaxRoomMotionMode.VideoCall)
            {
                SetIdle();
            }
        }

        private float ResolveVideoCallReleaseSeconds()
        {
            var configured = Mathf.Max(0f, videoCallStateReleaseSeconds);
            var putDownClip = GetPhonePutDownClip(ReplyFolderHint);
            var clipSeconds = putDownClip != null ? putDownClip.length : 0f;
            var minimum = Mathf.Max(MinimumVideoCallPutDownSeconds, clipSeconds);
            return Mathf.Clamp(Mathf.Max(configured, minimum), 0.1f, 8f);
        }

        private float PlayPhonePickupMotion(string folderHint, RelaxRoomMotionMode nextMode)
        {
            var fallbackDuration = SegmentDuration(GetClip("Texting", folderHint), textingPickupRange);
            var pickupClip = GetPhonePickupClip(folderHint);
            if (pickupClip != null)
            {
                fallbackDuration = ClipDuration(pickupClip, fallbackDuration);
            }

            LogControllerOnlyFallbackBlocked(nameof(PlayPhonePickupMotion), TriggerReplyRequest);
            return fallbackDuration;
        }

        private float PlayPhonePutDownMotion(string folderHint, RelaxRoomMotionMode nextMode, RelaxRoomPhoneGrip grip)
        {
            var fallbackDuration = PhonePutDownDuration(folderHint);
            var putDownClip = GetPhonePutDownClip(folderHint);
            if (putDownClip != null)
            {
                fallbackDuration = ClipDuration(putDownClip, fallbackDuration);
            }

            LogControllerOnlyFallbackBlocked(nameof(PlayPhonePutDownMotion), TriggerReplyPutDown);
            return fallbackDuration;
        }

        private float PhonePutDownDuration(string folderHint)
        {
            return ClipDuration(GetPhonePutDownClip(folderHint), SegmentDuration(GetClip("Texting", folderHint), textingPutDownRange));
        }

        private void PlayPhoneTextingLoop(string folderHint, RelaxRoomMotionMode nextMode, RelaxRoomPhoneGrip grip)
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayPhoneTextingLoop), "phone texting controller state");
        }

        private bool PlayPhoneFullBodyClip(AnimationClip clip, RelaxRoomMotionMode nextMode, bool loop)
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayPhoneFullBodyClip), clip != null ? clip.name : "missing clip");
            return false;
        }

        private AnimationClip GetPhonePickupClip(string folderHint)
        {
            return GetDedicatedPhoneClip(folderHint, phonePickupMotionHint, "K_sitting_up_phone", "K_PhoneUP", "PhoneUP", "Phone Up", "Pick Up Phone", "Pickup Phone");
        }

        private AnimationClip GetPhonePutDownClip(string folderHint)
        {
            return GetDedicatedPhoneClip(folderHint, phonePutDownMotionHint, "K_sitting_down_phone_Reverse", "K_sitting_down_phone", "K_PhoneDown", "PhoneDown", "Phone Down", "Put Down Phone", "Putdown Phone");
        }

        private AnimationClip GetPhoneTextingLoopClip(string folderHint)
        {
            return GetDedicatedPhoneClip(folderHint, phoneTextingLoopMotionHint, "K_Texting");
        }

        private AnimationClip GetPhoneTextingStopClip(string folderHint)
        {
            return GetDedicatedPhoneClip(folderHint, phoneTextingStopMotionHint, "K_Texting_stop_hand");
        }

        private AnimationClip GetDedicatedPhoneClip(string folderHint, params string[] hints)
        {
            if (!preferDedicatedPhoneClips || hints == null)
            {
                return null;
            }

            var folders = new[] { folderHint, ReplyFolderHint, "" };
            for (var i = 0; i < hints.Length; i++)
            {
                var hint = hints[i];
                if (string.IsNullOrWhiteSpace(hint))
                {
                    continue;
                }

                for (var j = 0; j < folders.Length; j++)
                {
                    var clip = GetClip(hint, folders[j]);
                    if (clip != null)
                    {
                        return clip;
                    }
                }
            }

            return null;
        }

        private void PlayTextingSegment(string folderHint, Vector2 normalizedRange, bool loop, bool freezeAtStart, bool ensurePhoneInHand = true, RelaxRoomPhoneGrip? phoneGrip = null)
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayTextingSegment), "Texting clip segment");
        }

        private void PlayReplyWaitingPose()
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayReplyWaitingPose), TriggerReplyWaitingPose);
        }

        private void PlayReplyDoubleTyping()
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayReplyDoubleTyping), TriggerReplyDoubleTyping);
        }

        private void PlayFullBodyClip(string clipHint, string folderHint, RelaxRoomMotionMode nextMode, bool loop)
        {
            LogControllerOnlyFallbackBlocked($"{nameof(PlayFullBodyClip)}({clipHint})", folderHint);
        }

        private void PlaySingle(string clipHint, string folderHint, bool loop, RelaxRoomMotionMode nextMode)
        {
            LogControllerOnlyFallbackBlocked($"{nameof(PlaySingle)}({clipHint})", folderHint);
        }

        private void PlayLayered(params LayerSpec[] specs)
        {
            PlayLayered(specs, motionTransitionSeconds);
        }

        private void PlayLayered(LayerSpec[] specs, float transitionSeconds)
        {
            LogControllerOnlyFallbackBlocked(nameof(PlayLayered), "scripted clip playback");
        }

        private void ScheduleIdleReturn(float seconds)
        {
            StartManagedRoutine(ReturnIdleAfter(seconds));
        }

        private IEnumerator ReturnIdleAfter(float seconds)
        {
            yield return new WaitForSeconds(Mathf.Max(0.1f, seconds));
            SetIdle();
        }

        private AnimationClip GetSittingClip()
        {
            return GetClip(sittingBaseMotionHint, sittingBaseFolderHint) ??
                GetClip("Sitting Idle", IdleFolderHint) ??
                GetClip("Sitting", IdleActionFolderHint) ??
                GetClip("Sitting Idle", "") ??
                GetClip("Seated Idle", IdleFolderHint) ??
                GetClip("Seated Idle", "");
        }

        private AnimationClip GetClip(string hint, string folderHint)
        {
            if (motionPlayer == null)
            {
                return null;
            }

            if (motionPlayer.Clips.Count == 0)
            {
                motionPlayer.RefreshClipCatalog();
            }

            var index = string.IsNullOrWhiteSpace(folderHint)
                ? motionPlayer.FindClipIndex(hint)
                : motionPlayer.FindClipIndex(hint, folderHint);
            if ((index < 0 || index >= motionPlayer.Clips.Count) && motionPlayer.Clips.Count > 0)
            {
                motionPlayer.RefreshClipCatalog();
                index = string.IsNullOrWhiteSpace(folderHint)
                    ? motionPlayer.FindClipIndex(hint)
                    : motionPlayer.FindClipIndex(hint, folderHint);
            }

            if (index < 0 || index >= motionPlayer.Clips.Count)
            {
                return null;
            }

            return motionPlayer.Clips[index].clip;
        }

        private static float SegmentDuration(AnimationClip clip, Vector2 normalizedRange)
        {
            if (clip == null || clip.length <= 0.01f)
            {
                return 0.8f;
            }

            var start = Mathf.Clamp01(normalizedRange.x) * clip.length;
            var end = Mathf.Clamp01(normalizedRange.y) * clip.length;
            return Mathf.Clamp(Mathf.Abs(end - start), 0.35f, 4f);
        }

        private static float ClipDuration(AnimationClip clip, float fallbackSeconds)
        {
            if (clip == null || clip.length <= 0.01f)
            {
                return Mathf.Clamp(fallbackSeconds, 0.35f, 8f);
            }

            return Mathf.Clamp(clip.length, 0.35f, 8f);
        }

        private static double NormalizedToSeconds(AnimationClip clip, float normalized)
        {
            if (clip == null)
            {
                return 0d;
            }

            return Mathf.Clamp01(normalized) * clip.length;
        }

        private bool CanUseAnimatorController()
        {
            ResolveReferences();
            ConfigureAnimatorRootMotion();
            return useAnimatorControllerStateMachine &&
                animator != null &&
                animator.runtimeAnimatorController != null &&
                animator.isActiveAndEnabled;
        }

        private void ConfigureAnimatorRootMotion()
        {
            if (animator != null)
            {
                animator.applyRootMotion = enableAnimatorRootMotion;
            }
        }

        private bool SetSleepAnimatorParameters(bool sleeping)
        {
            if (!CanUseAnimatorController())
            {
                return false;
            }

            PrepareAnimatorControllerDriver();
            if (sleeping)
            {
                ResetControllerTriggers();
            }

            return SetAnimatorParameterValue(
                bedSleepingParameterName,
                sleeping,
                logMissing: true);
        }

        private void PrepareAnimatorControllerDriver()
        {
            ConfigureAnimatorRootMotion();
            motionPlayer?.ConfigureForAnimatorControllerDriver();
            motionPlayer?.IkAdjuster?.ResetAllWeights();
            StopGraph();
        }

        private bool SetAnimatorParameterValue(string parameterName, bool active, bool logMissing)
        {
            if (animator == null || string.IsNullOrWhiteSpace(parameterName))
            {
                return false;
            }

            if (!TryGetControllerParameter(parameterName, out var parameter))
            {
                if (logMissing)
                {
                    Debug.LogError($"Animator Controller is missing parameter '{parameterName}'.", this);
                }

                return false;
            }

            switch (parameter.type)
            {
                case AnimatorControllerParameterType.Bool:
                    animator.SetBool(parameterName, active);
                    break;
                case AnimatorControllerParameterType.Trigger:
                    animator.ResetTrigger(parameterName);
                    if (active)
                    {
                        animator.SetTrigger(parameterName);
                    }
                    break;
                case AnimatorControllerParameterType.Float:
                    animator.SetFloat(parameterName, active ? 1f : 0f);
                    break;
                case AnimatorControllerParameterType.Int:
                    animator.SetInteger(parameterName, active ? 1 : 0);
                    break;
                default:
                    return false;
            }

            Debug.Log($"RelaxRoom set Animator parameter '{parameterName}' ({parameter.type}) = {(active ? "on" : "off")}.", this);
            return true;
        }

        private bool TryGetControllerParameter(string parameterName, out AnimatorControllerParameter parameter)
        {
            parameter = null;
            if (animator == null || string.IsNullOrWhiteSpace(parameterName))
            {
                return false;
            }

            var parameters = animator.parameters;
            for (var i = 0; i < parameters.Length; i++)
            {
                if (parameters[i].name == parameterName)
                {
                    parameter = parameters[i];
                    return true;
                }
            }

            return false;
        }

        private bool TryTriggerController(string triggerName)
        {
            if (!CanUseAnimatorController())
            {
                return false;
            }

            if (!HasControllerTrigger(triggerName))
            {
                return false;
            }

            motionPlayer?.ConfigureForAnimatorControllerDriver();
            StopGraph();
            ResetControllerTriggers();
            animator.SetTrigger(triggerName);
            return true;
        }

        private void LogControllerOnlyFallbackBlocked(string context, string controllerEntry)
        {
            var key = $"{context}|{controllerEntry}";
            if (!loggedControllerOnlyFallbackBlocks.Add(key))
            {
                return;
            }

            Debug.LogError(
                $"RelaxRoom motion is Animator Controller-only. Scripted clip/playable fallback was blocked for '{context}'. Check Animator Controller entry '{controllerEntry}'.",
                this);
        }

        private void ResetControllerTriggers()
        {
            if (animator == null)
            {
                return;
            }

            for (var i = 0; i < ControllerTriggers.Length; i++)
            {
                if (HasControllerTrigger(ControllerTriggers[i]))
                {
                    animator.ResetTrigger(ControllerTriggers[i]);
                }
            }
        }

        private bool HasControllerTrigger(string triggerName)
        {
            return HasControllerParameter(triggerName, AnimatorControllerParameterType.Trigger);
        }

        private bool TrySetControllerBool(string parameterName, bool value)
        {
            if (!CanUseAnimatorController() ||
                !HasControllerParameter(parameterName, AnimatorControllerParameterType.Bool))
            {
                return false;
            }

            motionPlayer?.ConfigureForAnimatorControllerDriver();
            StopGraph();
            animator.SetBool(parameterName, value);
            return true;
        }

        private bool HasControllerParameter(string parameterName, AnimatorControllerParameterType parameterType)
        {
            if (animator == null || string.IsNullOrWhiteSpace(parameterName))
            {
                return false;
            }

            var parameters = animator.parameters;
            for (var i = 0; i < parameters.Length; i++)
            {
                if (parameters[i].type == parameterType &&
                    parameters[i].name == parameterName)
                {
                    return true;
                }
            }

            return false;
        }

        private void ApplyVideoCallAnimatorState(bool triggerBegin, bool triggerEnd)
        {
            var wroteParameter = false;
            wroteParameter |= TrySetControllerBool(BoolVideoCallActive, videoCallActive);
            wroteParameter |= TrySetControllerBool(BoolVideoCallAiSpeaking, videoCallAiSpeaking);
            wroteParameter |= TrySetControllerBool(BoolVideoCallUserSpeaking, videoCallUserSpeaking);

            if (triggerBegin)
            {
                wroteParameter |= TryTriggerController(TriggerVideoCallBegin);
            }

            if (triggerEnd)
            {
                wroteParameter |= TryTriggerController(TriggerVideoCallEnd);
            }

            if (!wroteParameter && CanUseAnimatorController() && !loggedMissingVideoCallAnimatorContract)
            {
                loggedMissingVideoCallAnimatorContract = true;
                Debug.LogWarning(
                    "Animator Controller is missing the video-call contract parameters: " +
                    "VideoCall_Begin, VideoCall_End, VideoCall_Active, VideoCall_AiSpeaking, VideoCall_UserSpeaking.",
                    this);
            }
        }

        private void StopManagedRoutine()
        {
            if (managedRoutine == null)
            {
                return;
            }

            StopCoroutine(managedRoutine);
            managedRoutine = null;
        }

        private void StopDelayedPutDownRoutine()
        {
            if (delayedPutDownRoutine == null)
            {
                return;
            }

            StopCoroutine(delayedPutDownRoutine);
            delayedPutDownRoutine = null;
        }

        private void ClearReplyState()
        {
            replyPhoneHeld = false;
            replyWaitingPoseScheduled = false;
            replyWaitingPoseActive = false;
            replyVoiceEnded = false;
        }

        private void ClearVideoCallState()
        {
            videoCallActive = false;
            videoCallEnding = false;
            videoCallAiSpeaking = false;
            videoCallUserSpeaking = false;
            ApplyVideoCallAnimatorState(triggerBegin: false, triggerEnd: false);
        }

        private static bool IsPhoneIdleActionKey(string key)
        {
            return key == "phone" ||
                key == "phonetablet" ||
                key == "idletablet" ||
                key == "standingusingtouchscreentablet" ||
                key == "phonetexting" ||
                key == "idletexting" ||
                key == "texting";
        }

        private static RelaxRoomPhoneGrip GetIdleActionPhoneGrip(string key)
        {
            return key == "phone" ||
                key == "phonetablet" ||
                key == "idletablet" ||
                key == "standingusingtouchscreentablet"
                ? RelaxRoomPhoneGrip.IdleTablet
                : RelaxRoomPhoneGrip.IdleTexting;
        }

        private static RelaxRoomPhoneGrip GetTextingSegmentPhoneGrip(string folderHint)
        {
            return string.Equals(folderHint, IdleActionFolderHint, StringComparison.Ordinal)
                ? RelaxRoomPhoneGrip.IdleTexting
                : RelaxRoomPhoneGrip.ReplyTexting;
        }

        private void SetPhoneVisible(bool visible, RelaxRoomPhoneGrip grip = RelaxRoomPhoneGrip.Default)
        {
            ResolveReferences();
            if (phoneAttachment == null)
            {
                if (visible && !loggedMissingPhoneAttachment)
                {
                    Debug.LogWarning("RelaxRoom phone attachment controller is not assigned. Add AIGalgameRelaxRoomPhoneAttachmentController to the character and assign it on Motion Director.", this);
                    loggedMissingPhoneAttachment = true;
                }

                return;
            }

            if (!visible)
            {
                if (phonePickup != null && phonePickup.enabled)
                {
                    phonePickup.PlacePhoneOnTable();
                }
                else
                {
                    phoneAttachment.PlaceOnTable(true);
                }
                return;
            }

            phoneAttachment.SetAnimator(animator);
            EnsurePhoneInHand(grip);
        }

        private bool ShouldUsePhonePickupFlow()
        {
            ResolveReferences();
            return phonePickup != null && phonePickup.enabled && phonePickup.UsePickupFlow;
        }

        private bool ShouldDeferPhoneReleaseToAnimator()
        {
            ResolveReferences();
            return phoneAttachment != null &&
                phoneAttachment.IsAttachedToHand &&
                CanUseAnimatorController() &&
                HasControllerTrigger(TriggerToIdle);
        }

        private void PreparePhoneOnTable()
        {
            ResolveReferences();
            if (phonePickup != null && phonePickup.enabled)
            {
                phonePickup.PlacePhoneOnTable();
                return;
            }

            phoneAttachment?.PlaceOnTable();
        }

        private Transform GetCharacterRoot()
        {
            ResolveReferences();
            if (sleepMoveRoot != null)
            {
                return sleepMoveRoot;
            }

            if (animator != null)
            {
                return animator.transform.IsChildOf(transform) ? transform : animator.transform;
            }

            return transform;
        }

        private Transform ResolveBedSleepPoint()
        {
            if (bedSleepPoint != null)
            {
                return bedSleepPoint;
            }

            bedSleepPoint = FindSceneTransformByName(
                "\u5e8a1",
                "Bed1",
                "bed1",
                "Bed_1",
                "BedSleepPoint",
                "Bed_SleepPoint",
                "bed_sleep_point");
            return bedSleepPoint;
        }

        private Transform ResolveSeatPoint()
        {
            if (seatPoint != null)
            {
                return seatPoint;
            }

            seatPoint = FindSceneTransformByName(
                "seatpoint",
                "SeatPoint",
                "Seat_Point",
                "SittingPoint",
                "DeskSeatPoint");
            return seatPoint;
        }

        private bool MoveCharacterToSleepPoint()
        {
            var sleepPoint = ResolveBedSleepPoint();
            if (sleepPoint == null)
            {
                Debug.LogWarning("RelaxRoom sleep point is not assigned and no scene transform named BedSleepPoint/Bed1/床1 was found. Character position was not changed.", this);
                return false;
            }

            return MoveCharacterTo(sleepPoint);
        }

        private bool MoveCharacterToSeatPoint()
        {
            var targetSeatPoint = ResolveSeatPoint();
            if (targetSeatPoint == null)
            {
                Debug.LogWarning("RelaxRoom seat point is not assigned and no scene transform named seatpoint was found. Character parent was not changed.", this);
                return false;
            }

            return MoveCharacterTo(targetSeatPoint);
        }

        private bool MoveCharacterTo(Transform target)
        {
            var root = GetCharacterRoot();
            if (root == null || target == null)
            {
                return false;
            }

            if (target == root || target.IsChildOf(root))
            {
                Debug.LogWarning($"RelaxRoom cannot parent character root '{root.name}' under '{target.name}' because the target is inside the character hierarchy.", this);
                return false;
            }

            var localScale = root.localScale;
            root.SetParent(target, false);
            root.localPosition = Vector3.zero;
            root.localRotation = Quaternion.identity;
            root.localScale = localScale;
            Debug.Log($"RelaxRoom parented character root '{root.name}' under '{target.name}' and reset local position/rotation.", this);
            return true;
        }

        private void EnsurePhoneInHand(RelaxRoomPhoneGrip grip)
        {
            ResolveReferences();
            if (phonePickup != null && phonePickup.enabled)
            {
                phonePickup.EnsurePhoneInHand(grip);
                return;
            }

            if (phoneAttachment != null)
            {
                phoneAttachment.SetAnimator(animator);
                phoneAttachment.AttachToHand(grip);
            }
        }

        private void MigrateSerializedDefaults()
        {
            if (Normalize(sittingBaseMotionHint) == "seatedidle")
            {
                sittingBaseMotionHint = "Sitting Idle";
            }

            if (string.IsNullOrWhiteSpace(sittingBaseFolderHint))
            {
                sittingBaseFolderHint = IdleFolderHint;
            }
        }

        private void StartManagedRoutine(IEnumerator routine)
        {
            StopManagedRoutine();
            managedRoutine = StartCoroutine(ManagedRoutine(routine));
        }

        private IEnumerator ManagedRoutine(IEnumerator routine)
        {
            yield return routine;
            managedRoutine = null;
        }

#if UNITY_EDITOR
        private static bool IsEditorAnimationPreviewActive()
        {
            return AnimationMode.InAnimationMode();
        }
#endif

        private void StopGraph()
        {
        }

        private void ResolveReferences()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }

            if (motionPlayer == null)
            {
                motionPlayer = GetComponent<AIGalgamePlayableMotionPlayer>() ?? GetComponentInChildren<AIGalgamePlayableMotionPlayer>() ?? GetComponentInParent<AIGalgamePlayableMotionPlayer>();
            }

            if (phoneAttachment == null)
            {
                phoneAttachment = GetComponent<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneAttachmentController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneAttachmentController>();
            }

            if (phonePickup == null)
            {
                phonePickup = GetComponent<AIGalgameRelaxRoomPhonePickupController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhonePickupController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhonePickupController>();
            }

            if (phoneScreen == null)
            {
                phoneScreen = GetComponent<AIGalgameRelaxRoomPhoneScreenController>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomPhoneScreenController>() ??
                    GetComponentInParent<AIGalgameRelaxRoomPhoneScreenController>();
            }

            if (gazeDirector == null)
            {
                gazeDirector = GetComponent<AIGalgameRelaxRoomGazeDirector>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomGazeDirector>() ??
                    GetComponentInParent<AIGalgameRelaxRoomGazeDirector>();
            }

            if (expressionDirector == null)
            {
                expressionDirector = GetComponent<AIGalgameRelaxRoomExpressionDirector>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomExpressionDirector>() ??
                    GetComponentInParent<AIGalgameRelaxRoomExpressionDirector>();
            }

            if (motionPlayer != null && animator != null)
            {
                motionPlayer.SetAnimator(animator);
            }

            if (phoneAttachment != null && animator != null)
            {
                phoneAttachment.SetAnimator(animator);
            }

            if (phonePickup != null)
            {
                phonePickup.SetTargets(animator, motionPlayer, phoneAttachment);
            }

            gazeDirector?.SetTargets(null, animator, this);
        }

        public void StartAmbientAudio()
        {
            PlayBgmIfNeeded();
        }

        private void PlayBgmIfNeeded()
        {
            LoadAudioConfigIfNeeded();
            if (!playBgmOnStart || bgmClip == null)
            {
                return;
            }

            var source = EnsureBgmAudioSource();
            if (source == null)
            {
                return;
            }

            if (source.clip != bgmClip)
            {
                source.clip = bgmClip;
            }

            source.loop = true;
            source.volume = bgmVolume;
            if (!source.isPlaying)
            {
                source.Play();
            }
        }

        private AudioSource EnsureBgmAudioSource()
        {
            if (bgmAudioSource == null)
            {
                bgmAudioSource = EnsureChildAudioSource(transform, "RelaxRoom BGM AudioSource");
            }

            ConfigureBgmSource(bgmAudioSource);
            return bgmAudioSource;
        }

        private AudioSource EnsurePhoneAudioSource()
        {
            LoadAudioConfigIfNeeded();
            if (phoneAudioTarget == null)
            {
                ResolveReferences();
                phoneAudioTarget = phoneAttachment != null && phoneAttachment.PhoneInstance != null
                    ? phoneAttachment.PhoneInstance
                    : FindSceneTransformByName("RelaxRoom Phone Prop", "\u624b\u673a", "Phone");
            }

            if (phoneAudioSource == null && phoneAudioTarget != null)
            {
                phoneAudioSource = phoneAudioTarget.GetComponent<AudioSource>();
            }

            ConfigureSfxSource(phoneAudioSource);
            return phoneAudioSource;
        }

        private AudioSource EnsureKeyboardAudioSource()
        {
            LoadAudioConfigIfNeeded();
            if (keyboardAudioTarget == null)
            {
                keyboardAudioTarget = FindSceneTransformByName("KeyBoard_Apt_01", "Keyboard", "\u952e\u76d8");
            }

            if (keyboardAudioSource == null && keyboardAudioTarget != null)
            {
                keyboardAudioSource = keyboardAudioTarget.GetComponent<AudioSource>() ?? keyboardAudioTarget.gameObject.AddComponent<AudioSource>();
            }

            ConfigureSfxSource(keyboardAudioSource);
            return keyboardAudioSource;
        }

        private void LoadAudioConfigIfNeeded()
        {
            if (audioConfig == null)
            {
                audioConfig = Resources.Load<AIGalgameRelaxRoomAudioConfig>(AudioConfigResource);
            }

            if (audioConfig == null)
            {
                return;
            }

            if (bgmClip == null)
            {
                bgmClip = audioConfig.BgmClip;
            }

            if (phoneNotificationClip == null)
            {
                phoneNotificationClip = audioConfig.PhoneNotificationClip;
            }

            if (phoneTypingClips == null || phoneTypingClips.Length == 0)
            {
                phoneTypingClips = audioConfig.PhoneTypingClips;
            }

            if (keyboardTypingClips == null || keyboardTypingClips.Length == 0)
            {
                keyboardTypingClips = audioConfig.KeyboardTypingClips;
            }
        }

        private void ConfigureAssignedAudioSources()
        {
            ConfigureBgmSource(bgmAudioSource);
            ConfigureSfxSource(phoneAudioSource);
            ConfigureSfxSource(keyboardAudioSource);
        }

        private static AudioSource EnsureChildAudioSource(Transform parent, string childName)
        {
            if (parent == null)
            {
                return null;
            }

            var child = parent.Find(childName);
            if (child == null)
            {
                var childObject = new GameObject(childName);
                child = childObject.transform;
                child.SetParent(parent, false);
            }

            return child.GetComponent<AudioSource>() ?? child.gameObject.AddComponent<AudioSource>();
        }

        private void ConfigureBgmSource(AudioSource source)
        {
            if (source == null)
            {
                return;
            }

            source.playOnAwake = false;
            source.loop = true;
            source.spatialBlend = 0f;
            source.volume = bgmVolume;
            source.pitch = 1f;
        }

        private void ConfigureSfxSource(AudioSource source)
        {
            if (source == null)
            {
                return;
            }

            source.playOnAwake = false;
            source.loop = false;
            source.spatialBlend = 1f;
            source.volume = sfxVolume;
            source.dopplerLevel = 0f;
            source.minDistance = 0.2f;
            source.maxDistance = 8f;
            source.rolloffMode = AudioRolloffMode.Logarithmic;
        }

        private void PlayOneShot(AudioSource source, AudioClip clip, float volumeScale, bool randomizePitch)
        {
            if (source == null || clip == null)
            {
                return;
            }

            source.pitch = randomizePitch
                ? UnityEngine.Random.Range(Mathf.Min(typingPitchRange.x, typingPitchRange.y), Mathf.Max(typingPitchRange.x, typingPitchRange.y))
                : 1f;
            source.PlayOneShot(clip, Mathf.Clamp01(volumeScale));
        }

        private static AudioClip PickRandomClip(AudioClip[] clips, ref int lastIndex)
        {
            if (clips == null || clips.Length == 0)
            {
                return null;
            }

            var validCount = 0;
            for (var i = 0; i < clips.Length; i++)
            {
                if (clips[i] != null)
                {
                    validCount++;
                }
            }

            if (validCount == 0)
            {
                return null;
            }

            var selectedOrdinal = UnityEngine.Random.Range(0, validCount);
            var selectedIndex = -1;
            for (var i = 0; i < clips.Length; i++)
            {
                if (clips[i] == null)
                {
                    continue;
                }

                if (selectedOrdinal == 0)
                {
                    selectedIndex = i;
                    break;
                }

                selectedOrdinal--;
            }

            if (validCount > 1 && selectedIndex == lastIndex)
            {
                for (var i = 1; i < clips.Length; i++)
                {
                    var candidate = (selectedIndex + i) % clips.Length;
                    if (clips[candidate] != null)
                    {
                        selectedIndex = candidate;
                        break;
                    }
                }
            }

            lastIndex = selectedIndex;
            return selectedIndex >= 0 ? clips[selectedIndex] : null;
        }

        private static Transform FindSceneTransformByName(params string[] names)
        {
            var transforms = UnityEngine.Object.FindObjectsByType<Transform>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var name in names)
            {
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }

                for (var i = 0; i < transforms.Length; i++)
                {
                    var candidate = transforms[i];
                    if (candidate != null &&
                        candidate.gameObject.scene.IsValid() &&
                        string.Equals(candidate.name, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return candidate;
                    }
                }
            }

            foreach (var name in names)
            {
                if (string.IsNullOrWhiteSpace(name))
                {
                    continue;
                }

                for (var i = 0; i < transforms.Length; i++)
                {
                    var candidate = transforms[i];
                    if (candidate != null &&
                        candidate.gameObject.scene.IsValid() &&
                        candidate.name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        return candidate;
                    }
                }
            }

            return null;
        }

        private static float RandomRange(Vector2 range, float fallbackMin, float fallbackMax)
        {
            var min = range.x > 0f ? range.x : fallbackMin;
            var max = range.y >= min ? range.y : fallbackMax;
            return UnityEngine.Random.Range(min, max);
        }

        private static bool IsIdleKey(string key)
        {
            return string.IsNullOrWhiteSpace(key) || key == "idle" || key == "calm" || key == "neutral";
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (var value in values)
            {
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value.Trim();
                }
            }

            return "";
        }

        private static string Normalize(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            var chars = new List<char>(value.Length);
            foreach (var ch in value)
            {
                if (char.IsLetterOrDigit(ch))
                {
                    chars.Add(char.ToLowerInvariant(ch));
                }
            }

            return new string(chars.ToArray());
        }

        private readonly struct LayerSpec
        {
            public readonly AnimationClip Clip;
            public readonly AvatarMask Mask;
            public readonly float Weight;
            public readonly double StartSeconds;
            public readonly double EndSeconds;
            public readonly bool Loop;
            public readonly double Speed;

            public LayerSpec(AnimationClip clip, AvatarMask mask, float weight, double startSeconds, double endSeconds, bool loop, double speed)
            {
                Clip = clip;
                Mask = mask;
                Weight = weight;
                StartSeconds = startSeconds;
                EndSeconds = endSeconds;
                Loop = loop;
                Speed = speed;
            }
        }

    }
}
