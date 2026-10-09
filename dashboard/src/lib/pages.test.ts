import { describe, expect, it } from "vitest";
import { PAGES, currentPage, isCurrentPage } from "./pages";

describe("which page an address belongs to", () => {
  it("knows the first page by its bare address, and only that", () => {
    expect(isCurrentPage("/", "/")).toBe(true);
    expect(isCurrentPage("", "/")).toBe(true);
    expect(isCurrentPage("/history/", "/")).toBe(false);
  });

  it("does not mind a trailing slash on either side", () => {
    expect(isCurrentPage("/history/", "/history/")).toBe(true);
    expect(isCurrentPage("/history", "/history/")).toBe(true);
    expect(isCurrentPage("/history/", "/history")).toBe(true);
    expect(isCurrentPage("/history//", "/history/")).toBe(true);
  });

  it("owns what is below it, but not a page whose name merely starts the same", () => {
    expect(isCurrentPage("/history/2026-10-09/", "/history/")).toBe(true);
    expect(isCurrentPage("/historyx/", "/history/")).toBe(false);
    expect(isCurrentPage("/setup/", "/history/")).toBe(false);
  });

  it("finds the page for each address in the navigation", () => {
    for (const page of PAGES) expect(currentPage(page.href)).toBe(page);
    expect(currentPage("/nowhere/")).toBeUndefined();
  });

  it("lists the pages the owner asked for, in order, each with a folder-style address", () => {
    expect(PAGES.map((page) => page.label)).toEqual(["Live", "History", "Setup", "System"]);
    expect(PAGES.every((page) => page.href.startsWith("/") && page.href.endsWith("/"))).toBe(true);
  });
});
