import type {ImageSourcePropType} from 'react-native';

const LOCAL_ASSETS: Record<string, ImageSourcePropType> = {
  'local://RelaxRoomUI/avatar_xiaobai': require('../assets/relaxroom/avatar_xiaobai.png'),
  'local://RelaxRoomUI/avatar_user': require('../assets/relaxroom/avatar_user.png'),
  'local://RelaxRoomUI/moment_curry_01': require('../assets/relaxroom/moment_curry_01.png'),
  'local://RelaxRoomUI/moment_curry_02': require('../assets/relaxroom/moment_curry_02.png'),
  'local://RelaxRoomUI/moment_curry_03': require('../assets/relaxroom/moment_curry_03.png'),
  'local://RelaxRoomUI/moment_rain_city': require('../assets/relaxroom/moment_rain_city.png'),
  'local://RelaxRoomUI/moment_room_lamp': require('../assets/relaxroom/moment_room_lamp.png'),
  'local://RelaxRoomUI/moment_video_thumb': require('../assets/relaxroom/moment_video_thumb.png'),
};

export function resolveLocalAsset(url?: string): ImageSourcePropType | undefined {
  if (!url) {
    return undefined;
  }
  return LOCAL_ASSETS[url];
}
