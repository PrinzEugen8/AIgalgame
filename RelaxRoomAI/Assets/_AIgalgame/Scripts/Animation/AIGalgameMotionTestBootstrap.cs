using UniVRM10;
using UnityEngine;
using UnityEngine.SceneManagement;

#if UNITY_EDITOR
using UnityEditor;
#endif

namespace AIgalgame.Motion
{
    public static class AIGalgameMotionTestBootstrap
    {
        private const string DefaultVrmCharacterPath = "Assets/Character/wogua/Hayaseyuuka.vrm";
        private const string FallbackFbxCharacterPath = "Assets/Character/keyi/kei.fbx";
        private const string TestCharacterName = "AIgalgame_MotionTestCharacter";
        private static readonly Vector3 TestCharacterPosition = new(2.087f, 4.126f, -6.942f);
        private static readonly Vector3 TestCharacterEuler = new(0f, 189.595f, 0f);
        private static readonly Vector3 TestCharacterScale = new(1.4f, 1.4f, 1.4f);

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void InstallAfterSceneLoad()
        {
            SceneManager.sceneLoaded -= OnSceneLoaded;
            SceneManager.sceneLoaded += OnSceneLoaded;
            TryInstall(SceneManager.GetActiveScene());
        }

        public static void Install(Scene scene)
        {
            TryInstall(scene);
        }

        private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            TryInstall(scene);
        }

        private static void TryInstall(Scene scene)
        {
            if (!scene.IsValid())
            {
                return;
            }

            var animator = FindCharacterAnimator();
            if (animator == null)
            {
                animator = CreateEditorTestCharacter();
            }

            if (animator == null)
            {
                Debug.LogWarning($"AIgalgame RelaxRoom UI did not find a humanoid Animator in scene '{scene.name}'.");
                return;
            }

            var player = animator.GetComponent<AIGalgamePlayableMotionPlayer>();
            if (player == null)
            {
                player = animator.gameObject.AddComponent<AIGalgamePlayableMotionPlayer>();
            }

            player.SetAnimator(animator);

            var director = animator.GetComponent<AIGalgameRelaxRoomMotionDirector>();
            if (director == null)
            {
                director = animator.gameObject.AddComponent<AIGalgameRelaxRoomMotionDirector>();
            }
            director.SetTargets(animator, player);

            var controller = animator.GetComponent<AIGalgameChatdollController>();
            if (controller == null)
            {
                controller = animator.gameObject.AddComponent<AIGalgameChatdollController>();
            }

            var audioSource = animator.GetComponent<AudioSource>();
            if (audioSource == null)
            {
                audioSource = animator.gameObject.AddComponent<AudioSource>();
            }

            var vrm = animator.GetComponent<Vrm10Instance>() ?? animator.GetComponentInChildren<Vrm10Instance>() ?? animator.GetComponentInParent<Vrm10Instance>();
            controller.SetTargets(vrm, player, animator, audioSource, director);

            var touch = animator.GetComponent<AIGalgameRelaxRoomTouchController>();
            if (touch == null)
            {
                touch = animator.gameObject.AddComponent<AIGalgameRelaxRoomTouchController>();
            }
            touch.SetTargets(controller, director, animator, audioSource);

            var motionUi = Object.FindFirstObjectByType<AIGalgameMotionTestUI>();
            if (motionUi != null)
            {
                motionUi.SetPlayer(player);
                motionUi.SetStartVisible(false);
            }

            var demo = Object.FindFirstObjectByType<AIGalgameChatdollDemoUI>();
            if (demo != null)
            {
                demo.SetController(controller);
                demo.SetStartVisible(false);
            }

            var mainUi = Object.FindFirstObjectByType<AIGalgameRelaxRoomUI>();
            mainUi?.SetTargets(controller, motionUi, demo, audioSource, director, touch);
        }

        private static Animator FindCharacterAnimator()
        {
            var vrm = Object.FindFirstObjectByType<Vrm10Instance>();
            if (vrm != null && vrm.TryGetComponent<Animator>(out var vrmAnimator))
            {
                return vrmAnimator;
            }

            var animators = Object.FindObjectsByType<Animator>(FindObjectsSortMode.None);
            foreach (var candidate in animators)
            {
                if (candidate.avatar != null && candidate.avatar.isHuman)
                {
                    return candidate;
                }
            }

            return animators.Length > 0 ? animators[0] : null;
        }

        private static Animator CreateEditorTestCharacter()
        {
#if UNITY_EDITOR
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(DefaultVrmCharacterPath);
            if (prefab == null)
            {
                prefab = AssetDatabase.LoadAssetAtPath<GameObject>(FallbackFbxCharacterPath);
            }

            if (prefab == null)
            {
                return null;
            }

            var instance = Object.Instantiate(prefab);
            instance.name = TestCharacterName;
            instance.transform.SetPositionAndRotation(TestCharacterPosition, Quaternion.Euler(TestCharacterEuler));
            instance.transform.localScale = TestCharacterScale;

            return instance.GetComponent<Animator>() ?? instance.GetComponentInChildren<Animator>();
#else
            return null;
#endif
        }
    }
}
