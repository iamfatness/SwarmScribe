{{- define "swarmscribe-console.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "swarmscribe-console.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
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

{{/* The Ingress host: publicUrl without its scheme. publicUrl is an https origin with no
port and no path. */}}
{{- define "swarmscribe-console.host" -}}
{{- $url := required "publicUrl is required: the console's https origin, e.g. https://console.example.org" .Values.publicUrl }}
{{- if not (hasPrefix "https://" $url) }}
{{- fail "publicUrl must start with https://" }}
{{- end }}
{{- $host := trimPrefix "https://" $url }}
{{- if or (contains "/" $host) (contains ":" $host) (eq $host "") }}
{{- fail "publicUrl is an origin on the default port: https://<host>, with no port and no path" }}
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
{{- range $name, $_ := .Values.settings }}
{{- if or (has $name (list "DATABASE_URL" "KEY" "GOOGLE_SERVICE_ACCOUNT" "PUBLIC_URL" "LEADER_CA_FILE" "PORT")) (hasSuffix "_SECRET" $name) }}
{{- fail (printf "settings.%s is not set here: secrets come from secrets.existingSecret, and PUBLIC_URL, PORT and LEADER_CA_FILE from publicUrl, port and leaderCa" $name) }}
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
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.databaseUrl }}
- name: SWARMSCRIBE_CONSOLE_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.consoleKey }}
{{- if .Values.oidc.entra.enabled }}
- name: SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.entraClientSecret }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.googleClientSecret }}
{{- if .Values.oidc.google.serviceAccount }}
- name: SWARMSCRIBE_CONSOLE_GOOGLE_SERVICE_ACCOUNT
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.secrets.keys.googleServiceAccount }}
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
