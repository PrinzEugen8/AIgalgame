using System;
using System.IO;
using System.Linq;
using AIgalgame.Motion;
using UniVRM10;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AIGalgameMotionSetupTool
{
    private const string SittingFbxPath = "Assets/motion/FBX/X Bot@Sitting Talking.fbx";
    private const string FbxMotionFolder = "Assets/motion";
    private const string Vrma01Path = "Assets/motion/VRMA/VRMA_01.vrma";
    private const string Scene01Path = "Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity";
    private const string DefaultVrmCharacterPath = "Assets/Character/wogua/Hayaseyuuka.vrm";
    private const string FallbackFbxCharacterPath = "Assets/Character/keyi/kei.fbx";
    private const string SittingRoot = "Assets/motion/\u5750\u7740";
    private const string ReplyFolder = SittingRoot + "/\u56de\u590d";
    private const string IdleActionFolder = SittingRoot + "/\u95f2\u65f6\u52a8\u753b";
    private const string TestCharacterName = "AIgalgame_MotionTestCharacter";
    private const string BgmPath = "Assets/audio/bgm/\u591c\u95f4\u966a\u4f34.mp3";
    private const string PhoneNotificationPath = "Assets/audio/sfx/\u624b\u673a\u63d0\u9192\u97f3\u6548.wav";
    private const string PhoneTypingAudioFolder = "Assets/audio/sfx/typephone";
    private const string KeyboardTypingAudioFolder = "Assets/audio/sfx/keyboard";
    private const string AudioConfigPath = "Assets/_AIgalgame/Resources/RelaxRoom/RelaxRoomAudioConfig.asset";
    private const string PhoneTypingEvent = "PlayRelaxRoomPhoneTypingSfx";
    private const string KeyboardTypingEvent = "PlayRelaxRoomKeyboardTypingSfx";
    private static readonly Vector3 TestCharacterPosition = new(2.087f, 4.126f, -6.942f);
    private static readonly Vector3 TestCharacterEuler = new(0f, 189.595f, 0f);
    private static readonly Vector3 TestCharacterScale = new(1.4f, 1.4f, 1.4f);

    [MenuItem("Tools/AIgalgame/Motion/Setup Scene_01 Motion Test UI")]
    public static void SetupScene01MotionTestUi()
    {
        PrepareAllFbxMotions();
        SetupScene01RelaxRoomCharacterComponents();

        var scene = EditorSceneManager.OpenScene(Scene01Path, OpenSceneMode.Single);
        var animator = FindSceneCharacterAnimator();
        if (animator == null)
        {
            animator = CreateSceneTestCharacter();
        }

        if (animator == null)
        {
            Debug.LogError("Could not find or create a humanoid Animator in Scene_01.");
            return;
        }

        var player = animator.GetComponent<AIGalgamePlayableMotionPlayer>();
        if (player == null)
        {
            player = Undo.AddComponent<AIGalgamePlayableMotionPlayer>(animator.gameObject);
        }

        var playerSo = new SerializedObject(player);
        playerSo.FindProperty("animator").objectReferenceValue = animator;
        playerSo.ApplyModifiedProperties();
        player.RefreshClipCatalog();
        EditorUtility.SetDirty(player);

        var uiRoot = GameObject.Find("AIgalgame_MotionTestUI");
        if (uiRoot == null)
        {
            uiRoot = new GameObject("AIgalgame_MotionTestUI");
            Undo.RegisterCreatedObjectUndo(uiRoot, "Create Motion Test UI");
        }

        var ui = uiRoot.GetComponent<AIGalgameMotionTestUI>();
        if (ui == null)
        {
            ui = Undo.AddComponent<AIGalgameMotionTestUI>(uiRoot);
        }

        var uiSo = new SerializedObject(ui);
        uiSo.FindProperty("player").objectReferenceValue = player;
        uiSo.ApplyModifiedProperties();
        EditorUtility.SetDirty(ui);

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Selection.activeGameObject = uiRoot;
        Debug.Log("Scene_01 motion test UI is ready. Enter Play Mode in Scene_01 to test FBX motion switching.");
    }

    [MenuItem("Tools/AIgalgame/RelaxRoom/Setup Scene_01 RelaxRoom UI")]
    public static void SetupScene01RelaxRoomUi()
    {
        var scene = EditorSceneManager.OpenScene(Scene01Path, OpenSceneMode.Single);
        AIGalgameMotionTestBootstrap.Install(scene);
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("Scene_01 RelaxRoom UI components are installed. Enter Play Mode in Scene_01 to see the chat canvas.");
    }

    [MenuItem("Tools/AIgalgame/RelaxRoom/Setup Scene_01 Character Components")]
    public static void SetupScene01RelaxRoomCharacterComponents()
    {
        PrepareAllFbxMotions();

        var scene = EditorSceneManager.OpenScene(Scene01Path, OpenSceneMode.Single);
        var animator = FindSceneCharacterAnimator();
        if (animator == null)
        {
            animator = CreateSceneTestCharacter();
        }

        if (animator == null)
        {
            Debug.LogError("Could not find or create a humanoid Animator in Scene_01.");
            return;
        }

        var character = animator.gameObject;
        var player = EnsureComponent<AIGalgamePlayableMotionPlayer>(character, "Add RelaxRoom Motion Player");
        var phone = EnsureComponent<AIGalgameRelaxRoomPhoneAttachmentController>(character, "Add RelaxRoom Phone Attachment");
        var phonePickup = EnsureComponent<AIGalgameRelaxRoomPhonePickupController>(character, "Add RelaxRoom Phone Pickup");
        var director = EnsureComponent<AIGalgameRelaxRoomMotionDirector>(character, "Add RelaxRoom Motion Director");
        var expressionDirector = EnsureComponent<AIGalgameRelaxRoomExpressionDirector>(character, "Add RelaxRoom Expression Director");
        var gazeDirector = EnsureComponent<AIGalgameRelaxRoomGazeDirector>(character, "Add RelaxRoom Gaze Director");
        var controller = EnsureComponent<AIGalgameChatdollController>(character, "Add RelaxRoom Chatdoll Controller");
        var audioSource = EnsureComponent<AudioSource>(character, "Add RelaxRoom Audio Source");
        var touch = EnsureComponent<AIGalgameRelaxRoomTouchController>(character, "Add RelaxRoom Touch Controller");
        var vrm = character.GetComponent<Vrm10Instance>() ?? character.GetComponentInChildren<Vrm10Instance>();

        SetObjectReference(player, "animator", animator);
        player.SetAnimator(animator);
        player.RefreshClipCatalog();

        SetObjectReference(phone, "animator", animator);
        phone.SetAnimator(animator);

        SetObjectReference(phonePickup, "animator", animator);
        SetObjectReference(phonePickup, "motionPlayer", player);
        SetObjectReference(phonePickup, "phoneAttachment", phone);
        phonePickup.SetTargets(animator, player, phone);

        SetObjectReference(director, "animator", animator);
        SetObjectReference(director, "motionPlayer", player);
        SetObjectReference(director, "phoneAttachment", phone);
        SetObjectReference(director, "phonePickup", phonePickup);
        SetObjectReference(director, "gazeDirector", gazeDirector);
        SetObjectReference(director, "expressionDirector", expressionDirector);
        director.SetTargets(animator, player);
        director.SetVisualDirectors(gazeDirector, expressionDirector);

        SetObjectReference(expressionDirector, "vrmInstance", vrm);
        expressionDirector.SetTargets(vrm);

        SetObjectReference(gazeDirector, "vrmInstance", vrm);
        SetObjectReference(gazeDirector, "animator", animator);
        SetObjectReference(gazeDirector, "motionDirector", director);
        SetObjectReference(gazeDirector, "phoneAttachment", phone);
        gazeDirector.SetTargets(vrm, animator, director);

        SetObjectReference(controller, "vrmInstance", vrm);
        SetObjectReference(controller, "motionPlayer", player);
        SetObjectReference(controller, "relaxRoomMotion", director);
        SetObjectReference(controller, "expressionDirector", expressionDirector);
        SetObjectReference(controller, "gazeDirector", gazeDirector);
        SetObjectReference(controller, "animator", animator);
        SetObjectReference(controller, "audioSource", audioSource);
        controller.SetTargets(vrm, player, animator, audioSource, director);
        controller.SetVisualDirectors(expressionDirector, gazeDirector);

        SetObjectReference(touch, "animator", animator);
        SetObjectReference(touch, "controller", controller);
        SetObjectReference(touch, "motionDirector", director);
        SetObjectReference(touch, "audioSource", audioSource);
        touch.SetTargets(controller, director, animator, audioSource);

        ConfigureSceneAudio(character, director, phone);

        EditorUtility.SetDirty(player);
        EditorUtility.SetDirty(phone);
        EditorUtility.SetDirty(phonePickup);
        EditorUtility.SetDirty(director);
        EditorUtility.SetDirty(expressionDirector);
        EditorUtility.SetDirty(gazeDirector);
        EditorUtility.SetDirty(controller);
        EditorUtility.SetDirty(audioSource);
        EditorUtility.SetDirty(touch);
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Selection.activeGameObject = character;
        Debug.Log($"Scene_01 RelaxRoom character components are installed on {character.name}.");
    }

    [MenuItem("Tools/AIgalgame/RelaxRoom/Setup Scene_01 Audio")]
    public static void SetupScene01RelaxRoomAudio()
    {
        // Keep this menu audio-only: do not rebuild or assign the character animator controller here.
        var scene = EditorSceneManager.OpenScene(Scene01Path, OpenSceneMode.Single);
        var animator = FindSceneCharacterAnimator();
        if (animator == null)
        {
            Debug.LogError("Could not find a humanoid Animator in Scene_01.");
            return;
        }

        var character = animator.gameObject;
        var director = EnsureComponent<AIGalgameRelaxRoomMotionDirector>(character, "Add RelaxRoom Motion Director");
        var phone = EnsureComponent<AIGalgameRelaxRoomPhoneAttachmentController>(character, "Add RelaxRoom Phone Attachment");
        SetObjectReference(director, "animator", animator);
        SetObjectReference(phone, "animator", animator);
        ConfigureSceneAudio(character, director, phone);

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Selection.activeGameObject = character;
        Debug.Log("Scene_01 RelaxRoom audio is configured.");
    }

    [MenuItem("Tools/AIgalgame/RelaxRoom/Clear Auto Audio Animation Events")]
    public static void ClearRelaxRoomAutoAudioAnimationEvents()
    {
        ClearRelaxRoomAudioAnimationEvents();
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log("Cleared generated RelaxRoom audio animation events. Manual events with other function names are untouched.");
    }

    [MenuItem("Tools/AIgalgame/Motion/Prepare All FBX Motions")]
    public static void PrepareAllFbxMotions()
    {
        var fbxPaths = AssetDatabase.FindAssets("t:Model", new[] { FbxMotionFolder })
            .Select(AssetDatabase.GUIDToAssetPath)
            .Where(path => path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase))
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        foreach (var path in fbxPaths)
        {
            ConfigureHumanoidFbxImporter(path, loopTime: true);
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log($"Prepared {fbxPaths.Length} FBX motion asset(s) under {FbxMotionFolder}.");
    }

    [MenuItem("Tools/AIgalgame/Motion/Apply VRMA_01 To Selection")]
    public static void ApplyVrma01ToSelection()
    {
        var vrmaPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(Vrma01Path);
        if (vrmaPrefab == null)
        {
            Debug.LogError($"Could not load VRMA prefab at {Vrma01Path}");
            return;
        }

        foreach (var selected in Selection.gameObjects)
        {
            var player = EnsureMotionPlayer(selected);
            var so = new SerializedObject(player);
            so.FindProperty("initialVrmaPrefab").objectReferenceValue = vrmaPrefab;
            so.FindProperty("playInitialVrmaOnStart").boolValue = true;
            so.FindProperty("playInitialAnimatorStateOnStart").boolValue = false;
            so.ApplyModifiedProperties();

            EditorUtility.SetDirty(player);
            Debug.Log($"Assigned {Vrma01Path} to {selected.name}. Enter Play Mode to preview it.");
        }
    }

    public static void BatchSetupScene01Audio()
    {
        SetupScene01RelaxRoomAudio();
    }

    private static AIGalgameMotionPlayer EnsureMotionPlayer(GameObject selected)
    {
        var player = selected.GetComponent<AIGalgameMotionPlayer>();
        if (player == null)
        {
            player = Undo.AddComponent<AIGalgameMotionPlayer>(selected);
        }

        return player;
    }

    private static T EnsureComponent<T>(GameObject selected, string undoName) where T : Component
    {
        var component = selected.GetComponent<T>();
        if (component == null)
        {
            component = Undo.AddComponent<T>(selected);
        }

        return component;
    }

    private static void SetObjectReference(UnityEngine.Object target, string propertyName, UnityEngine.Object value)
    {
        if (target == null)
        {
            return;
        }

        var serialized = new SerializedObject(target);
        var property = serialized.FindProperty(propertyName);
        if (property == null)
        {
            return;
        }

        property.objectReferenceValue = value;
        serialized.ApplyModifiedProperties();
    }

    private static void ConfigureSceneAudio(
        GameObject character,
        AIGalgameRelaxRoomMotionDirector director,
        AIGalgameRelaxRoomPhoneAttachmentController phoneAttachment)
    {
        if (character == null || director == null)
        {
            return;
        }

        var bgmClip = AssetDatabase.LoadAssetAtPath<AudioClip>(BgmPath);
        var phoneNotificationClip = AssetDatabase.LoadAssetAtPath<AudioClip>(PhoneNotificationPath);
        var phoneTypingClips = LoadAudioClips(PhoneTypingAudioFolder);
        var keyboardTypingClips = LoadAudioClips(KeyboardTypingAudioFolder);
        var audioConfig = AssetDatabase.LoadAssetAtPath<AIGalgameRelaxRoomAudioConfig>(AudioConfigPath);

        var bgmSource = EnsureChildAudioSource(character.transform, "RelaxRoom BGM AudioSource");
        ConfigureAudioSource(bgmSource, spatialBlend: 0f, volume: 0.32f, loop: true);

        var phoneTarget = FindSceneTransformByName("RelaxRoom Phone Prop", "\u624b\u673a", "Phone");
        if (phoneTarget != null && phoneAttachment != null)
        {
            phoneAttachment.enabled = true;
            SetObjectReference(phoneAttachment, "phoneInstance", phoneTarget);
        }

        var phoneSource = phoneTarget != null ? EnsureComponent<AudioSource>(phoneTarget.gameObject, "Add Phone Audio Source") : null;
        ConfigureAudioSource(phoneSource, spatialBlend: 1f, volume: 0.85f, loop: false);

        var keyboardTarget = FindSceneTransformByName("KeyBoard_Apt_01", "Keyboard", "\u952e\u76d8");
        var keyboardSource = keyboardTarget != null ? EnsureComponent<AudioSource>(keyboardTarget.gameObject, "Add Keyboard Audio Source") : null;
        ConfigureAudioSource(keyboardSource, spatialBlend: 1f, volume: 0.85f, loop: false);

        var serializedDirector = new SerializedObject(director);
        SetSerializedReference(serializedDirector, "audioConfig", audioConfig);
        SetSerializedReference(serializedDirector, "bgmClip", bgmClip);
        SetSerializedReference(serializedDirector, "phoneNotificationClip", phoneNotificationClip);
        SetSerializedReference(serializedDirector, "phoneAudioTarget", phoneTarget);
        SetSerializedReference(serializedDirector, "keyboardAudioTarget", keyboardTarget);
        SetSerializedReference(serializedDirector, "bgmAudioSource", bgmSource);
        SetSerializedReference(serializedDirector, "phoneAudioSource", phoneSource);
        SetSerializedReference(serializedDirector, "keyboardAudioSource", keyboardSource);
        SetSerializedAudioClipArray(serializedDirector, "phoneTypingClips", phoneTypingClips);
        SetSerializedAudioClipArray(serializedDirector, "keyboardTypingClips", keyboardTypingClips);
        SetSerializedBool(serializedDirector, "playBgmOnStart", true);
        SetSerializedFloat(serializedDirector, "bgmVolume", 0.32f);
        SetSerializedFloat(serializedDirector, "sfxVolume", 0.85f);
        serializedDirector.ApplyModifiedProperties();

        EditorUtility.SetDirty(director);
        if (phoneAttachment != null)
        {
            EditorUtility.SetDirty(phoneAttachment);
        }
        if (bgmSource != null)
        {
            EditorUtility.SetDirty(bgmSource);
        }
        if (phoneSource != null)
        {
            EditorUtility.SetDirty(phoneSource);
        }
        if (keyboardSource != null)
        {
            EditorUtility.SetDirty(keyboardSource);
        }
    }

    private static void ClearRelaxRoomAudioAnimationEvents()
    {
        var replyTextingPath = ReplyFolder + "/X Bot@Texting.fbx";
        var idleTextingPath = IdleActionFolder + "/X Bot@Texting.fbx";
        var walkingTextingPath = ReplyFolder + "/X Bot@Walking While Texting.fbx";
        var tabletPath = IdleActionFolder + "/X Bot@Standing Using Touchscreen Tablet.fbx";
        var keyboardTypingPath = IdleActionFolder + "/X Bot@Typing.fbx";

        ImportAssetIfPresent(replyTextingPath);
        ImportAssetIfPresent(idleTextingPath);
        ImportAssetIfPresent(walkingTextingPath);
        ImportAssetIfPresent(tabletPath);
        ImportAssetIfPresent(keyboardTypingPath);

        StripAudioEvents(replyTextingPath);
        StripAudioEvents(idleTextingPath);
        StripAudioEvents(walkingTextingPath);
        StripAudioEvents(tabletPath);
        StripAudioEvents(keyboardTypingPath);
    }

    private static void ImportAssetIfPresent(string assetPath)
    {
        if (AssetDatabase.LoadMainAssetAtPath(assetPath) != null)
        {
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
        }
    }

    private static void StripAudioEvents(string assetPath)
    {
        var clips = AssetDatabase.LoadAllAssetsAtPath(assetPath)
            .OfType<AnimationClip>()
            .Where(clip => clip != null && !clip.name.StartsWith("__preview__", StringComparison.OrdinalIgnoreCase))
            .ToArray();

        foreach (var clip in clips)
        {
            var currentEvents = AnimationUtility.GetAnimationEvents(clip);
            var filteredEvents = currentEvents
                .Where(animationEvent => !IsRelaxRoomAudioEvent(animationEvent))
                .ToArray();
            if (filteredEvents.Length == currentEvents.Length)
            {
                continue;
            }

            AnimationUtility.SetAnimationEvents(clip, filteredEvents);
            EditorUtility.SetDirty(clip);
        }
    }

    private static bool IsRelaxRoomAudioEvent(AnimationEvent animationEvent)
    {
        return animationEvent != null &&
            (string.Equals(animationEvent.functionName, PhoneTypingEvent, StringComparison.Ordinal) ||
                string.Equals(animationEvent.functionName, KeyboardTypingEvent, StringComparison.Ordinal));
    }

    private static AudioClip[] LoadAudioClips(string folder)
    {
        return AssetDatabase.FindAssets("t:AudioClip", new[] { folder })
            .Select(AssetDatabase.GUIDToAssetPath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .Select(AssetDatabase.LoadAssetAtPath<AudioClip>)
            .Where(clip => clip != null)
            .ToArray();
    }

    private static AudioSource EnsureChildAudioSource(Transform parent, string name)
    {
        var child = parent.Find(name);
        if (child == null)
        {
            var childObject = new GameObject(name);
            Undo.RegisterCreatedObjectUndo(childObject, "Create RelaxRoom Audio Source");
            child = childObject.transform;
            child.SetParent(parent, false);
        }

        return EnsureComponent<AudioSource>(child.gameObject, "Add RelaxRoom Audio Source");
    }

    private static void ConfigureAudioSource(AudioSource source, float spatialBlend, float volume, bool loop)
    {
        if (source == null)
        {
            return;
        }

        Undo.RecordObject(source, "Configure RelaxRoom Audio Source");
        source.playOnAwake = false;
        source.loop = loop;
        source.spatialBlend = spatialBlend;
        source.volume = volume;
        source.dopplerLevel = 0f;
        source.minDistance = spatialBlend > 0f ? 0.2f : 1f;
        source.maxDistance = spatialBlend > 0f ? 8f : 500f;
        source.rolloffMode = AudioRolloffMode.Logarithmic;
    }

    private static Transform FindSceneTransformByName(params string[] names)
    {
        var transforms = UnityEngine.Object.FindObjectsByType<Transform>(FindObjectsInactive.Include, FindObjectsSortMode.None);
        foreach (var name in names)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }

            foreach (var transform in transforms)
            {
                if (transform != null &&
                    transform.gameObject.scene.IsValid() &&
                    string.Equals(transform.name, name, StringComparison.OrdinalIgnoreCase))
                {
                    return transform;
                }
            }
        }

        foreach (var name in names)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }

            foreach (var transform in transforms)
            {
                if (transform != null &&
                    transform.gameObject.scene.IsValid() &&
                    transform.name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return transform;
                }
            }
        }

        return null;
    }

    private static void SetSerializedReference(SerializedObject serialized, string propertyName, UnityEngine.Object value)
    {
        var property = serialized.FindProperty(propertyName);
        if (property != null)
        {
            property.objectReferenceValue = value;
        }
    }

    private static void SetSerializedAudioClipArray(SerializedObject serialized, string propertyName, AudioClip[] clips)
    {
        var property = serialized.FindProperty(propertyName);
        if (property == null)
        {
            return;
        }

        property.arraySize = clips?.Length ?? 0;
        for (var i = 0; i < property.arraySize; i++)
        {
            property.GetArrayElementAtIndex(i).objectReferenceValue = clips[i];
        }
    }

    private static void SetSerializedBool(SerializedObject serialized, string propertyName, bool value)
    {
        var property = serialized.FindProperty(propertyName);
        if (property != null)
        {
            property.boolValue = value;
        }
    }

    private static void SetSerializedFloat(SerializedObject serialized, string propertyName, float value)
    {
        var property = serialized.FindProperty(propertyName);
        if (property != null)
        {
            property.floatValue = value;
        }
    }

    private static Animator FindSceneCharacterAnimator()
    {
        var vrm = UnityEngine.Object.FindFirstObjectByType<Vrm10Instance>();
        if (vrm != null && vrm.TryGetComponent<Animator>(out var vrmAnimator))
        {
            return vrmAnimator;
        }

        var animators = UnityEngine.Object.FindObjectsByType<Animator>(FindObjectsSortMode.None);
        foreach (var candidate in animators)
        {
            if (candidate.avatar != null && candidate.avatar.isHuman)
            {
                return candidate;
            }
        }

        return animators.Length > 0 ? animators[0] : null;
    }

    private static Animator CreateSceneTestCharacter()
    {
        var prefab = LoadDefaultCharacterPrefab(out var characterPath);
        if (prefab == null)
        {
            Debug.LogWarning($"No test character found at {DefaultVrmCharacterPath} or {FallbackFbxCharacterPath}.");
            return null;
        }

        var instance = PrefabUtility.InstantiatePrefab(prefab) as GameObject;
        if (instance == null)
        {
            instance = UnityEngine.Object.Instantiate(prefab);
        }

        Undo.RegisterCreatedObjectUndo(instance, "Create AIgalgame Motion Test Character");
        instance.name = TestCharacterName;
        instance.transform.SetPositionAndRotation(TestCharacterPosition, Quaternion.Euler(TestCharacterEuler));
        instance.transform.localScale = TestCharacterScale;

        var animator = instance.GetComponent<Animator>() ?? instance.GetComponentInChildren<Animator>();
        if (animator == null)
        {
            animator = Undo.AddComponent<Animator>(instance);
        }

        Debug.Log($"Created {TestCharacterName} from {characterPath} for Scene_01 motion testing.");
        return animator;
    }

    private static GameObject LoadDefaultCharacterPrefab(out string characterPath)
    {
        characterPath = DefaultVrmCharacterPath;
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(DefaultVrmCharacterPath);
        if (prefab != null)
        {
            return prefab;
        }

        characterPath = FallbackFbxCharacterPath;
        return AssetDatabase.LoadAssetAtPath<GameObject>(FallbackFbxCharacterPath);
    }

    private static void ConfigureHumanoidFbxImporter(string assetPath, bool loopTime)
    {
        var importer = AssetImporter.GetAtPath(assetPath) as ModelImporter;
        if (importer == null)
        {
            Debug.LogError($"Could not find a ModelImporter at {assetPath}");
            return;
        }

        var changed = false;
        if (importer.animationType != ModelImporterAnimationType.Human)
        {
            importer.animationType = ModelImporterAnimationType.Human;
            changed = true;
        }

        if (importer.avatarSetup != ModelImporterAvatarSetup.CreateFromThisModel)
        {
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            changed = true;
        }

        if (!importer.importAnimation)
        {
            importer.importAnimation = true;
            changed = true;
        }

        var clips = importer.clipAnimations;
        if (clips == null || clips.Length == 0)
        {
            clips = importer.defaultClipAnimations;
        }

        foreach (var clip in clips)
        {
            if (clip.loopTime != loopTime)
            {
                clip.loopTime = loopTime;
                changed = true;
            }

            if (clip.loopPose != loopTime)
            {
                clip.loopPose = loopTime;
                changed = true;
            }
        }

        if (clips != null && clips.Length > 0)
        {
            importer.clipAnimations = clips;
        }

        if (changed)
        {
            importer.SaveAndReimport();
        }
        else
        {
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
        }
    }

    private static AnimationClip FindAnimationClip(string assetPath)
    {
        var clips = AssetDatabase.LoadAllAssetRepresentationsAtPath(assetPath)
            .Concat(AssetDatabase.LoadAllAssetsAtPath(assetPath))
            .OfType<AnimationClip>()
            .Where(clip => clip != null)
            .Where(clip => !clip.name.StartsWith("__preview__", StringComparison.OrdinalIgnoreCase))
            .Distinct()
            .ToArray();

        return clips
            .OrderByDescending(clip => clip.name.IndexOf("Sitting", StringComparison.OrdinalIgnoreCase) >= 0)
            .ThenByDescending(clip => clip.length)
            .FirstOrDefault();
    }

    private static void EnsureFolder(string assetFolder)
    {
        if (AssetDatabase.IsValidFolder(assetFolder))
        {
            return;
        }

        var parent = Path.GetDirectoryName(assetFolder)?.Replace("\\", "/");
        var name = Path.GetFileName(assetFolder);
        if (string.IsNullOrEmpty(parent) || string.IsNullOrEmpty(name))
        {
            return;
        }

        EnsureFolder(parent);
        AssetDatabase.CreateFolder(parent, name);
    }
}
