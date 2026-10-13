import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  Alert,
  ApiToken,
  CreatedApiToken,
  Device,
  DeviceEvent,
  DeviceUpdate,
  Meta,
  Paginated,
  Session,
  Stats,
} from "./types";

/** Polling interval for live-ish data until WebSockets arrive (Phase 7). */
export const REFRESH_MS = 15_000;

export const keys = {
  session: ["session"] as const,
  stats: ["stats"] as const,
  meta: ["meta"] as const,
  devices: (filters?: DeviceFilters) => ["devices", filters ?? {}] as const,
  device: (id: number) => ["device", id] as const,
  deviceEvents: (id: number) => ["device", id, "events"] as const,
  alerts: (filters?: AlertFilters) => ["alerts", filters ?? {}] as const,
  tokens: ["tokens"] as const,
};

// --- Session ------------------------------------------------------------------------

export function useSession() {
  return useQuery({
    queryKey: keys.session,
    queryFn: () => api<Session>("auth/session/"),
    staleTime: Infinity,
    retry: false,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      api<Session>("auth/login/", { method: "POST", body: credentials }),
    onSuccess: (session) => {
      qc.clear();
      qc.setQueryData(keys.session, session);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("auth/logout/", { method: "POST" }),
    onSettled: () => {
      qc.clear();
      qc.setQueryData<Session>(keys.session, { authenticated: false, user: null });
    },
  });
}

// --- Stats / meta ---------------------------------------------------------------------

export function useStats() {
  return useQuery({ queryKey: keys.stats, queryFn: () => api<Stats>("stats/"), refetchInterval: REFRESH_MS });
}

export function useMeta() {
  return useQuery({ queryKey: keys.meta, queryFn: () => api<Meta>("meta/"), staleTime: Infinity });
}

// --- Devices ----------------------------------------------------------------------------

export interface DeviceFilters {
  status?: "online" | "offline";
  known?: boolean;
  search?: string;
}

export function useDevices(filters: DeviceFilters) {
  return useQuery({
    queryKey: keys.devices(filters),
    queryFn: () => api<Paginated<Device>>("devices/", { params: { ...filters, page_size: 500 } }),
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData,
  });
}

export function useDevice(id: number) {
  return useQuery({
    queryKey: keys.device(id),
    queryFn: () => api<Device>(`devices/${id}/`),
    refetchInterval: REFRESH_MS,
  });
}

export function useDeviceEvents(id: number) {
  return useQuery({
    queryKey: keys.deviceEvents(id),
    queryFn: () => api<Paginated<DeviceEvent>>(`devices/${id}/events/`, { params: { page_size: 100 } }),
    refetchInterval: REFRESH_MS,
  });
}

function useInvalidateAll() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      [["devices"], ["device"], ["alerts"], keys.stats].map((queryKey) => qc.invalidateQueries({ queryKey })),
    );
}

export function useUpdateDevice(id: number) {
  const qc = useQueryClient();
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (changes: DeviceUpdate) => api<Device>(`devices/${id}/`, { method: "PATCH", body: changes }),
    onSuccess: (device) => {
      qc.setQueryData(keys.device(id), device);
      return invalidate();
    },
  });
}

export function useMarkKnown() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (ids: number[]) =>
      api<{ marked: number; alerts_acknowledged: number }>("devices/mark-known/", {
        method: "POST",
        body: { ids },
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteDevice() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (id: number) => api<void>(`devices/${id}/`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

// --- Alerts -----------------------------------------------------------------------------

export interface AlertFilters {
  acknowledged?: boolean;
  device?: number;
}

export function useAlerts(filters: AlertFilters = {}) {
  return useQuery({
    queryKey: keys.alerts(filters),
    queryFn: () => api<Paginated<Alert>>("alerts/", { params: { ...filters } }),
    refetchInterval: REFRESH_MS,
    placeholderData: keepPreviousData,
  });
}

export function useAcknowledgeAlert() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (id: number) => api<Alert>(`alerts/${id}/acknowledge/`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useAcknowledgeAll() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: () => api<{ acknowledged: number }>("alerts/acknowledge-all/", { method: "POST" }),
    onSuccess: invalidate,
  });
}

// --- API tokens --------------------------------------------------------------------------

export function useTokens() {
  return useQuery({ queryKey: keys.tokens, queryFn: () => api<ApiToken[]>("tokens/") });
}

export function useCreateToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api<CreatedApiToken>("tokens/", { method: "POST", body: { name } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.tokens }),
  });
}

export function useDeleteToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`tokens/${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.tokens }),
  });
}
