using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using UnityEngine;

namespace AIgalgame.Motion
{
    public sealed class AIGalgameLocalAsrClient : IDisposable
        {
            private const int AndroidAsrSampleRate = 16000;
            private const int AndroidAsrFeatureDim = 80;
            private const int StandaloneAsrSampleRate = 16000;
            private const int StandaloneAsrFeatureDim = 80;
            private const string SenseVoiceModelDirectory = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17";
            private const string SenseVoiceModelFileName = "model.int8.onnx";
            private const string SenseVoiceTokensFileName = "tokens.txt";
            private const string AndroidAsrAssetsAar = "aigalgame-sherpa-asr-assets.aar";
            private readonly object sync = new();
            private string language = "zh";
            private int numThreads = 2;

#if UNITY_ANDROID && !UNITY_EDITOR
            private AndroidJavaObject offlineRecognizer;
#endif

#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
            private object standaloneRecognizer;

            [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern bool SetDllDirectory(string lpPathName);
#endif

            public void Configure(string targetLanguage, int targetThreads)
            {
                language = string.IsNullOrWhiteSpace(targetLanguage) ? "zh" : targetLanguage.Trim();
                numThreads = Math.Max(1, Math.Min(4, targetThreads));
            }

            public static bool IsStandaloneRuntimeAvailable()
            {
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
                return TryGetStandaloneManagedDllPath(out _) &&
                    TryGetStandaloneNativePluginDir(out _) &&
                    (HasSenseVoiceFiles(GetStandaloneStreamingModelDir()) ||
#if UNITY_EDITOR
                        File.Exists(GetAndroidAsrAssetsAarPath())
#else
                        false
#endif
                    );
#else
                return false;
#endif
            }

            public Task<AIGalgameLocalAsrResult> PrepareAndroidAsync()
            {
#if UNITY_ANDROID && !UNITY_EDITOR
                return Task.Run(() =>
                {
                    AndroidJNI.AttachCurrentThread();
                    try
                    {
                        lock (sync)
                        {
                            EnsureAndroidRecognizer();
                            return AIGalgameLocalAsrResult.Success("ready");
                        }
                    }
                    catch (Exception error)
                    {
                        return AIGalgameLocalAsrResult.Fail(error.Message);
                    }
                    finally
                    {
                        AndroidJNI.DetachCurrentThread();
                    }
                });
#else
                return Task.FromResult(AIGalgameLocalAsrResult.Fail("Android sherpa ASR is only available in an Android player build"));
#endif
            }

            public Task<AIGalgameLocalAsrResult> RecognizeAndroidAsync(float[] monoSamples, int sampleRate)
            {
#if UNITY_ANDROID && !UNITY_EDITOR
                var samples = monoSamples ?? Array.Empty<float>();
                return Task.Run(() =>
                {
                    AndroidJNI.AttachCurrentThread();
                    try
                    {
                        lock (sync)
                        {
                            EnsureAndroidRecognizer();
                            return RecognizeAndroidLocked(samples, sampleRate);
                        }
                    }
                    catch (Exception error)
                    {
                        return AIGalgameLocalAsrResult.Fail(error.Message);
                    }
                    finally
                    {
                        AndroidJNI.DetachCurrentThread();
                    }
                });
#else
                return Task.FromResult(AIGalgameLocalAsrResult.Fail("Android sherpa ASR is only available in an Android player build"));
#endif
            }

            public Task<AIGalgameLocalAsrResult> RecognizeStandaloneAsync(float[] monoSamples, int sampleRate)
            {
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
                var samples = monoSamples ?? Array.Empty<float>();
                string managedDllPath;
                string nativePluginDir;
                string modelDir;
                try
                {
                    managedDllPath = GetStandaloneManagedDllPath();
                    nativePluginDir = GetStandaloneNativePluginDir();
                    modelDir = PrepareStandaloneSenseVoiceModelDir();
                }
                catch (Exception error)
                {
                    return Task.FromResult(AIGalgameLocalAsrResult.Fail(error.Message));
                }

                return Task.Run(() =>
                {
                    try
                    {
                        lock (sync)
                        {
                            EnsureStandaloneRecognizer(managedDllPath, nativePluginDir, modelDir);
                            return RecognizeStandaloneLocked(samples, sampleRate);
                        }
                    }
                    catch (Exception error)
                    {
                        return AIGalgameLocalAsrResult.Fail(error.Message);
                    }
                });
#else
                return Task.FromResult(AIGalgameLocalAsrResult.Fail("Windows sherpa ASR runtime is not available on this platform"));
#endif
            }

            public void Dispose()
            {
#if UNITY_ANDROID && !UNITY_EDITOR
                lock (sync)
                {
                    if (offlineRecognizer == null)
                    {
                        return;
                    }

                    try
                    {
                        offlineRecognizer.Call("release");
                    }
                    catch (Exception)
                    {
                    }
                    offlineRecognizer.Dispose();
                    offlineRecognizer = null;
                }
#endif
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
                lock (sync)
                {
                    if (standaloneRecognizer is IDisposable disposable)
                    {
                        disposable.Dispose();
                    }

                    standaloneRecognizer = null;
                }
#endif
            }

#if UNITY_ANDROID && !UNITY_EDITOR
            private void EnsureAndroidRecognizer()
            {
                if (offlineRecognizer != null)
                {
                    return;
                }

                using var featureConfigKt = new AndroidJavaClass("com.k2fsa.sherpa.onnx.FeatureConfigKt");
                using var offlineRecognizerKt = new AndroidJavaClass("com.k2fsa.sherpa.onnx.OfflineRecognizerKt");
                using var unityPlayer = new AndroidJavaClass("com.unity3d.player.UnityPlayer");
                using var activity = unityPlayer.GetStatic<AndroidJavaObject>("currentActivity");
                using var assetManager = activity.Call<AndroidJavaObject>("getAssets");

                using var featureConfig = featureConfigKt.CallStatic<AndroidJavaObject>(
                    "getFeatureConfig",
                    AndroidAsrSampleRate,
                    AndroidAsrFeatureDim);
                using var modelConfig = offlineRecognizerKt.CallStatic<AndroidJavaObject>("getOfflineModelConfig", 15);
                if (modelConfig == null)
                {
                    throw new InvalidOperationException("Sherpa SenseVoice model config is missing");
                }

                using var senseVoice = modelConfig.Call<AndroidJavaObject>("getSenseVoice");
                senseVoice.Call("setLanguage", language);
                senseVoice.Call("setUseInverseTextNormalization", true);
                modelConfig.Call("setNumThreads", numThreads);
                modelConfig.Call("setDebug", false);

                using var recognizerConfig = new AndroidJavaObject("com.k2fsa.sherpa.onnx.OfflineRecognizerConfig");
                recognizerConfig.Call("setFeatConfig", featureConfig);
                recognizerConfig.Call("setModelConfig", modelConfig);
                offlineRecognizer = new AndroidJavaObject(
                    "com.k2fsa.sherpa.onnx.OfflineRecognizer",
                    assetManager,
                    recognizerConfig);
            }

            private AIGalgameLocalAsrResult RecognizeAndroidLocked(float[] monoSamples, int sampleRate)
            {
                if (monoSamples == null || monoSamples.Length == 0)
                {
                    return AIGalgameLocalAsrResult.Fail("No audio samples");
                }

                var samples = ResampleLinear(monoSamples, sampleRate, AndroidAsrSampleRate);
                using var stream = offlineRecognizer.Call<AndroidJavaObject>("createStream");
                try
                {
                    stream.Call("acceptWaveform", samples, AndroidAsrSampleRate);
                    offlineRecognizer.Call("decode", stream);
                    using var result = offlineRecognizer.Call<AndroidJavaObject>("getResult", stream);
                    var text = StripSherpaTags(result.Call<string>("getText"));
                    return string.IsNullOrWhiteSpace(text)
                        ? AIGalgameLocalAsrResult.Fail("Empty ASR result")
                        : AIGalgameLocalAsrResult.Success(text);
                }
                finally
                {
                    stream.Call("release");
                }
            }
#endif

#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
            private void EnsureStandaloneRecognizer(string managedDllPath, string nativePluginDir, string modelDir)
            {
                if (standaloneRecognizer != null)
                {
                    return;
                }

                SetDllDirectory(nativePluginDir);
                var assembly = LoadStandaloneAssembly(managedDllPath);
                var modelPath = Path.Combine(modelDir, SenseVoiceModelFileName);
                var tokensPath = Path.Combine(modelDir, SenseVoiceTokensFileName);
                if (!File.Exists(modelPath) || !File.Exists(tokensPath))
                {
                    throw new FileNotFoundException("SenseVoice model files are missing");
                }

                var featureConfig = CreateSherpaObject(assembly, "SherpaOnnx.FeatureConfig");
                SetSherpaField(featureConfig, "SampleRate", StandaloneAsrSampleRate);
                SetSherpaField(featureConfig, "FeatureDim", StandaloneAsrFeatureDim);

                var senseVoiceConfig = CreateSherpaObject(assembly, "SherpaOnnx.OfflineSenseVoiceModelConfig");
                SetSherpaField(senseVoiceConfig, "Model", modelPath);
                SetSherpaField(senseVoiceConfig, "Language", language);
                SetSherpaField(senseVoiceConfig, "UseInverseTextNormalization", 1);

                var modelConfig = CreateSherpaObject(assembly, "SherpaOnnx.OfflineModelConfig");
                SetSherpaField(modelConfig, "Tokens", tokensPath);
                SetSherpaField(modelConfig, "NumThreads", numThreads);
                SetSherpaField(modelConfig, "Debug", 0);
                SetSherpaField(modelConfig, "Provider", "cpu");
                SetSherpaField(modelConfig, "SenseVoice", senseVoiceConfig);

                var recognizerConfig = CreateSherpaObject(assembly, "SherpaOnnx.OfflineRecognizerConfig");
                SetSherpaField(recognizerConfig, "FeatConfig", featureConfig);
                SetSherpaField(recognizerConfig, "ModelConfig", modelConfig);

                var recognizerType = assembly.GetType("SherpaOnnx.OfflineRecognizer", true);
                standaloneRecognizer = Activator.CreateInstance(recognizerType, recognizerConfig);
            }

            private AIGalgameLocalAsrResult RecognizeStandaloneLocked(float[] monoSamples, int sampleRate)
            {
                if (monoSamples == null || monoSamples.Length == 0)
                {
                    return AIGalgameLocalAsrResult.Fail("No audio samples");
                }

                var samples = ResampleLinear(monoSamples, sampleRate, StandaloneAsrSampleRate);
                var recognizerType = standaloneRecognizer.GetType();
                var createStream = recognizerType.GetMethod("CreateStream", Type.EmptyTypes) ??
                    throw new MissingMethodException(recognizerType.FullName, "CreateStream");
                var stream = createStream.Invoke(standaloneRecognizer, Array.Empty<object>());
                try
                {
                    var streamType = stream.GetType();
                    var acceptWaveform = streamType.GetMethod("AcceptWaveform", new[] { typeof(int), typeof(float[]) }) ??
                        throw new MissingMethodException(streamType.FullName, "AcceptWaveform");
                    acceptWaveform.Invoke(stream, new object[] { StandaloneAsrSampleRate, samples });

                    var decode = recognizerType.GetMethod("Decode", new[] { streamType }) ??
                        throw new MissingMethodException(recognizerType.FullName, "Decode");
                    decode.Invoke(standaloneRecognizer, new[] { stream });

                    var result = streamType.GetProperty("Result")?.GetValue(stream);
                    var text = result?.GetType().GetProperty("Text")?.GetValue(result) as string;
                    text = StripSherpaTags(text);
                    return string.IsNullOrWhiteSpace(text)
                        ? AIGalgameLocalAsrResult.Fail("Empty ASR result")
                        : AIGalgameLocalAsrResult.Success(text);
                }
                finally
                {
                    if (stream is IDisposable disposable)
                    {
                        disposable.Dispose();
                    }
                }
            }

            private static Assembly LoadStandaloneAssembly(string managedDllPath)
            {
                foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (string.Equals(assembly.GetName().Name, "sherpa-onnx", StringComparison.OrdinalIgnoreCase))
                    {
                        return assembly;
                    }
                }

                if (!File.Exists(managedDllPath))
                {
                    throw new FileNotFoundException("sherpa-onnx.dll is missing", managedDllPath);
                }

                return Assembly.LoadFrom(managedDllPath);
            }

            private static object CreateSherpaObject(Assembly assembly, string typeName)
            {
                var type = assembly.GetType(typeName, true);
                return Activator.CreateInstance(type);
            }

            private static void SetSherpaField(object target, string fieldName, object value)
            {
                var field = target.GetType().GetField(fieldName) ??
                    throw new MissingFieldException(target.GetType().FullName, fieldName);
                field.SetValue(target, value);
            }

            private static string PrepareStandaloneSenseVoiceModelDir()
            {
                var streamingDir = GetStandaloneStreamingModelDir();
                if (HasSenseVoiceFiles(streamingDir))
                {
                    return streamingDir;
                }

#if UNITY_EDITOR
                var projectRoot = Directory.GetParent(Application.dataPath)?.FullName ?? Application.dataPath;
                var cacheDir = Path.Combine(projectRoot, "Library", "SherpaOnnx", SenseVoiceModelDirectory);
                if (!HasSenseVoiceFiles(cacheDir))
                {
                    ExtractSenseVoiceFromAar(cacheDir);
                }

                if (HasSenseVoiceFiles(cacheDir))
                {
                    return cacheDir;
                }
#endif

                throw new FileNotFoundException("SenseVoice model files are missing for Windows local ASR");
            }

            private static void ExtractSenseVoiceFromAar(string outputDir)
            {
                var aarPath = GetAndroidAsrAssetsAarPath();
                if (!File.Exists(aarPath))
                {
                    throw new FileNotFoundException("Bundled SenseVoice AAR is missing", aarPath);
                }

                Directory.CreateDirectory(outputDir);
                using var archive = ZipFile.OpenRead(aarPath);
                ExtractAarEntry(
                    archive,
                    $"assets/{SenseVoiceModelDirectory}/{SenseVoiceModelFileName}",
                    Path.Combine(outputDir, SenseVoiceModelFileName));
                ExtractAarEntry(
                    archive,
                    $"assets/{SenseVoiceModelDirectory}/{SenseVoiceTokensFileName}",
                    Path.Combine(outputDir, SenseVoiceTokensFileName));
            }

            private static void ExtractAarEntry(ZipArchive archive, string entryName, string destination)
            {
                var entry = archive.GetEntry(entryName) ??
                    throw new FileNotFoundException($"AAR entry is missing: {entryName}");
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                entry.ExtractToFile(destination, true);
            }

            private static string GetStandaloneManagedDllPath()
            {
                return TryGetStandaloneManagedDllPath(out var path)
                    ? path
                    : throw new FileNotFoundException("sherpa-onnx.dll is missing from Assets/Plugins");
            }

            private static bool TryGetStandaloneManagedDllPath(out string path)
            {
                return TryFindExistingFile(
                    out path,
                    Path.Combine(Application.dataPath, "Plugins", "sherpa-onnx.dll"),
                    Path.Combine(Application.dataPath, "Managed", "sherpa-onnx.dll"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "sherpa-onnx.dll"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Managed", "sherpa-onnx.dll"));
            }

            private static string GetStandaloneNativePluginDir()
            {
                return TryGetStandaloneNativePluginDir(out var path)
                    ? path
                    : throw new FileNotFoundException("sherpa-onnx Windows native DLLs are missing from Assets/Plugins/x86_64");
            }

            private static bool TryGetStandaloneNativePluginDir(out string path)
            {
                var candidates = new[]
                {
                    Path.Combine(Application.dataPath, "Plugins", "x86_64"),
                    Path.Combine(Application.dataPath, "Plugins"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Plugins", "x86_64"),
                    Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Plugins"),
                };

                foreach (var candidate in candidates)
                {
                    if (File.Exists(Path.Combine(candidate, "sherpa-onnx-c-api.dll")) &&
                        File.Exists(Path.Combine(candidate, "onnxruntime.dll")))
                    {
                        path = candidate;
                        return true;
                    }
                }

                path = "";
                return false;
            }

            private static string GetStandaloneStreamingModelDir()
            {
                return Path.Combine(Application.streamingAssetsPath, "SherpaOnnx", SenseVoiceModelDirectory);
            }

            private static string GetAndroidAsrAssetsAarPath()
            {
                return Path.Combine(Application.dataPath, "Plugins", "Android", AndroidAsrAssetsAar);
            }

            private static bool HasSenseVoiceFiles(string directory)
            {
                return !string.IsNullOrWhiteSpace(directory) &&
                    File.Exists(Path.Combine(directory, SenseVoiceModelFileName)) &&
                    File.Exists(Path.Combine(directory, SenseVoiceTokensFileName));
            }

            private static bool TryFindExistingFile(out string path, params string[] candidates)
            {
                foreach (var candidate in candidates)
                {
                    if (File.Exists(candidate))
                    {
                        path = candidate;
                        return true;
                    }
                }

                path = "";
                return false;
            }
#endif

            private static float[] ResampleLinear(float[] source, int sourceRate, int targetRate)
            {
                if (source == null || source.Length == 0)
                {
                    return Array.Empty<float>();
                }

                if (sourceRate <= 0 || sourceRate == targetRate)
                {
                    return source;
                }

                var targetLength = Math.Max(1, (int)Math.Round(source.Length * (double)targetRate / sourceRate));
                if (targetLength == source.Length)
                {
                    return source;
                }

                var target = new float[targetLength];
                if (targetLength == 1)
                {
                    target[0] = source[0];
                    return target;
                }

                var scale = (source.Length - 1d) / (targetLength - 1d);
                for (var i = 0; i < target.Length; i++)
                {
                    var position = i * scale;
                    var left = (int)Math.Floor(position);
                    var right = Math.Min(left + 1, source.Length - 1);
                    var t = (float)(position - left);
                    target[i] = source[left] + ((source[right] - source[left]) * t);
                }

                return target;
            }

            private static string StripSherpaTags(string text)
            {
                if (string.IsNullOrWhiteSpace(text))
                {
                    return "";
                }

                var value = text.Trim();
                while (true)
                {
                    var start = value.IndexOf("<|", StringComparison.Ordinal);
                    if (start < 0)
                    {
                        break;
                    }

                    var end = value.IndexOf("|>", start, StringComparison.Ordinal);
                    if (end < 0)
                    {
                        break;
                    }

                    value = value.Remove(start, end - start + 2).Trim();
                }

                return value;
            }
        }
}
