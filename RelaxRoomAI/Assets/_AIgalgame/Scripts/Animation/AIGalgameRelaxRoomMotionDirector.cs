using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

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
        Speaking
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
        };

        private readonly List<LayerRuntime> layers = new();
        private PlayableGraph graph;
        private AnimationLayerMixerPlayable layerMixer;
        private Coroutine managedRoutine;
        private Coroutine delayedPutDownRoutine;
        private Coroutine graphBlendRoutine;
        private RelaxRoomMotionMode mode = RelaxRoomMotionMode.Idle;
        private float busyUntil;
        private PlayableGraph previousGraph;
        private AnimationPlayableOutput currentOutput;
        private AnimationPlayableOutput previousOutput;
        private bool replyPhoneHeld;
        private bool replyWaitingPoseScheduled;
        private bool replyWaitingPoseActive;
        private bool replyVoiceEnded;
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
            LoadAudioConfigIfNeeded();
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

#if RELAXROOM_RN
            SetIdle(0f);
#else
            PlayBgmIfNeeded();
            SetIdle(0f);
#endif
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

            if (!graph.IsValid())
            {
                return;
            }

            for (var i = 0; i < layers.Count; i++)
            {
                UpdateLayer(layers[i]);
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
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Typing);
            expressionDirector?.SetEmotion("thinking", 0.48f);
            replyPhoneHeld = true;
            replyWaitingPoseScheduled = false;
            replyWaitingPoseActive = false;
            replyVoiceEnded = false;

            if ((!ShouldUsePhonePickupFlow() || continueWithPhone) &&
                TryTriggerController(continueWithPhone ? TriggerReplyDoubleTyping : TriggerReplyRequest))
            {
                return;
            }

            if (continueWithPhone)
            {
                PlayReplyDoubleTyping();
                return;
            }

            StartManagedRoutine(ReplyRequestRoutine());
        }

        public void BeginReplyReceived(bool hasDialogue)
        {
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
            ResolveReferences();
            phoneScreen?.ShowMessage(4.5f, false);

            var source = EnsurePhoneAudioSource();
            if (source == null || phoneNotificationClip == null)
            {
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
            SetPhoneVisible(false);
            mode = RelaxRoomMotionMode.Idle;
            busyUntil = 0f;
            gazeDirector?.SetMotionMode(mode, "idle");
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Idle);

            var baseClip = GetSittingClip();
            if (TryTriggerController(TriggerToIdle))
            {
                return;
            }

            if (baseClip != null)
            {
                PlayLayered(new[] { new LayerSpec(baseClip, null, 1f, 0d, baseClip.length, true, 1d) }, transitionSeconds);
                return;
            }

            PlaySingle("Sitting Idle", IdleFolderHint, loop: true, RelaxRoomMotionMode.Idle);
        }

        public void PlayTouch(string hitArea, string motionHint = "")
        {
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

                PlayFullBodyClip("Bashful", TouchFolderHint, RelaxRoomMotionMode.Touch, loop: false);
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

                PlayFullBodyClip("Sitting Rubbing Arm", TouchFolderHint, RelaxRoomMotionMode.Touch, loop: false);
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
            PlaySingle("Sitting Disapproval", TouchFolderHint, loop: false, RelaxRoomMotionMode.Touch);
            ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, -1f, "Sitting Disapproval", "Disapproval"));
        }

        public void PlayIdleAction(string motion)
        {
            PlayIdleAction(motion, -1f);
        }

        public void PlayIdleAction(string motion, float requestedDurationSeconds)
        {
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

            switch (key)
            {
                case "phone":
                case "phonetablet":
                case "idletablet":
                case "standingusingtouchscreentablet":
                    StartManagedRoutine(LongPhoneLoopRoutine("K_play", RelaxRoomPhoneGrip.IdleTablet, holdSeconds));
                    break;
                case "phonetexting":
                case "idletexting":
                case "texting":
                    StartManagedRoutine(IdleTextingRoutine(holdSeconds));
                    break;
                case "typing":
                case "idletyping":
                    StartManagedRoutine(IdleTypingRoutine(holdSeconds));
                    break;
                case "yawn":
                case "idleyawn":
                    PlayFullBodyClip("Yawn", IdleActionFolderHint, RelaxRoomMotionMode.IdleAction, loop: false);
                    ScheduleIdleReturn(ResolveOneShotDurationSeconds(key, requestedDurationSeconds, "Yawn", "haqie"));
                    break;
                case "waking":
                case "wakeup":
                case "idlewaking":
                case "sleeping":
                case "sleepy":
                case "dozing":
                case "doze":
                case "idlesleeping":
                case "idledozing":
                    StartManagedRoutine(DozingRoutine(holdSeconds));
                    break;
                case "sitting":
                    StartManagedRoutine(LongSingleLoopRoutine("Sitting", holdSeconds));
                    break;
                default:
                    SetIdle();
                    break;
            }
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

        private IEnumerator ReplyRequestRoutine()
        {
            mode = RelaxRoomMotionMode.Reply;
            busyUntil = Time.time + 2f;
            PreparePhoneOnTable();
            phoneScreen?.ShowMessage(0f, true);
            replyPhoneHeld = true;
            replyWaitingPoseScheduled = false;
            replyWaitingPoseActive = false;
            replyVoiceEnded = false;
            var grip = RelaxRoomPhoneGrip.ReplyTexting;
            var pickupDuration = PlayPhonePickupMotion(ReplyFolderHint, RelaxRoomMotionMode.Reply);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PickupDuring(grip, pickupDuration);
            }
            else
            {
                yield return new WaitForSeconds(pickupDuration);
                EnsurePhoneInHand(grip);
            }

            PlayPhoneTextingLoop(ReplyFolderHint, RelaxRoomMotionMode.Reply, grip);
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

            if (ShouldUsePhonePickupFlow() && IsPhoneIdleActionKey(key))
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

            var stopClip = GetPhoneTextingStopClip(ReplyFolderHint);
            if (stopClip != null && PlayPhoneFullBodyClip(stopClip, RelaxRoomMotionMode.Reply, loop: true))
            {
                return;
            }

            PlayPhoneTextingLoop(ReplyFolderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyTexting);
        }

        private void PlayReplyWaitingPoseState()
        {
            if (TryTriggerController(TriggerReplyWaitingPose))
            {
                return;
            }

            PlayReplyWaitingPose();
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
            yield return new WaitForSeconds(Mathf.Clamp(replyHoldAfterVoiceSeconds, 5f, 8f));
            replyWaitingPoseActive = false;
            replyWaitingPoseScheduled = false;
            mode = RelaxRoomMotionMode.Reply;
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyTexting);
            gazeDirector?.SetMotionMode(RelaxRoomMotionMode.Reply, "phone_texting");
            var putDownDuration = PhonePutDownDuration(ReplyFolderHint);

            if (!ShouldUsePhonePickupFlow() && TryTriggerController(TriggerReplyPutDown))
            {
                yield return new WaitForSeconds(putDownDuration);
            }
            else
            {
                putDownDuration = PlayPhonePutDownMotion(ReplyFolderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyTexting);
                if (ShouldUsePhonePickupFlow())
                {
                    yield return phonePickup.PutDownDuring(RelaxRoomPhoneGrip.ReplyTexting, putDownDuration);
                }
                else
                {
                    yield return new WaitForSeconds(putDownDuration);
                }
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

            if (!ShouldUsePhonePickupFlow() && TryTriggerController(TriggerReplyPutDown))
            {
                yield return new WaitForSeconds(putDownDuration);
                ClearReplyState();
                SetIdle();
                yield break;
            }

            putDownDuration = PlayPhonePutDownMotion(folderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyTexting);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PutDownDuring(RelaxRoomPhoneGrip.ReplyTexting, putDownDuration);
            }
            else
            {
                yield return new WaitForSeconds(putDownDuration);
            }
            ClearReplyState();
            SetIdle();
        }

        private IEnumerator IdleTextingRoutine(float holdSeconds)
        {
            PreparePhoneOnTable();
            phoneScreen?.ShowMessage(holdSeconds, false);

            var grip = RelaxRoomPhoneGrip.IdleTexting;
            var pickupDuration = PlayPhonePickupMotion(IdleActionFolderHint, RelaxRoomMotionMode.IdleAction);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PickupDuring(grip, pickupDuration);
            }
            else
            {
                yield return new WaitForSeconds(pickupDuration);
                EnsurePhoneInHand(grip);
            }

            PlayPhoneTextingLoop(IdleActionFolderHint, RelaxRoomMotionMode.IdleAction, grip);
            yield return new WaitForSeconds(Mathf.Max(0.5f, holdSeconds));
            var putDownDuration = PlayPhonePutDownMotion(IdleActionFolderHint, RelaxRoomMotionMode.IdleAction, grip);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PutDownDuring(grip, putDownDuration);
            }
            else
            {
                yield return new WaitForSeconds(putDownDuration);
            }
            SetIdle();
        }

        private IEnumerator IdleTypingRoutine(float holdSeconds)
        {
            PlaySingle("K_Typing", IdleActionFolderHint, loop: true, RelaxRoomMotionMode.IdleAction);
            if (GetClip("K_Typing", IdleActionFolderHint) == null)
            {
                PlaySingle("Typing", IdleActionFolderHint, loop: true, RelaxRoomMotionMode.IdleAction);
            }

            yield return new WaitForSeconds(Mathf.Max(0.5f, holdSeconds));
            SetIdle();
        }

        private IEnumerator LongPhoneLoopRoutine(string clipHint, RelaxRoomPhoneGrip grip, float holdSeconds)
        {
            PreparePhoneOnTable();
            phoneScreen?.ShowMessage(holdSeconds, false);
            var pickupDuration = PlayPhonePickupMotion(IdleActionFolderHint, RelaxRoomMotionMode.IdleAction);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PickupDuring(grip, pickupDuration);
            }
            else
            {
                yield return new WaitForSeconds(pickupDuration);
                EnsurePhoneInHand(grip);
            }

            PlayFullBodyClip(clipHint, IdleActionFolderHint, RelaxRoomMotionMode.IdleAction, loop: true);
            yield return new WaitForSeconds(Mathf.Max(0.5f, holdSeconds));

            var putDownDuration = PlayPhonePutDownMotion(IdleActionFolderHint, RelaxRoomMotionMode.IdleAction, grip);
            if (ShouldUsePhonePickupFlow())
            {
                yield return phonePickup.PutDownDuring(grip, putDownDuration);
            }
            else
            {
                yield return new WaitForSeconds(putDownDuration);
            }

            SetIdle();
        }

        private IEnumerator DozingRoutine(float holdSeconds)
        {
            var endAt = Time.time + Mathf.Max(0.5f, holdSeconds);
            var sleepClip = GetClip("K_wake_Reverse", IdleActionFolderHint) ?? GetClip("Idle_sleeping", IdleActionFolderHint);
            var wakeClip = GetClip("K_wake", IdleActionFolderHint) ?? GetClip("Waking", IdleActionFolderHint);
            var sleepDuration = ClipDuration(sleepClip, 1.8f);
            var wakeDuration = ClipDuration(wakeClip, 1.2f);

            while (Time.time < endAt)
            {
                gazeDirector?.SetMotionMode(RelaxRoomMotionMode.IdleAction, "sleeping");
                expressionDirector?.SetEmotion("relaxed", 0.5f, sleepDuration + 2.5f);
                PlaySingle("K_wake_Reverse", IdleActionFolderHint, loop: false, RelaxRoomMotionMode.IdleAction);
                if (sleepClip == null)
                {
                    PlaySingle("Waking", IdleActionFolderHint, loop: false, RelaxRoomMotionMode.IdleAction);
                }

                yield return new WaitForSeconds(sleepDuration);
                yield return new WaitForSeconds(Mathf.Min(UnityEngine.Random.Range(2.5f, 6f), Mathf.Max(0f, endAt - Time.time)));
                if (Time.time >= endAt)
                {
                    break;
                }

                gazeDirector?.SetMotionMode(RelaxRoomMotionMode.IdleAction, "waking");
                expressionDirector?.AddTransient("surprised", 0.34f, 0.9f);
                PlaySingle("K_wake", IdleActionFolderHint, loop: false, RelaxRoomMotionMode.IdleAction);
                if (wakeClip == null)
                {
                    PlaySingle("Waking", IdleActionFolderHint, loop: false, RelaxRoomMotionMode.IdleAction);
                }

                yield return new WaitForSeconds(wakeDuration);
                yield return new WaitForSeconds(Mathf.Min(UnityEngine.Random.Range(2f, 5f), Mathf.Max(0f, endAt - Time.time)));
            }

            SetIdle();
        }

        private IEnumerator LongSingleLoopRoutine(string clipHint, float holdSeconds)
        {
            PlaySingle(clipHint, IdleActionFolderHint, loop: true, RelaxRoomMotionMode.IdleAction);
            yield return new WaitForSeconds(Mathf.Max(0.5f, holdSeconds));
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

            PlayPhoneTextingLoop(ReplyFolderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyTexting);
        }

        private void PlaySittingSpeaking()
        {
            StopManagedRoutine();
            mode = RelaxRoomMotionMode.Speaking;
            expressionDirector?.SetAvatarState(AIGalgameAvatarState.Speaking);
            if (motionPlayer != null)
            {
                StopGraph();
                if (motionPlayer.PlayClip("Sitting Talking", 0.2f) ||
                    motionPlayer.PlayClip("Sitting Talking", 0.2f, "FBX"))
                {
                    return;
                }
            }

            PlaySingle("Sitting Talking", "", loop: true, RelaxRoomMotionMode.Speaking);
        }

        private float PlayPhonePickupMotion(string folderHint, RelaxRoomMotionMode nextMode)
        {
            var fallbackDuration = SegmentDuration(GetClip("Texting", folderHint), textingPickupRange);
            var pickupClip = GetPhonePickupClip(folderHint);
            if (pickupClip != null && PlayPhoneFullBodyClip(pickupClip, nextMode, loop: false))
            {
                return ClipDuration(pickupClip, fallbackDuration);
            }

            PlayTextingSegment(folderHint, textingPickupRange, loop: false, freezeAtStart: false, ensurePhoneInHand: false);
            return fallbackDuration;
        }

        private float PlayPhonePutDownMotion(string folderHint, RelaxRoomMotionMode nextMode, RelaxRoomPhoneGrip grip)
        {
            var fallbackDuration = PhonePutDownDuration(folderHint);
            var putDownClip = GetPhonePutDownClip(folderHint);
            if (putDownClip != null)
            {
                EnsurePhoneInHand(grip);
                if (PlayPhoneFullBodyClip(putDownClip, nextMode, loop: false))
                {
                    return ClipDuration(putDownClip, fallbackDuration);
                }
            }

            PlayTextingSegment(folderHint, textingPutDownRange, loop: false, freezeAtStart: false, phoneGrip: grip);
            return fallbackDuration;
        }

        private float PhonePutDownDuration(string folderHint)
        {
            return ClipDuration(GetPhonePutDownClip(folderHint), SegmentDuration(GetClip("Texting", folderHint), textingPutDownRange));
        }

        private void PlayPhoneTextingLoop(string folderHint, RelaxRoomMotionMode nextMode, RelaxRoomPhoneGrip grip)
        {
            EnsurePhoneInHand(grip);
            if (nextMode == RelaxRoomMotionMode.Reply)
            {
                phoneScreen?.ShowMessage(0f, true);
            }

            var loopClip = GetPhoneTextingLoopClip(folderHint);
            if (loopClip != null && PlayPhoneFullBodyClip(loopClip, nextMode, loop: true))
            {
                return;
            }

            PlayTextingSegment(folderHint, textingLoopRange, loop: true, freezeAtStart: false, phoneGrip: grip);
        }

        private bool PlayPhoneFullBodyClip(AnimationClip clip, RelaxRoomMotionMode nextMode, bool loop)
        {
            if (clip == null || clip.length <= 0.01f)
            {
                return false;
            }

            if (CanUseAnimatorController())
            {
                return false;
            }

            mode = nextMode;
            PlayLayered(new LayerSpec(clip, null, 1f, 0d, clip.length, loop, 1d));
            return true;
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
            if (ensurePhoneInHand)
            {
                EnsurePhoneInHand(phoneGrip ?? GetTextingSegmentPhoneGrip(folderHint));
            }
            var texting = GetClip("Texting", folderHint);
            if (texting == null)
            {
                SetIdle();
                return;
            }

            var start = NormalizedToSeconds(texting, normalizedRange.x);
            var end = NormalizedToSeconds(texting, normalizedRange.y);
            PlayLayered(new LayerSpec(texting, null, 1f, start, end, loop, freezeAtStart ? 0d : 1d));
        }

        private void PlayReplyWaitingPose()
        {
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyWaiting);
            var waitingClip = GetDedicatedPhoneClip(ReplyFolderHint, "K_sitting_waiting", "wait_for_reply", "Waiting");
            if (waitingClip != null && PlayPhoneFullBodyClip(waitingClip, RelaxRoomMotionMode.Reply, loop: true))
            {
                return;
            }

            PlayPhoneTextingLoop(ReplyFolderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyWaiting);
        }

        private void PlayReplyDoubleTyping()
        {
            EnsurePhoneInHand(RelaxRoomPhoneGrip.ReplyDoubleTyping);
            PlayPhoneTextingLoop(ReplyFolderHint, RelaxRoomMotionMode.Reply, RelaxRoomPhoneGrip.ReplyDoubleTyping);
        }

        private void PlayFullBodyClip(string clipHint, string folderHint, RelaxRoomMotionMode nextMode, bool loop)
        {
            var clip = GetClip(clipHint, folderHint) ?? GetClip(clipHint, "");
            if (clip == null)
            {
                SetIdle();
                return;
            }

            mode = nextMode;
            PlayLayered(new LayerSpec(clip, null, 1f, 0d, clip.length, loop, 1d));
        }

        private void PlaySingle(string clipHint, string folderHint, bool loop, RelaxRoomMotionMode nextMode)
        {
            var clip = GetClip(clipHint, folderHint) ?? GetClip(clipHint, "");
            if (clip == null)
            {
                return;
            }

            mode = nextMode;
            PlayLayered(new LayerSpec(clip, null, 1f, 0d, clip.length, loop, 1d));
        }

        private void PlayLayered(params LayerSpec[] specs)
        {
            PlayLayered(specs, motionTransitionSeconds);
        }

        private void PlayLayered(LayerSpec[] specs, float transitionSeconds)
        {
#if UNITY_EDITOR
            if (IsEditorAnimationPreviewActive())
            {
                StopGraph();
                return;
            }
#endif

            if (CanUseAnimatorController())
            {
                return;
            }

            ResolveReferences();
            if (animator == null)
            {
                return;
            }

            motionPlayer?.Stop();
            PrepareGraphTransition();

            var validSpecs = new List<LayerSpec>();
            foreach (var spec in specs)
            {
                if (spec.Clip != null)
                {
                    validSpecs.Add(spec);
                }
            }

            if (validSpecs.Count == 0)
            {
                return;
            }

            graph = PlayableGraph.Create("AIgalgame RelaxRoom Motion");
            graph.SetTimeUpdateMode(DirectorUpdateMode.GameTime);
            layerMixer = AnimationLayerMixerPlayable.Create(graph, validSpecs.Count);
            currentOutput = AnimationPlayableOutput.Create(graph, "RelaxRoom Motion Output", animator);
            if (!IsOutputValid(currentOutput))
            {
                StopGraph();
                return;
            }

            currentOutput.SetSourcePlayable(layerMixer);
            layers.Clear();

            for (var i = 0; i < validSpecs.Count; i++)
            {
                var spec = validSpecs[i];
                var playable = AnimationClipPlayable.Create(graph, spec.Clip);
                playable.SetApplyFootIK(applyFootIk);
                playable.SetTime(spec.StartSeconds);
                playable.SetSpeed(spec.Speed);
                graph.Connect(playable, 0, layerMixer, i);
                layerMixer.SetInputWeight(i, spec.Weight);
                if (i > 0 && spec.Mask != null)
                {
                    layerMixer.SetLayerMaskFromAvatarMask((uint)i, spec.Mask);
                }

                layers.Add(new LayerRuntime
                {
                    Input = i,
                    Clip = spec.Clip,
                    StartSeconds = spec.StartSeconds,
                    EndSeconds = Math.Max(spec.StartSeconds, spec.EndSeconds),
                    Loop = spec.Loop,
                    Speed = spec.Speed,
                });
            }

            graph.Play();
            StartGraphBlend(transitionSeconds);
        }

        private void UpdateLayer(LayerRuntime layer)
        {
            if (!layerMixer.IsValid() || layer.Input < 0 || layer.Input >= layers.Count || layer.Clip == null || layer.EndSeconds <= layer.StartSeconds)
            {
                return;
            }

            var playable = layerMixer.GetInput(layer.Input);
            if (!playable.IsValid())
            {
                return;
            }

            var time = playable.GetTime();
            if (layer.Speed == 0d)
            {
                playable.SetTime(layer.StartSeconds);
                return;
            }

            if (time < layer.StartSeconds)
            {
                playable.SetTime(layer.StartSeconds);
                return;
            }

            if (time < layer.EndSeconds)
            {
                return;
            }

            if (layer.Loop)
            {
                var span = Math.Max(0.05d, layer.EndSeconds - layer.StartSeconds);
                playable.SetTime(layer.StartSeconds + ((time - layer.StartSeconds) % span));
            }
            else
            {
                playable.SetTime(layer.EndSeconds);
                playable.SetSpeed(0d);
            }
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
            for (var i = 0; i < ControllerTriggers.Length; i++)
            {
                if (HasControllerTrigger(ControllerTriggers[i]))
                {
                    animator.ResetTrigger(ControllerTriggers[i]);
                }
            }

            animator.SetTrigger(triggerName);
            return true;
        }

        private bool HasControllerTrigger(string triggerName)
        {
            if (animator == null)
            {
                return false;
            }

            var parameters = animator.parameters;
            for (var i = 0; i < parameters.Length; i++)
            {
                if (parameters[i].type == AnimatorControllerParameterType.Trigger &&
                    parameters[i].name == triggerName)
                {
                    return true;
                }
            }

            return false;
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

        private void PrepareGraphTransition()
        {
            if (graphBlendRoutine != null)
            {
                StopCoroutine(graphBlendRoutine);
                graphBlendRoutine = null;
            }

            if (previousGraph.IsValid())
            {
                previousGraph.Destroy();
                previousGraph = default;
                previousOutput = default;
            }

            if (!graph.IsValid())
            {
                return;
            }

            previousGraph = graph;
            previousOutput = currentOutput;
            if (IsOutputValid(previousOutput))
            {
                previousOutput.SetWeight(1f);
            }
            graph = default;
            currentOutput = default;
        }

        private void StartGraphBlend(float transitionSeconds)
        {
            var duration = transitionSeconds < 0f
                ? Mathf.Clamp(motionTransitionSeconds, 0f, 2f)
                : Mathf.Clamp(transitionSeconds, 0f, 2f);
            if (!graph.IsValid() || !IsOutputValid(currentOutput))
            {
                if (previousGraph.IsValid())
                {
                    previousGraph.Destroy();
                }

                previousGraph = default;
                previousOutput = default;
                return;
            }

            if (!previousGraph.IsValid() || !IsOutputValid(previousOutput) || duration <= 0.001f)
            {
                currentOutput.SetWeight(1f);
                if (previousGraph.IsValid())
                {
                    previousGraph.Destroy();
                    previousGraph = default;
                    previousOutput = default;
                }
                return;
            }

            currentOutput.SetWeight(0f);
            if (IsOutputValid(previousOutput))
            {
                previousOutput.SetWeight(1f);
            }
            graphBlendRoutine = StartCoroutine(GraphBlendRoutine(duration));
        }

        private IEnumerator GraphBlendRoutine(float duration)
        {
            var elapsed = 0f;
            while (elapsed < duration && graph.IsValid() && IsOutputValid(currentOutput))
            {
                elapsed += Time.deltaTime;
                var weight = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(elapsed / duration));
                currentOutput.SetWeight(weight);
                if (previousGraph.IsValid() && IsOutputValid(previousOutput))
                {
                    previousOutput.SetWeight(1f - weight);
                }

                yield return null;
            }

            if (graph.IsValid() && IsOutputValid(currentOutput))
            {
                currentOutput.SetWeight(1f);
            }

            if (previousGraph.IsValid())
            {
                previousGraph.Destroy();
            }

            previousGraph = default;
            previousOutput = default;
            graphBlendRoutine = null;
        }

        private static bool IsOutputValid(AnimationPlayableOutput output)
        {
            return output.IsOutputValid();
        }

#if UNITY_EDITOR
        private static bool IsEditorAnimationPreviewActive()
        {
            return AnimationMode.InAnimationMode();
        }
#endif

        private void StopGraph()
        {
            if (graphBlendRoutine != null)
            {
                StopCoroutine(graphBlendRoutine);
                graphBlendRoutine = null;
            }

            layers.Clear();
            if (graph.IsValid())
            {
                graph.Destroy();
            }
            if (previousGraph.IsValid())
            {
                previousGraph.Destroy();
            }

            graph = default;
            previousGraph = default;
            currentOutput = default;
            previousOutput = default;
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
                phoneAudioSource = phoneAudioTarget.GetComponent<AudioSource>() ?? phoneAudioTarget.gameObject.AddComponent<AudioSource>();
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

        private sealed class LayerRuntime
        {
            public int Input;
            public AnimationClip Clip;
            public double StartSeconds;
            public double EndSeconds;
            public bool Loop;
            public double Speed;
        }
    }
}
