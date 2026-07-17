import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { ProfileForm } from "./ProfileForm";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProfileForm extensions", () => {
  it("loads configured extensions and selects defaults", async () => {
    const extensionId = "a".repeat(32);
    vi.spyOn(api, "listExtensions").mockResolvedValue([
      {
        id: extensionId,
        name: "uBlock Origin Lite",
        version: "1.2.3",
        default: true,
        source: "chrome_web_store",
        cached: false,
      },
    ]);
    render(
      <ProfileForm
        profile={null}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onCancel={vi.fn()}
      />,
    );
    const checkbox = await screen.findByRole("checkbox", { name: /uBlock Origin Lite/ });
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    fireEvent.click(checkbox);
    expect((checkbox as HTMLInputElement).checked).toBe(false);
  });
});
