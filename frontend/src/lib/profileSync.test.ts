import { describe, expect, it } from "vitest";
import { fromRemote, newerSide, toRemote, type RemoteProfile } from "./profileSync";
import { emptyProfile } from "./storage";
import type { Profile } from "./types";

const local: Profile = {
  ...emptyProfile(),
  home: { lat: 35.59, lon: -82.57 },
  floor: 2,
  household: ["kids", "limited_mobility"],
  sensitive_health: true,
};

const remote = (patch: Partial<RemoteProfile> = {}): RemoteProfile => ({
  profile: { home: { lat: 35.78, lon: -78.64 }, floor: 0, household: ["pets"], sensitive_health: false },
  include_sensitive: true,
  updated_at: "2026-10-03T20:00:00Z",
  ...patch,
});

describe("toRemote", () => {
  it("leaves out household and health answers unless the user opted in", () => {
    const up = toRemote(local, false);
    expect(up).not.toHaveProperty("household");
    expect(up).not.toHaveProperty("sensitive_health");
    expect(up.home).toEqual(local.home);
    expect(up.floor).toBe(2);
  });

  it("includes everything when the user opted in", () => {
    expect(toRemote(local, true)).toEqual(local);
  });
});

describe("fromRemote", () => {
  it("takes the account's profile, including sensitive answers it holds", () => {
    const p = fromRemote(remote(), local, emptyProfile());
    expect(p.home).toEqual({ lat: 35.78, lon: -78.64 });
    expect(p.household).toEqual(["pets"]);
    expect(p.sensitive_health).toBe(false);
  });

  it("keeps this device's household and health answers when the account has none", () => {
    const r = remote({ include_sensitive: false, profile: { home: { lat: 35.78, lon: -78.64 }, floor: 0 } });
    const p = fromRemote(r, local, emptyProfile());
    expect(p.floor).toBe(0);
    expect(p.household).toEqual(["kids", "limited_mobility"]);
    expect(p.sensitive_health).toBe(true);
  });

  it("fills fields an older account copy lacks with defaults", () => {
    const p = fromRemote(remote({ profile: { floor: 1 } }), local, emptyProfile());
    expect(p.language).toBe("en");
    expect(p.work).toBeNull();
  });
});

describe("newerSide", () => {
  it("uploads this device's profile when the account has none", () => {
    expect(newerSide("2026-10-03T19:00:00Z", null)).toBe("local");
  });
  it("does nothing when neither side has a profile", () => {
    expect(newerSide(null, null)).toBe("none");
  });
  it("restores the account copy on a device that never saved one", () => {
    expect(newerSide(null, remote())).toBe("remote");
  });
  it("picks the more recent edit", () => {
    expect(newerSide("2026-10-03T21:00:00Z", remote())).toBe("local");
    expect(newerSide("2026-10-03T19:00:00Z", remote())).toBe("remote");
  });
});
