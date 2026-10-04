{{/* The app name label: the chart name, or nameOverride. */}}
{{- define "swarmscribe-console.name" -}}
{{- regexReplaceAll "-+$" (default .Chart.Name .Values.nameOverride | trunc 63) "" }}
{{- end }}

{{/* The base of every object name. At most 55 characters, so that the longest suffix the
chart appends (-migrate, -sign-in: 8) keeps every derived name within 63, the limit for a
Service name and for a label value (a Job's name becomes its pods' job-name label). A
release name has up to 53 characters and may hold dots, which a Service name may not. */}}
{{- define "swarmscribe-console.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if .Values.fullnameOverride }}
{{- regexReplaceAll "-+$" (.Values.fullnameOverride | trunc 55) "" }}
{{- else if contains $name .Release.Name }}
{{- regexReplaceAll "-+$" (.Release.Name | replace "." "-" | trunc 55) "" }}
{{- else }}
{{- regexReplaceAll "-+$" (printf "%s-%s" (.Release.Name | replace "." "-") $name | trunc 55) "" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-console.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "swarmscribe-console.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-console.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-console.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-console.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.image" -}}
{{- $repository := required "image.repository is required: the console image is not published; build it and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the console image is not published; build it and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* The Ingress host: publicUrl without its scheme. publicUrl is exactly
https://<lowercase DNS hostname>: no port, path, query, fragment, userinfo, uppercase
letters, spaces or IP literal. */}}
{{- define "swarmscribe-console.host" -}}
{{- $url := required "publicUrl is required: the console's https origin, e.g. https://console.example.org" .Values.publicUrl }}
{{- $host := trimPrefix "https://" $url }}
{{- if or (not (hasPrefix "https://" $url)) (not (regexMatch `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$` $host)) (gt (len $host) 253) (regexMatch `(^|\.)[0-9]+$` $host) }}
{{- fail (printf "publicUrl must be exactly https://<lowercase DNS hostname>: no port, path, query, fragment, user, uppercase letters or IP address (got %q)" $url) }}
{{- end }}
{{- $host }}
{{- end }}

{{/* Fails the render on values that would deploy a console that cannot start. */}}
{{- define "swarmscribe-console.validate" -}}
{{- $_ := include "swarmscribe-console.host" . }}
{{- $_ := required "secrets.existingSecret is required: the Secret holding the database URL and the console key" .Values.secrets.existingSecret }}
{{- if not (or .Values.oidc.entra.enabled .Values.oidc.google.enabled) }}
{{- fail "enable oidc.entra or oidc.google (or both): the console has no other sign-in" }}
{{- end }}
{{- /* Names the chart sets itself or reads from the Secret, without the prefix. The console
reads its environment case-insensitively, so every comparison is on the upper-cased name. */}}
{{- $owned := list "PUBLIC_URL" "PORT" "LEADER_CA_FILE" "ENTRA_TENANT_ID" "ENTRA_CLIENT_ID" "GOOGLE_CLIENT_ID" "GOOGLE_HOSTED_DOMAIN" }}
{{- $secret := list "DATABASE_URL" "KEY" "GOOGLE_SERVICE_ACCOUNT" }}
{{- range $name, $_ := .Values.settings }}
{{- $upper := upper $name }}
{{- if or (has $upper $secret) (hasSuffix "_SECRET" $upper) }}
{{- fail (printf "settings.%s is a secret: secrets come from secrets.existingSecret, never from values" $name) }}
{{- end }}
{{- if has $upper $owned }}
{{- fail (printf "settings.%s is set by the chart: use publicUrl, port, leaderCa or oidc" $name) }}
{{- end }}
{{- if not (regexMatch "^[A-Z][A-Z0-9_]*$" $name) }}
{{- fail (printf "settings key %q must match ^[A-Z][A-Z0-9_]*$ (an upper-case environment name without the SWARMSCRIBE_CONSOLE_ prefix)" $name) }}
{{- end }}
{{- end }}
{{- range .Values.extraEnv }}
{{- $upper := upper (toString .name) }}
{{- if hasPrefix "SWARMSCRIBE_CONSOLE_" $upper }}
{{- $base := trimPrefix "SWARMSCRIBE_CONSOLE_" $upper }}
{{- if or (has $base $secret) (hasSuffix "_SECRET" $base) (has $base $owned) }}
{{- fail (printf "extraEnv %s collides with a variable the chart owns or reads from the Secret: use secrets.existingSecret, publicUrl, port, leaderCa or oidc" .name) }}
{{- end }}
{{- end }}
{{- end }}
{{- range .Values.ingress.paths }}
{{- if and (eq .path "/") (ne .pathType "Exact") }}
{{- fail "ingress.paths must not hold a Prefix (or ImplementationSpecific) \"/\": it would also publish /healthz and /readyz, and /readyz tells an anonymous caller whether the database is up. List the console's routes instead (the default does)" }}
{{- end }}
{{- end }}
{{- if and .Values.ingress.enabled (not .Values.ingress.tls.secretName) }}
{{- fail "ingress.tls.secretName is required: the console is served over TLS only" }}
{{- end }}
{{- if and .Values.networkPolicy.enabled (not .Values.networkPolicy.egress.postgres.peers) }}
{{- fail "networkPolicy.egress.postgres.peers is required with the NetworkPolicy on: say where the console's Postgres is" }}
{{- end }}
{{- end }}

{{/* Non-secret settings as a YAML map of environment names to strings. */}}
{{- define "swarmscribe-console.settings" -}}
SWARMSCRIBE_CONSOLE_PUBLIC_URL: {{ .Values.publicUrl | quote }}
SWARMSCRIBE_CONSOLE_PORT: {{ .Values.port | toString | quote }}
{{- if .Values.oidc.entra.enabled }}
SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID: {{ required "oidc.entra.tenantId is required with oidc.entra.enabled" .Values.oidc.entra.tenantId | quote }}
SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID: {{ required "oidc.entra.clientId is required with oidc.entra.enabled" .Values.oidc.entra.clientId | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID: {{ required "oidc.google.clientId is required with oidc.google.enabled" .Values.oidc.google.clientId | quote }}
{{- with .Values.oidc.google.hostedDomain }}
SWARMSCRIBE_CONSOLE_GOOGLE_HOSTED_DOMAIN: {{ . | quote }}
{{- end }}
{{- end }}
{{- range $name, $value := .Values.settings }}
SWARMSCRIBE_CONSOLE_{{ $name }}: {{ $value | toString | quote }}
{{- end }}
{{- end }}

{{/* Secret settings, as container env entries reading the existing Secret. */}}
{{- define "swarmscribe-console.secretEnv" -}}
{{- $secret := .Values.secrets.existingSecret -}}
- name: SWARMSCRIBE_CONSOLE_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.databaseUrl | quote }}
- name: SWARMSCRIBE_CONSOLE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.consoleKey | quote }}
{{- if .Values.oidc.entra.enabled }}
- name: SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.entraClientSecret | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleClientSecret | quote }}
{{- if .Values.oidc.google.serviceAccount }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_SERVICE_ACCOUNT
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleServiceAccount | quote }}
{{- end }}
{{- end }}
{{- end }}

{{- define "swarmscribe-console.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-console.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Destinations the console refuses as leader addresses (README, "Deployment note:
egress"), cut out of 0.0.0.0/0: this network, Alibaba Cloud metadata, loopback, the Azure
platform address, link-local (cloud metadata), multicast and reserved. */}}
{{- define "swarmscribe-console.refusedV4" -}}
- 0.0.0.0/8
- 100.100.100.200/32
- 127.0.0.0/8
- 168.63.129.16/32
- 169.254.0.0/16
- 224.0.0.0/4
- 240.0.0.0/4
{{- end }}

{{/* The same for ::/0: loopback, IPv4-mapped and NAT64 (all inside ::/8), Teredo, 6to4,
the AWS IPv6 metadata address, link-local, site-local and multicast. */}}
{{- define "swarmscribe-console.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
