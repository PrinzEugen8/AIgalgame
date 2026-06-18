using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AIgalgame.Motion
{
    public static class AIGalgameStartupDiagnostics
    {
        private static float _processStartSeconds = -1f;
        private static readonly List<(string stage, int elapsedMs)> Stages = new();

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void MarkProcessStart()
        {
            _processStartSeconds = Time.realtimeSinceStartup;
            SceneManager.sceneLoaded += OnSceneLoaded;
            Log("process_start");
        }

        private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            Log($"scene_loaded scene={scene.name}");
        }

        public static void Log(string stage)
        {
            Log(stage, null);
        }

        public static void Log(string stage, string detail)
        {
            var elapsedMs = Mathf.RoundToInt(ElapsedSeconds * 1000f);
            Stages.Add((stage, elapsedMs));

            var suffix = string.IsNullOrEmpty(detail) ? string.Empty : $" detail={detail}";
            Debug.Log(
                $"[RelaxRoomStartup] layer=unity stage={stage} elapsed_ms={elapsedMs} elapsed_s={ElapsedSeconds:F3}{suffix}");
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
