import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "vitest";

test("dependency image skips Puppeteer browser downloads before npm ci", () => {
  const dockerfilePath = resolve(process.cwd(), "Dockerfile");
  const dockerfile = readFileSync(dockerfilePath, "utf8");
  const skipDownload = dockerfile.indexOf("ENV PUPPETEER_SKIP_DOWNLOAD=true");
  const npmCi = dockerfile.indexOf("RUN npm ci");

  expect(skipDownload).toBeGreaterThan(-1);
  expect(skipDownload).toBeLessThan(npmCi);
});
