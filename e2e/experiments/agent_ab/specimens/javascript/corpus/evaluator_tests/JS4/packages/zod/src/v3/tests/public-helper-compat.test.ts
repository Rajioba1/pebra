import { expect, test } from "vitest";
import * as publicApi from "../external.js";

const exported = publicApi as unknown as Record<string, unknown>;

test("keeps the established issue helper export when the clearer name is introduced", () => {
  expect(typeof exported.addIssueToContext).toBe("function");

  const issues: unknown[] = [];
  (publicApi.addIssueToContext as typeof publicApi.addIssueToContext)(
    {
      common: { issues, async: false },
      path: [],
      parent: null,
      data: "bad",
      parsedType: "string",
    } as never,
    { code: "custom", message: "compatibility probe" },
  );
  expect(issues).toHaveLength(1);
});
