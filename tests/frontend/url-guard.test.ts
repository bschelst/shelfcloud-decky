// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What may be handed to Steam's URL handler.
//
// `login_url` arrives from the gateway and is pasted into
// `steam://openurl/${url}`, which Steam parses as a command. Nothing checked
// it: whatever the server said went straight to that parser. The launcher has
// held its own launch URLs to an allowlist from the beginning; this is the
// same rule at the other door.

import { describe, expect, it } from "vitest";

import { isSafeExternalUrl } from "../../src/lib/url";

describe("addresses that are fine", () => {
  it("accepts the gateway", () => {
    expect(isSafeExternalUrl("https://shelfcloud.cc/my")).toBe(true);
  });

  it("accepts a hostname with a hyphen", () => {
    // The first version of this guard rejected every one of these: its
    // character class read "\s -", which is whitespace, space AND hyphen.
    expect(isSafeExternalUrl("https://my-gateway.example.com/sign-in")).toBe(true);
  });

  it("accepts a query string and a fragment", () => {
    expect(isSafeExternalUrl("https://shelfcloud.cc/a?b=c&d=e#f")).toBe(true);
  });
});

describe("addresses that are not", () => {
  it("refuses http, which anything on the path could rewrite", () => {
    expect(isSafeExternalUrl("http://shelfcloud.cc/my")).toBe(false);
  });

  it("refuses a javascript: url", () => {
    expect(isSafeExternalUrl("javascript:alert(1)")).toBe(false);
  });

  it("refuses a file: url", () => {
    expect(isSafeExternalUrl("file:///etc/passwd")).toBe(false);
  });

  it("refuses another steam: command", () => {
    expect(isSafeExternalUrl("steam://install/730")).toBe(false);
  });

  it("accepts an empty authority, which the parser normalises away", () => {
    // Checked against the runtime rather than assumed: new URL() turns
    // "https:///nowhere" into host "nowhere". It is a real https address, so
    // refusing it would be the guard being wrong, not strict.
    expect(isSafeExternalUrl("https:///nowhere")).toBe(true);
  });

  it("refuses whitespace, which could end the command and start another", () => {
    expect(isSafeExternalUrl("https://a.example/ steam://install/730")).toBe(false);
  });

  it("refuses control characters", () => {
    expect(isSafeExternalUrl("https://a.example/\u0000x")).toBe(false);
    expect(isSafeExternalUrl("https://a.example/\nx")).toBe(false);
    expect(isSafeExternalUrl("https://a.example/\u007f")).toBe(false);
  });

  it("refuses something that is not a url at all", () => {
    expect(isSafeExternalUrl("not a url")).toBe(false);
  });
});

describe("values that are not even strings", () => {
  // The gateway can answer with anything. null, a number and an object all
  // reached this function before it existed.
  it("refuses them without throwing", () => {
    for (const value of [null, undefined, 0, 1, {}, [], true, NaN]) {
      expect(isSafeExternalUrl(value)).toBe(false);
    }
  });

  it("refuses the empty string", () => {
    expect(isSafeExternalUrl("")).toBe(false);
  });
});
