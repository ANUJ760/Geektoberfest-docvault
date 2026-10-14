// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import * as api from "./api";

vi.mock("./api", () => ({
  default: { get: vi.fn(), defaults: { baseURL: "https://api.test" } },
  getMe: vi.fn(), getFolders: vi.fn(),
  createFolder: vi.fn(), renameFolder: vi.fn(), deleteFolder: vi.fn(),
  uploadDocument: vi.fn(), deleteDocument: vi.fn(), searchDocuments: vi.fn(),
  login: vi.fn(), register: vi.fn(),
  getStorageUsage: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.setItem("token", "test-token");
  api.getMe.mockResolvedValue({ data: { username: "alice" } });
  api.default.get.mockResolvedValue({ data: [] });
  api.searchDocuments.mockResolvedValue({ data: [] });
  api.getFolders.mockResolvedValue({ data: [{ id: 1, name: "Reports" }] });
  api.getStorageUsage.mockResolvedValue({
    data: {
      used_bytes: 10485760,
      quota_bytes: 104857600,
      remaining_bytes: 94371840,
      used_percentage: 10.0,
    },
  });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

async function openApp() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("button", { name: "Rename Reports" });
  return user;
}

describe("folder creation", () => {
  it("rejects whitespace visibly and retains the input", async () => {
    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "New folder name" });
    await user.type(input, "   ");
    await user.click(screen.getByRole("button", { name: "Create folder" }));
    expect(screen.getByRole("alert").textContent).toContain("cannot be empty");
    expect(input.value).toBe("   ");
    expect(api.createFolder).not.toHaveBeenCalled();
  });

  it.each([
    ["Folder already exists", "Folder already exists"],
    [[{ msg: "Folder name is invalid" }], "Folder name is invalid"],
  ])("retains input after API errors: %j", async (detail, message) => {
    api.createFolder.mockRejectedValue({ response: { data: { detail } } });
    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "New folder name" });
    await user.type(input, " Reports ");
    await user.click(screen.getByRole("button", { name: "Create folder" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(input.value).toBe(" Reports ");
    expect(api.createFolder).toHaveBeenCalledWith("Reports");
    expect(input.disabled).toBe(false);
  });

  it("clears input only after successful creation", async () => {
    api.createFolder.mockResolvedValue({ data: { id: 2, name: "Archive" } });
    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "New folder name" });
    await user.type(input, " Archive ");
    await user.click(screen.getByRole("button", { name: "Create folder" }));
    await waitFor(() => expect(input.value).toBe(""));
    expect(api.createFolder).toHaveBeenCalledWith("Archive");
    expect(api.getFolders).toHaveBeenCalledTimes(2);
  });
});

describe("folder renaming", () => {
  it("rejects blank names and keeps the editor open", async () => {
    const user = await openApp();
    await user.click(screen.getByRole("button", { name: "Rename Reports" }));
    const input = screen.getByRole("textbox", { name: "Rename folder" });
    await user.clear(input);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert").textContent).toContain("cannot be empty");
    expect(input.value).toBe("");
    expect(api.renameFolder).not.toHaveBeenCalled();
  });

  it("retains a failed rename and lets the user correct and save it", async () => {
    api.renameFolder.mockRejectedValueOnce({ response: { data: { detail: "Folder already exists" } } });
    api.renameFolder.mockResolvedValueOnce({ data: { id: 1, name: "Archive" } });
    const user = await openApp();
    await user.click(screen.getByRole("button", { name: "Reports", exact: true }));
    await user.click(screen.getByRole("button", { name: "Rename Reports" }));
    const input = screen.getByRole("textbox", { name: "Rename folder" });
    expect(input.value).toBe("Reports");
    await user.clear(input);
    await user.type(input, " Existing ");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Folder already exists");
    expect(input.value).toBe(" Existing ");
    await user.clear(input);
    await user.type(input, " Archive ");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const renamed = await screen.findByRole("button", { name: "Archive", exact: true });
    expect(renamed.className).toContain("bg-indigo-50");
    expect(api.renameFolder).toHaveBeenLastCalledWith(1, "Archive");
    expect(screen.queryByRole("textbox", { name: "Rename folder" })).toBeNull();
  });

  it("shows a network error without losing the attempted name", async () => {
    api.renameFolder.mockRejectedValue(new Error("Network error"));
    const user = await openApp();
    await user.click(screen.getByRole("button", { name: "Rename Reports" }));
    const input = screen.getByRole("textbox", { name: "Rename folder" });
    await user.clear(input);
    await user.type(input, "Archive");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Failed to rename folder");
    expect(input.value).toBe("Archive");
  });

  it("cancels editing without sending a request", async () => {
    const user = await openApp();
    await user.click(screen.getByRole("button", { name: "Rename Reports" }));
    await user.type(screen.getByRole("textbox", { name: "Rename folder" }), " changed");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.renameFolder).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Reports", exact: true })).toBeTruthy();
  });
});


function makeDocument(id, name, folder_id, content_type) {
  return {
    id, folder_id, original_filename: name, content_type, file_size: 1024,
    created_at: "2026-10-01T00:00:00.000Z",
  };
}

describe("document search clear", () => {
  it("hides the clear control while the search is empty", async () => {
    await openApp();
    expect(screen.getByRole("textbox", { name: "Search documents" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Clear document search" })).toBeNull();
  });

  it("clears search, restores all documents without reloading, and allows a new search", async () => {
    const fullList = [
      makeDocument(10, "quarterly-report.pdf", 1, "application/pdf"),
      makeDocument(11, "annual-budget.pdf", 1, "application/pdf"),
    ];
    const searchResult = makeDocument(12, "needle-match.txt", 1, "text/plain");
    const afterClearSearch = makeDocument(13, "second-search.txt", 1, "text/plain");
    api.default.get.mockResolvedValue({ data: fullList });
    api.searchDocuments.mockImplementation((query) =>
      Promise.resolve({ data: query === "needle" ? [searchResult] : query === "again" ? [afterClearSearch] : [] })
    );

    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "Search documents" });
    await user.type(input, "needle");
    expect(await screen.findByText("needle-match.txt")).toBeTruthy();
    expect(screen.queryByText("quarterly-report.pdf")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Clear document search" }));
    expect(await screen.findByText("quarterly-report.pdf")).toBeTruthy();
    expect(screen.getByText("annual-budget.pdf")).toBeTruthy();
    expect(screen.queryByText("needle-match.txt")).toBeNull();
    await waitFor(() => expect(api.default.get).toHaveBeenLastCalledWith("/documents/"));
    expect(input.value).toBe("");
    expect(document.activeElement).toBe(input);
    expect(api.getMe).toHaveBeenCalledTimes(1);

    await user.type(input, "again");
    expect(await screen.findByText("second-search.txt")).toBeTruthy();
    expect(api.searchDocuments).toHaveBeenLastCalledWith("again");
  });

  it("restores the active file-type and folder results", async () => {
    const report = makeDocument(20, "reports-q1.pdf", 1, "application/pdf");
    const archive = makeDocument(21, "archive-q1.pdf", 2, "application/pdf");
    const note = makeDocument(22, "reports-notes.txt", 1, "text/plain");
    const searchHit = makeDocument(23, "search-hit.pdf", 1, "application/pdf");
    api.default.get.mockImplementation((url) =>
      Promise.resolve({ data: url.includes("file_type=pdf") ? [report, archive] : [report, archive, note] })
    );
    api.searchDocuments.mockResolvedValue({ data: [searchHit] });

    const user = await openApp();
    await user.selectOptions(screen.getByRole("combobox", { name: "Type:" }), "pdf");
    expect(await screen.findByText("archive-q1.pdf")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Reports", exact: true }));
    const input = screen.getByRole("textbox", { name: "Search documents" });
    await user.type(input, "hit");
    expect(await screen.findByText("search-hit.pdf")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Clear document search" }));
    expect(await screen.findByText("reports-q1.pdf")).toBeTruthy();
    expect(screen.queryByText("archive-q1.pdf")).toBeNull();
    expect(screen.queryByText("reports-notes.txt")).toBeNull();
    expect(screen.queryByText("search-hit.pdf")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Type:" }).value).toBe("pdf");
    expect(screen.getByRole("button", { name: "Reports", exact: true }).className).toContain("bg-indigo-50");
    expect(api.default.get).toHaveBeenLastCalledWith("/documents/?file_type=pdf");
  });

  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("clears the query with %s and returns focus to search", async (_key, activation) => {
    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "Search documents" });
    await user.type(input, "x");
    const clear = await screen.findByRole("button", { name: "Clear document search" });
    expect(clear.type).toBe("button");
    await user.tab();
    expect(document.activeElement).toBe(clear);
    await user.keyboard(activation);
    await waitFor(() => expect(input.value).toBe(""));
    expect(document.activeElement).toBe(input);
    expect(screen.queryByRole("button", { name: "Clear document search" })).toBeNull();
  });

  it("ignores a search response that arrives after the clear request", async () => {
    const fullDocument = makeDocument(30, "current-list.pdf", 1, "application/pdf");
    const staleDocument = makeDocument(31, "stale-search.pdf", 1, "application/pdf");
    let resolveSearch;
    api.default.get.mockResolvedValue({ data: [fullDocument] });
    api.searchDocuments.mockImplementation(() => new Promise((resolve) => { resolveSearch = resolve; }));

    const user = await openApp();
    const input = screen.getByRole("textbox", { name: "Search documents" });
    await user.type(input, "x");
    await user.click(await screen.findByRole("button", { name: "Clear document search" }));
    expect(await screen.findByText("current-list.pdf")).toBeTruthy();
    await act(async () => {
      resolveSearch({ data: [staleDocument] });
    });
    expect(screen.queryByText("stale-search.pdf")).toBeNull();
    expect(screen.getByText("current-list.pdf")).toBeTruthy();
  });
});

describe("storage quota display and error handling", () => {
  it("displays storage quota usage and progress", async () => {
    await openApp();
    expect(screen.getByText("Storage")).toBeTruthy();
    expect(screen.getByText("10%")).toBeTruthy();
    expect(screen.getByText(/10 MB of 100 MB used/i)).toBeTruthy();

    const progressBar = screen.getByRole("progressbar", { name: "Storage quota usage" });
    expect(progressBar.getAttribute("aria-valuenow")).toBe("10");
  });

  it("shows actionable error message when upload is rejected due to quota limit", async () => {
    api.uploadDocument.mockRejectedValue({
      response: {
        status: 413,
        data: {
          detail: "Storage quota exceeded. Allowed quota: 100 MB (104857600 bytes), currently used: 98 MB",
        },
      },
    });

    const user = await openApp();
    const file = new File(["dummy content"], "big.pdf", { type: "application/pdf" });
    const fileInput = screen.getByLabelText("Choose file to upload");
    await user.upload(fileInput, file);
    await user.click(screen.getByRole("button", { name: /upload/i }));

    expect(await screen.findByText(/Storage quota exceeded/i)).toBeTruthy();
  });

  it("refreshes storage usage after document deletion", async () => {
    const doc = {
      id: 55,
      filename: "test.pdf",
      original_filename: "test.pdf",
      file_size: 1024,
      content_type: "application/pdf",
      folder_id: null,
      created_at: new Date().toISOString(),
    };
    api.default.get.mockResolvedValue({ data: [doc] });
    api.deleteDocument.mockResolvedValue({});
    window.confirm = vi.fn().mockReturnValue(true);

    const user = await openApp();
    expect(await screen.findByText("test.pdf")).toBeTruthy();

    const deleteBtn = screen.getByRole("button", { name: "Delete test.pdf" });
    await user.click(deleteBtn);

    expect(api.deleteDocument).toHaveBeenCalledWith(55);
    expect(api.getStorageUsage).toHaveBeenCalledTimes(2); // Initial fetch + after deletion
  });
});

