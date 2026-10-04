import { describe, expect, it } from "vitest";
import { ApiError } from "../lib/api";
import { searchError } from "./LocationPicker";

describe("searchError", () => {
  it("passes through the server's busy message", () => {
    const msg = searchError(new ApiError(503, "Place search is busy. Wait a few seconds and try again, or tap a spot on the map."));
    expect(msg).toContain("busy");
  });

  it("replaces a bare Internal Server Error", () => {
    expect(searchError(new ApiError(500, "Internal Server Error"))).toContain("unavailable right now");
  });

  it("never shows the raw fetch failure", () => {
    expect(searchError(new TypeError("Failed to fetch"))).not.toContain("Failed to fetch");
  });
});
