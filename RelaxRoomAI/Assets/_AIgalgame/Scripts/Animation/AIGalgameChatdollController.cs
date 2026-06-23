using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using UniVRM10;
using UnityEngine;

namespace AIgalgame.Motion
{
    public enum AIGalgameAvatarState
    {
        Idle,
        Typing,
        Speaking
    }

    [Serializable]
    public sealed class AIGalgameDialogueControllerCommand
    {
        public string state = "";
        public string face = "";
        public string animation = "";
        public string focus = "";
        public string mouth = "auto";
        public bool lipsync = true;
        public float pause;
        public string[] tags = Array.Empty<string>();
    }

    [Serializable]
    public sealed class AIGalgameDialogueVisualCue
    {
        public string text = "";
        public string face = "";
        public string expression = "";
        public string focus = "";
        public float weight = 1f;
    }

    [Serializable]
    public sealed class AIGalgameDialogueLine
    {
        public string line_id = "";
        public string text = "";
        public string emotion = "calm";
        public string pose = "idle";
        public string expression = "";
        public string motion = "";
        public string tts_audio_url = "";
        public AIGalgameDialogueControllerCommand controller;
        public AIGalgameDialogueVisualCue[] visual_cues = Array.Empty<AIGalgameDialogueVisualCue>();
    }

    [Serializable]
    public sealed class AIGalgameDialoguePayload
    {
        public AIGalgameDialogueLine[] lines = Array.Empty<AIGalgameDialogueLine>();
    }

    public sealed class AIGalgameAvatarCommand
    {
        public AIGalgameAvatarState State = AIGalgameAvatarState.Speaking;
        public string Text = "";
        public string RawText = "";
        public string Face = "";
        public string Animation = "";
        public string Focus = "";
        public string Mouth = "auto";
        public bool LipSync = true;
        public float DurationSeconds;
        public float PreGapSeconds;
        public AIGalgameDialogueVisualCue[] VisualCues = Array.Empty<AIGalgameDialogueVisualCue>();
    }

    [DefaultExecutionOrder(160)]
    public sealed class AIGalgameChatdollController : MonoBehaviour
    {
        [Header("Targets")]
        [SerializeField] private Vrm10Instance vrmInstance;
        [SerializeField] private AIGalgamePlayableMotionPlayer motionPlayer;
        [SerializeField] private AIGalgameRelaxRoomMotionDirector relaxRoomMotion;
        [SerializeField] private AIGalgameRelaxRoomExpressionDirector expressionDirector;
        [SerializeField] private AIGalgameRelaxRoomGazeDirector gazeDirector;
        [SerializeField] private Animator animator;
        [SerializeField] private AudioSource audioSource;

        [Header("Motion hints")]
        [SerializeField] private string idleMotionHint = "Sitting Idle";
        [SerializeField] private string typingMotionHint = "Sit To Type";
        [SerializeField] private string speakingMotionHint = "Sitting Talking";
        [SerializeField] private string disapprovalMotionHint = "Sitting Disapproval";
        [SerializeField] private float motionBlendSeconds = 0.25f;

        [Header("Lip sync")]
        [SerializeField] private float minSpeechSeconds = 1.1f;
        [SerializeField] private float maxSpeechSeconds = 7.5f;
        [SerializeField] private float secondsPerCharacter = 0.075f;
        [SerializeField] private float mouthOpenScale = 0.92f;
        [SerializeField] private float mouthSmoothSpeed = 12f;
        [SerializeField] private bool faceCameraWhileSpeaking = true;
        [SerializeField] private float faceCameraWeight = 0.28f;
        [SerializeField] private float faceCameraSmoothSpeed = 5f;

        private const string AutoMouth = "auto";
        private static readonly Regex ControlTagRegex = new(@"\[(face|anim|pause)\s*:\s*([^\]]+)\]", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly Regex LegacyFaceTagRegex = new(@"\[(happy|shy|thinking|calm|sad|angry|neutral|relaxed|surprised)\]", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly Regex ExpressionCueTagRegex = new(@"\[(?:face\s*:\s*(?<face>[^\]]+)|(?<legacy>happy|shy|thinking|calm|sad|angry|neutral|relaxed|surprised))\]", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        private static readonly ExpressionKey[] MouthKeys =
        {
            ExpressionKey.Aa,
            ExpressionKey.Ih,
            ExpressionKey.Ou,
            ExpressionKey.Ee,
            ExpressionKey.Oh
        };

        private readonly HashSet<ExpressionKey> controlledFaceKeys = new(ExpressionKey.Comparer);
        private readonly float[] audioSamples = new float[256];
        private Coroutine activeRoutine;
        private Coroutine expressionTimelineRoutine;
        private AIGalgameDialogueLine currentPlayingLine;
        private AIGalgameAvatarState state = AIGalgameAvatarState.Idle;
        private ExpressionKey? activeFaceKey;
        private string activeFaceName = "neutral";
        private string activeMotionName = "idle";
        private string currentText = "";
        private bool lipSyncActive;
        private float lipSyncEndTime;
        private float speechElapsed;
        private float faceWeight;
        private float lookAtCameraWeight;
        private float mouthOpen;
        private int lastMotionIndex = -1;

        public AIGalgameAvatarState State => state;
        public string ActiveFaceName => activeFaceName;
        public string ActiveMotionName => activeMotionName;
        public bool LipSyncActive => lipSyncActive;

        public event Action<AIGalgameDialogueLine> LineCompleted;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
        }

        private void Start()
        {
            SetIdle();
        }

        private void LateUpdate()
        {
            ResolveReferences();
            if (expressionDirector != null && vrmInstance != null)
            {
                UpdateMouth(vrmInstance.Runtime.Expression);
            }
            else
            {
                UpdateExpressionWeights();
            }

            UpdateFaceCamera();
        }

        public void SetTargets(
            Vrm10Instance vrm,
            AIGalgamePlayableMotionPlayer motion,
            Animator targetAnimator,
            AudioSource source = null,
            AIGalgameRelaxRoomMotionDirector relaxMotion = null)
        {
            vrmInstance = vrm;
            motionPlayer = motion;
            animator = targetAnimator;
            audioSource = source != null ? source : audioSource;
            relaxRoomMotion = relaxMotion != null ? relaxMotion : relaxRoomMotion;
            ResolveReferences();

            if (motionPlayer != null && animator != null)
            {
                motionPlayer.SetAnimator(animator);
            }

            if (relaxRoomMotion != null && animator != null)
            {
                relaxRoomMotion.SetTargets(animator, motionPlayer);
                relaxRoomMotion.SetVisualDirectors(gazeDirector, expressionDirector);
            }

            expressionDirector?.SetTargets(vrmInstance);
            gazeDirector?.SetTargets(vrmInstance, animator, relaxRoomMotion);
        }

        public void SetVisualDirectors(AIGalgameRelaxRoomExpressionDirector expression, AIGalgameRelaxRoomGazeDirector gaze)
        {
            expressionDirector = expression != null ? expression : expressionDirector;
            gazeDirector = gaze != null ? gaze : gazeDirector;
            expressionDirector?.SetTargets(vrmInstance);
            gazeDirector?.SetTargets(vrmInstance, animator, relaxRoomMotion);
            relaxRoomMotion?.SetVisualDirectors(gazeDirector, expressionDirector);
        }

        public void SetTouchFocus(Vector3 worldPosition, float seconds = 1.8f)
        {
            ResolveReferences();
            gazeDirector?.SetTouchPoint(worldPosition, seconds);
            expressionDirector?.AddTransient("shy", 0.32f, Mathf.Max(0.4f, seconds));
        }

        public void SetIdle()
        {
            if (this == null)
            {
                return;
            }

            StopActiveRoutine();
            ApplyCommand(new AIGalgameAvatarCommand
            {
                State = AIGalgameAvatarState.Idle,
                Face = "neutral",
                Animation = "idle",
                LipSync = false
            });
        }

        public void SetTyping()
        {
            if (this == null)
            {
                return;
            }

            StopActiveRoutine();
            ApplyCommand(new AIGalgameAvatarCommand
            {
                State = AIGalgameAvatarState.Typing,
                Face = "thinking",
                Animation = "typing",
                LipSync = false
            });
        }

        public void BeginReplyRequest()
        {
            StopActiveRoutine();
            ResolveReferences();
            gazeDirector?.SetFocus("phone");
            expressionDirector?.SetEmotion("thinking", 0.48f);
            relaxRoomMotion?.BeginReplyRequest();
        }

        public void BeginReplyReceived(bool hasDialogue)
        {
            ResolveReferences();
            gazeDirector?.SetFocus(hasDialogue ? "phone" : "user", 2.4f);
            relaxRoomMotion?.BeginReplyReceived(hasDialogue);
        }

        public void EndReplyWithoutMessage()
        {
            ResolveReferences();
            gazeDirector?.SetFocus("user", 1.4f);
            relaxRoomMotion?.EndReplyWithoutMessage();
        }

        public void SetFace(string face)
        {
            if (this == null)
            {
                return;
            }

            SetFaceInternal(face);
        }

        public void PreviewLipSync(float seconds = 2.4f)
        {
            if (this == null)
            {
                return;
            }

            StopActiveRoutine();
            ApplyCommand(new AIGalgameAvatarCommand
            {
                State = AIGalgameAvatarState.Speaking,
                Face = string.IsNullOrWhiteSpace(activeFaceName) ? "happy" : activeFaceName,
                Animation = "speaking",
                Text = "lip sync preview",
                LipSync = true,
                DurationSeconds = Mathf.Max(0.5f, seconds)
            });
            activeRoutine = StartCoroutine(ReturnIdleAfter(seconds));
        }

        public void SayTagged(string taggedText)
        {
            if (this == null)
            {
                return;
            }

            var parsed = ParseTags(taggedText);
            var command = new AIGalgameAvatarCommand
            {
                State = AIGalgameAvatarState.Speaking,
                Text = parsed.CleanText,
                RawText = taggedText ?? "",
                Face = FirstNonEmpty(parsed.Face, "happy"),
                Animation = FirstNonEmpty(parsed.Animation, "speaking"),
                Mouth = AutoMouth,
                LipSync = true,
                PreGapSeconds = parsed.PauseSeconds
            };
            command.DurationSeconds = EstimateSpeechSeconds(command.Text);
            PlayCommand(command);
        }

        public void ApplyLineJson(string json)
        {
            if (this == null)
            {
                return;
            }

            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            var line = JsonUtility.FromJson<AIGalgameDialogueLine>(json);
            PlayLine(line);
        }

        public void ApplyPayloadJson(string json)
        {
            if (this == null)
            {
                return;
            }

            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            var payload = JsonUtility.FromJson<AIGalgameDialoguePayload>(json);
            PlayPayload(payload);
        }

        public void PlayLine(AIGalgameDialogueLine line)
        {
            PlayLine(line, returnIdleWhenDone: true);
        }

        public void PlayLine(AIGalgameDialogueLine line, bool returnIdleWhenDone)
        {
            if (this == null)
            {
                return;
            }

            if (line == null)
            {
                return;
            }

            currentPlayingLine = line;
            PlayCommand(BuildCommand(line), returnIdleWhenDone);
        }

        public void PlayPayload(AIGalgameDialoguePayload payload)
        {
            if (this == null)
            {
                return;
            }

            if (payload?.lines == null || payload.lines.Length == 0)
            {
                SetIdle();
                return;
            }

            StopActiveRoutine();
            activeRoutine = StartCoroutine(PlayPayloadRoutine(payload));
        }

        public string GetDebugSummary()
        {
            var expressionNames = GetAvailableExpressionNames();
            var motionNames = GetAvailableMotionNames();
            return
                $"State: {state}\n" +
                $"Face: {activeFaceName}\n" +
                $"Motion: {activeMotionName}\n" +
                $"LipSync: {(lipSyncActive ? "on" : "off")}\n" +
                $"Expressions: {expressionNames}\n" +
                $"Motions: {motionNames}";
        }

        private void PlayCommand(AIGalgameAvatarCommand command)
        {
            PlayCommand(command, returnIdleWhenDone: true);
        }

        private void PlayCommand(AIGalgameAvatarCommand command, bool returnIdleWhenDone)
        {
            if (this == null)
            {
                return;
            }

            StopActiveRoutine();
            activeRoutine = StartCoroutine(PlayCommandRoutine(command, returnIdleWhenDone));
        }

        private IEnumerator PlayPayloadRoutine(AIGalgameDialoguePayload payload)
        {
            foreach (var line in payload.lines)
            {
                currentPlayingLine = line;
                var command = BuildCommand(line);
                yield return PlayCommandRoutine(command, returnIdleWhenDone: false);
            }

            currentPlayingLine = null;
            SetIdle();
        }

        private IEnumerator PlayCommandRoutine(AIGalgameAvatarCommand command, bool returnIdleWhenDone = true)
        {
            if (command == null)
            {
                yield break;
            }

            if (command.PreGapSeconds > 0f)
            {
                yield return new WaitForSeconds(command.PreGapSeconds);
            }

            if (command.State == AIGalgameAvatarState.Speaking && command.DurationSeconds <= 0f)
            {
                command.DurationSeconds = EstimateSpeechSeconds(command.Text);
            }

            ApplyCommand(command);

            if (command.State != AIGalgameAvatarState.Speaking)
            {
                yield break;
            }

            var duration = command.DurationSeconds > 0f ? command.DurationSeconds : EstimateSpeechSeconds(command.Text);
            StartExpressionTimeline(command, duration);
            var endAt = Time.time + duration;
            while (Time.time < endAt || (audioSource != null && audioSource.isPlaying))
            {
                yield return null;
            }

            if (currentPlayingLine != null)
            {
                LineCompleted?.Invoke(currentPlayingLine);
                currentPlayingLine = null;
            }

            if (returnIdleWhenDone)
            {
                SetIdle();
            }
        }

        private IEnumerator ReturnIdleAfter(float seconds)
        {
            yield return new WaitForSeconds(Mathf.Max(0f, seconds));
            SetIdle();
        }

        private void ApplyCommand(AIGalgameAvatarCommand command)
        {
            if (this == null)
            {
                return;
            }

            ResolveReferences();

            state = command.State;
            currentText = command.Text ?? "";
            activeMotionName = FirstNonEmpty(command.Animation, DefaultMotionForState(state));
            SetFaceInternal(FirstNonEmpty(command.Face, DefaultFaceForState(state)));
            PlayMotion(activeMotionName);
            expressionDirector?.SetAvatarState(state);
            expressionDirector?.SetEmotion(activeFaceName, state == AIGalgameAvatarState.Idle ? 0.42f : 0.88f, command.DurationSeconds);
            gazeDirector?.SetAvatarState(state, activeMotionName);
            if (!string.IsNullOrWhiteSpace(command.Focus))
            {
                gazeDirector?.SetFocus(command.Focus, command.DurationSeconds);
            }

            var mouthMode = (command.Mouth ?? AutoMouth).Trim().ToLowerInvariant();
            lipSyncActive = state == AIGalgameAvatarState.Speaking &&
                command.LipSync &&
                mouthMode != "none" &&
                mouthMode != "off";
            speechElapsed = 0f;
            lipSyncEndTime = Time.time + (command.DurationSeconds > 0f ? command.DurationSeconds : EstimateSpeechSeconds(currentText));

            if (!lipSyncActive)
            {
                mouthOpen = 0f;
                ResetMouthWeights();
            }
        }

        private AIGalgameAvatarCommand BuildCommand(AIGalgameDialogueLine line)
        {
            var parsed = ParseTags(line.text);
            var controller = line.controller;
            var stateName = FirstNonEmpty(controller?.state, "speaking");
            var nextState = ParseState(stateName);
            var mouth = FirstNonEmpty(controller?.mouth, AutoMouth);
            var lipSync = controller == null || controller.lipsync;
            if (string.Equals(mouth, "none", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(mouth, "off", StringComparison.OrdinalIgnoreCase))
            {
                lipSync = false;
            }

            var command = new AIGalgameAvatarCommand
            {
                State = nextState,
                Text = parsed.CleanText,
                RawText = line.text ?? "",
                Face = FirstNonEmpty(controller?.face, line.expression, parsed.Face, line.emotion, DefaultFaceForState(nextState)),
                Animation = FirstNonEmpty(controller?.animation, line.motion, parsed.Animation, line.pose, DefaultMotionForState(nextState)),
                Focus = FirstNonEmpty(controller?.focus, ""),
                Mouth = mouth,
                LipSync = lipSync,
                PreGapSeconds = controller != null && controller.pause > 0f ? Mathf.Clamp(controller.pause, 0f, 10f) : parsed.PauseSeconds,
                VisualCues = line.visual_cues ?? Array.Empty<AIGalgameDialogueVisualCue>()
            };
            command.DurationSeconds = EstimateSpeechSeconds(command.Text);
            return command;
        }

        private void SetFaceInternal(string face)
        {
            activeFaceName = FirstNonEmpty(face, "neutral");
            expressionDirector?.SetEmotion(activeFaceName, state == AIGalgameAvatarState.Idle ? 0.42f : 0.88f);
            if (TryResolveFaceKey(activeFaceName, out var key))
            {
                activeFaceKey = key;
                controlledFaceKeys.Add(key);
            }
            else
            {
                activeFaceKey = null;
            }
        }

        private void PlayMotion(string animation)
        {
            if (relaxRoomMotion != null && relaxRoomMotion.PlaySemanticMotion(animation, state, motionBlendSeconds))
            {
                lastMotionIndex = -1;
                return;
            }

            if (motionPlayer == null)
            {
                return;
            }

            if (motionPlayer.Clips.Count == 0)
            {
                motionPlayer.RefreshClipCatalog();
            }

            var index = FindMotionIndex(animation);
            if (index < 0)
            {
                return;
            }

            if (index == lastMotionIndex && state != AIGalgameAvatarState.Speaking)
            {
                return;
            }

            lastMotionIndex = index;
            motionPlayer.PlayClip(index, motionBlendSeconds);
        }

        private int FindMotionIndex(string animation)
        {
            if (motionPlayer == null || motionPlayer.Clips.Count == 0)
            {
                return -1;
            }

            var candidates = MotionCandidates(animation).Select(NormalizeToken).Where(item => !string.IsNullOrWhiteSpace(item)).Distinct().ToArray();
            for (var pass = 0; pass < 2; pass++)
            {
                for (var i = 0; i < motionPlayer.Clips.Count; i++)
                {
                    var entry = motionPlayer.Clips[i];
                    var names = new[]
                    {
                        entry?.displayName,
                        entry?.clip != null ? entry.clip.name : ""
                    }.Select(NormalizeToken).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();

                    foreach (var candidate in candidates)
                    {
                        if (pass == 0 && names.Any(name => name == candidate))
                        {
                            return i;
                        }

                        if (pass == 1 && names.Any(name => name.Contains(candidate) || candidate.Contains(name)))
                        {
                            return i;
                        }
                    }
                }
            }

            return -1;
        }

        private IEnumerable<string> MotionCandidates(string animation)
        {
            var key = NormalizeToken(animation);
            yield return animation;

            switch (key)
            {
                case "":
                case "idle":
                case "calm":
                case "neutral":
                    yield return idleMotionHint;
                    yield return "Sitting Idle";
                    yield return "Idle";
                    break;
                case "typing":
                case "type":
                case "input":
                    yield return typingMotionHint;
                    yield return "Sit To Type";
                    yield return "Type";
                    break;
                case "speaking":
                case "speak":
                case "talk":
                case "talking":
                case "happy":
                case "shy":
                    yield return speakingMotionHint;
                    yield return "Sitting Talking";
                    yield return "Talking";
                    break;
                case "angry":
                case "sad":
                case "disapproval":
                    yield return disapprovalMotionHint;
                    yield return "Sitting Disapproval";
                    yield return "Disapproval";
                    break;
                case "thinking":
                case "think":
                    yield return idleMotionHint;
                    yield return "Sitting Idle";
                    break;
            }
        }

        private void UpdateExpressionWeights()
        {
            if (vrmInstance == null)
            {
                return;
            }

            var expression = vrmInstance.Runtime.Expression;
            var targetFaceWeight = activeFaceKey.HasValue ? 1f : 0f;
            faceWeight = Mathf.MoveTowards(faceWeight, targetFaceWeight, Time.deltaTime * 5f);

            foreach (var key in controlledFaceKeys)
            {
                var weight = activeFaceKey.HasValue && ExpressionKey.Comparer.Equals(activeFaceKey.Value, key) ? faceWeight : 0f;
                expression.SetWeight(key, weight);
            }

            UpdateMouth(expression);
        }

        private void UpdateFaceCamera()
        {
            if (gazeDirector != null)
            {
                lookAtCameraWeight = 0f;
                return;
            }

            if (!faceCameraWhileSpeaking || animator == null)
            {
                lookAtCameraWeight = 0f;
                return;
            }

            var targetWeight = state == AIGalgameAvatarState.Speaking && lipSyncActive ? Mathf.Clamp01(faceCameraWeight) : 0f;
            lookAtCameraWeight = Mathf.MoveTowards(lookAtCameraWeight, targetWeight, Time.deltaTime * Mathf.Max(0.1f, faceCameraSmoothSpeed));
            if (lookAtCameraWeight <= 0.001f)
            {
                return;
            }

            var camera = Camera.main;
            var head = animator.GetBoneTransform(HumanBodyBones.Head);
            if (camera == null || head == null)
            {
                return;
            }

            var direction = camera.transform.position - head.position;
            if (direction.sqrMagnitude <= 0.001f)
            {
                return;
            }

            var desired = Quaternion.LookRotation(direction.normalized, animator.transform.up);
            head.rotation = Quaternion.Slerp(head.rotation, desired, lookAtCameraWeight);
        }

        private void UpdateMouth(Vrm10RuntimeExpression expression)
        {
            if (lipSyncActive)
            {
                speechElapsed += Time.deltaTime;
                if (Time.time > lipSyncEndTime && (audioSource == null || !audioSource.isPlaying))
                {
                    lipSyncActive = false;
                }
            }

            var targetOpen = lipSyncActive ? EstimateMouthOpen() : 0f;
            mouthOpen = Mathf.MoveTowards(mouthOpen, targetOpen, Time.deltaTime * mouthSmoothSpeed);

            for (var i = 0; i < MouthKeys.Length; i++)
            {
                expression.SetWeight(MouthKeys[i], 0f);
            }

            if (mouthOpen <= 0.001f)
            {
                return;
            }

            var visemeIndex = Mathf.Abs(Mathf.FloorToInt(speechElapsed * 9.5f)) % MouthKeys.Length;
            expression.SetWeight(MouthKeys[visemeIndex], mouthOpen);
        }

        private float EstimateMouthOpen()
        {
            var audioLevel = GetAudioLevel();
            if (audioLevel > 0.001f)
            {
                return Mathf.Clamp01(audioLevel * 13f) * mouthOpenScale;
            }

            var textFactor = string.IsNullOrEmpty(currentText) ? 0.65f : Mathf.Clamp01(0.45f + currentText.Length / 60f);
            var wave = Mathf.Abs(Mathf.Sin((speechElapsed * 13.7f) + (currentText.Length * 0.31f)));
            var pulse = Mathf.Abs(Mathf.Sin((speechElapsed * 21.3f) + 1.4f)) * 0.25f;
            return Mathf.Clamp01((0.18f + wave * 0.62f + pulse) * textFactor) * mouthOpenScale;
        }

        private float GetAudioLevel()
        {
            if (audioSource == null || !audioSource.isPlaying)
            {
                return 0f;
            }

            audioSource.GetOutputData(audioSamples, 0);
            var sum = 0f;
            for (var i = 0; i < audioSamples.Length; i++)
            {
                sum += audioSamples[i] * audioSamples[i];
            }

            return Mathf.Sqrt(sum / audioSamples.Length);
        }

        private void ResetMouthWeights()
        {
            if (vrmInstance == null)
            {
                return;
            }

            var expression = vrmInstance.Runtime.Expression;
            for (var i = 0; i < MouthKeys.Length; i++)
            {
                expression.SetWeight(MouthKeys[i], 0f);
            }
        }

        private bool TryResolveFaceKey(string face, out ExpressionKey key)
        {
            var normalized = NormalizeToken(face);
            switch (normalized)
            {
                case "":
                case "idle":
                case "calm":
                case "neutral":
                    return TryFirstExpression(out key, ExpressionKey.Neutral, ExpressionKey.Relaxed, "Neutral", "Calm", "Relaxed");
                case "happy":
                case "joy":
                case "smile":
                    return TryFirstExpression(out key, ExpressionKey.Happy, "Joy", "Fun", "Happy");
                case "fun":
                    return TryFirstExpression(out key, "Fun", ExpressionKey.Happy, "Joy");
                case "angry":
                    return TryFirstExpression(out key, ExpressionKey.Angry, "Angry");
                case "sad":
                case "sorrow":
                    return TryFirstExpression(out key, ExpressionKey.Sad, "Sorrow", "Sad");
                case "shy":
                    return TryFirstExpression(out key, "Shy", "Fun", ExpressionKey.Relaxed, ExpressionKey.Happy);
                case "thinking":
                case "think":
                    return TryFirstExpression(out key, "Thinking", ExpressionKey.Relaxed, ExpressionKey.Neutral);
                case "surprised":
                case "surprise":
                    return TryFirstExpression(out key, ExpressionKey.Surprised, "Surprised");
                case "relaxed":
                    return TryFirstExpression(out key, ExpressionKey.Relaxed, "Relaxed");
                default:
                    return TryFirstExpression(out key, face);
            }
        }

        private bool TryFirstExpression(out ExpressionKey key, params object[] candidates)
        {
            foreach (var candidate in candidates)
            {
                if (candidate is ExpressionKey expressionKey && HasExpressionKey(expressionKey))
                {
                    key = expressionKey;
                    return true;
                }

                if (candidate is string name && TryFindExpressionByName(name, out key))
                {
                    return true;
                }
            }

            key = default;
            return false;
        }

        private bool HasExpressionKey(ExpressionKey expected)
        {
            if (vrmInstance == null)
            {
                return false;
            }

            foreach (var key in vrmInstance.Runtime.Expression.ExpressionKeys)
            {
                if (ExpressionKey.Comparer.Equals(key, expected))
                {
                    return true;
                }
            }

            return false;
        }

        private bool TryFindExpressionByName(string name, out ExpressionKey key)
        {
            key = default;
            if (vrmInstance == null || string.IsNullOrWhiteSpace(name))
            {
                return false;
            }

            var normalized = NormalizeToken(name);
            foreach (var candidate in vrmInstance.Runtime.Expression.ExpressionKeys)
            {
                if (string.Equals(candidate.Name, name, StringComparison.OrdinalIgnoreCase) ||
                    NormalizeToken(candidate.Name) == normalized)
                {
                    key = candidate;
                    return true;
                }
            }

            return false;
        }

        private string GetAvailableExpressionNames()
        {
            if (vrmInstance == null)
            {
                return "none";
            }

            return string.Join(", ", vrmInstance.Runtime.Expression.ExpressionKeys.Select(key => key.Name).Distinct().Take(18));
        }

        private string GetAvailableMotionNames()
        {
            if (motionPlayer == null)
            {
                return "none";
            }

            if (motionPlayer.Clips.Count == 0)
            {
                motionPlayer.RefreshClipCatalog();
            }

            return string.Join(", ", motionPlayer.Clips.Select(clip => clip.displayName).Where(name => !string.IsNullOrWhiteSpace(name)).Take(8));
        }

        private float EstimateSpeechSeconds(string text)
        {
            var count = string.IsNullOrWhiteSpace(text) ? 12 : text.Trim().Length;
            return Mathf.Clamp(count * secondsPerCharacter, minSpeechSeconds, maxSpeechSeconds);
        }

        private static AIGalgameAvatarState ParseState(string value)
        {
            switch (NormalizeToken(value))
            {
                case "idle":
                    return AIGalgameAvatarState.Idle;
                case "typing":
                case "type":
                case "input":
                    return AIGalgameAvatarState.Typing;
                default:
                    return AIGalgameAvatarState.Speaking;
            }
        }

        private static string DefaultFaceForState(AIGalgameAvatarState nextState)
        {
            return nextState switch
            {
                AIGalgameAvatarState.Typing => "thinking",
                AIGalgameAvatarState.Speaking => "happy",
                _ => "neutral"
            };
        }

        private static string DefaultMotionForState(AIGalgameAvatarState nextState)
        {
            return nextState switch
            {
                AIGalgameAvatarState.Typing => "typing",
                AIGalgameAvatarState.Speaking => "speaking",
                _ => "idle"
            };
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

        private static string NormalizeToken(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "";
            }

            return new string(value.Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant).ToArray());
        }

        private static TagParseResult ParseTags(string rawText)
        {
            var result = new TagParseResult
            {
                CleanText = rawText ?? ""
            };

            result.CleanText = ControlTagRegex.Replace(result.CleanText, match =>
            {
                var name = match.Groups[1].Value.Trim().ToLowerInvariant();
                var value = match.Groups[2].Value.Trim();
                switch (name)
                {
                    case "face":
                        result.Face = value;
                        break;
                    case "anim":
                        result.Animation = value;
                        break;
                    case "pause":
                        if (float.TryParse(value, out var pause))
                        {
                            result.PauseSeconds = Mathf.Clamp(pause, 0f, 10f);
                        }
                        break;
                }

                return "";
            });

            result.CleanText = LegacyFaceTagRegex.Replace(result.CleanText, match =>
            {
                if (string.IsNullOrWhiteSpace(result.Face))
                {
                    result.Face = match.Groups[1].Value.Trim();
                }

                return "";
            });

            result.CleanText = Regex.Replace(result.CleanText, @"\s+", " ").Trim();
            return result;
        }

        private void StartExpressionTimeline(AIGalgameAvatarCommand command, float duration)
        {
            StopExpressionTimeline();
            if (expressionDirector == null && gazeDirector == null)
            {
                return;
            }

            var cues = BuildExpressionTimeline(command, duration);
            if (cues.Count == 0)
            {
                return;
            }

            expressionTimelineRoutine = StartCoroutine(PlayExpressionTimeline(cues));
        }

        private IEnumerator PlayExpressionTimeline(List<ExpressionTimelineCue> cues)
        {
            var startedAt = Time.time;
            foreach (var cue in cues)
            {
                var delay = cue.StartSeconds - (Time.time - startedAt);
                if (delay > 0f)
                {
                    yield return new WaitForSeconds(delay);
                }

                if (!string.IsNullOrWhiteSpace(cue.Face))
                {
                    expressionDirector?.SetEmotion(cue.Face, cue.Weight, cue.DurationSeconds + 0.25f);
                }

                if (!string.IsNullOrWhiteSpace(cue.Focus))
                {
                    gazeDirector?.SetFocus(cue.Focus, cue.DurationSeconds);
                }
            }

            expressionTimelineRoutine = null;
        }

        private List<ExpressionTimelineCue> BuildExpressionTimeline(AIGalgameAvatarCommand command, float duration)
        {
            duration = Mathf.Max(0.25f, duration);
            var visualCues = command.VisualCues ?? Array.Empty<AIGalgameDialogueVisualCue>();
            if (visualCues.Length > 0)
            {
                return BuildStructuredExpressionTimeline(command, duration, visualCues);
            }

            if (ExpressionCueTagRegex.IsMatch(command.RawText ?? ""))
            {
                return BuildTaggedExpressionTimeline(command.RawText, command.Face, duration);
            }

            return BuildDefaultExpressionTimeline(command, duration);
        }

        private List<ExpressionTimelineCue> BuildStructuredExpressionTimeline(
            AIGalgameAvatarCommand command,
            float duration,
            AIGalgameDialogueVisualCue[] visualCues)
        {
            var validCues = visualCues
                .Where(cue => cue != null)
                .Select(cue => new
                {
                    Face = FirstNonEmpty(cue.face, cue.expression, command.Face),
                    Focus = cue.focus ?? "",
                    Weight = Mathf.Clamp(cue.weight <= 0f ? 1f : cue.weight, 0.1f, 1f),
                    TextLength = Mathf.Max(1, StripTagsForLength(cue.text).Length)
                })
                .Where(cue => !string.IsNullOrWhiteSpace(cue.Face) || !string.IsNullOrWhiteSpace(cue.Focus))
                .ToArray();
            if (validCues.Length == 0)
            {
                return BuildSingleExpressionCue(command.Face, command.Focus, duration);
            }

            var totalLength = Mathf.Max(1, validCues.Sum(cue => cue.TextLength));
            var cues = new List<ExpressionTimelineCue>(validCues.Length);
            var start = 0f;
            for (var i = 0; i < validCues.Length; i++)
            {
                var span = i == validCues.Length - 1
                    ? Mathf.Max(0.15f, duration - start)
                    : Mathf.Max(0.15f, duration * validCues[i].TextLength / totalLength);
                cues.Add(new ExpressionTimelineCue
                {
                    Face = validCues[i].Face,
                    Focus = validCues[i].Focus,
                    Weight = validCues[i].Weight,
                    StartSeconds = start,
                    DurationSeconds = span
                });
                start += span;
            }

            return cues;
        }

        private List<ExpressionTimelineCue> BuildTaggedExpressionTimeline(string rawText, string fallbackFace, float duration)
        {
            var source = rawText ?? "";
            var matches = ExpressionCueTagRegex.Matches(source);
            if (matches.Count == 0)
            {
                return BuildSingleExpressionCue(fallbackFace, "", duration);
            }

            var segments = new List<TaggedExpressionSegment>();
            var currentFace = FirstNonEmpty(fallbackFace, "happy");
            var lastIndex = 0;
            foreach (Match match in matches)
            {
                AddTaggedSegment(segments, currentFace, source.Substring(lastIndex, match.Index - lastIndex));
                currentFace = FirstNonEmpty(match.Groups["face"].Value, match.Groups["legacy"].Value, currentFace);
                lastIndex = match.Index + match.Length;
            }

            AddTaggedSegment(segments, currentFace, source.Substring(lastIndex));
            if (segments.Count == 0)
            {
                return BuildSingleExpressionCue(fallbackFace, "", duration);
            }

            var totalLength = Mathf.Max(1, segments.Sum(segment => Mathf.Max(1, segment.TextLength)));
            var cues = new List<ExpressionTimelineCue>(segments.Count);
            var start = 0f;
            for (var i = 0; i < segments.Count; i++)
            {
                var span = i == segments.Count - 1
                    ? Mathf.Max(0.15f, duration - start)
                    : Mathf.Max(0.15f, duration * segments[i].TextLength / totalLength);
                cues.Add(new ExpressionTimelineCue
                {
                    Face = segments[i].Face,
                    Weight = 0.88f,
                    StartSeconds = start,
                    DurationSeconds = span
                });
                start += span;
            }

            return cues;
        }

        private static void AddTaggedSegment(List<TaggedExpressionSegment> segments, string face, string text)
        {
            var clean = StripTagsForLength(text);
            if (clean.Length == 0)
            {
                return;
            }

            segments.Add(new TaggedExpressionSegment
            {
                Face = FirstNonEmpty(face, "happy"),
                TextLength = clean.Length
            });
        }

        private static List<ExpressionTimelineCue> BuildSingleExpressionCue(string face, string focus, float duration)
        {
            return new List<ExpressionTimelineCue>
            {
                new()
                {
                    Face = FirstNonEmpty(face, "happy"),
                    Focus = focus ?? "",
                    Weight = 0.88f,
                    StartSeconds = 0f,
                    DurationSeconds = Mathf.Max(0.25f, duration)
                }
            };
        }

        private static List<ExpressionTimelineCue> BuildDefaultExpressionTimeline(AIGalgameAvatarCommand command, float duration)
        {
            var face = FirstNonEmpty(command.Face, DefaultFaceForState(command.State));
            var focus = command.Focus ?? "";
            if (duration < 2.8f)
            {
                return BuildSingleExpressionCue(face, focus, duration);
            }

            var secondaryFace = SecondaryFaceFor(face);
            var firstSpan = Mathf.Max(0.7f, duration * 0.46f);
            var secondSpan = Mathf.Max(0.55f, duration * 0.24f);
            var thirdSpan = Mathf.Max(0.45f, duration - firstSpan - secondSpan);
            return new List<ExpressionTimelineCue>
            {
                new()
                {
                    Face = face,
                    Focus = focus,
                    Weight = 0.82f,
                    StartSeconds = 0f,
                    DurationSeconds = firstSpan
                },
                new()
                {
                    Face = secondaryFace,
                    Weight = 0.48f,
                    StartSeconds = firstSpan,
                    DurationSeconds = secondSpan
                },
                new()
                {
                    Face = face,
                    Weight = 0.78f,
                    StartSeconds = firstSpan + secondSpan,
                    DurationSeconds = thirdSpan
                }
            };
        }

        private static string SecondaryFaceFor(string face)
        {
            switch (NormalizeToken(face))
            {
                case "happy":
                case "joy":
                case "smile":
                    return "relaxed";
                case "shy":
                    return "happy";
                case "thinking":
                case "think":
                    return "neutral";
                case "angry":
                case "disapproval":
                    return "sad";
                case "sad":
                case "sorrow":
                    return "relaxed";
                case "surprised":
                case "surprise":
                    return "thinking";
                case "relaxed":
                    return "neutral";
                default:
                    return "relaxed";
            }
        }

        private static string StripTagsForLength(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
            {
                return "";
            }

            var cleaned = ControlTagRegex.Replace(text, "");
            cleaned = LegacyFaceTagRegex.Replace(cleaned, "");
            return Regex.Replace(cleaned, @"\s+", "").Trim();
        }

        private void ResolveReferences()
        {
            if (this == null)
            {
                return;
            }

            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }

            if (vrmInstance == null)
            {
                vrmInstance = GetComponent<Vrm10Instance>() ?? GetComponentInChildren<Vrm10Instance>() ?? GetComponentInParent<Vrm10Instance>();
            }

            if (motionPlayer == null)
            {
                motionPlayer = GetComponent<AIGalgamePlayableMotionPlayer>() ?? GetComponentInChildren<AIGalgamePlayableMotionPlayer>() ?? GetComponentInParent<AIGalgamePlayableMotionPlayer>();
            }

            if (relaxRoomMotion == null)
            {
                relaxRoomMotion = GetComponent<AIGalgameRelaxRoomMotionDirector>() ?? GetComponentInChildren<AIGalgameRelaxRoomMotionDirector>() ?? GetComponentInParent<AIGalgameRelaxRoomMotionDirector>();
            }

            if (expressionDirector == null)
            {
                expressionDirector = GetComponent<AIGalgameRelaxRoomExpressionDirector>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomExpressionDirector>() ??
                    GetComponentInParent<AIGalgameRelaxRoomExpressionDirector>();
            }

            if (gazeDirector == null)
            {
                gazeDirector = GetComponent<AIGalgameRelaxRoomGazeDirector>() ??
                    GetComponentInChildren<AIGalgameRelaxRoomGazeDirector>() ??
                    GetComponentInParent<AIGalgameRelaxRoomGazeDirector>();
            }

            if (audioSource == null)
            {
                audioSource = GetComponent<AudioSource>() ?? GetComponentInChildren<AudioSource>() ?? GetComponentInParent<AudioSource>();
            }

            if (vrmInstance != null && expressionDirector == null)
            {
                Debug.LogError(
                    "AIGalgameChatdollController is missing AIGalgameRelaxRoomExpressionDirector. Add it to the character in the scene so it is editable in the Inspector.",
                    this);
            }

            if (vrmInstance != null && gazeDirector == null)
            {
                Debug.LogError(
                    "AIGalgameChatdollController is missing AIGalgameRelaxRoomGazeDirector. Add it to the character in the scene so it is editable in the Inspector.",
                    this);
            }

            if (motionPlayer != null && animator != null)
            {
                motionPlayer.SetAnimator(animator);
            }

            if (relaxRoomMotion != null && animator != null)
            {
                relaxRoomMotion.SetTargets(animator, motionPlayer);
                relaxRoomMotion.SetVisualDirectors(gazeDirector, expressionDirector);
            }

            expressionDirector?.SetTargets(vrmInstance);
            gazeDirector?.SetTargets(vrmInstance, animator, relaxRoomMotion);
        }

        private void StopActiveRoutine()
        {
            if (this == null)
            {
                return;
            }

            if (activeRoutine != null)
            {
                StopCoroutine(activeRoutine);
                activeRoutine = null;
            }

            StopExpressionTimeline();
        }

        private void StopExpressionTimeline()
        {
            if (expressionTimelineRoutine == null)
            {
                return;
            }

            StopCoroutine(expressionTimelineRoutine);
            expressionTimelineRoutine = null;
        }

        private sealed class TagParseResult
        {
            public string CleanText = "";
            public string Face = "";
            public string Animation = "";
            public float PauseSeconds;
        }

        private sealed class TaggedExpressionSegment
        {
            public string Face = "";
            public int TextLength;
        }

        private sealed class ExpressionTimelineCue
        {
            public string Face = "";
            public string Focus = "";
            public float Weight = 1f;
            public float StartSeconds;
            public float DurationSeconds;
        }
    }
}
