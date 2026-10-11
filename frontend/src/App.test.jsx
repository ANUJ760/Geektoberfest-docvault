// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import * as api from "./api";

vi.mock("./api", () => ({
  getMe: vi.fn(), getFolders: vi.fn(), getDocuments: vi.fn(),
  createFolder: vi.fn(), renameFolder: vi.fn(), deleteFolder: vi.fn(),
  uploadDocument: vi.fn(), deleteDocument: vi.fn(), searchDocuments: vi.fn(),
  login: vi.fn(), register: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.setItem("token", "test-token");
  api.getMe.mockResolvedValue({ data: { username: "alice" } });
  api.getDocuments.mockResolvedValue({ data: [] });
  api.getFolders.mockResolvedValue({ data: [{ id: 1, name: "Reports" }] });
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
