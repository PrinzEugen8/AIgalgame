using UnityEditor;

public sealed class AIGalgameMotionFbxPostprocessor : AssetPostprocessor
{
    private const string MotionFolder = "Assets/motion/";
    private const float TextingPickupEnd = 0.28f;
    private const float TextingLoopEnd = 0.78f;

    private void OnPreprocessModel()
    {
        if (!assetPath.StartsWith(MotionFolder) || !assetPath.ToLowerInvariant().EndsWith(".fbx"))
        {
            return;
        }

        var importer = assetImporter as ModelImporter;
        if (importer == null)
        {
            return;
        }

        importer.importAnimation = true;
        importer.animationType = ModelImporterAnimationType.Human;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;

        var clips = importer.defaultClipAnimations;
        if (IsTextingMotion(assetPath) && clips.Length > 0)
        {
            importer.clipAnimations = CreateTextingClips(clips[0]);
            return;
        }

        for (var i = 0; i < clips.Length; i++)
        {
            ConfigureRoot(clips[i]);
            clips[i].loopTime = ShouldLoop(assetPath);
            clips[i].loopPose = clips[i].loopTime;
        }

        importer.clipAnimations = clips;
    }

    private static ModelImporterClipAnimation[] CreateTextingClips(ModelImporterClipAnimation source)
    {
        var first = source.firstFrame;
        var last = source.lastFrame;
        var span = last - first;
        var pickupEnd = first + span * TextingPickupEnd;
        var loopEnd = first + span * TextingLoopEnd;

        return new[]
        {
            CreateClip(source, "Texting", first, last, loop: false),
            CreateClip(source, "Texting_Pickup", first, pickupEnd, loop: false),
            CreateClip(source, "Texting_Loop", pickupEnd, loopEnd, loop: true),
            CreateClip(source, "Texting_PutDown", loopEnd, last, loop: false),
        };
    }

    private static ModelImporterClipAnimation CreateClip(ModelImporterClipAnimation source, string name, float firstFrame, float lastFrame, bool loop)
    {
        var clip = new ModelImporterClipAnimation
        {
            name = name,
            takeName = source.takeName,
            firstFrame = firstFrame,
            lastFrame = lastFrame,
            loopTime = loop,
            loopPose = loop,
        };
        ConfigureRoot(clip);
        return clip;
    }

    private static void ConfigureRoot(ModelImporterClipAnimation clip)
    {
        clip.lockRootRotation = true;
        clip.lockRootHeightY = true;
        clip.lockRootPositionXZ = true;
        clip.keepOriginalOrientation = true;
        clip.keepOriginalPositionY = true;
        clip.keepOriginalPositionXZ = true;
        clip.heightFromFeet = false;
    }

    private static bool IsTextingMotion(string path)
    {
        return path.ToLowerInvariant().EndsWith("x bot@texting.fbx");
    }

    private static bool ShouldLoop(string path)
    {
        var lower = path.ToLowerInvariant();
        return lower.Contains("seated idle") ||
            lower.Contains("sitting idle") ||
            lower.EndsWith("x bot@sitting.fbx") ||
            lower.Contains("standing using touchscreen tablet") ||
            lower.Contains("walking while texting") ||
            lower.EndsWith("x bot@typing.fbx") ||
            lower.Contains("male standing pose");
    }
}
