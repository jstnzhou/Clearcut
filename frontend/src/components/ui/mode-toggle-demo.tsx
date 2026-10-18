"use client";

import { useState } from "react";
import { ModeToggle, type IntakeMode } from "@/components/ui/mode-toggle";
import { ToggleTheme } from "@/components/ui/toggle-theme";
import { cn } from "@/lib/utils";

const COPY: Record<IntakeMode, string> = {
  box: "Drag a rectangle around the submitted plot.",
  shape: "Freehand-drag any shape around the property boundary.",
  upload: "Upload or paste GeoJSON, KML, or GPX.",
};

export default function ModeToggleDemo() {
  const [mode, setMode] = useState<IntakeMode>("box");

  return (
    <div className="relative flex min-h-screen w-full flex-col items-center justify-center gap-8 px-6">
      <div
        aria-hidden="true"
        className={cn(
          "absolute inset-0 -z-10 size-full",
          "bg-[radial-gradient(color-mix(in_oklab,hsl(var(--foreground)/.1)_30%,transparent)_2px,transparent_2px)]",
          "bg-[size:12px_12px]",
        )}
      />
      <div className="absolute right-4 top-4">
        <ToggleTheme />
      </div>
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <p className="text-sm uppercase tracking-[0.18em] text-muted-foreground">
          Clearcut intake
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Choose how to submit a plot</h1>
        <ModeToggle value={mode} onValueChange={setMode} />
        <p className="text-sm text-muted-foreground">{COPY[mode]}</p>
        <a
          className="text-sm underline underline-offset-4 hover:text-foreground"
          href="http://127.0.0.1:8765/web/"
        >
          Open map viewer
        </a>
      </div>
    </div>
  );
}
