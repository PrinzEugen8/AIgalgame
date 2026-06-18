module.exports = {
  preset: 'react-native',
  setupFiles: ['<rootDir>/jest.setup.js'],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?|react-native-image-picker|react-native-safe-area-context|react-native-svg|@azesmway/react-native-unity)/)',
  ],
};
