using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameAnimationClipReverseTool
    {
        private const string MenuPath = "AIgalgame/Animation/Create Reversed Clip From Selection";

        [MenuItem(MenuPath, true)]
        private static bool ValidateCreateReversedClip()
        {
            return GetSelectedAnimationClips().Count > 0;
        }

        [MenuItem(MenuPath)]
        private static void CreateReversedClip()
        {
            var clips = GetSelectedAnimationClips();
            if (clips.Count == 0)
            {
                EditorUtility.DisplayDialog("Reverse Animation Clip", "Select one or more AnimationClip assets first.", "OK");
                return;
            }

            var created = new List<UnityEngine.Object>();
            foreach (var source in clips)
            {
                var outputPath = GenerateOutputPath(source);
                var reversed = BuildReversedClip(source);
                AssetDatabase.CreateAsset(reversed, outputPath);
                created.Add(reversed);
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Selection.objects = created.ToArray();
            Debug.Log($"Created {created.Count} reversed animation clip(s).");
        }

        private static AnimationClip BuildReversedClip(AnimationClip source)
        {
            var length = Mathf.Max(0f, source.length);
            var clip = new AnimationClip
            {
                name = $"{source.name}_Reverse",
                frameRate = source.frameRate,
                legacy = source.legacy,
                wrapMode = source.wrapMode
            };

            var settings = AnimationUtility.GetAnimationClipSettings(source);
            AnimationUtility.SetAnimationClipSettings(clip, settings);

            foreach (var binding in AnimationUtility.GetCurveBindings(source))
            {
                var curve = AnimationUtility.GetEditorCurve(source, binding);
                if (curve == null)
                {
                    continue;
                }

                AnimationUtility.SetEditorCurve(clip, binding, ReverseCurve(curve, length));
            }

            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(source))
            {
                var keys = AnimationUtility.GetObjectReferenceCurve(source, binding);
                if (keys == null)
                {
                    continue;
                }

                AnimationUtility.SetObjectReferenceCurve(clip, binding, ReverseObjectReferenceCurve(keys, length));
            }

            AnimationUtility.SetAnimationEvents(clip, ReverseEvents(AnimationUtility.GetAnimationEvents(source), length));
            EditorUtility.SetDirty(clip);
            return clip;
        }

        private static AnimationCurve ReverseCurve(AnimationCurve source, float length)
        {
            var reversedKeys = source.keys
                .Select(key => ReverseKey(key, length))
                .OrderBy(key => key.time)
                .ToArray();

            var curve = new AnimationCurve(reversedKeys)
            {
                preWrapMode = source.postWrapMode,
                postWrapMode = source.preWrapMode
            };
            return curve;
        }

        private static Keyframe ReverseKey(Keyframe source, float length)
        {
            var key = new Keyframe(Mathf.Max(0f, length - source.time), source.value)
            {
                inTangent = -source.outTangent,
                outTangent = -source.inTangent,
                inWeight = source.outWeight,
                outWeight = source.inWeight,
                weightedMode = SwapWeightedMode(source.weightedMode)
            };
            return key;
        }

        private static WeightedMode SwapWeightedMode(WeightedMode mode)
        {
            return mode switch
            {
                WeightedMode.In => WeightedMode.Out,
                WeightedMode.Out => WeightedMode.In,
                _ => mode
            };
        }

        private static ObjectReferenceKeyframe[] ReverseObjectReferenceCurve(ObjectReferenceKeyframe[] keys, float length)
        {
            return keys
                .Select(key => new ObjectReferenceKeyframe
                {
                    time = Mathf.Max(0f, length - key.time),
                    value = key.value
                })
                .OrderBy(key => key.time)
                .ToArray();
        }

        private static AnimationEvent[] ReverseEvents(AnimationEvent[] events, float length)
        {
            return (events ?? Array.Empty<AnimationEvent>())
                .Select(source => new AnimationEvent
                {
                    time = Mathf.Max(0f, length - source.time),
                    functionName = ReverseEventFunctionName(source.functionName),
                    stringParameter = source.stringParameter,
                    floatParameter = source.floatParameter,
                    intParameter = source.intParameter,
                    objectReferenceParameter = source.objectReferenceParameter,
                    messageOptions = source.messageOptions
                })
                .OrderBy(evt => evt.time)
                .ToArray();
        }

        private static string ReverseEventFunctionName(string functionName)
        {
            return functionName switch
            {
                "OnPhoneGrab" => "OnPhoneRelease",
                "OnPhoneRelease" => "OnPhoneGrab",
                _ => functionName
            };
        }

        private static string GenerateOutputPath(AnimationClip source)
        {
            var sourcePath = AssetDatabase.GetAssetPath(source);
            var folder = string.IsNullOrWhiteSpace(sourcePath)
                ? "Assets"
                : Path.GetDirectoryName(sourcePath)?.Replace('\\', '/') ?? "Assets";
            var fileName = SanitizeFileName($"{source.name}_Reverse.anim");
            return AssetDatabase.GenerateUniqueAssetPath($"{folder}/{fileName}");
        }

        private static string SanitizeFileName(string value)
        {
            var invalid = Path.GetInvalidFileNameChars();
            return new string(value.Select(ch => invalid.Contains(ch) ? '_' : ch).ToArray());
        }

        private static List<AnimationClip> GetSelectedAnimationClips()
        {
            var clips = new List<AnimationClip>();
            foreach (var selected in Selection.objects ?? Array.Empty<UnityEngine.Object>())
            {
                if (selected is AnimationClip clip && IsUsableClip(clip))
                {
                    clips.Add(clip);
                    continue;
                }

                var path = AssetDatabase.GetAssetPath(selected);
                if (string.IsNullOrWhiteSpace(path))
                {
                    continue;
                }

                clips.AddRange(AssetDatabase.LoadAllAssetRepresentationsAtPath(path)
                    .OfType<AnimationClip>()
                    .Where(IsUsableClip));
            }

            return clips.Distinct().ToList();
        }

        private static bool IsUsableClip(AnimationClip clip)
        {
            return clip != null &&
                !string.IsNullOrWhiteSpace(clip.name) &&
                !clip.name.StartsWith("__preview__", StringComparison.OrdinalIgnoreCase);
        }
    }
}
