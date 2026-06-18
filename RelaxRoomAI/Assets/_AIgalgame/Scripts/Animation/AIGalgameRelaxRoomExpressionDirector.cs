using System;
using System.Collections.Generic;
using System.Linq;
using UniVRM10;
using UnityEngine;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(140)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomExpressionDirector : MonoBehaviour
    {
        [Header("Targets")]
        [SerializeField] private Vrm10Instance vrmInstance;

        [Header("Blending")]
        [SerializeField] private float expressionInSpeed = 3.2f;
        [SerializeField] private float expressionOutSpeed = 2.2f;
        [SerializeField] private float expressionSmoothSeconds = 0.26f;
        [SerializeField, Range(0f, 1f)] private float speakingIntensity = 0.82f;
        [SerializeField, Range(0f, 1f)] private float idleIntensity = 0.36f;

        [Header("Idle micro expression")]
        [SerializeField] private Vector2 idleMicroIntervalRange = new(4.5f, 10f);
        [SerializeField] private Vector2 idleMicroHoldRange = new(1.2f, 3.2f);
        [SerializeField, Range(0f, 1f)] private float idleMicroWeight = 0.22f;

        private readonly HashSet<ExpressionKey> controlledKeys = new(ExpressionKey.Comparer);
        private readonly Dictionary<ExpressionKey, float> currentWeights = new(ExpressionKey.Comparer);
        private readonly Dictionary<ExpressionKey, float> targetWeights = new(ExpressionKey.Comparer);
        private readonly Dictionary<ExpressionKey, float> weightVelocities = new(ExpressionKey.Comparer);

        private AIGalgameAvatarState avatarState = AIGalgameAvatarState.Idle;
        private string currentEmotion = "neutral";
        private float currentIntensity = 0.42f;
        private string microEmotion = "";
        private float microWeightOverride = -1f;
        private float microUntil;
        private float nextMicroAt;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
            ScheduleNextIdleMicro();
        }

        private void LateUpdate()
        {
            ResolveReferences();
            if (vrmInstance == null || vrmInstance.Runtime?.Expression == null)
            {
                return;
            }

            UpdateIdleMicroExpression();
            BuildTargetWeights();
            ApplyWeights(vrmInstance.Runtime.Expression);
        }

        public void SetTargets(Vrm10Instance targetVrm)
        {
            if (targetVrm != null)
            {
                vrmInstance = targetVrm;
            }
        }

        public void SetAvatarState(AIGalgameAvatarState nextState)
        {
            avatarState = nextState;
            if (avatarState == AIGalgameAvatarState.Idle)
            {
                SetEmotion("neutral", idleIntensity);
            }
        }

        public void SetEmotion(string emotion, float intensity = 1f, float holdSeconds = 0f)
        {
            currentEmotion = FirstNonEmpty(emotion, "neutral");
            currentIntensity = Mathf.Clamp01(intensity <= 0f ? 1f : intensity);
            if (holdSeconds > 0f)
            {
                microEmotion = "";
                microUntil = Time.time + holdSeconds;
            }
        }

        public void AddTransient(string emotion, float intensity = 0.45f, float seconds = 1.2f)
        {
            microEmotion = FirstNonEmpty(emotion, "");
            microWeightOverride = Mathf.Clamp01(intensity <= 0f ? idleMicroWeight : intensity);
            microUntil = Time.time + Mathf.Clamp(seconds, 0.2f, 8f);
        }

        private void UpdateIdleMicroExpression()
        {
            if (avatarState != AIGalgameAvatarState.Idle)
            {
                return;
            }

            if (!string.IsNullOrWhiteSpace(microEmotion) && Time.time < microUntil)
            {
                return;
            }

            if (!string.IsNullOrWhiteSpace(microEmotion))
            {
                microEmotion = "";
                microWeightOverride = -1f;
                ScheduleNextIdleMicro();
                return;
            }

            if (Time.time < nextMicroAt)
            {
                return;
            }

            var options = new[] { "relaxed", "thinking", "happy", "shy" };
            microEmotion = options[UnityEngine.Random.Range(0, options.Length)];
            microUntil = Time.time + RandomRange(idleMicroHoldRange, 1.2f, 3.2f);
        }

        private void BuildTargetWeights()
        {
            targetWeights.Clear();
            foreach (var key in controlledKeys)
            {
                targetWeights[key] = 0f;
            }

            var stateIntensity = avatarState == AIGalgameAvatarState.Idle
                ? Mathf.Min(currentIntensity, idleIntensity)
                : Mathf.Min(currentIntensity, speakingIntensity);
            AddEmotionProfile(currentEmotion, stateIntensity);

            if (!string.IsNullOrWhiteSpace(microEmotion) && Time.time < microUntil)
            {
                AddEmotionProfile(microEmotion, microWeightOverride > 0f ? microWeightOverride : idleMicroWeight);
            }
        }

        private void ApplyWeights(Vrm10RuntimeExpression expression)
        {
            foreach (var key in targetWeights.Keys.ToArray())
            {
                var current = currentWeights.TryGetValue(key, out var value) ? value : 0f;
                var target = Mathf.Clamp01(targetWeights[key]);
                var speed = target > current ? expressionInSpeed : expressionOutSpeed;
                var velocity = weightVelocities.TryGetValue(key, out var storedVelocity) ? storedVelocity : 0f;
                var smoothSeconds = Mathf.Max(0.04f, expressionSmoothSeconds / Mathf.Max(0.1f, speed * 0.32f));
                var next = Mathf.SmoothDamp(current, target, ref velocity, smoothSeconds, Mathf.Infinity, Time.deltaTime);
                if (Mathf.Abs(next - target) < 0.001f && Mathf.Abs(velocity) < 0.01f)
                {
                    next = target;
                    velocity = 0f;
                }

                weightVelocities[key] = velocity;
                currentWeights[key] = next;
                expression.SetWeight(key, next);
            }
        }

        private void AddEmotionProfile(string emotion, float intensity)
        {
            var normalized = NormalizeToken(emotion);
            intensity = Mathf.Clamp01(intensity);

            switch (normalized)
            {
                case "":
                case "idle":
                case "calm":
                case "neutral":
                    AddFirstAvailable(intensity * 0.34f, ExpressionKey.Relaxed, ExpressionKey.Neutral, "Calm", "Relaxed", "Neutral");
                    break;
                case "relaxed":
                    AddFirstAvailable(intensity * 0.52f, ExpressionKey.Relaxed, "Relaxed", "Calm");
                    break;
                case "happy":
                case "joy":
                case "smile":
                    AddFirstAvailable(intensity * 0.78f, ExpressionKey.Happy, "Joy", "Fun", "Happy");
                    AddFirstAvailable(intensity * 0.16f, ExpressionKey.Relaxed, "Relaxed");
                    break;
                case "fun":
                    AddFirstAvailable(intensity * 0.72f, "Fun", ExpressionKey.Happy, "Joy");
                    break;
                case "shy":
                    AddFirstAvailable(intensity * 0.76f, "Shy", "Fun", ExpressionKey.Happy);
                    AddFirstAvailable(intensity * 0.18f, ExpressionKey.Relaxed, "Relaxed");
                    break;
                case "thinking":
                case "think":
                case "ponder":
                    AddFirstAvailable(intensity * 0.64f, "Thinking", "Think", ExpressionKey.Relaxed, ExpressionKey.Neutral);
                    break;
                case "sad":
                case "sorrow":
                    AddFirstAvailable(intensity * 0.72f, ExpressionKey.Sad, "Sorrow", "Sad");
                    break;
                case "angry":
                case "disapproval":
                    AddFirstAvailable(intensity * 0.68f, ExpressionKey.Angry, "Angry", "Disapproval");
                    break;
                case "surprised":
                case "surprise":
                    AddFirstAvailable(intensity * 0.82f, ExpressionKey.Surprised, "Surprised", "Surprise");
                    break;
                default:
                    AddFirstAvailable(intensity * 0.72f, emotion);
                    break;
            }
        }

        private void AddFirstAvailable(float weight, params object[] candidates)
        {
            foreach (var candidate in candidates)
            {
                if (TryResolveKey(candidate, out var key))
                {
                    AddWeight(key, weight);
                    return;
                }
            }
        }

        private void AddWeight(ExpressionKey key, float weight)
        {
            if (key.IsProcedual)
            {
                return;
            }

            controlledKeys.Add(key);
            targetWeights[key] = targetWeights.TryGetValue(key, out var value)
                ? Mathf.Max(value, weight)
                : weight;
        }

        private bool TryResolveKey(object candidate, out ExpressionKey key)
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

            key = default;
            return false;
        }

        private bool HasExpressionKey(ExpressionKey expected)
        {
            if (vrmInstance == null || vrmInstance.Runtime?.Expression == null)
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
            if (vrmInstance == null || vrmInstance.Runtime?.Expression == null || string.IsNullOrWhiteSpace(name))
            {
                return false;
            }

            var normalized = NormalizeToken(name);
            foreach (var candidate in vrmInstance.Runtime.Expression.ExpressionKeys)
            {
                if (candidate.IsProcedual)
                {
                    continue;
                }

                if (string.Equals(candidate.Name, name, StringComparison.OrdinalIgnoreCase) ||
                    NormalizeToken(candidate.Name) == normalized)
                {
                    key = candidate;
                    return true;
                }
            }

            return false;
        }

        private void ResolveReferences()
        {
            if (vrmInstance == null)
            {
                vrmInstance = GetComponent<Vrm10Instance>() ?? GetComponentInChildren<Vrm10Instance>() ?? GetComponentInParent<Vrm10Instance>();
            }
        }

        private void ScheduleNextIdleMicro()
        {
            nextMicroAt = Time.time + RandomRange(idleMicroIntervalRange, 4.5f, 10f);
        }

        private static float RandomRange(Vector2 range, float fallbackMin, float fallbackMax)
        {
            var min = range.x > 0f ? range.x : fallbackMin;
            var max = range.y >= min ? range.y : fallbackMax;
            return UnityEngine.Random.Range(min, max);
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
    }
}
