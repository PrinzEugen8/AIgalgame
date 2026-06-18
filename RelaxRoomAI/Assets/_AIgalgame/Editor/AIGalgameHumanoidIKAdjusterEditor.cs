using AIgalgame.Motion;
using UnityEditor;
using UnityEngine;

namespace AIgalgame.Motion.EditorTools
{
    [CustomEditor(typeof(AIGalgameHumanoidIKAdjuster))]
    public sealed class AIGalgameHumanoidIKAdjusterEditor : Editor
    {
        private MotionIkTarget selectedTarget = MotionIkTarget.LeftHand;
        private MotionIkHandleKind selectedHandle = MotionIkHandleKind.Effector;

        public override void OnInspectorGUI()
        {
            DrawDefaultInspector();

            EditorGUILayout.Space(8f);
            EditorGUILayout.LabelField("Scene IK Handle", EditorStyles.boldLabel);
            selectedTarget = (MotionIkTarget)EditorGUILayout.EnumPopup("Target", selectedTarget);
            selectedHandle = (MotionIkHandleKind)EditorGUILayout.EnumPopup("Handle", selectedHandle);

            if (selectedTarget == MotionIkTarget.LookAt && selectedHandle == MotionIkHandleKind.Pole)
            {
                selectedHandle = MotionIkHandleKind.Effector;
            }

            EditorGUILayout.HelpBox(
                "Select this component, then move the selected IK handle in Scene view. Effector supports position and rotation; Pole controls bend direction.",
                MessageType.Info);
        }

        private void OnSceneGUI()
        {
            var adjuster = (AIGalgameHumanoidIKAdjuster)target;
            if (selectedTarget == MotionIkTarget.LookAt && selectedHandle == MotionIkHandleKind.Pole)
            {
                selectedHandle = MotionIkHandleKind.Effector;
            }

            var handle = adjuster.GetControlTransform(selectedTarget, selectedHandle);
            if (handle == null)
            {
                return;
            }

            Handles.color = selectedHandle == MotionIkHandleKind.Pole
                ? new Color(1f, 0.72f, 0.28f, 1f)
                : new Color(0.34f, 0.62f, 0.95f, 1f);
            Handles.Label(handle.position, $"{selectedTarget} {selectedHandle}");

            EditorGUI.BeginChangeCheck();
            var nextPosition = Handles.PositionHandle(handle.position, handle.rotation);
            var nextRotation = selectedHandle == MotionIkHandleKind.Effector
                ? Handles.RotationHandle(handle.rotation, nextPosition)
                : handle.rotation;

            if (EditorGUI.EndChangeCheck())
            {
                Undo.RecordObject(handle, "Move AIgalgame IK Handle");
                handle.SetPositionAndRotation(nextPosition, nextRotation);
                adjuster.MarkSceneHandleChanged();
                EditorUtility.SetDirty(adjuster);
                SceneView.RepaintAll();
            }
        }
    }
}
