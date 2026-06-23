using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

namespace AIgalgame.Motion
{
    [DefaultExecutionOrder(-65)]
    [DisallowMultipleComponent]
    public sealed class AIGalgameRelaxRoomTouchController : MonoBehaviour
    {
        [Header("Backend")]
        [SerializeField] private string backendBaseUrl = "http://127.0.0.1:8899";
        [SerializeField] private string userId = "demo_user";
        [SerializeField] private string characterId = "atri";
        [SerializeField] private string appearanceId = "neko";

        [Header("Targets")]
        [SerializeField] private Animator animator;
        [SerializeField] private AIGalgameChatdollController controller;
        [SerializeField] private AIGalgameRelaxRoomMotionDirector motionDirector;
        [SerializeField] private AudioSource audioSource;
        [SerializeField] private Camera raycastCamera;

        [Header("Touch")]
        [SerializeField] private float raycastDistance = 120f;
        [SerializeField] private float fallbackCooldownSeconds = 1.6f;
        [SerializeField] private bool drawTouchDebugGizmos;

        private bool requestInFlight;
        private float headCooldownUntil;
        private float chestCooldownUntil;
        private float handCooldownUntil;
        private float bodyCooldownUntil;
        private Vector3 lastTouchWorldPoint;
        private bool hasLastTouchWorldPoint;

        public event Action<string> TouchStarted;
        public event Action<string, string, string, bool> TouchFinished;

        private void Reset()
        {
            ResolveReferences();
        }

        private void Awake()
        {
            ResolveReferences();
            EnsureTouchColliders();
        }

        private void Update()
        {
            if (requestInFlight || !CanAcceptTouch())
            {
                return;
            }

            if (Input.GetMouseButtonDown(0))
            {
                if (!IsPointerOverUi())
                {
                    TryHandleScreenTouch(Input.mousePosition, -1);
                }
                return;
            }

            if (Input.touchCount <= 0)
            {
                return;
            }

            var touch = Input.GetTouch(0);
            if (touch.phase == TouchPhase.Began && !IsPointerOverUi(touch.fingerId))
            {
                TryHandleScreenTouch(touch.position, touch.fingerId);
            }
        }

        public void SetTargets(
            AIGalgameChatdollController targetController,
            AIGalgameRelaxRoomMotionDirector targetMotionDirector,
            Animator targetAnimator,
            AudioSource targetAudioSource)
        {
            controller = targetController;
            motionDirector = targetMotionDirector;
            animator = targetAnimator;
            audioSource = targetAudioSource != null ? targetAudioSource : audioSource;
            ResolveReferences();
            EnsureTouchColliders();
        }

        public void ConfigureBackend(string baseUrl, string user, string character, string appearance = "neko")
        {
            if (!string.IsNullOrWhiteSpace(baseUrl))
            {
                backendBaseUrl = baseUrl.Trim();
            }

            if (!string.IsNullOrWhiteSpace(user))
            {
                userId = user.Trim();
            }

            if (!string.IsNullOrWhiteSpace(character))
            {
                characterId = character.Trim();
            }

            if (!string.IsNullOrWhiteSpace(appearance))
            {
                appearanceId = appearance.Trim();
            }
        }

        public void SimulateTouch(string hitArea)
        {
            if (requestInFlight || !CanAcceptTouch())
            {
                return;
            }

            HandleTouchArea(hitArea, Vector3.zero);
        }

        private bool CanAcceptTouch()
        {
            ResolveReferences();
            if (controller != null && controller.State != AIGalgameAvatarState.Idle)
            {
                return false;
            }

            return motionDirector == null || motionDirector.CanAcceptTouch;
        }

        private void TryHandleScreenTouch(Vector2 screenPosition, int pointerId)
        {
            ResolveReferences();
            var camera = raycastCamera != null ? raycastCamera : Camera.main;
            if (camera == null)
            {
                return;
            }

            var ray = camera.ScreenPointToRay(screenPosition);
            if (!TryFindTouchAreaFromRay(ray, out var hitArea, out var hitPoint) &&
                !TryFindProjectedTouchArea(screenPosition, camera, out hitArea, out hitPoint))
            {
                return;
            }

            HandleTouchArea(hitArea, hitPoint);
        }

        private bool TryFindTouchAreaFromRay(Ray ray, out string hitArea, out Vector3 hitPoint)
        {
            hitArea = "";
            hitPoint = Vector3.zero;
            Physics.SyncTransforms();
            var hits = Physics.RaycastAll(ray, raycastDistance, Physics.DefaultRaycastLayers, QueryTriggerInteraction.Collide);
            var nearest = float.MaxValue;
            for (var i = 0; i < hits.Length; i++)
            {
                var hit = hits[i];
                var area = hit.collider != null ? hit.collider.GetComponentInParent<AIGalgameRelaxRoomTouchArea>() : null;
                if (area == null || string.IsNullOrWhiteSpace(area.HitArea) || hit.distance >= nearest)
                {
                    continue;
                }

                nearest = hit.distance;
                hitArea = area.HitArea.Trim().ToLowerInvariant();
                hitPoint = hit.point;
            }

            return !string.IsNullOrWhiteSpace(hitArea);
        }

        private bool TryFindProjectedTouchArea(Vector2 screenPosition, Camera camera, out string hitArea, out Vector3 hitPoint)
        {
            hitArea = "";
            hitPoint = Vector3.zero;
            ResolveReferences();
            if (animator == null || !animator.isHuman || camera == null)
            {
                return false;
            }

            var nearest = float.MaxValue;
            ConsiderProjectedBone(HumanBodyBones.Head, "head", 120f, screenPosition, camera, ref nearest, ref hitArea, ref hitPoint);
            ConsiderProjectedBone(ResolveChestBone(), "chest", 170f, screenPosition, camera, ref nearest, ref hitArea, ref hitPoint);
            ConsiderProjectedBone(HumanBodyBones.LeftHand, "hand", 115f, screenPosition, camera, ref nearest, ref hitArea, ref hitPoint);
            ConsiderProjectedBone(HumanBodyBones.RightHand, "hand", 115f, screenPosition, camera, ref nearest, ref hitArea, ref hitPoint);
            return !string.IsNullOrWhiteSpace(hitArea);
        }

        private void ConsiderProjectedBone(
            HumanBodyBones bone,
            string area,
            float radiusPixels,
            Vector2 screenPosition,
            Camera camera,
            ref float nearest,
            ref string hitArea,
            ref Vector3 hitPoint)
        {
            var target = animator.GetBoneTransform(bone);
            if (target == null)
            {
                return;
            }

            var projected = camera.WorldToScreenPoint(target.position);
            if (projected.z <= 0f)
            {
                return;
            }

            var distance = Vector2.Distance(screenPosition, new Vector2(projected.x, projected.y));
            if (distance > radiusPixels || distance >= nearest)
            {
                return;
            }

            nearest = distance;
            hitArea = area;
            hitPoint = target.position;
        }

        private void HandleTouchArea(string hitArea, Vector3 hitPoint)
        {
            hitArea = string.IsNullOrWhiteSpace(hitArea) ? "body" : hitArea.Trim().ToLowerInvariant();
            if (IsCoolingDown(hitArea))
            {
                return;
            }

            SetCooldown(hitArea, fallbackCooldownSeconds);
            lastTouchWorldPoint = hitPoint;
            hasLastTouchWorldPoint = hitPoint != Vector3.zero;
            if (hasLastTouchWorldPoint)
            {
                controller?.SetTouchFocus(lastTouchWorldPoint, 1.8f);
            }

            TouchStarted?.Invoke(hitArea);
            StartCoroutine(PostTouch(hitArea));
        }

        private IEnumerator PostTouch(string hitArea)
        {
            requestInFlight = true;
            motionDirector?.PlayTouch(hitArea);

            var url = CombineUrl(backendBaseUrl, "/api/live2d/touch");
            var body =
                "{" +
                $"\"user_id\":\"{EscapeJson(userId)}\"," +
                $"\"character_id\":\"{EscapeJson(characterId)}\"," +
                $"\"appearance_id\":\"{EscapeJson(appearanceId)}\"," +
                $"\"hit_area\":\"{EscapeJson(hitArea)}\"" +
                "}";

            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.SetRequestHeader("Accept", "application/json");
            yield return request.SendWebRequest();

            TouchReactionResponse response = null;
            if (request.result == UnityWebRequest.Result.Success)
            {
                response = JsonUtility.FromJson<TouchReactionResponse>(request.downloadHandler.text);
            }

            if (response == null || !response.ok)
            {
                response = BuildFallbackResponse(hitArea);
                StartCoroutine(QueueTouchPoolRefresh(hitArea));
            }

            var cooldownSeconds = Mathf.Max(fallbackCooldownSeconds, response.cooldown_ms / 1000f);
            SetCooldown(hitArea, cooldownSeconds);
            yield return PlayResponse(response);
            requestInFlight = false;
            TouchFinished?.Invoke(
                hitArea,
                response?.text ?? "",
                response?.line_id ?? "",
                response != null && response.ok);
        }

        private IEnumerator QueueTouchPoolRefresh(string hitArea)
        {
            var url = CombineUrl(backendBaseUrl, "/api/live2d/touch/refresh");
            var body =
                "{" +
                $"\"user_id\":\"{EscapeJson(userId)}\"," +
                $"\"character_id\":\"{EscapeJson(characterId)}\"," +
                $"\"appearance_id\":\"{EscapeJson(appearanceId)}\"," +
                $"\"hit_area\":\"{EscapeJson(hitArea)}\"" +
                "}";
            using var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            yield return request.SendWebRequest();
        }

        private IEnumerator PlayResponse(TouchReactionResponse response)
        {
            if (response == null)
            {
                yield break;
            }

            var motion = NormalizeTouchMotion(response.hit_area, response.motion);
            motionDirector?.PlayTouch(response.hit_area, motion);

            AudioClip clip = null;
            if (!string.IsNullOrWhiteSpace(response.tts_audio_url))
            {
                yield return DownloadAudio(response.tts_audio_url, value => clip = value);
            }

            var audioStarted = false;
            if (clip != null && audioSource != null)
            {
                audioSource.Stop();
                audioSource.clip = clip;
                audioSource.Play();
                audioStarted = true;
            }

            if (controller != null && !string.IsNullOrWhiteSpace(response.text))
            {
                controller.PlayLine(new AIGalgameDialogueLine
                {
                    line_id = response.line_id,
                    text = response.text,
                    emotion = FirstNonEmpty(response.emotion, "shy"),
                    expression = FirstNonEmpty(response.expression, response.emotion, "shy"),
                    motion = motion,
                    pose = motion,
                    tts_audio_url = audioStarted ? "" : response.tts_audio_url,
                    controller = new AIGalgameDialogueControllerCommand
                    {
                        state = "speaking",
                        face = FirstNonEmpty(response.expression, response.emotion, "shy"),
                        animation = motion,
                        mouth = "auto",
                        lipsync = true
                    }
                });
            }
        }

        private IEnumerator DownloadAudio(string url, Action<AudioClip> onComplete)
        {
            var fullUrl = CombineUrl(backendBaseUrl, url);
            using var request = UnityWebRequestMultimedia.GetAudioClip(fullUrl, AudioTypeForUrl(fullUrl));
            yield return request.SendWebRequest();
            if (request.result != UnityWebRequest.Result.Success)
            {
                onComplete?.Invoke(null);
                yield break;
            }

            onComplete?.Invoke(DownloadHandlerAudioClip.GetContent(request));
        }

        private void EnsureTouchColliders()
        {
            ResolveReferences();
            if (animator == null || !animator.isHuman)
            {
                return;
            }

            EnsureSphere(HumanBodyBones.Head, "head", new Vector3(0f, 0.045f, 0.015f), 0.17f);
            EnsureBox(ResolveChestBone(), "chest", new Vector3(0f, 0.02f, 0.035f), new Vector3(0.40f, 0.30f, 0.22f));
            EnsureSphere(HumanBodyBones.LeftHand, "hand", Vector3.zero, 0.11f);
            EnsureSphere(HumanBodyBones.RightHand, "hand", Vector3.zero, 0.11f);
        }

        private HumanBodyBones ResolveChestBone()
        {
            return animator.GetBoneTransform(HumanBodyBones.UpperChest) != null ? HumanBodyBones.UpperChest : HumanBodyBones.Chest;
        }

        private void EnsureSphere(HumanBodyBones bone, string hitArea, Vector3 center, float radius)
        {
            var target = animator.GetBoneTransform(bone);
            if (target == null)
            {
                return;
            }

            var area = target.GetComponent<AIGalgameRelaxRoomTouchArea>();
            if (area == null)
            {
                Debug.LogError(
                    $"Missing AIGalgameRelaxRoomTouchArea on '{target.name}'. Add it in the scene so the hit area is editable in the Inspector.",
                    this);
                return;
            }

            area.HitArea = hitArea;
            var collider = target.GetComponent<SphereCollider>();
            if (collider == null)
            {
                collider = target.gameObject.AddComponent<SphereCollider>();
            }

            collider.isTrigger = true;
            collider.center = center;
            collider.radius = radius;
        }

        private void EnsureBox(HumanBodyBones bone, string hitArea, Vector3 center, Vector3 size)
        {
            var target = animator.GetBoneTransform(bone);
            if (target == null)
            {
                return;
            }

            var area = target.GetComponent<AIGalgameRelaxRoomTouchArea>();
            if (area == null)
            {
                Debug.LogError(
                    $"Missing AIGalgameRelaxRoomTouchArea on '{target.name}'. Add it in the scene so the hit area is editable in the Inspector.",
                    this);
                return;
            }

            area.HitArea = hitArea;
            var collider = target.GetComponent<BoxCollider>();
            if (collider == null)
            {
                collider = target.gameObject.AddComponent<BoxCollider>();
            }

            collider.isTrigger = true;
            collider.center = center;
            collider.size = size;
        }

#if UNITY_EDITOR
        private void OnDrawGizmosSelected()
        {
            if (!drawTouchDebugGizmos)
            {
                return;
            }

            ResolveReferences();
            if (animator == null || !animator.isHuman)
            {
                return;
            }

            DrawBoneSphereGizmo(HumanBodyBones.Head, Color.yellow);
            DrawBoneBoxGizmo(ResolveChestBone(), new Vector3(0f, 0.02f, 0.035f), new Vector3(0.40f, 0.30f, 0.22f), Color.cyan);
            DrawBoneSphereGizmo(HumanBodyBones.LeftHand, Color.green);
            DrawBoneSphereGizmo(HumanBodyBones.RightHand, Color.green);
        }

        private void DrawBoneSphereGizmo(HumanBodyBones bone, Color color)
        {
            var target = animator.GetBoneTransform(bone);
            if (target == null)
            {
                return;
            }

            var collider = target.GetComponent<SphereCollider>();
            if (collider == null)
            {
                return;
            }

            Gizmos.color = color;
            Gizmos.matrix = target.localToWorldMatrix;
            Gizmos.DrawWireSphere(collider.center, collider.radius);
        }

        private void DrawBoneBoxGizmo(HumanBodyBones bone, Vector3 center, Vector3 size, Color color)
        {
            var target = animator.GetBoneTransform(bone);
            if (target == null)
            {
                return;
            }

            Gizmos.color = color;
            Gizmos.matrix = target.localToWorldMatrix;
            Gizmos.DrawWireCube(center, size);
        }
#endif

        private bool IsCoolingDown(string hitArea)
        {
            var now = Time.time;
            return hitArea switch
            {
                "head" => now < headCooldownUntil,
                "chest" => now < chestCooldownUntil,
                "hand" => now < handCooldownUntil,
                _ => now < bodyCooldownUntil
            };
        }

        private void SetCooldown(string hitArea, float seconds)
        {
            var until = Time.time + Mathf.Max(0.2f, seconds);
            switch (hitArea)
            {
                case "head":
                    headCooldownUntil = until;
                    break;
                case "chest":
                    chestCooldownUntil = until;
                    break;
                case "hand":
                    handCooldownUntil = until;
                    break;
                default:
                    bodyCooldownUntil = until;
                    break;
            }
        }

        private void ResolveReferences()
        {
            if (animator == null)
            {
                animator = GetComponent<Animator>() ?? GetComponentInChildren<Animator>() ?? GetComponentInParent<Animator>();
            }

            if (controller == null)
            {
                controller = GetComponent<AIGalgameChatdollController>() ?? GetComponentInChildren<AIGalgameChatdollController>() ?? GetComponentInParent<AIGalgameChatdollController>();
            }

            if (motionDirector == null)
            {
                motionDirector = GetComponent<AIGalgameRelaxRoomMotionDirector>() ?? GetComponentInChildren<AIGalgameRelaxRoomMotionDirector>() ?? GetComponentInParent<AIGalgameRelaxRoomMotionDirector>();
            }

            if (audioSource == null)
            {
                audioSource = GetComponent<AudioSource>() ?? GetComponentInChildren<AudioSource>() ?? GetComponentInParent<AudioSource>();
            }

            if (raycastCamera == null)
            {
                raycastCamera = Camera.main;
                if (raycastCamera == null)
                {
                    raycastCamera = FindFirstObjectByType<Camera>();
                }
            }
        }

        private static TouchReactionResponse BuildFallbackResponse(string hitArea)
        {
            return new TouchReactionResponse
            {
                ok = true,
                hit_area = hitArea,
                line_id = $"local_{hitArea}",
                text = hitArea switch
                {
                    "head" => "诶？突然摸头会有点害羞啦。",
                    "chest" => "这里不可以乱碰，知道吗？",
                    "hand" => "手的话，可以轻一点牵。",
                    _ => "嗯？怎么突然碰我。"
                },
                emotion = hitArea == "chest" ? "shy" : "happy",
                expression = hitArea == "chest" ? "shy" : "happy",
                motion = hitArea switch
                {
                    "head" => "TapHead",
                    "chest" => "TapChest",
                    "hand" => "TapHand",
                    _ => "TapBody"
                },
                cooldown_ms = 1600
            };
        }

        private static string NormalizeTouchMotion(string hitArea, string backendMotion)
        {
            var value = (backendMotion ?? "").Trim();
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }

            return hitArea switch
            {
                "head" => "TapHead",
                "chest" => "TapChest",
                "hand" => "TapHand",
                _ => "TapBody"
            };
        }

        private static bool IsPointerOverUi(int pointerId = -1)
        {
            return false;
        }

        private static AudioType AudioTypeForUrl(string url)
        {
            var lower = (url ?? "").ToLowerInvariant();
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

        private static string CombineUrl(string baseUrl, string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return baseUrl;
            }

            if (path.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || path.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                return path;
            }

            var trimmedBase = (baseUrl ?? "").TrimEnd('/');
            var trimmedPath = path.StartsWith("/") ? path : "/" + path;
            return trimmedBase + trimmedPath;
        }

        private static string EscapeJson(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "";
            }

            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\b", "\\b")
                .Replace("\f", "\\f")
                .Replace("\n", "\\n")
                .Replace("\r", "\\r")
                .Replace("\t", "\\t");
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

        [Serializable]
        private sealed class TouchReactionResponse
        {
            public bool ok;
            public string hit_area = "";
            public string tier = "";
            public string line_id = "";
            public string content_hash = "";
            public string text = "";
            public string emotion = "calm";
            public string expression = "calm";
            public string motion = "";
            public string tts_audio_url = "";
            public int tts_duration_ms;
            public int cooldown_ms = 1600;
        }
    }
}
