/* eslint-env jest */

jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

jest.mock('@azesmway/react-native-unity', () => {
  const {View} = require('react-native');
  return View;
});

jest.mock('react-native-image-picker', () => ({
  launchImageLibrary: jest.fn(async () => ({assets: []})),
}));
