import { useEffect } from "react";
import { api, ApiError } from "../api/client";
import { useRobotStore } from "../store/robotStore";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export function useRobotStream(): void {
  useEffect(() => {
    let disposed = false;
    let stateWs: WebSocket | null = null;
    let eventWs: WebSocket | null = null;
    let stateRetry: number | undefined;
    let eventRetry: number | undefined;
    let pollTimer: number | undefined;
    let diagnosticsTimer: number | undefined;

    const store = useRobotStore.getState();

    const refreshDiagnostics = async () => {
      try {
        const diagnostics = await api.diagnostics();
        if (!disposed) {
          store.setDiagnostics(diagnostics);
        }
      } catch (error) {
        if (!disposed) {
          store.setError(errorMessage(error));
        }
      }
    };

    const refresh = async () => {
      try {
        const [state, diagnostics, events] = await Promise.all([
          api.state(),
          api.diagnostics(),
          api.events({ limit: 200 }),
        ]);
        if (disposed) {
          return;
        }
        store.setState(state);
        store.setDiagnostics(diagnostics);
        store.setEvents(events.items);
        store.setError(null);
        store.setLoading(false);
      } catch (error) {
        if (!disposed) {
          store.setError(errorMessage(error));
          store.setLoading(false);
        }
      }
    };

    const connectState = () => {
      if (disposed) {
        return;
      }
      stateWs = new WebSocket(api.wsUrl("/api/ws/state"));
      stateWs.onopen = () => {
        store.setWsConnected(true);
      };
      stateWs.onmessage = (message) => {
        try {
          store.setState(api.parseState(JSON.parse(String(message.data))));
          store.setError(null);
        } catch (error) {
          store.setError(errorMessage(error));
        }
      };
      stateWs.onerror = () => {
        stateWs?.close();
      };
      stateWs.onclose = () => {
        store.setWsConnected(false);
        if (!disposed) {
          stateRetry = window.setTimeout(connectState, 1500);
        }
      };
    };

    const connectEvents = () => {
      if (disposed) {
        return;
      }
      eventWs = new WebSocket(api.wsUrl("/api/ws/events"));
      eventWs.onmessage = (message) => {
        try {
          store.addEvents([api.parseEvent(JSON.parse(String(message.data)))]);
        } catch (error) {
          store.setError(errorMessage(error));
        }
      };
      eventWs.onerror = () => {
        eventWs?.close();
      };
      eventWs.onclose = () => {
        if (!disposed) {
          eventRetry = window.setTimeout(connectEvents, 1500);
        }
      };
    };

    void refresh();
    connectState();
    connectEvents();

    pollTimer = window.setInterval(() => {
      if (!stateWs || stateWs.readyState !== WebSocket.OPEN) {
        void refresh();
      }
    }, 3000);
    diagnosticsTimer = window.setInterval(() => {
      void refreshDiagnostics();
    }, 10000);

    return () => {
      disposed = true;
      if (stateRetry !== undefined) window.clearTimeout(stateRetry);
      if (eventRetry !== undefined) window.clearTimeout(eventRetry);
      if (pollTimer !== undefined) window.clearInterval(pollTimer);
      if (diagnosticsTimer !== undefined) window.clearInterval(diagnosticsTimer);
      stateWs?.close();
      eventWs?.close();
    };
  }, []);
}
