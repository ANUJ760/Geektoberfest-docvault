export const VALID_FILE_TYPES = ["all", "pdf", "image", "text"];
export const VALID_SORT_OPTIONS = ["date_desc", "date_asc", "name_asc", "name_desc"];
export const MAX_PRESETS = 10;
export const MAX_NAME_LENGTH = 50;

/**
 * Generates a storage key scoped to the user's stable identifier.
 */
export function getPresetsStorageKey(userId) {
  if (userId === null || userId === undefined || userId === "") {
    return null;
  }
  return `docvault_filter_presets_${userId}`;
}

/**
 * Loads presets for the user from browser storage.
 * Safely handles corrupted JSON, malformed items, and unavailable localStorage.
 */
export function loadPresets(userId) {
  const key = getPresetsStorageKey(userId);
  if (!key) return [];
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter((item) => {
        if (!item || typeof item !== "object") return false;
        if (typeof item.name !== "string" || !item.name.trim()) return false;
        if (item.name.trim().length > MAX_NAME_LENGTH) return false;
        if (!VALID_FILE_TYPES.includes(item.fileType)) return false;
        if (!VALID_SORT_OPTIONS.includes(item.sortBy)) return false;
        if (
          item.folderId !== null &&
          typeof item.folderId !== "number" &&
          typeof item.folderId !== "string"
        ) {
          return false;
        }
        return true;
      })
      .map((item) => ({
        name: item.name.trim(),
        folderId: item.folderId === null ? null : Number(item.folderId) || item.folderId,
        fileType: item.fileType,
        sortBy: item.sortBy,
      }));
  } catch (err) {
    console.error("Failed to load filter presets from localStorage:", err);
    return [];
  }
}

/**
 * Saves a new filter preset for the user.
 * Validates criteria-only content, name length, uniqueness, and folder validity.
 */
export function savePreset(userId, name, criteria, accessibleFolders = []) {
  const key = getPresetsStorageKey(userId);
  if (!key) {
    throw new Error("User must be authenticated to save presets.");
  }

  if (typeof name !== "string") {
    throw new Error("Preset name must be a string.");
  }

  const trimmedName = name.trim();
  if (!trimmedName) {
    throw new Error("Preset name cannot be empty.");
  }

  if (trimmedName.length > MAX_NAME_LENGTH) {
    throw new Error(`Preset name cannot exceed ${MAX_NAME_LENGTH} characters.`);
  }

  const existing = loadPresets(userId);
  if (existing.some((p) => p.name.toLowerCase() === trimmedName.toLowerCase())) {
    throw new Error(`A preset named "${trimmedName}" already exists.`);
  }

  if (existing.length >= MAX_PRESETS) {
    throw new Error(
      `Maximum of ${MAX_PRESETS} presets allowed. Please remove an existing preset before saving a new one.`
    );
  }

  const fileType = criteria.fileType || "all";
  if (!VALID_FILE_TYPES.includes(fileType)) {
    throw new Error(`Invalid file type: ${fileType}`);
  }

  const sortBy = criteria.sortBy || "date_desc";
  if (!VALID_SORT_OPTIONS.includes(sortBy)) {
    throw new Error(`Invalid sort option: ${sortBy}`);
  }

  let folderId = criteria.folderId ?? null;
  if (folderId !== null) {
    folderId = Number(folderId) || folderId;
    const folderExists =
      Array.isArray(accessibleFolders) && accessibleFolders.some((f) => f.id === folderId);
    if (!folderExists) {
      throw new Error("Selected folder is invalid or inaccessible.");
    }
  }

  const newPreset = {
    name: trimmedName,
    folderId,
    fileType,
    sortBy,
  };

  const updated = [...existing, newPreset];
  try {
    localStorage.setItem(key, JSON.stringify(updated));
  } catch (err) {
    console.error("Failed to save preset to localStorage:", err);
    throw new Error("Failed to save preset to browser storage (storage may be full or disabled).");
  }

  return updated;
}

/**
 * Removes a preset by name for the user.
 */
export function deletePreset(userId, name) {
  const key = getPresetsStorageKey(userId);
  if (!key) return [];
  const existing = loadPresets(userId);
  const updated = existing.filter((p) => p.name.toLowerCase() !== name.trim().toLowerCase());
  try {
    localStorage.setItem(key, JSON.stringify(updated));
  } catch (err) {
    console.error("Failed to delete preset from storage:", err);
  }
  return updated;
}

/**
 * Validates a preset before applying it.
 * Verifies that the saved folder is still in the user's accessible folder list.
 */
export function validatePresetForApply(preset, accessibleFolders = []) {
  if (!preset || typeof preset !== "object") {
    return { valid: false, error: "Invalid preset selected." };
  }

  if (!VALID_FILE_TYPES.includes(preset.fileType)) {
    return { valid: false, error: "Preset contains an unsupported file type filter." };
  }

  if (!VALID_SORT_OPTIONS.includes(preset.sortBy)) {
    return { valid: false, error: "Preset contains an unsupported sort order." };
  }

  if (preset.folderId !== null && preset.folderId !== undefined) {
    const folderExists =
      Array.isArray(accessibleFolders) && accessibleFolders.some((f) => f.id === preset.folderId);
    if (!folderExists) {
      return {
        valid: false,
        error: "The saved folder for this preset no longer exists or is inaccessible.",
      };
    }
  }

  return { valid: true };
}
