using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace AIgalgame.EditorTools
{
    public static class AIGalgameHayaseyuukaRigSetup
    {
        private const string TargetScenePath = "Assets/Brick Project Studio/Apartment Kit/Scenes/Scene_01.unity";

        [MenuItem("Tools/AIgalgame/Rigging/Setup Hayaseyuuka Rig and Sockets")]
        public static void SetupHayaseyuukaRigAndSockets()
        {
            // 1. Open and load Scene_01 if not open
            if (EditorSceneManager.GetActiveScene().path != TargetScenePath)
            {
                if (EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo())
                {
                    EditorSceneManager.OpenScene(TargetScenePath);
                }
                else
                {
                    Debug.LogWarning("Setup cancelled: Current scene was not saved.");
                    return;
                }
            }

            // 2. Find 'Hayaseyuuka' GameObject in the active scene
            GameObject hayaseyuuka = GameObject.Find("Hayaseyuuka");
            if (hayaseyuuka == null)
            {
                // Backup search: search in root objects
                var roots = EditorSceneManager.GetActiveScene().GetRootGameObjects();
                foreach (var root in roots)
                {
                    if (root.name == "Hayaseyuuka")
                    {
                        hayaseyuuka = root;
                        break;
                    }
                    var child = root.transform.Find("Hayaseyuuka");
                    if (child != null)
                    {
                        hayaseyuuka = child.gameObject;
                        break;
                    }
                }
            }

            if (hayaseyuuka == null)
            {
                Debug.LogError("Could not find 'Hayaseyuuka' GameObject in the active scene.");
                EditorUtility.DisplayDialog("Error", "Could not find 'Hayaseyuuka' GameObject in the scene.", "OK");
                return;
            }

            Undo.RecordObject(hayaseyuuka, "Setup Hayaseyuuka Rig and Sockets");

            // 3. Clean up the missing component on her root (if possible, or just add the RigBuilder)
            GameObjectUtility.RemoveMonoBehavioursWithMissingScript(hayaseyuuka);

            // 4. Find or add RigBuilder component to her root
            var rigBuilder = hayaseyuuka.GetComponent<RigBuilder>();
            if (rigBuilder == null)
            {
                rigBuilder = Undo.AddComponent<RigBuilder>(hayaseyuuka);
            }

            // 5. Find 'Rig 1' child under Hayaseyuuka
            Transform rig1Transform = hayaseyuuka.transform.Find("Rig 1");
            Rig rig1 = null;
            if (rig1Transform == null)
            {
                GameObject rig1Go = new GameObject("Rig 1");
                rig1Go.transform.SetParent(hayaseyuuka.transform);
                rig1Go.transform.localPosition = Vector3.zero;
                rig1Go.transform.localRotation = Quaternion.identity;
                rig1Go.transform.localScale = Vector3.one;
                Undo.RegisterCreatedObjectUndo(rig1Go, "Create Rig 1");
                rig1Transform = rig1Go.transform;
            }

            rig1 = rig1Transform.GetComponent<Rig>();
            if (rig1 == null)
            {
                rig1 = Undo.AddComponent<Rig>(rig1Transform.gameObject);
            }

            // 6. Clean up/destroy any existing children under 'Rig 1' (to allow clean rebuilding)
            var children = new List<GameObject>();
            foreach (Transform child in rig1Transform)
            {
                children.Add(child.gameObject);
            }
            foreach (var child in children)
            {
                Undo.DestroyObjectImmediate(child);
            }

            // Resolve Bone paths relative to Hayaseyuuka
            Transform leftUpperArm = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.L/upper_arm.L");
            Transform leftLowerArm = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.L/upper_arm.L/lower_arm.L");
            Transform leftHand = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.L/upper_arm.L/lower_arm.L/Hand.L");

            Transform rightUpperArm = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.R/upper_arm.R");
            Transform rightLowerArm = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.R/upper_arm.R/lower_arm.R");
            Transform rightHand = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/shoulder.R/upper_arm.R/lower_arm.R/Hand.R");

            Transform leftUpperLeg = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.L");
            Transform leftLowerLeg = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.L/lower_leg.L");
            Transform leftFoot = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.L/lower_leg.L/foot.L");

            Transform rightUpperLeg = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.R");
            Transform rightLowerLeg = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.R/lower_leg.R");
            Transform rightFoot = hayaseyuuka.transform.Find("骨架/root/hips/upper_leg.R/lower_leg.R/foot.R");

            Transform headBone = hayaseyuuka.transform.Find("骨架/root/hips/spine/chest/neck/Head");

            if (leftHand == null || rightHand == null || leftFoot == null || rightFoot == null || headBone == null)
            {
                Debug.LogError("One or more key bones were not found. Please verify bone paths under Hayaseyuuka.");
                return;
            }

            // Resolve or create Targets under 'AIgalgame_IKTargets'
            Transform ikTargetsRoot = hayaseyuuka.transform.Find("AIgalgame_IKTargets");
            if (ikTargetsRoot == null)
            {
                var ikTargetsGo = new GameObject("AIgalgame_IKTargets");
                ikTargetsGo.transform.SetParent(hayaseyuuka.transform);
                ikTargetsGo.transform.localPosition = Vector3.zero;
                ikTargetsGo.transform.localRotation = Quaternion.identity;
                ikTargetsGo.transform.localScale = Vector3.one;
                Undo.RegisterCreatedObjectUndo(ikTargetsGo, "Create AIgalgame_IKTargets");
                ikTargetsRoot = ikTargetsGo.transform;
            }

            Transform leftHandTarget = EnsureTargetSnapped(ikTargetsRoot, "LeftHand_Target", leftHand);
            Transform rightHandTarget = EnsureTargetSnapped(ikTargetsRoot, "RightHand_Target", rightHand);
            Transform leftFootTarget = EnsureTargetSnapped(ikTargetsRoot, "LeftFoot_Target", leftFoot);
            Transform rightFootTarget = EnsureTargetSnapped(ikTargetsRoot, "RightFoot_Target", rightFoot);

            Transform lookAtTarget = EnsureTargetSnapped(ikTargetsRoot, "LookAt_Target", headBone);
            if (lookAtTarget != null && lookAtTarget.localPosition == Vector3.zero)
            {
                // Position lookAtTarget in front of head bone
                lookAtTarget.position = headBone.position + headBone.forward * 1.5f;
            }

            Transform leftHandPole = EnsurePoleSnapped(ikTargetsRoot, "LeftHand_Pole", leftUpperArm, leftLowerArm);
            Transform rightHandPole = EnsurePoleSnapped(ikTargetsRoot, "RightHand_Pole", rightUpperArm, rightLowerArm);
            Transform leftFootPole = EnsurePoleSnapped(ikTargetsRoot, "LeftFoot_Pole", leftUpperLeg, leftLowerLeg);
            Transform rightFootPole = EnsurePoleSnapped(ikTargetsRoot, "RightFoot_Pole", rightUpperLeg, rightLowerLeg);

            // 7. Create 'LeftArm_IK' child under 'Rig 1' and add TwoBoneIKConstraint component
            var leftArmIKGo = new GameObject("LeftArm_IK");
            leftArmIKGo.transform.SetParent(rig1Transform);
            leftArmIKGo.transform.localPosition = Vector3.zero;
            leftArmIKGo.transform.localRotation = Quaternion.identity;
            leftArmIKGo.transform.localScale = Vector3.one;
            Undo.RegisterCreatedObjectUndo(leftArmIKGo, "Create LeftArm_IK");

            var leftArmIK = leftArmIKGo.AddComponent<TwoBoneIKConstraint>();
            var leftArmData = leftArmIK.data;
            leftArmData.root = leftUpperArm;
            leftArmData.mid = leftLowerArm;
            leftArmData.tip = leftHand;
            leftArmData.target = leftHandTarget;
            leftArmData.hint = leftHandPole;
            leftArmIK.data = leftArmData;

            // 8. Create 'RightArm_IK' child under 'Rig 1' and setup TwoBoneIKConstraint
            var rightArmIKGo = new GameObject("RightArm_IK");
            rightArmIKGo.transform.SetParent(rig1Transform);
            rightArmIKGo.transform.localPosition = Vector3.zero;
            rightArmIKGo.transform.localRotation = Quaternion.identity;
            rightArmIKGo.transform.localScale = Vector3.one;
            Undo.RegisterCreatedObjectUndo(rightArmIKGo, "Create RightArm_IK");

            var rightArmIK = rightArmIKGo.AddComponent<TwoBoneIKConstraint>();
            var rightArmData = rightArmIK.data;
            rightArmData.root = rightUpperArm;
            rightArmData.mid = rightLowerArm;
            rightArmData.tip = rightHand;
            rightArmData.target = rightHandTarget;
            rightArmData.hint = rightHandPole;
            rightArmIK.data = rightArmData;

            // 9. Create 'LeftLeg_IK' child under 'Rig 1' and setup TwoBoneIKConstraint
            var leftLegIKGo = new GameObject("LeftLeg_IK");
            leftLegIKGo.transform.SetParent(rig1Transform);
            leftLegIKGo.transform.localPosition = Vector3.zero;
            leftLegIKGo.transform.localRotation = Quaternion.identity;
            leftLegIKGo.transform.localScale = Vector3.one;
            Undo.RegisterCreatedObjectUndo(leftLegIKGo, "Create LeftLeg_IK");

            var leftLegIK = leftLegIKGo.AddComponent<TwoBoneIKConstraint>();
            var leftLegData = leftLegIK.data;
            leftLegData.root = leftUpperLeg;
            leftLegData.mid = leftLowerLeg;
            leftLegData.tip = leftFoot;
            leftLegData.target = leftFootTarget;
            leftLegData.hint = leftFootPole;
            leftLegIK.data = leftLegData;

            // 10. Create 'RightLeg_IK' child under 'Rig 1' and setup TwoBoneIKConstraint
            var rightLegIKGo = new GameObject("RightLeg_IK");
            rightLegIKGo.transform.SetParent(rig1Transform);
            rightLegIKGo.transform.localPosition = Vector3.zero;
            rightLegIKGo.transform.localRotation = Quaternion.identity;
            rightLegIKGo.transform.localScale = Vector3.one;
            Undo.RegisterCreatedObjectUndo(rightLegIKGo, "Create RightLeg_IK");

            var rightLegIK = rightLegIKGo.AddComponent<TwoBoneIKConstraint>();
            var rightLegData = rightLegIK.data;
            rightLegData.root = rightUpperLeg;
            rightLegData.mid = rightLowerLeg;
            rightLegData.tip = rightFoot;
            rightLegData.target = rightFootTarget;
            rightLegData.hint = rightFootPole;
            rightLegIK.data = rightLegData;

            // 11. Create 'Head_LookAt' child under 'Rig 1' and setup MultiAimConstraint
            var headLookAtGo = new GameObject("Head_LookAt");
            headLookAtGo.transform.SetParent(rig1Transform);
            headLookAtGo.transform.localPosition = Vector3.zero;
            headLookAtGo.transform.localRotation = Quaternion.identity;
            headLookAtGo.transform.localScale = Vector3.one;
            Undo.RegisterCreatedObjectUndo(headLookAtGo, "Create Head_LookAt");

            var headLookAt = headLookAtGo.AddComponent<MultiAimConstraint>();
            var headData = headLookAt.data;
            headData.constrainedObject = headBone;
            var sources = new WeightedTransformArray();
            sources.Add(new WeightedTransform(lookAtTarget, 1.0f));
            headData.sourceObjects = sources;
            headLookAt.data = headData;

            // 12. Assign 'Rig 1' to 'RigBuilder.layers' list
            Undo.RecordObject(rigBuilder, "Assign Rig 1 Layer");
            rigBuilder.layers = new List<RigLayer> { new RigLayer(rig1) };

            // 13. Create empty GameObjects 'LeftHand_Socket' under 'Hand.L' and 'RightHand_Socket' under 'Hand.R' if they don't already exist
            if (leftHand != null)
            {
                var leftSocket = leftHand.Find("LeftHand_Socket");
                if (leftSocket == null)
                {
                    var socketGo = new GameObject("LeftHand_Socket");
                    socketGo.transform.SetParent(leftHand);
                    socketGo.transform.localPosition = Vector3.zero;
                    socketGo.transform.localRotation = Quaternion.identity;
                    socketGo.transform.localScale = Vector3.one;
                    Undo.RegisterCreatedObjectUndo(socketGo, "Create LeftHand_Socket");
                }
            }

            if (rightHand != null)
            {
                var rightSocket = rightHand.Find("RightHand_Socket");
                if (rightSocket == null)
                {
                    var socketGo = new GameObject("RightHand_Socket");
                    socketGo.transform.SetParent(rightHand);
                    socketGo.transform.localPosition = Vector3.zero;
                    socketGo.transform.localRotation = Quaternion.identity;
                    socketGo.transform.localScale = Vector3.one;
                    Undo.RegisterCreatedObjectUndo(socketGo, "Create RightHand_Socket");
                }
            }

            // 14. Mark the scene dirty and save
            EditorUtility.SetDirty(hayaseyuuka);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene());

            Debug.Log("Hayaseyuuka rigging setup and hand sockets configured successfully.");
            EditorUtility.DisplayDialog("Success", "Hayaseyuuka Rigging and Sockets created and configured successfully!", "OK");
        }

        private static Transform EnsureTargetSnapped(Transform parent, string name, Transform snapToBone)
        {
            var target = parent.Find(name);
            if (target == null)
            {
                var go = new GameObject(name);
                go.transform.SetParent(parent);
                if (snapToBone != null)
                {
                    go.transform.position = snapToBone.position;
                    go.transform.rotation = snapToBone.rotation;
                }
                else
                {
                    go.transform.localPosition = Vector3.zero;
                    go.transform.localRotation = Quaternion.identity;
                }
                go.transform.localScale = Vector3.one;
                Undo.RegisterCreatedObjectUndo(go, "Create " + name);
                target = go.transform;
            }
            return target;
        }

        private static Transform EnsurePoleSnapped(Transform parent, string name, Transform upper, Transform lower)
        {
            var pole = parent.Find(name);
            if (pole == null)
            {
                var go = new GameObject(name);
                go.transform.SetParent(parent);
                if (upper != null && lower != null)
                {
                    var bendDir = (lower.position - upper.position).normalized;
                    var length = Mathf.Max(0.25f, Vector3.Distance(upper.position, lower.position));
                    go.transform.position = lower.position + bendDir * length;
                }
                else
                {
                    go.transform.localPosition = Vector3.zero;
                }
                go.transform.localRotation = Quaternion.identity;
                go.transform.localScale = Vector3.one;
                Undo.RegisterCreatedObjectUndo(go, "Create " + name);
                pole = go.transform;
            }
            return pole;
        }
    }
}
