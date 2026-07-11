import { expect, test } from "vitest";
import { array, number, object, schemaTypeLabel, string } from "../index.js";

test("returns stable lowercase labels for representative schemas", () => {
  expect(schemaTypeLabel(string())).toBe("string");
  expect(schemaTypeLabel(number())).toBe("number");
  expect(schemaTypeLabel(array(string()))).toBe("array");
  expect(schemaTypeLabel(object({ name: string() }))).toBe("object");
});
