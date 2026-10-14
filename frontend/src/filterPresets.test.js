// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  deletePreset,
  getPresetsStorageKey,
  loadPresets,
  MAX_NAME_LENGTH,
  MAX_PRESETS,
  savePreset,
  validatePresetForApply,
} from "./filterPresets";

describe("filterPresets storage utility", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  describe("getPresetsStorageKey", () => {
    it("returns null for missing, null, or empty user IDs", () => {
      expect(getPresetsStorageKey(null)).toBeNull();
      expect(getPresetsStorageKey(undefined)).toBeNull();
      expect(getPresetsStorageKey("")).toBeNull();
    });

    it("scopes key to user ID", () => {
      expect(getPresetsStorageKey(42)).toBe("docvault_filter_presets_42");
      expect(getPresetsStorageKey("alice")).toBe("docvault_filter_presets_alice");
    });
  });

  describe("loadPresets", () => {
    it("returns empty array when nothing is stored", () => {
      expect(loadPresets(1)).toEqual([]);
    });

    it("returns empty array for invalid user ID", () => {
      expect(loadPresets(null)).toEqual([]);
    });

    it("handles corrupted or malformed JSON gracefully", () => {
      localStorage.setItem("docvault_filter_presets_1", "invalid json {{{");
      expect(loadPresets(1)).toEqual([]);
    });

    it("filters out invalid items and returns valid presets", () => {
      const data = [
        { name: "Valid Preset", folderId: 5, fileType: "pdf", sortBy: "name_asc" },
        { name: "", folderId: 5, fileType: "pdf", sortBy: "name_asc" }, // invalid name
        { name: "Bad Type", folderId: null, fileType: "audio", sortBy: "date_desc" }, // invalid type
        { name: "Bad Sort", folderId: null, fileType: "all", sortBy: "random" }, // invalid sort
        null,
        "string item",
      ];
      localStorage.setItem("docvault_filter_presets_1", JSON.stringify(data));
      const loaded = loadPresets(1);
      expect(loaded).toHaveLength(1);
      expect(loaded[0]).toEqual({
        name: "Valid Preset",
        folderId: 5,
        fileType: "pdf",
        sortBy: "name_asc",
      });
    });
  });

  describe("savePreset", () => {
    const folders = [{ id: 1, name: "Reports" }, { id: 2, name: "Invoices" }];

    it("saves a valid preset with folder, type, and sort", () => {
      const presets = savePreset(
        1,
        "My Reports",
        { folderId: 1, fileType: "pdf", sortBy: "name_asc" },
        folders
      );
      expect(presets).toHaveLength(1);
      expect(presets[0]).toEqual({
        name: "My Reports",
        folderId: 1,
        fileType: "pdf",
        sortBy: "name_asc",
      });
      expect(loadPresets(1)).toEqual(presets);
    });

    it("saves criteria-only, ignoring extraneous fields", () => {
      const presets = savePreset(
        1,
        "Clean Criteria",
        {
          folderId: null,
          fileType: "all",
          sortBy: "date_desc",
          token: "secret",
          documents: [1, 2, 3],
        },
        folders
      );
      expect(presets[0]).toEqual({
        name: "Clean Criteria",
        folderId: null,
        fileType: "all",
        sortBy: "date_desc",
      });
    });

    it("rejects unauthenticated user", () => {
      expect(() =>
        savePreset(null, "Test", { fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow("User must be authenticated");
    });

    it("rejects blank or whitespace-only preset names", () => {
      expect(() =>
        savePreset(1, "   ", { fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow("Preset name cannot be empty");
    });

    it("rejects names exceeding max length", () => {
      const longName = "a".repeat(MAX_NAME_LENGTH + 1);
      expect(() =>
        savePreset(1, longName, { fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow(`Preset name cannot exceed ${MAX_NAME_LENGTH} characters`);
    });

    it("rejects duplicate preset names (case-insensitive)", () => {
      savePreset(1, "Invoices", { fileType: "pdf", sortBy: "date_desc" }, folders);
      expect(() =>
        savePreset(1, "invoices", { fileType: "all", sortBy: "date_asc" }, folders)
      ).toThrow('A preset named "invoices" already exists');
    });

    it("rejects invalid or inaccessible folders", () => {
      expect(() =>
        savePreset(1, "Foreign", { folderId: 999, fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow("Selected folder is invalid or inaccessible");
    });

    it("rejects invalid file types or sort options", () => {
      expect(() =>
        savePreset(1, "Bad Type", { fileType: "exe", sortBy: "date_desc" }, folders)
      ).toThrow("Invalid file type");

      expect(() =>
        savePreset(1, "Bad Sort", { fileType: "all", sortBy: "unknown_sort" }, folders)
      ).toThrow("Invalid sort option");
    });

    it("enforces maximum collection limit", () => {
      for (let i = 1; i <= MAX_PRESETS; i++) {
        savePreset(1, `Preset ${i}`, { fileType: "all", sortBy: "date_desc" }, folders);
      }
      expect(() =>
        savePreset(1, `Preset ${MAX_PRESETS + 1}`, { fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow(`Maximum of ${MAX_PRESETS} presets allowed`);
    });

    it("handles storage write errors gracefully", () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new Error("QuotaExceededError");
      });
      expect(() =>
        savePreset(1, "Test", { fileType: "all", sortBy: "date_desc" }, folders)
      ).toThrow("Failed to save preset to browser storage");
    });
  });

  describe("deletePreset", () => {
    it("deletes a preset by name", () => {
      savePreset(1, "First", { fileType: "all", sortBy: "date_desc" }, []);
      savePreset(1, "Second", { fileType: "pdf", sortBy: "date_asc" }, []);

      const updated = deletePreset(1, "first");
      expect(updated).toHaveLength(1);
      expect(updated[0].name).toBe("Second");
      expect(loadPresets(1)).toEqual(updated);
    });
  });

  describe("validatePresetForApply", () => {
    const accessibleFolders = [{ id: 10, name: "Finance" }];

    it("validates preset with existing accessible folder", () => {
      const result = validatePresetForApply(
        { name: "P1", folderId: 10, fileType: "pdf", sortBy: "date_desc" },
        accessibleFolders
      );
      expect(result.valid).toBe(true);
    });

    it("validates preset without folder (all documents)", () => {
      const result = validatePresetForApply(
        { name: "P2", folderId: null, fileType: "all", sortBy: "name_asc" },
        accessibleFolders
      );
      expect(result.valid).toBe(true);
    });

    it("rejects preset if saved folder no longer exists or is inaccessible", () => {
      const result = validatePresetForApply(
        { name: "P3", folderId: 99, fileType: "text", sortBy: "date_desc" },
        accessibleFolders
      );
      expect(result.valid).toBe(false);
      expect(result.error).toContain("saved folder for this preset no longer exists or is inaccessible");
    });
  });
});
