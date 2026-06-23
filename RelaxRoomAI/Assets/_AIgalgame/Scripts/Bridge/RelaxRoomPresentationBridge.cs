using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-50)]
    [DisallowMultipleComponent]
    public sealed class RelaxRoomPresentationBridge : MonoBehaviour
    {
        [Serializable]
        private sealed class BridgeEnvelope
        {
            public string cmd = "";
            public string id = "";
            public string payload_json = "";
            public bool has_dialogue;
            public string backend_base_url = "";
            public string user_id = "";
            public string character_id = "";
            public string session_id = "";
            public string appearance_id = "";
            public string motion = "";
            public float duration_seconds;
            public string emotion = "";
            public float intensity = 1f;
            public float hold_seconds;
            public string focus = "";
            public string hit_area = "";
            public string motion_hint = "";
            public string text = "";
            public float x;
            public float y;
            public float z;
            public AIGalgameDialogueLine line;
        }

        [Serializable]
        private sealed class BridgeEvent
        {
            public string evt = "";
            public string id = "";
            public string hit_area = "";
            public string line_id = "";
            public string text = "";
            public string state = "";
            public string face = "";
            public string motion = "";
            public bool lip_sync;
            public string summary = "";
        }

        [Header("Defaults")]
        [SerializeField] private string defaultBackendBaseUrl = "http://10.0.2.2:8899";
        [SerializeField] private string defaultUserId = "demo_user";
        [SerializeField] private string defaultCharacterId = "atri";
        [SerializeField] private string defaultSessionId = "relaxroom_rn";
        [SerializeField] private string defaultAppearanceId = "neko";

        private AIGalgameChatdollController controller;
        private AIGalgameRelaxRoomMotionDirector motionDirector;
        private AIGalgameRelaxRoomExpressionDirector expressionDirector;
        private AIGalgameRelaxRoomGazeDirector gazeDirector;
        private AIGalgameRelaxRoomTouchController touchController;
        private AIGalgameRelaxRoomRnAsrController asrController;
        private RelaxRoomCameraDirector cameraDirector;
        private AudioSource audioSource;
        private string backendBaseUrl;
        private bool readySent;
        private bool roomReadySent;
        private bool environmentReadySent;
        private bool pendingAmbientAudio;
        private bool replySequenceActive;

        public void SetTargets(
            AIGalgameChatdollController targetController,
            AIGalgameRelaxRoomMotionDirector targetMotionDirector,
            AIGalgameRelaxRoomExpressionDirector targetExpressionDirector,
            AIGalgameRelaxRoomGazeDirector targetGazeDirector,
            AIGalgameRelaxRoomTouchController targetTouchController,
            AudioSource targetAudioSource)
        {
            controller = targetController;
            motionDirector = targetMotionDirector;
            expressionDirector = targetExpressionDirector;
            gazeDirector = targetGazeDirector;
            touchController = targetTouchController;
            audioSource = targetAudioSource;

            asrController = ResolveSceneComponent<AIGalgameRelaxRoomRnAsrController>(
                "Add AIGalgameRelaxRoomRnAsrController to the RelaxRoomBridge scene object.");
            asrController?.BindBridge(this);

            cameraDirector = ResolveSceneComponent<RelaxRoomCameraDirector>(
                "Add RelaxRoomCameraDirector to the RelaxRoomBridge scene object.");

            backendBaseUrl = defaultBackendBaseUrl;
            controller?.ConfigureBackendBaseUrl(backendBaseUrl);
            touchController?.ConfigureBackend(defaultBackendBaseUrl, defaultUserId, defaultCharacterId, defaultAppearanceId);

            if (controller != null)
            {
                controller.LineCompleted -= OnLineCompleted;
                controller.LineCompleted += OnLineCompleted;
            }

            if (touchController != null)
            {
                touchController.TouchStarted -= OnTouchStarted;
                touchController.TouchStarted += OnTouchStarted;
                touchController.TouchFinished -= OnTouchFinished;
                touchController.TouchFinished += OnTouchFinished;
            }

            AIGalgameStartupDiagnostics.Log("bridge_set_targets_end");
            SendRoomReady();
        }

        public void NotifyEnvironmentReady()
        {
            SendEnvironmentReady();
        }

        private void OnDestroy()
        {
            if (controller != null)
            {
                controller.LineCompleted -= OnLineCompleted;
            }

            if (touchController != null)
            {
                touchController.TouchStarted -= OnTouchStarted;
                touchController.TouchFinished -= OnTouchFinished;
            }
        }

        public void OnCommand(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            BridgeEnvelope envelope;
            try
            {
                envelope = JsonUtility.FromJson<BridgeEnvelope>(json);
            }
            catch (Exception exception)
            {
                EmitError($"Invalid bridge JSON: {exception.Message}", "");
                return;
            }

            if (envelope == null || string.IsNullOrWhiteSpace(envelope.cmd))
            {
                EmitError("Missing bridge cmd", envelope?.id);
                return;
            }

            try
            {
                Dispatch(envelope);
            }
            catch (Exception exception)
            {
                EmitError(exception.Message, envelope.id);
            }
        }

        private void Dispatch(BridgeEnvelope envelope)
        {
            switch (envelope.cmd)
            {
                case "configure":
                    ApplyConfigure(envelope);
                    break;
                case "avatar.idle":
                    controller?.SetIdle();
                    break;
                case "avatar.typing":
                    controller?.SetTyping();
                    break;
                case "avatar.reply.begin":
                    replySequenceActive = true;
                    controller?.BeginReplyRequest();
                    break;
                case "avatar.reply.received":
                    replySequenceActive = envelope.has_dialogue;
                    controller?.BeginReplyReceived(envelope.has_dialogue);
                    break;
                case "avatar.reply.end":
                    replySequenceActive = false;
                    controller?.EndReplyWithoutMessage();
                    break;
                case "avatar.play_line":
                    if (envelope.line != null)
                    {
                        controller?.PlayLine(envelope.line, returnIdleWhenDone: !replySequenceActive);
                    }
                    else if (!string.IsNullOrWhiteSpace(envelope.payload_json))
                    {
                        controller?.ApplyLineJson(envelope.payload_json);
                    }
                    else
                    {
                        EmitError("avatar.play_line missing line", envelope.id);
                    }
                    break;
                case "avatar.play_payload":
                    if (!string.IsNullOrWhiteSpace(envelope.payload_json))
                    {
                        controller?.ApplyPayloadJson(envelope.payload_json);
                    }
                    else
                    {
                        EmitError("avatar.play_payload missing payload_json", envelope.id);
                    }
                    break;
                case "avatar.set_face":
                    controller?.SetFace(envelope.emotion);
                    break;
                case "avatar.say_tagged":
                    controller?.SayTagged(envelope.text);
                    break;
                case "avatar.touch_focus":
                    controller?.SetTouchFocus(new Vector3(envelope.x, envelope.y, envelope.z), envelope.hold_seconds);
                    break;
                case "motion.idle":
                    motionDirector?.SetIdle();
                    break;
                case "motion.idle_action":
                    motionDirector?.PlayIdleAction(envelope.motion, envelope.duration_seconds);
                    break;
                case "motion.touch":
                    motionDirector?.PlayTouch(envelope.hit_area, envelope.motion_hint);
                    break;
                case "motion.phone_notification":
                    motionDirector?.PlayPhoneNotificationSfx();
                    break;
                case "sleep.enter":
                    motionDirector?.EnterSleep();
                    EmitSleepState("sleeping");
                    break;
                case "sleep.exit":
                    motionDirector?.ExitSleep();
                    EmitSleepState("idle");
                    break;
                case "sleep.notify":
                    motionDirector?.NotifyWhileSleeping(envelope.duration_seconds);
                    break;
                case "camera.next":
                    EnsureCameraDirector()?.NextCamera();
                    EmitCameraState();
                    break;
                case "camera.prev":
                    EnsureCameraDirector()?.PreviousCamera();
                    EmitCameraState();
                    break;
                case "camera.set":
                    EnsureCameraDirector()?.SetCamera(envelope.text);
                    EmitCameraState();
                    break;
                case "video_call.begin":
                    EnsureCameraDirector()?.BeginVideoCallCamera();
                    motionDirector?.BeginVideoCall();
                    EmitCameraState();
                    break;
                case "video_call.end":
                    motionDirector?.EndVideoCall();
                    EnsureCameraDirector()?.EndVideoCallCamera();
                    EmitCameraState();
                    break;
                case "video_call.ai_speaking_start":
                    motionDirector?.SetVideoCallAiSpeaking(true);
                    break;
                case "video_call.ai_speaking_stop":
                    motionDirector?.SetVideoCallAiSpeaking(false);
                    break;
                case "video_call.user_speaking_start":
                    motionDirector?.SetVideoCallUserSpeaking(true);
                    break;
                case "video_call.user_speaking_stop":
                    motionDirector?.SetVideoCallUserSpeaking(false);
                    break;
                case "video_call.expression":
                    expressionDirector?.SetEmotion(envelope.emotion, envelope.intensity, envelope.hold_seconds);
                    break;
                case "expression.set":
                    expressionDirector?.SetEmotion(envelope.emotion, envelope.intensity, envelope.hold_seconds);
                    break;
                case "expression.transient":
                    expressionDirector?.AddTransient(envelope.emotion, envelope.intensity, envelope.hold_seconds);
                    break;
                case "gaze.set_focus":
                    gazeDirector?.SetFocus(envelope.focus, envelope.hold_seconds);
                    break;
                case "gaze.touch_point":
                    gazeDirector?.SetTouchPoint(new Vector3(envelope.x, envelope.y, envelope.z), envelope.hold_seconds);
                    break;
                case "audio.play_url":
                    StartCoroutine(PlayAudioUrl(envelope.text));
                    break;
                case "touch.simulate":
                    touchController?.SimulateTouch(envelope.hit_area);
                    break;
                case "asr.start":
                    asrController?.StartRecording(envelope.id);
                    break;
                case "asr.stop":
                    asrController?.StopRecording(envelope.id);
                    break;
                default:
                    EmitError($"Unknown cmd: {envelope.cmd}", envelope.id);
                    break;
            }
        }

        private void ApplyConfigure(BridgeEnvelope envelope)
        {
            AIGalgameStartupDiagnostics.Log("configure_received");
            backendBaseUrl = FirstNonEmpty(envelope.backend_base_url, defaultBackendBaseUrl);
            defaultUserId = FirstNonEmpty(envelope.user_id, defaultUserId);
            defaultCharacterId = FirstNonEmpty(envelope.character_id, defaultCharacterId);
            defaultSessionId = FirstNonEmpty(envelope.session_id, defaultSessionId);
            defaultAppearanceId = FirstNonEmpty(envelope.appearance_id, defaultAppearanceId);
            controller?.ConfigureBackendBaseUrl(backendBaseUrl);
            touchController?.ConfigureBackend(backendBaseUrl, defaultUserId, defaultCharacterId, defaultAppearanceId);
#if RELAXROOM_RN
            pendingAmbientAudio = true;
#endif
            SendRoomReady();
        }

        private IEnumerator PlayAudioUrl(string url)
        {
            if (audioSource == null || string.IsNullOrWhiteSpace(url))
            {
                yield break;
            }

            var fullUrl = url.StartsWith("http", StringComparison.OrdinalIgnoreCase)
                ? url
                : backendBaseUrl.TrimEnd('/') + (url.StartsWith("/") ? url : "/" + url);

            using var request = UnityWebRequestMultimedia.GetAudioClip(fullUrl, AudioTypeForUrl(fullUrl));
            yield return request.SendWebRequest();
            if (request.result != UnityWebRequest.Result.Success)
            {
                EmitError($"audio.play_url failed: {request.error}", "");
                yield break;
            }

            var clip = DownloadHandlerAudioClip.GetContent(request);
            if (clip == null)
            {
                yield break;
            }

            audioSource.clip = clip;
            audioSource.Play();
        }

        private static AudioType AudioTypeForUrl(string url)
        {
            var lower = (url ?? "").Split('?')[0].ToLowerInvariant();
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

        private void OnLineCompleted(AIGalgameDialogueLine line)
        {
            Emit(new BridgeEvent
            {
                evt = "line.finished",
                line_id = line?.line_id ?? "",
                text = line?.text ?? ""
            });
        }

        private void OnTouchStarted(string hitArea)
        {
            Emit(new BridgeEvent
            {
                evt = "touch.started",
                hit_area = hitArea ?? ""
            });
        }

        private void OnTouchFinished(string hitArea, string text, string lineId, bool ok)
        {
            Emit(new BridgeEvent
            {
                evt = "touch.finished",
                hit_area = hitArea ?? "",
                text = text ?? "",
                line_id = lineId ?? ""
            });
        }

        private void SendRoomReady()
        {
            if (roomReadySent)
            {
                return;
            }

            roomReadySent = true;
            readySent = true;
            AIGalgameStartupDiagnostics.Log("room_ready");

            var summary = controller != null ? controller.GetDebugSummary() : "RelaxRoom bridge online";
            var elapsedMs = Mathf.RoundToInt(AIGalgameStartupDiagnostics.ElapsedSeconds * 1000f);

            Emit(new BridgeEvent
            {
                evt = "room_ready",
                summary = summary,
                state = elapsedMs.ToString()
            });

            Emit(new BridgeEvent
            {
                evt = "ready",
                summary = summary,
                state = elapsedMs.ToString()
            });

            SendEnvironmentReady();
            StartCoroutine(FinishStartupDiagnostics());
        }

        private IEnumerator FinishStartupDiagnostics()
        {
            yield return null;
            AIGalgameStartupDiagnostics.Log("first_frame");
            yield return null;
            AIGalgameStartupDiagnostics.Log("stable_frame");

            var elapsedMs = Mathf.RoundToInt(AIGalgameStartupDiagnostics.ElapsedSeconds * 1000f);
            Emit(new BridgeEvent
            {
                evt = "stable_frame",
                state = elapsedMs.ToString()
            });

#if UNITY_ANDROID && !UNITY_EDITOR
            asrController?.PrepareRecognizerAfterStartup();
#endif

#if RELAXROOM_RN
            if (pendingAmbientAudio)
            {
                pendingAmbientAudio = false;
                motionDirector?.StartAmbientAudio();
                AIGalgameStartupDiagnostics.Log("configure_ambient_audio_started");
            }
#endif

            AIGalgameStartupDiagnostics.SendTimelineSummary();
        }

        private void SendEnvironmentReady()
        {
            if (environmentReadySent)
            {
                return;
            }

            environmentReadySent = true;
            AIGalgameStartupDiagnostics.Log("environment_ready");

            Emit(new BridgeEvent
            {
                evt = "environment_ready",
                state = Mathf.RoundToInt(AIGalgameStartupDiagnostics.ElapsedSeconds * 1000f).ToString()
            });
        }

        private void Emit(BridgeEvent bridgeEvent)
        {
            ReactNativeMessenger.Send(JsonUtility.ToJson(bridgeEvent));
        }

        private RelaxRoomCameraDirector EnsureCameraDirector()
        {
            if (cameraDirector == null)
            {
                cameraDirector = ResolveSceneComponent<RelaxRoomCameraDirector>(
                    "Add RelaxRoomCameraDirector to the RelaxRoomBridge scene object.");
            }

            return cameraDirector;
        }

        private T ResolveSceneComponent<T>(string setupHint) where T : Component
        {
            var component = GetComponent<T>();
            if (component == null)
            {
                component = UnityEngine.Object.FindFirstObjectByType<T>(FindObjectsInactive.Include);
            }

            if (component != null)
            {
                return component;
            }

            Debug.LogError(
                $"RelaxRoom scene setup is missing {typeof(T).Name}. {setupHint} Runtime AddComponent is disabled.",
                this);
            return null;
        }

        private void EmitSleepState(string state)
        {
            Emit(new BridgeEvent
            {
                evt = "sleep.changed",
                state = state ?? ""
            });
        }

        private void EmitCameraState()
        {
            var director = EnsureCameraDirector();
            Emit(new BridgeEvent
            {
                evt = "camera.changed",
                state = director != null ? director.CurrentCameraName : ""
            });
        }

        private void EmitError(string message, string id)
        {
            Emit(new BridgeEvent
            {
                evt = "error",
                id = id ?? "",
                text = message ?? ""
            });
        }

        internal void EmitAsrState(string id, string state)
        {
            Emit(new BridgeEvent
            {
                evt = "asr.state",
                id = id ?? "",
                state = state ?? ""
            });
        }

        internal void EmitAsrFinished(string id, string text)
        {
            Emit(new BridgeEvent
            {
                evt = "asr.finished",
                id = id ?? "",
                text = text ?? ""
            });
        }

        internal void EmitAsrError(string id, string message)
        {
            Emit(new BridgeEvent
            {
                evt = "asr.error",
                id = id ?? "",
                text = message ?? ""
            });
        }

        private static string FirstNonEmpty(string value, string fallback)
        {
            return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
        }
    }
}
