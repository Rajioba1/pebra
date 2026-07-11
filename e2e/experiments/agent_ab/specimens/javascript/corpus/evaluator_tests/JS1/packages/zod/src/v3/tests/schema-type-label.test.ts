import { expect, test } from "vitest";
import * as z from "../index.js";

type SchemaWithLabel = z.ZodTypeAny & { schemaTypeLabel?: () => string };
type ZodWithLabel = typeof z & { schemaTypeLabel?: (schema: z.ZodTypeAny) => string };

function labelFor(schema: z.ZodTypeAny): string {
  const topLevel = (z as ZodWithLabel).schemaTypeLabel;
  if (typeof topLevel === "function") return topLevel(schema);

  const instance = (schema as SchemaWithLabel).schemaTypeLabel;
  if (typeof instance === "function") return instance.call(schema);

  throw new Error("schemaTypeLabel API is missing");
}

test("returns stable lowercase labels for representative schemas", () => {
  expect(labelFor(z.string())).toBe("string");
  expect(labelFor(z.number())).toBe("number");
  expect(labelFor(z.array(z.string()))).toBe("array");
  expect(labelFor(z.object({ name: z.string() }))).toBe("object");
});
