import type { Fault } from "./api/types";
import type { DisplayUnit, TimeMode } from "./store/robotStore";

const RAD_TO_DEG = 180 / Math.PI;

export function formatNumber(value: number | null | undefined, decimals = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return value.toFixed(decimals);
}

export function formatAngle(
  value: number | null | undefined,
  unit: DisplayUnit,
  decimals = 3,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "不可用";
  }
  const converted = unit === "deg" ? value * RAD_TO_DEG : value;
  return `${converted.toFixed(decimals)} ${unit}`;
}

export function formatAge(ageMs: number | null | undefined): string {
  if (ageMs === null || ageMs === undefined) {
    return "不可用";
  }
  if (ageMs < 1000) {
    return `${Math.round(ageMs)} ms`;
  }
  return `${(ageMs / 1000).toFixed(1)} s`;
}

export function formatTime(iso: string | null | undefined, mode: TimeMode): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString("zh-CN", {
    hour12: false,
    timeZone: mode === "utc" ? "UTC" : undefined,
  });
}

export function formatClock(iso: string | null | undefined, mode: TimeMode): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    timeZone: mode === "utc" ? "UTC" : undefined,
  });
}

export function faultRank(level: string): number {
  switch (level) {
    case "critical":
      return 4;
    case "fault":
      return 3;
    case "warning":
      return 2;
    case "info":
      return 1;
    default:
      return 0;
  }
}

export function highestFaultLevel(faults: Fault[]): string | null {
  if (faults.length === 0) {
    return null;
  }
  return faults.reduce((highest, fault) =>
    faultRank(fault.level) > faultRank(highest) ? fault.level : highest,
  faults[0].level);
}

export function maxFeedbackAge(joints: Array<{ sample_age_ms: number | null }>): number | null {
  const ages = joints
    .map((joint) => joint.sample_age_ms)
    .filter((age): age is number => age !== null && age !== undefined);
  return ages.length ? Math.max(...ages) : null;
}

export function isFaultStage(stage: string): boolean {
  return (
    stage === "Failed" ||
    stage === "Unknown" ||
    stage === "StopUnknown" ||
    stage === "FaultInjected"
  );
}

export function isWarningStage(stage: string): boolean {
  return stage === "StopDispatched" || stage === "StopRequested";
}
