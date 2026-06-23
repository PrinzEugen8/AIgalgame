using System;
using AIgalgame.Motion;
using UnityEditor;
using UnityEngine;

namespace AIgalgame.EditorTools
{
    [CustomEditor(typeof(AIGalgameRelaxRoomMotionDirector))]
    public sealed class AIGalgameRelaxRoomMotionDirectorEditor : Editor
    {
        private static readonly IdleActionButton[] IdleActionButtons =
        {
            new("Tablet", "idletablet"),
            new("Texting", "idletexting"),
            new("Typing", "idletyping"),
            new("Yawn", "idleyawn"),
            new("Dozing", "idlesleeping"),
            new("Waking", "idlewaking"),
            new("Sitting", "sitting"),
        };

        private static float idleActionSeconds = 30f;
        private static string customIdleAction = "typing";

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

            DrawIdleActionTest();

            if (!Application.isPlaying)
            {
                EditorGUILayout.HelpBox("Enter Play Mode to use the runtime test buttons.", MessageType.None);
            }
        }

        private void DrawIdleActionTest()
        {
            EditorGUILayout.Space(10f);
            EditorGUILayout.LabelField("Runtime Idle Action Test (Editor Only)", EditorStyles.boldLabel);
            idleActionSeconds = Mathf.Max(0f, EditorGUILayout.FloatField("Hold Seconds", idleActionSeconds));

            using (new EditorGUI.DisabledScope(!Application.isPlaying))
            {
                const int columns = 3;
                for (var i = 0; i < IdleActionButtons.Length; i += columns)
                {
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        for (var offset = 0; offset < columns; offset++)
                        {
                            var index = i + offset;
                            if (index >= IdleActionButtons.Length)
                            {
                                GUILayout.FlexibleSpace();
                                continue;
                            }

                            var button = IdleActionButtons[index];
                            if (GUILayout.Button(button.Label))
                            {
                                PlayIdleAction(button.Key);
                            }
                        }
                    }
                }

                using (new EditorGUILayout.HorizontalScope())
                {
                    customIdleAction = EditorGUILayout.TextField("Custom Key", customIdleAction);
                    if (GUILayout.Button("Play Custom", GUILayout.Width(110f)))
                    {
                        PlayIdleAction(customIdleAction);
                    }
                }

                if (GUILayout.Button("Return Idle"))
                {
                    InvokeOnSelectedDirectors(director => director.SetIdle());
                }
            }
        }

        private void PlayIdleAction(string motion)
        {
            if (string.IsNullOrWhiteSpace(motion))
            {
                return;
            }

            InvokeOnSelectedDirectors(director => director.PlayIdleAction(motion, idleActionSeconds));
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

        private readonly struct IdleActionButton
        {
            public IdleActionButton(string label, string key)
            {
                Label = label;
                Key = key;
            }

            public string Label { get; }
            public string Key { get; }
        }
    }
}
