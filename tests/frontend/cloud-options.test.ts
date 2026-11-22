import { describe, expect, it } from "vitest";

import { cloudOptionsFor, otherServicesFor } from "../../src/lib/cloudOptions";

// tile app id -> Steam app id, and tile app id -> provider, as the backend gives them
const steamIds = { "3159715564": "502500", "3000000001": "502500", "3000000002": "620" };
const providers = { "3159715564": "boosteroid", "3000000001": "geforce_now", "3000000002": "xbox_cloud" };

describe("cloudOptionsFor", () => {
  it("finds every tile that streams the Steam game", () => {
    expect(cloudOptionsFor("502500", steamIds, providers)).toEqual([
      { appId: "3159715564", provider: "boosteroid" },
      { appId: "3000000001", provider: "geforce_now" },
    ]);
  });

  it("accepts a numeric app id like Steam's overview has", () => {
    expect(cloudOptionsFor(620, steamIds, providers)).toEqual([
      { appId: "3000000002", provider: "xbox_cloud" },
    ]);
  });

  it("is empty for a game on no service", () => {
    expect(cloudOptionsFor("730", steamIds, providers)).toEqual([]);
  });

  it("skips a tile with no known provider", () => {
    expect(cloudOptionsFor("620", steamIds, {})).toEqual([]);
  });

  it("rejects ids that are not plain numbers", () => {
    for (const bad of ["", "abc", "12 3", "-5", undefined, null, Number.NaN, 1.5]) {
      expect(cloudOptionsFor(bad as never, steamIds, providers)).toEqual([]);
    }
  });

  it("lists a service once when it has two tiles for the game", () => {
    const ids = { "3924072759": "502500", "3159715564": "502500" };
    const prov = { "3924072759": "boosteroid", "3159715564": "boosteroid" };
    expect(cloudOptionsFor("502500", ids, prov))
      .toEqual([{ appId: "3159715564", provider: "boosteroid" }]);
  });

  it("orders by service so the menu stays the same every open", () => {
    const ids = { "9": "1", "8": "1", "7": "1" };
    const prov = { "9": "xbox_cloud", "8": "boosteroid", "7": "geforce_now" };
    expect(cloudOptionsFor("1", ids, prov).map((o) => o.provider))
      .toEqual(["boosteroid", "geforce_now", "xbox_cloud"]);
  });
});

describe("otherServicesFor", () => {
  // tile app id -> catalog game id, and tile -> service, as the backend sends them
  const gameIds = { "4053800932": "2", "3966200939": "2", "3574121123": "2", "999": "7", "998": "2" };
  const prov = {
    "4053800932": "geforce_now", "3966200939": "boosteroid", "3574121123": "amazon_luna",
    "999": "geforce_now", "998": "boosteroid",
  };

  it("offers the same game on the other services, one each", () => {
    expect(otherServicesFor("4053800932", gameIds, prov)).toEqual([
      { appId: "3574121123", provider: "amazon_luna" },
      { appId: "3966200939", provider: "boosteroid" },
    ]);
  });

  it("never offers the service the tile itself is on", () => {
    const own = otherServicesFor("3966200939", gameIds, prov).map((o) => o.provider);
    expect(own).not.toContain("boosteroid");
  });

  it("is empty for a game on one service only", () => {
    expect(otherServicesFor("999", gameIds, prov)).toEqual([]);
  });

  it("is empty for an app that is not one of our tiles", () => {
    expect(otherServicesFor("12345", gameIds, prov)).toEqual([]);
    expect(otherServicesFor("", gameIds, prov)).toEqual([]);
  });

  it("accepts the numeric app id Steam's overview carries", () => {
    expect(otherServicesFor(4053800932, gameIds, prov)).toHaveLength(2);
  });
});
