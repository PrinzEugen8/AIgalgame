declare module '@azesmway/react-native-unity' {
  import type {Component} from 'react';
  import type {ViewStyle} from 'react-native';

  export type UnityMessage = {
    nativeEvent: {
      message: string;
    };
  };

  export type UnityViewProps = {
    style?: ViewStyle;
    onUnityMessage?: (event: UnityMessage) => void;
    androidKeepPlayerMounted?: boolean;
    fullScreen?: boolean;
  };

  export default class UnityView extends Component<UnityViewProps> {
    postMessage(gameObject: string, methodName: string, message: string): void;
    unloadUnity(): void;
    pauseUnity?(pause: boolean): void;
    resumeUnity?(): void;
    windowFocusChanged?(hasFocus: boolean): void;
  }
}
