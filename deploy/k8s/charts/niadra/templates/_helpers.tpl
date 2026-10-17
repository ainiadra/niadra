{{- define "niadra.labels" -}}
app.kubernetes.io/part-of: niadra
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- /* The server's tag: it changes with the cell's code, not with a release of the site or the chart. */}}
app.kubernetes.io/version: {{ required "image.tags.server is set at deploy" .Values.image.tags.server | quote }}
{{- end }}

{{- define "niadra.selector" -}}
app.kubernetes.io/name: {{ . }}
{{- end }}

{{- define "niadra.image" -}}
{{- $ctx := index . 0 -}}{{- $name := index . 1 -}}
{{ required "image.registry is set at deploy" $ctx.Values.image.registry }}/niadra/{{ $name }}:{{ required (printf "image.tags.%s is set at deploy" $name) (index $ctx.Values.image.tags $name) }}
{{- end }}

{{- define "niadra.resources" -}}
resources:
  requests: {cpu: {{ .cpu }}, memory: {{ .memory }}}
  limits: {memory: {{ .memoryLimit }}}
{{- end }}

{{/* Environment every cell process shares: the config map, then the cell's secrets. */}}
{{- define "niadra.cellEnv" -}}
envFrom:
  - configMapRef: {name: niadra-cell}
  - secretRef: {name: niadra-cell}
{{- end }}

{{- define "niadra.probes" -}}
readinessProbe:
  httpGet: {path: /healthz, port: http}
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:
  httpGet: {path: /healthz, port: http}
  initialDelaySeconds: 30
  periodSeconds: 15
  failureThreshold: 4
{{- end }}

{{- /* The HTTP deployables of the cell and the control plane: ready once the process warmed up (server,
entrypoints/warmup.py, 20 s at most), alive from the start. A new read pod took its first requests cold, and a
replica that started in a burst took the history search's p95 from 70 to 330 ms on 10/10/2026. A slow warm-up
delays readiness and never restarts the pod. */}}
{{- define "niadra.apiProbes" -}}
readinessProbe:
  httpGet: {path: /readyz, port: http}
  periodSeconds: 2
  failureThreshold: 3
livenessProbe:
  httpGet: {path: /healthz, port: http}
  initialDelaySeconds: 30
  periodSeconds: 15
  failureThreshold: 4
{{- end }}

{{- define "niadra.securityContext" -}}
securityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  capabilities: {drop: [ALL]}
  seccompProfile: {type: RuntimeDefault}
{{- end }}

{{/* Spread replicas over the machines once there is more than one, so losing one keeps the service up. */}}
{{- define "niadra.spread" -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels: {{- include "niadra.selector" . | nindent 8 }}
{{- end }}

{{/*
PgBouncer's configuration. Every database name maps to the
same instance, so the space databases (s_<space id>) need no entry of their own. Clients authenticate
with SCRAM against the userlist the pod renders from the roles' secrets, and PgBouncer logs in to the
database with the same passwords over TLS.
*/}}
{{- define "niadra.pgbouncerIni" -}}
[databases]
; The cell's and the control plane's databases serve every process and every space, so they get more
; server connections than a space's.
niadra_cell = host={{ .Values.database.host }} port=5432 pool_size={{ .Values.pgbouncer.cellDbConnections }} max_db_connections={{ .Values.pgbouncer.cellDbConnections }}
niadra_control = host={{ .Values.database.host }} port=5432 pool_size={{ .Values.pgbouncer.cellDbConnections }} max_db_connections={{ .Values.pgbouncer.cellDbConnections }}
* = host={{ required "database.host is set at deploy" .Values.database.host }} port=5432

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = {{ .Values.pgbouncer.port }}
unix_socket_dir =
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/auth/userlist.txt
; The cell pauses, resumes and empties a space database through the admin console (restore, deletion).
admin_users = niadra_owner
; `set_config(..., true)`, SET LOCAL and transaction advisory locks all live inside one transaction.
pool_mode = transaction
max_client_conn = {{ .Values.pgbouncer.maxClientConn }}
default_pool_size = {{ .Values.pgbouncer.defaultPoolSize }}
min_pool_size = 0
reserve_pool_size = 1
reserve_pool_timeout = 3
max_db_connections = {{ .Values.pgbouncer.maxDbConnections }}
server_idle_timeout = {{ .Values.pgbouncer.serverIdleTimeout }}
server_lifetime = 1800
query_wait_timeout = 10
server_tls_sslmode = {{ .Values.database.sslmode }}
ignore_startup_parameters = extra_float_digits
; Pooled clients connect and leave all the time; errors are still logged.
log_connections = 0
log_disconnections = 0
log_stats = 0

[users]
; The workers and the loops log in as niadra_worker (the application role under another name): their pool
; in each database is capped below the database's limit, so a space's last server connection is always
; free for the requests, and an acknowledgement never waits behind background work.
niadra_worker = pool_size={{ .Values.pgbouncer.workerDbConnections }}
{{- end }}

{{/* The site hosts, as the browser origins allowed to call the APIs. */}}
{{- define "niadra.origins" -}}
{{- $origins := list -}}
{{- range .Values.hosts.site }}{{ $origins = append $origins (printf "https://%s" .) }}{{ end -}}
{{- join "," $origins -}}
{{- end }}

{{/* The host of the site's admin panel: hosts.siteAdmin, or admin. and the first site host. */}}
{{- define "niadra.siteAdminHost" -}}
{{- .Values.hosts.siteAdmin | default (printf "admin.%s" (first .Values.hosts.site)) -}}
{{- end }}
