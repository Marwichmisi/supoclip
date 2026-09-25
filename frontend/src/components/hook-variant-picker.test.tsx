import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { HookVariantPicker } from "./hook-variant-picker";

describe("HookVariantPicker", () => {
  it("selects one of the three generated variants", async () => {
    const onSelect = vi.fn().mockResolvedValue(true);
    render(
      <HookVariantPicker
        variants={["Variante A", "Variante B", "Variante C"]}
        selectedIndex={0}
        onSelect={onSelect}
        onSaveTitle={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Variante B/ }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(1));
  });

  it("edits and saves a free-form hook title", async () => {
    const onSaveTitle = vi.fn().mockResolvedValue(true);
    render(
      <HookVariantPicker
        variants={["Variante A", "Variante B", "Variante C"]}
        selectedIndex={0}
        onSelect={vi.fn()}
        onSaveTitle={onSaveTitle}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Edit title/ }));
    const input = screen.getByRole("textbox", { name: "Hook title" });
    fireEvent.change(input, { target: { value: "Mon titre corrigé" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSaveTitle).toHaveBeenCalledWith("Mon titre corrigé"));
  });
});
