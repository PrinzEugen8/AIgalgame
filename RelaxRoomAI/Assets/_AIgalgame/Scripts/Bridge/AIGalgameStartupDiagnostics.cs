using System.Collections.Generic;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AIgalgame.Motion
{
    public static class AIGalgameStartupDiagnostics
    {
        private const int SplashHeartbeatIntervalMs = 2000;
        private static float _processStartSeconds = -1f;
        private static volatile bool _beforeSceneLoadReached;
        private static Thread _splashHeartbeatThread;
        private static readonly List<(string stage, int elapsedMs)> Stages = new();

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        private static void MarkSubsystemRegistration()
        {
            EnsureProcessStart();
            Log("subsystem_registration");
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterAssembliesLoaded)]
        private static void MarkAfterAssembliesLoaded()
        {
            EnsureProcessStart();
            Log("after_assemblies_loaded");
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSplashScreen)]
        private static void MarkBeforeSplashScreen()
        {
            EnsureProcessStart();
            Log("before_splash_screen");
            StartSplashHeartbeat();
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void MarkProcessStart()
        {
            EnsureProcessStart();
            StopSplashHeartbeat();
            SceneManager.sceneLoaded += OnSceneLoaded;
            Log("process_start");
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void MarkAfterSceneLoad()
        {
            EnsureProcessStart();
            var scene = SceneManager.GetActiveScene();
            Log("after_scene_load", $"scene={scene.name} loaded={scene.isLoaded}");
        }

        private static void EnsureProcessStart()
        {
            if (_processStartSeconds >= 0f)
            {
                return;
            }

            _processStartSeconds = Time.realtimeSinceStartup;
        }

        private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            var rootCount = scene.isLoaded ? scene.rootCount : 0;
            Log("scene_loaded", $"scene={scene.name} roots={rootCount}");
        }

        public static void Log(string stage)
        {
            Log(stage, null);
        }

        public static void Log(string stage, string detail)
        {
            EnsureProcessStart();

            var elapsedMs = Mathf.RoundToInt(ElapsedSeconds * 1000f);
            Stages.Add((stage, elapsedMs));

            var suffix = string.IsNullOrEmpty(detail) ? string.Empty : $" detail={detail}";
            Debug.Log(
                $"[RelaxRoomStartup] layer=unity stage={stage} elapsed_ms={elapsedMs} elapsed_s={ElapsedSeconds:F3}{suffix}");

            if (ShouldForwardMilestone(stage))
            {
                ForwardMilestone(stage, elapsedMs, detail);
            }
        }

        private static bool ShouldForwardMilestone(string stage)
        {
            return stage is "subsystem_registration"
                or "after_assemblies_loaded"
                or "before_splash_screen"
                or "splash_loading"
                or "process_start"
                or "after_scene_load"
                or "scene_loaded"
                or "first_update"
                or "asr_prepare_begin"
                or "asr_ready"
                or "asr_prepare_failed";
        }

        private static void StartSplashHeartbeat()
        {
            StopSplashHeartbeat();
            _beforeSceneLoadReached = false;

            _splashHeartbeatThread = new Thread(() =>
            {
                var tick = 0;
                while (!_beforeSceneLoadReached)
                {
                    Thread.Sleep(SplashHeartbeatIntervalMs);
                    if (_beforeSceneLoadReached)
                    {
                        break;
                    }

                    tick += 1;
                    Log("splash_loading", $"tick={tick}");
                }
            })
            {
                IsBackground = true,
                Name = "RelaxRoomSplashHeartbeat",
            };
            _splashHeartbeatThread.Start();
        }

        private static void StopSplashHeartbeat()
        {
            _beforeSceneLoadReached = true;
            _splashHeartbeatThread = null;
        }

        private static void ForwardMilestone(string stage, int elapsedMs, string detail)
        {
            var builder = new StringBuilder();
            builder.Append("{\"evt\":\"startup_milestone\",\"source\":\"unity\",\"stage\":\"");
            builder.Append(EscapeJson(stage));
            builder.Append("\",\"elapsed_ms\":");
            builder.Append(elapsedMs);
            if (!string.IsNullOrEmpty(detail))
            {
                builder.Append(",\"detail\":\"");
                builder.Append(EscapeJson(detail));
                builder.Append('"');
            }

            builder.Append('}');
            ReactNativeMessenger.Send(builder.ToString());
        }

        public static float ElapsedSeconds =>
            _processStartSeconds >= 0f
                ? Time.realtimeSinceStartup - _processStartSeconds
                : Time.realtimeSinceStartup;

        public static void SendTimelineSummary()
        {
            if (Stages.Count == 0)
            {
                return;
            }

            var builder = new StringBuilder();
            builder.Append("{\"evt\":\"startup_timeline\",\"source\":\"unity\",\"stages\":[");
            for (var i = 0; i < Stages.Count; i++)
            {
                var entry = Stages[i];
                if (i > 0)
                {
                    builder.Append(',');
                }

                builder.Append("{\"stage\":\"");
                builder.Append(EscapeJson(entry.stage));
                builder.Append("\",\"elapsed_ms\":");
                builder.Append(entry.elapsedMs);
                builder.Append('}');
            }

            builder.Append("]}");
            ReactNativeMessenger.Send(builder.ToString());
        }

        private static string EscapeJson(string value)
        {
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"");
        }
    }
}
