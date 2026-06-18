import {useWindowDimensions} from 'react-native';
import {useSafeAreaInsets} from 'react-native-safe-area-context';

export const INTERACTION_MAX_RATIO = 1 / 3;

export function useInteractionPanelHeight() {
  const {height} = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const maxHeight = Math.floor(height * INTERACTION_MAX_RATIO);
  return {
    maxHeight,
    bottomInset: insets.bottom,
    topInset: insets.top,
  };
}
