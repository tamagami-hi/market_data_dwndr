import { expect, test } from "vitest";

import { progressColor, progressHue } from "@/lib/monitor/progressColor";

test("interpolates continuously from red through yellow to green", () => {
  expect(progressHue(0)).toBe(0);
  expect(progressHue(25)).toBe(30);
  expect(progressHue(50)).toBe(60);
  expect(progressHue(75)).toBe(90);
  expect(progressHue(100)).toBe(120);

  const hues = Array.from({ length: 101 }, (_, value) => progressHue(value));
  expect(new Set(hues)).toHaveLength(101);
  expect(hues.every((hue, index) => index === 0 || hue > hues[index - 1])).toBe(true);
});

test("clamps progress before deriving a color", () => {
  expect(progressColor(-10)).toBe(progressColor(0));
  expect(progressColor(110)).toBe(progressColor(100));
});
