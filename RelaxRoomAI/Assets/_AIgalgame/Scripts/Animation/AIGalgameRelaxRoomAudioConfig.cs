using UnityEngine;

namespace AIgalgame.Motion
{
    public sealed class AIGalgameRelaxRoomAudioConfig : ScriptableObject
    {
        [SerializeField] private AudioClip bgmClip;
        [SerializeField] private AudioClip phoneNotificationClip;
        [SerializeField] private AudioClip[] phoneTypingClips = new AudioClip[0];
        [SerializeField] private AudioClip[] keyboardTypingClips = new AudioClip[0];

        public AudioClip BgmClip => bgmClip;
        public AudioClip PhoneNotificationClip => phoneNotificationClip;
        public AudioClip[] PhoneTypingClips => phoneTypingClips;
        public AudioClip[] KeyboardTypingClips => keyboardTypingClips;
    }
}
