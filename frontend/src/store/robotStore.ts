import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Diagnostics, EventRecord, Lease, RobotState } from "../api/types";

interface RobotStore {
  state: RobotState | null;
  events: EventRecord[];
  diagnostics: Diagnostics | null;
  lease: Lease | null;
  loading: boolean;
  error: string | null;
  wsConnected: boolean;
  replayActive: boolean;
  setState: (state: RobotState) => void;
  setEvents: (events: EventRecord[]) => void;
  addEvents: (events: EventRecord[]) => void;
  setDiagnostics: (diagnostics: Diagnostics | null) => void;
  setLease: (lease: Lease | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setWsConnected: (connected: boolean) => void;
  setReplayActive: (active: boolean) => void;
}

export const useRobotStore = create<RobotStore>((set) => ({
  state: null,
  events: [],
  diagnostics: null,
  lease: null,
  loading: true,
  error: null,
  wsConnected: false,
  replayActive: false,
  setState: (state) => set({ state }),
  setEvents: (events) => set({ events }),
  addEvents: (incoming) =>
    set((current) => {
      const byId = new Map<string, EventRecord>();
      for (const event of [...current.events, ...incoming]) {
        byId.set(event.event_id, event);
      }
      return {
        events: Array.from(byId.values())
          .sort((a, b) => b.seq - a.seq)
          .slice(0, 500),
      };
    }),
  setDiagnostics: (diagnostics) => set({ diagnostics }),
  setLease: (lease) => set({ lease }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setWsConnected: (wsConnected) => set({ wsConnected }),
  setReplayActive: (replayActive) => set({ replayActive }),
}));

export type DisplayUnit = "rad" | "deg";
export type TimeMode = "local" | "utc";
export type Density = "compact" | "comfortable";

interface Preferences {
  unit: DisplayUnit;
  decimals: number;
  timeMode: TimeMode;
  density: Density;
  setUnit: (unit: DisplayUnit) => void;
  setDecimals: (decimals: number) => void;
  setTimeMode: (mode: TimeMode) => void;
  setDensity: (density: Density) => void;
}

export const usePreferences = create<Preferences>()(
  persist(
    (set) => ({
      unit: "rad",
      decimals: 3,
      timeMode: "local",
      density: "compact",
      setUnit: (unit) => set({ unit }),
      setDecimals: (decimals) => set({ decimals }),
      setTimeMode: (timeMode) => set({ timeMode }),
      setDensity: (density) => set({ density }),
    }),
    { name: "armpi.preferences" },
  ),
);
