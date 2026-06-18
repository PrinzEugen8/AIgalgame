import React from 'react';
import {SafeAreaProvider} from 'react-native-safe-area-context';
import {RelaxRoomScreen} from './src/screens/RelaxRoomScreen';

function App(): React.JSX.Element {
  return (
    <SafeAreaProvider>
      <RelaxRoomScreen />
    </SafeAreaProvider>
  );
}

export default App;
