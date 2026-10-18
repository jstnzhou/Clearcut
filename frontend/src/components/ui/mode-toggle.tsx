"use client";

import React from "react";
import { Lasso, SquareDashed, Upload } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export type IntakeMode = "box" | "shape" | "upload";

const MODE_OPTIONS = [
  {
    icon: SquareDashed,
    value: "box" as const,
    label: "Draw box",
    description: "Drag a rectangle around the plot",
  },
  {
    icon: Lasso,
    value: "shape" as const,
    label: "Draw shape",
    description: "Freehand-drag any property boundary",
  },
  {
    icon: Upload,
    value: "upload" as const,
    label: "Upload",
    description: "GeoJSON, KML, or GPX",
  },
];

type ModeToggleProps = {
  value?: IntakeMode;
  defaultValue?: IntakeMode;
  onValueChange?: (value: IntakeMode) => void;
  className?: string;
};

export function ModeToggle({
  value,
  defaultValue = "box",
  onValueChange,
  className,
}: ModeToggleProps) {
  const [uncontrolled, setUncontrolled] = React.useState<IntakeMode>(defaultValue);
  const current = value ?? uncontrolled;

  function select(next: IntakeMode) {
    if (value === undefined) setUncontrolled(next);
    onValueChange?.(next);
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className={cn(
        "bg-muted/80 inline-flex items-center overflow-hidden rounded-md border",
        className,
      )}
      role="radiogroup"
      aria-label="Property intake mode"
    >
      {MODE_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          title={option.description}
          className={cn(
            "relative flex h-8 min-w-8 cursor-pointer items-center justify-center gap-1.5 rounded-md px-2 transition-all",
            current === option.value
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
          role="radio"
          aria-checked={current === option.value}
          aria-label={option.label}
          onClick={() => select(option.value)}
        >
          {current === option.value && (
            <motion.div
              layoutId="intake-mode-option"
              transition={{ type: "spring", bounce: 0.1, duration: 0.75 }}
              className="border-muted-foreground/50 absolute inset-0 rounded-md border"
            />
          )}
          <option.icon className="relative size-3.5" />
          <span className="relative hidden text-xs font-medium sm:inline">
            {option.label}
          </span>
        </button>
      ))}
    </motion.div>
  );
}

export { MODE_OPTIONS };
