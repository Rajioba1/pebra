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
  const cases: Array<[z.ZodTypeAny, string]> = [
    [z.string(), "string"],
    [z.number(), "number"],
    [z.array(z.string()), "array"],
    [z.object({ name: z.string() }), "object"],
    [z.string().optional(), "optional"],
    [z.union([z.string(), z.number()]), "union"],
    [z.enum(["a", "b"]), "enum"],
    [z.record(z.string()), "record"],
    [z.string().default("x"), "default"],
    [z.custom<unknown>(() => true), "effects"],
  ];
  for (const [schema, expected] of cases) expect(labelFor(schema)).toBe(expected);
});
