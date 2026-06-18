import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../App';

test('renders app root', () => {
  const tree = ReactTestRenderer.create(<App />);
  expect(tree).toBeTruthy();
});
