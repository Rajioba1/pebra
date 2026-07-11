import { expect, test } from "vitest";
import * as publicApi from "../external.js";

const exported = publicApi as unknown as Record<string, unknown>;

test("keeps the established issue helper export when the clearer name is introduced", () => {
  expect(typeof exported.addIssueToContext).toBe("function");
  expect(typeof exported.reportIssue).toBe("function");
});
