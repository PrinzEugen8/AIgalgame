using System;
using AIgalgame.Motion;
using UnityEditor;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    [CustomEditor(typeof(AIGalgameRelaxRoomMotionDirector))]
    public sealed class AIGalgameRelaxRoomMotionDirectorEditor : Editor
    {
        public override void OnInspectorGUI()
        {
            DrawDefaultInspector();

            EditorGUILayout.Space(10f);
            EditorGUILayout.LabelField("Runtime Sleep Test (Editor Only)", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                "These buttons only exist in the Unity Editor inspector. They work in Play Mode and are not included in player builds.",
                MessageType.Info);

            using (new EditorGUI.DisabledScope(!Application.isPlaying))
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button("Enter Sleep"))
                {
                    InvokeOnSelectedDirectors(director => director.EnterSleep());
                }

                if (GUILayout.Button("Exit Sleep"))
                {
                    InvokeOnSelectedDirectors(director => director.ExitSleep());
                }
            }

            if (!Application.isPlaying)
            {
                EditorGUILayout.HelpBox("Enter Play Mode to use the sleep test buttons.", MessageType.None);
            }
        }

        private void InvokeOnSelectedDirectors(Action<AIGalgameRelaxRoomMotionDirector> action)
        {
            foreach (var selectedTarget in targets)
            {
                if (selectedTarget is AIGalgameRelaxRoomMotionDirector director)
                {
                    action(director);
                }
            }

            SceneView.RepaintAll();
            Repaint();
        }
    }
}
