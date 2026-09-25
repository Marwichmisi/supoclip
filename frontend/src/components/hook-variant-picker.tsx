"use client";

import { useEffect, useState } from "react";
import { Check, Edit2, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface HookVariantPickerProps {
  variants: string[];
  selectedIndex: number | null;
  currentTitle?: string | null;
  disabled?: boolean;
  onSelect: (index: number) => Promise<boolean>;
  onSaveTitle: (title: string) => Promise<boolean>;
}

export function HookVariantPicker({
  variants,
  selectedIndex,
  currentTitle,
  disabled = false,
  onSelect,
  onSaveTitle,
}: HookVariantPickerProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!isEditing) {
      setDraft(
        selectedIndex !== null ? variants[selectedIndex] || "" : currentTitle || "",
      );
    }
  }, [currentTitle, isEditing, selectedIndex, variants]);

  const choose = async (index: number) => {
    if (disabled || pending) return;
    setPending(true);
    try {
      const saved = await onSelect(index);
      if (saved) setIsEditing(false);
    } finally {
      setPending(false);
    }
  };

  const save = async () => {
    const title = draft.trim();
    if (!title || disabled || pending) return;
    setPending(true);
    try {
      const saved = await onSaveTitle(title);
      if (saved) setIsEditing(false);
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3" aria-label="Hook variants">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Hook variants</p>
        {!isEditing && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            disabled={disabled || pending}
            onClick={() => {
              setDraft(
                selectedIndex !== null ? variants[selectedIndex] || "" : currentTitle || "",
              );
              setIsEditing(true);
            }}
          >
            <Edit2 className="mr-1 h-3 w-3" />
            Edit title
          </Button>
        )}
      </div>

      {variants.length === 0 ? (
        <p className="text-xs text-neutral-500">No generated variant available.</p>
      ) : (
        <div className="grid gap-1.5">
          {variants.map((variant, index) => {
            const selected = selectedIndex === index;
            return (
              <button
                key={`${variant}-${index}`}
                type="button"
                disabled={disabled || pending}
                aria-pressed={selected}
                onClick={() => void choose(index)}
                className={`flex min-h-9 items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors ${
                  selected
                    ? "border-neutral-900 bg-neutral-900 text-white"
                    : "border-neutral-200 bg-white text-neutral-700 hover:border-neutral-400"
                }`}
              >
                <span className="min-w-0 truncate">{variant}</span>
                {selected && <Check className="h-3.5 w-3.5 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}

      {isEditing && (
        <div className="mt-2 flex gap-2">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void save();
              }
            }}
            placeholder="Write a hook title"
            aria-label="Hook title"
            disabled={disabled || pending}
            className="h-8 text-xs"
          />
          <Button type="button" size="sm" className="h-8" disabled={!draft.trim() || pending} onClick={() => void save()}>
            {pending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : "Save"}
          </Button>
        </div>
      )}
    </section>
  );
}
