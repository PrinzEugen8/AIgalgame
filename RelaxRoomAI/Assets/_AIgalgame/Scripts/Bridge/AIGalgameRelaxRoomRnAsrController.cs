using System;
using System.Collections;
using UnityEngine;

namespace AIgalgame.Motion
{
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomRnAsrController : MonoBehaviour
    {
        [SerializeField] private string asrLanguage = "zh";
        [SerializeField, Range(1, 4)] private int asrThreads = 2;
        [SerializeField] private int recordMaxSeconds = 10;
        [SerializeField] private int recordSampleRate = 16000;

        private RelaxRoomPresentationBridge bridge;
        private AIGalgameLocalAsrClient localAsrClient;
        private bool recordingVoice;
        private bool asrInFlight;
        private bool asrPrepareStarted;
        private bool asrReady;
        private Coroutine startVoiceRoutine;
        private Coroutine prepareAsrRoutine;
        private AudioClip recordedClip;
        private int recordedSamplePosition;
        private string activeMicrophoneDevice;
        private string activeRequestId = "";

        public void BindBridge(RelaxRoomPresentationBridge targetBridge)
        {
            bridge = targetBridge;
        }

        public void PrepareRecognizerAfterStartup()
        {
            if (asrPrepareStarted || asrReady || prepareAsrRoutine != null)
            {
                return;
            }

            prepareAsrRoutine = StartCoroutine(PrepareRecognizer());
        }

        private IEnumerator PrepareRecognizer()
        {
            asrPrepareStarted = true;
            AIGalgameStartupDiagnostics.Log("asr_prepare_begin");

#if UNITY_ANDROID && !UNITY_EDITOR
            localAsrClient ??= new AIGalgameLocalAsrClient();
            localAsrClient.Configure(asrLanguage, asrThreads);
            var prepareTask = localAsrClient.PrepareAndroidAsync();
            while (!prepareTask.IsCompleted)
            {
                yield return null;
            }

            prepareAsrRoutine = null;
            var result = prepareTask.IsFaulted
                ? AIGalgameLocalAsrResult.Fail(prepareTask.Exception?.GetBaseException().Message ?? "Android ASR prepare failed")
                : prepareTask.Result;

            if (result.Ok)
            {
                asrReady = true;
                AIGalgameStartupDiagnostics.Log("asr_ready");
                yield break;
            }

            AIGalgameStartupDiagnostics.Log("asr_prepare_failed", result.Error);
#else
            yield return null;
            prepareAsrRoutine = null;
            AIGalgameStartupDiagnostics.Log("asr_prepare_failed", "Android only");
#endif
        }

        public void StartRecording(string requestId)
        {
            if (asrInFlight)
            {
                EmitAsrError(requestId, "语音识别还在进行");
                return;
            }

            if (recordingVoice)
            {
                EmitAsrError(requestId, "正在录音，请先松开");
                return;
            }

            if (startVoiceRoutine != null)
            {
                return;
            }

            activeRequestId = requestId ?? "";
            startVoiceRoutine = StartCoroutine(StartVoiceRecording());
        }

        public void StopRecording(string requestId)
        {
            if (!recordingVoice)
            {
                EmitAsrError(requestId, "当前没有进行中的录音");
                return;
            }

            activeRequestId = string.IsNullOrWhiteSpace(requestId) ? activeRequestId : requestId;
            recordedSamplePosition = recordedClip != null ? Microphone.GetPosition(activeMicrophoneDevice) : 0;
            Microphone.End(activeMicrophoneDevice);
            recordingVoice = false;

            var sampleRate = Mathf.Clamp(recordSampleRate, 8000, 48000);
            var samples = ExtractRecordedMonoSamples(recordedClip, recordedSamplePosition);
            if (samples.Length == 0)
            {
                EmitAsrError(activeRequestId, "没有录到声音");
                return;
            }

            if (!HasAudibleVoice(samples))
            {
                EmitAsrError(activeRequestId, "没有检测到语音");
                return;
            }

            StartCoroutine(RecognizeVoiceSamples(activeRequestId, samples, sampleRate));
        }

        private IEnumerator StartVoiceRecording()
        {
#if UNITY_ANDROID || UNITY_IOS
            if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
            {
                EmitAsrState(activeRequestId, "requesting_mic");
                yield return Application.RequestUserAuthorization(UserAuthorization.Microphone);
                if (!Application.HasUserAuthorization(UserAuthorization.Microphone))
                {
                    startVoiceRoutine = null;
                    EmitAsrError(activeRequestId, "麦克风权限被拒绝");
                    yield break;
                }
            }
#endif

            if (Microphone.devices == null || Microphone.devices.Length == 0)
            {
                startVoiceRoutine = null;
                EmitAsrError(activeRequestId, "没有找到麦克风");
                yield break;
            }

            var maxSeconds = Mathf.Clamp(recordMaxSeconds, 1, 60);
            var sampleRate = Mathf.Clamp(recordSampleRate, 8000, 48000);
            activeMicrophoneDevice = null;
            recordedSamplePosition = 0;
            recordedClip = Microphone.Start(activeMicrophoneDevice, false, maxSeconds, sampleRate);
            if (recordedClip == null)
            {
                startVoiceRoutine = null;
                EmitAsrError(activeRequestId, "麦克风启动失败");
                yield break;
            }

            recordingVoice = true;
            startVoiceRoutine = null;
            EmitAsrState(activeRequestId, "recording");
        }

        private IEnumerator RecognizeVoiceSamples(string requestId, float[] monoSamples, int sampleRate)
        {
            asrInFlight = true;
            EmitAsrState(requestId, "recognizing");

            AIGalgameLocalAsrResult result;
#if UNITY_ANDROID && !UNITY_EDITOR
            localAsrClient ??= new AIGalgameLocalAsrClient();
            localAsrClient.Configure(asrLanguage, asrThreads);
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
            localAsrClient.Configure(asrLanguage, asrThreads);
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
            result = AIGalgameLocalAsrResult.Fail("本地 ASR 仅支持 Android 真机包");
#endif

            asrInFlight = false;
            var text = (result.Text ?? "").Trim();
            if (!result.Ok || string.IsNullOrWhiteSpace(text))
            {
                EmitAsrError(requestId, result.Ok ? "没有识别出内容" : $"语音识别失败：{result.Error}");
                yield break;
            }

            bridge?.EmitAsrFinished(requestId, text);
        }

        private void EmitAsrState(string requestId, string state)
        {
            bridge?.EmitAsrState(requestId, state);
        }

        private void EmitAsrError(string requestId, string message)
        {
            bridge?.EmitAsrError(requestId, message);
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

        private void OnDestroy()
        {
            if (recordingVoice)
            {
                Microphone.End(activeMicrophoneDevice);
                recordingVoice = false;
            }

            localAsrClient?.Dispose();
            localAsrClient = null;
        }
    }
}
