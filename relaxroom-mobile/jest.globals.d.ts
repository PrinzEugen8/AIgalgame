declare function test(
  name: string,
  fn: () => unknown | Promise<unknown>,
): void;

declare const expect: {
  (value: unknown): {
    toBeTruthy(): void;
    toEqual(expected: unknown): void;
    toMatchSnapshot(): void;
  };
};
