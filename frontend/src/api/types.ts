export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export type DeviceType =
  | "unknown"
  | "router"
  | "computer"
  | "phone"
  | "server"
  | "iot"
  | "media"
  | "printer"
  | "other";

export interface Device {
  id: number;
  mac: string;
  ip: string | null;
  hostname: string;
  vendor: string;
  label: string;
  display_name: string;
  device_type: DeviceType;
  device_type_display: string;
  notes: string;
  is_known: boolean;
  is_online: boolean;
  is_random_mac: boolean;
  missed_scans: number;
  first_seen: string;
  last_seen: string;
  source: "lan" | "agent";
  open_alerts: number;
}

export type DeviceUpdate = Partial<Pick<Device, "label" | "device_type" | "notes" | "is_known">>;

export type AlertKind = "new_device" | "new_random_mac" | "device_offline" | "host_offline" | "check_failed";
export type Severity = "info" | "warning" | "critical";

export interface Alert {
  id: number;
  kind: AlertKind;
  kind_display: string;
  severity: Severity;
  message: string;
  created_at: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  notified: boolean;
  suppressed: boolean;
  device: number | null;
  remote_host: number | null;
  subject_name: string;
}

export type EventType = "first_seen" | "online" | "offline" | "ip_changed" | "new_device";

export interface DeviceEvent {
  id: number;
  device: number;
  device_name: string;
  event_type: EventType;
  event_type_display: string;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface ScanRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  subnet: string;
  devices_found: number;
  status: "running" | "success" | "failed";
  error: string;
}

export interface Stats {
  devices: { total: number; online: number; unknown: number; unknown_online: number };
  open_alerts: number;
  last_scan: ScanRun | null;
  last_successful_scan_at: string | null;
  scan_interval_minutes: number;
  scan_requested: boolean;
  scanners: ScannerSummary[];
  subnet: string | null;
}

export interface ScanQueued {
  queued: true;
  after_scan_id: number | null;
}

export interface Session {
  authenticated: boolean;
  user: { username: string; is_staff: boolean } | null;
}

export interface Choice<T extends string = string> {
  value: T;
  label: string;
}

export interface Meta {
  device_types: Choice<DeviceType>[];
  alert_kinds: Choice<AlertKind>[];
}

export interface ApiToken {
  id: number;
  name: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreatedApiToken extends ApiToken {
  token: string;
}

export type HostStatus = "unknown" | "online" | "offline";
export type CheckKind = "ping" | "tcp" | "http";

export interface HostCheck {
  id: number;
  host: number;
  kind: CheckKind;
  target: string;
  port: number | null;
  interval_seconds: number;
  timeout_seconds: number;
  failure_threshold: number;
  enabled: boolean;
  description: string;
  status: "unknown" | "up" | "down";
  consecutive_failures: number;
  last_checked_at: string | null;
  last_latency_ms: number | null;
  last_error: string;
}

export type HostCheckInput = Pick<HostCheck, "host" | "kind" | "target"> &
  Partial<Pick<HostCheck, "port" | "interval_seconds" | "timeout_seconds" | "failure_threshold" | "enabled">>;

export interface RemoteHost {
  id: number;
  name: string;
  is_scanner: boolean;
  scan_subnet: string;
  is_active: boolean;
  expected_interval_seconds: number;
  status: HostStatus;
  last_checkin: string | null;
  last_ip: string | null;
  agent_version: string;
  reported_hostname: string;
  cpu_percent: number | null;
  mem_percent: number | null;
  disk_percent: number | null;
  uptime_seconds: number | null;
  token_prefix: string;
  created_at: string;
  checks: HostCheck[];
  open_alerts: number;
}

export type RemoteHostInput = Partial<
  Pick<RemoteHost, "name" | "is_scanner" | "scan_subnet" | "is_active" | "expected_interval_seconds">
>;

export interface CreatedRemoteHost extends RemoteHost {
  token: string;
}

export interface HostEvent {
  id: number;
  host: number;
  event_type: "online" | "offline" | "check_failed" | "check_recovered";
  event_type_display: string;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface ScannerSummary {
  id: number;
  name: string;
  status: HostStatus;
  last_checkin: string | null;
}
