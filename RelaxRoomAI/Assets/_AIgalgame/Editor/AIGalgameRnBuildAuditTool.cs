#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameRnBuildAuditTool
    {
        [MenuItem("Tools/AIgalgame/RN/Audit Player Assemblies")]
        public static void AuditPlayerAssemblies()
        {
            var scriptAssembliesDir = Path.Combine(Directory.GetParent(Application.dataPath)!.FullName, "Library", "ScriptAssemblies");
            if (!Directory.Exists(scriptAssembliesDir))
            {
                Debug.LogWarning("Library/ScriptAssemblies not found. Enter Play Mode or build once.");
                return;
            }

            var lines = new List<string> { "RelaxRoom RN assembly audit (Library/ScriptAssemblies):" };
            var dlls = Directory.GetFiles(scriptAssembliesDir, "*.dll")
                .Select(path => new FileInfo(path))
                .OrderByDescending(info => info.Length)
                .ToList();

            foreach (var dll in dlls)
            {
                lines.Add($"  {dll.Length / 1024f:F1} KB  {dll.Name}");
            }

            var removablePackages = new[]
            {
                "com.unity.collab-proxy (removed from manifest)",
            };
            lines.Add("");
            lines.Add("Startup stripping actions applied:");
            foreach (var item in removablePackages)
            {
                lines.Add($"  - {item}");
            }

            var report = string.Join("\n", lines);
            Debug.Log(report);

            var reportPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "relaxroom-mobile", "unity", "assembly-audit.txt"));
            Directory.CreateDirectory(Path.GetDirectoryName(reportPath)!);
            File.WriteAllText(reportPath, report, Encoding.UTF8);
            Debug.Log($"Assembly audit written to {reportPath}");
        }
    }
}
#endif
