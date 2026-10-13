import { describe, expect, it } from "vitest";

import { buildUrl, errorMessage, readCookie } from "../api/client";
import { isScanStale, pluralize, timeAgo } from "./format";

const NOW = new Date("2026-10-13T12:00:00Z").getTime();

describe("timeAgo", () => {
  it("handles missing and recent values", () => {
    expect(timeAgo(null)).toBe("never");
    expect(timeAgo("2026-10-13T11:59:30Z", NOW)).toBe("just now");
  });

  it("picks the largest sensible unit", () => {
    expect(timeAgo("2026-10-13T11:55:00Z", NOW)).toMatch(/5 min/);
    expect(timeAgo("2026-10-13T09:00:00Z", NOW)).toMatch(/3 hr/);
    expect(timeAgo("2026-10-12T12:00:00Z", NOW)).toMatch(/yesterday|1 day/);
  });
});

describe("isScanStale", () => {
  it("is stale without a successful scan or after 3 intervals", () => {
    expect(isScanStale(null, 2, NOW)).toBe(true);
    expect(isScanStale("2026-10-13T11:55:00Z", 2, NOW)).toBe(false);
    expect(isScanStale("2026-10-13T11:53:00Z", 2, NOW)).toBe(true);
  });
});

describe("pluralize", () => {
  it("pluralizes", () => {
    expect(pluralize(1, "device")).toBe("1 device");
    expect(pluralize(3, "device")).toBe("3 devices");
  });
});

describe("api client helpers", () => {
  it("reads cookies", () => {
    expect(readCookie("csrftoken", "sessionid=abc; csrftoken=x%3Dy; other=1")).toBe("x=y");
    expect(readCookie("missing", "a=1")).toBeNull();
  });

  it("builds URLs and drops empty params", () => {
    expect(buildUrl("devices/", { status: "online", search: "", known: false, x: undefined })).toBe(
      "/api/devices/?status=online&known=false",
    );
  });

  it("extracts DRF error messages", () => {
    expect(errorMessage({ detail: "Not found." }, 404)).toBe("Not found.");
    expect(errorMessage({ name: ["You already have a token with this name."] }, 400)).toBe(
      "name: You already have a token with this name.",
    );
    expect(errorMessage({ non_field_errors: ["Bad."] }, 400)).toBe("Bad.");
    expect(errorMessage(null, 500)).toBe("Request failed (HTTP 500)");
  });
});
