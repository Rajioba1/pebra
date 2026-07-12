import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import * as exported from "../helpers/parseUtil.js";

test("the clearer helper name is functional and used by the shared types module", () => {
  expect(typeof exported.reportIssue).toBe("function");

  const issues: unknown[] = [];
  exported.reportIssue(
    {
      common: { issues, async: false },
      path: [],
      parent: null,
      data: "bad",
      parsedType: "string",
    } as never,
    { code: "custom", message: "completion probe" },
  );
  expect(issues).toHaveLength(1);

  const typesSource = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  expect(typesSource).not.toMatch(/\baddIssueToContext\s*\(/);
  expect(typesSource.match(/\breportIssue\s*\(/g)?.length ?? 0).toBeGreaterThan(50);
});
