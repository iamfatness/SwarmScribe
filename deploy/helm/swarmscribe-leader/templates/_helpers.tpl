{{/* The app name label: the chart name, or nameOverride. */}}
{{- define "swarmscribe-leader.name" -}}
{{- regexReplaceAll "-+$" (default .Chart.Name .Values.nameOverride | trunc 63) "" }}
{{- end }}

{{/* The base of every object name. At most 55 characters, so that the suffix the chart
appends (-migrate: 8) keeps every derived name within 63, the limit for a Service name and
for a label value (a Job's name becomes its pods' job-name label). A release name has up to
53 characters and may hold dots, which a Service name may not. */}}
{{- define "swarmscribe-leader.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if .Values.fullnameOverride }}
{{- regexReplaceAll "-+$" (.Values.fullnameOverride | trunc 55) "" }}
{{- else if contains $name .Release.Name }}
{{- regexReplaceAll "-+$" (.Release.Name | replace "." "-" | trunc 55) "" }}
{{- else }}
{{- regexReplaceAll "-+$" (printf "%s-%s" (.Release.Name | replace "." "-") $name | trunc 55) "" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-leader.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-leader.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "swarmscribe-leader.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-leader.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-leader.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-leader.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-leader.image" -}}
{{- $repository := required "image.repository is required: the leader image is not published; build it and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the leader image is not published; build it and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* Whether any sign-in provider is on ("true" or ""). */}}
{{- define "swarmscribe-leader.signIn" -}}
{{- if or .Values.oidc.entra.enabled .Values.oidc.google.enabled }}true{{ end }}
{{- end }}

{{/* The host of publicUrl, which is also the Ingress host. publicUrl is exactly
https://<lowercase DNS hostname>[:port] (http:// only with allowHttpPublicUrl): no path,
query, fragment, userinfo, uppercase letters, spaces or IP literal. With the Ingress on it
is https and has no port. */}}
{{- define "swarmscribe-leader.host" -}}
{{- $url := required "publicUrl is required: the address followers and administrators reach the leader at, e.g. https://leader.example.org" .Values.publicUrl }}
{{- $https := hasPrefix "https://" $url }}
{{- if and (hasPrefix "http://" $url) (not .Values.allowHttpPublicUrl) }}
{{- fail (printf "publicUrl must be https:// (got %q): credentials and signed file links would cross the network in the clear. allowHttpPublicUrl: true accepts http:// on a test cluster" $url) }}
{{- end }}
{{- $rest := $url | trimPrefix "https://" | trimPrefix "http://" }}
{{- $host := regexReplaceAll ":[0-9]{1,5}$" $rest "" }}
{{- if or (not (or $https (hasPrefix "http://" $url))) (not (regexMatch `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$` $host)) (gt (len $host) 253) (regexMatch `(^|\.)[0-9]+$` $host) }}
{{- fail (printf "publicUrl must be exactly https://<lowercase DNS hostname>, with an optional :port: no path, query, fragment, user, uppercase letters or IP address (got %q)" $url) }}
{{- end }}
{{- if .Values.ingress.enabled }}
{{- if not $https }}
{{- fail (printf "publicUrl must be https:// with the Ingress on (got %q): the leader is published over TLS only" $url) }}
{{- end }}
{{- if ne $host $rest }}
{{- fail (printf "publicUrl must not carry a port with the Ingress on (got %q): an Ingress serves 443" $url) }}
{{- end }}
{{- end }}
{{- $host }}
{{- end }}

{{/* Fails the render on values that would deploy a leader that cannot start, cannot be
administered, or is cut off. */}}
{{- define "swarmscribe-leader.validate" -}}
{{- $_ := include "swarmscribe-leader.host" . }}
{{- $_ := required "secrets.existingSecret is required: the Secret holding the database URL and the link key" .Values.secrets.existingSecret }}
{{- $entra := .Values.oidc.entra.enabled }}
{{- $google := .Values.oidc.google.enabled }}
{{- if and (not (or $entra $google)) (not .Values.oidc.allowNone) }}
{{- fail "enable oidc.entra or oidc.google (or both): a leader without sign-in refuses every admin call, so nobody could create a pool token or a console credential. oidc.allowNone: true renders one anyway, for tests" }}
{{- end }}
{{- if and .Values.oidc.entra.clientSecret (not $entra) }}
{{- fail "oidc.entra.clientSecret needs oidc.entra.enabled" }}
{{- end }}
{{- if and .Values.oidc.google.serviceAccount (not $google) }}
{{- fail "oidc.google.serviceAccount needs oidc.google.enabled" }}
{{- end }}
{{- /* The same rules as the leader's own settings (config.py, _sign_in_is_complete), so a
bad mapping fails the render and not every pod at start-up. */}}
{{- range $role := list "viewer" "operator" "admin" }}
{{- $lists := index $.Values.roles $role | default dict }}
{{- if and $lists.entraGroups (not $entra) }}
{{- fail (printf "roles.%s.entraGroups needs oidc.entra.enabled" $role) }}
{{- end }}
{{- if and $lists.googleGroups (not $.Values.oidc.google.serviceAccount) }}
{{- fail (printf "roles.%s.googleGroups needs oidc.google.serviceAccount: the leader reads Google Groups with a service account" $role) }}
{{- end }}
{{- if and (or $lists.emails $lists.domains) (not $google) }}
{{- fail (printf "roles.%s.emails and .domains apply to Google sign-in: enable oidc.google" $role) }}
{{- end }}
{{- end }}
{{- $admin := .Values.roles.admin | default dict }}
{{- if and (or $entra $google) (not (or $admin.entraGroups $admin.googleGroups $admin.emails $admin.domains)) }}
{{- fail "roles.admin names nobody: with sign-in on, the first administrator is whoever roles.admin lists (a group, an email or a domain), and nothing else creates one" }}
{{- end }}
{{- /* Names the chart sets itself or reads from the Secret, without the prefix. The leader
reads its environment case-insensitively, so every comparison is on the upper-cased name. */}}
{{- $owned := list "PUBLIC_URL" "ENTRA_TENANT_ID" "ENTRA_CLIENT_ID" "GOOGLE_CLIENT_ID" "GOOGLE_HOSTED_DOMAIN" }}
{{- $secret := list "DATABASE_URL" "LINK_KEY" "GOOGLE_SERVICE_ACCOUNT" }}
{{- /* The twelve role lists (roles.*). ROLE_CACHE_SECONDS is not one: it goes in settings. */}}
{{- $roleList := "^ROLE_(VIEWER|OPERATOR|ADMIN)_" }}
{{- range $name, $_ := .Values.settings }}
{{- $upper := upper $name }}
{{- if or (has $upper $secret) (hasSuffix "_SECRET" $upper) }}
{{- fail (printf "settings.%s is a secret: secrets come from secrets.existingSecret, never from values" $name) }}
{{- end }}
{{- if or (has $upper $owned) (regexMatch $roleList $upper) }}
{{- fail (printf "settings.%s is set by the chart: use publicUrl, oidc or roles" $name) }}
{{- end }}
{{- if not (regexMatch "^[A-Z][A-Z0-9_]*$" $name) }}
{{- fail (printf "settings key %q must match ^[A-Z][A-Z0-9_]*$ (an upper-case environment name without the SWARMSCRIBE_ prefix)" $name) }}
{{- end }}
{{- end }}
{{- range .Values.extraEnv }}
{{- $upper := upper (toString .name) }}
{{- if hasPrefix "SWARMSCRIBE_" $upper }}
{{- $base := trimPrefix "SWARMSCRIBE_" $upper }}
{{- if or (has $base $secret) (hasSuffix "_SECRET" $base) (has $base $owned) (regexMatch $roleList $base) }}
{{- fail (printf "extraEnv %s collides with a variable the chart owns or reads from the Secret: use secrets.existingSecret, publicUrl, oidc or roles" .name) }}
{{- end }}
{{- end }}
{{- end }}
{{- if not .Values.storage.volumes }}
{{- fail "storage.volumes is required: the leader's only storage backend today is a folder on a filesystem, so without a volume no location can be added. Name a PersistentVolumeClaim you created (see values.yaml)" }}
{{- end }}
{{- $names := list }}
{{- $paths := list }}
{{- range .Values.storage.volumes }}
{{- if eq (empty .existingClaim) (empty .volume) }}
{{- fail (printf "storage.volumes %q needs exactly one of existingClaim and volume" .name) }}
{{- end }}
{{- if and .volume (hasKey .volume "emptyDir") }}
{{- fail (printf "storage.volumes %q is an emptyDir: every pod would get a folder of its own, and it is lost with the pod. Recordings need a volume every replica sees" .name) }}
{{- end }}
{{- $path := clean .mountPath }}
{{- if or (ne $path .mountPath) (eq $path "/") (eq $path "/app") (hasPrefix "/app/" $path) }}
{{- fail (printf "storage.volumes %q: mountPath %q must be a clean absolute path, not / and not under /app (the leader's own files)" .name .mountPath) }}
{{- end }}
{{- if or (has .name $names) (has $path $paths) }}
{{- fail (printf "storage.volumes %q: a name or mountPath is used twice" .name) }}
{{- end }}
{{- $names = append $names .name }}
{{- $paths = append $paths $path }}
{{- end }}
{{- range .Values.ingress.paths }}
{{- if or (eq .path "/") (hasPrefix "/healthz" .path) (hasPrefix "/readyz" .path) }}
{{- fail (printf "ingress.paths must not hold %q: \"/\" and the probes are never published (/readyz tells an anonymous caller whether the database is up). Everything the leader serves to the outside is under /v1" .path) }}
{{- end }}
{{- end }}
{{- if and .Values.ingress.enabled (not .Values.ingress.tls.secretName) }}
{{- fail "ingress.tls.secretName is required: the leader is published over TLS only" }}
{{- end }}
{{- if le (int .Values.terminationGracePeriodSeconds) (int .Values.preStopSleepSeconds) }}
{{- fail "terminationGracePeriodSeconds must be longer than preStopSleepSeconds: the leader would be killed before it is asked to stop" }}
{{- end }}
{{- if .Values.networkPolicy.enabled }}
{{- if not .Values.networkPolicy.egress.postgres.peers }}
{{- fail "networkPolicy.egress.postgres.peers is required with the NetworkPolicy on: say where the leader's Postgres is" }}
{{- end }}
{{- if and (not .Values.networkPolicy.ingress.from) (not .Values.networkPolicy.ingress.anySource) }}
{{- fail "networkPolicy.ingress.from is required with the NetworkPolicy on: say who may reach the leader (your ingress controller, the follower pools, a console), or set networkPolicy.ingress.anySource: true" }}
{{- end }}
{{- if and .Values.networkPolicy.ingress.from .Values.networkPolicy.ingress.anySource }}
{{- fail "networkPolicy.ingress.from and networkPolicy.ingress.anySource are both set: choose one" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Non-secret settings as a YAML map of environment names to strings. */}}
{{- define "swarmscribe-leader.settings" -}}
SWARMSCRIBE_PUBLIC_URL: {{ .Values.publicUrl | quote }}
{{- if .Values.oidc.entra.enabled }}
SWARMSCRIBE_ENTRA_TENANT_ID: {{ required "oidc.entra.tenantId is required with oidc.entra.enabled" .Values.oidc.entra.tenantId | quote }}
SWARMSCRIBE_ENTRA_CLIENT_ID: {{ required "oidc.entra.clientId is required with oidc.entra.enabled" .Values.oidc.entra.clientId | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
SWARMSCRIBE_GOOGLE_CLIENT_ID: {{ required "oidc.google.clientId is required with oidc.google.enabled" .Values.oidc.google.clientId | quote }}
{{- with .Values.oidc.google.hostedDomain }}
SWARMSCRIBE_GOOGLE_HOSTED_DOMAIN: {{ . | quote }}
{{- end }}
{{- end }}
{{- $kinds := dict "entraGroups" "ENTRA_GROUPS" "googleGroups" "GOOGLE_GROUPS" "emails" "EMAILS" "domains" "DOMAINS" }}
{{- range $role := list "viewer" "operator" "admin" }}
{{- $lists := index $.Values.roles $role | default dict }}
{{- range $key, $suffix := $kinds }}
{{- with index $lists $key }}
SWARMSCRIBE_ROLE_{{ upper $role }}_{{ $suffix }}: {{ join "," . | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- range $name, $value := .Values.settings }}
SWARMSCRIBE_{{ $name }}: {{ $value | toString | quote }}
{{- end }}
{{- end }}

{{/* Secret settings, as container env entries reading the existing Secret. */}}
{{- define "swarmscribe-leader.secretEnv" -}}
{{- $secret := .Values.secrets.existingSecret -}}
- name: SWARMSCRIBE_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.databaseUrl | quote }}
- name: SWARMSCRIBE_LINK_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.linkKey | quote }}
{{- if and .Values.oidc.entra.enabled .Values.oidc.entra.clientSecret }}
- name: SWARMSCRIBE_ENTRA_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.entraClientSecret | quote }}
{{- end }}
{{- if .Values.oidc.google.enabled }}
- name: SWARMSCRIBE_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleClientSecret | quote }}
{{- if .Values.oidc.google.serviceAccount }}
- name: SWARMSCRIBE_GOOGLE_SERVICE_ACCOUNT
  valueFrom:
    secretKeyRef:
      name: {{ $secret | quote }}
      key: {{ .Values.secrets.keys.googleServiceAccount | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/* The leader pods. fsGroup: the storage volumes are opened with the leader's group. */}}
{{- define "swarmscribe-leader.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: {{ .Values.storage.fsGroup }}
fsGroupChangePolicy: OnRootMismatch
{{- with .Values.storage.supplementalGroups }}
supplementalGroups:
  {{- toYaml . | nindent 2 }}
{{- end }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* The migration Job's pod: it mounts no volume, so it has no fsGroup. */}}
{{- define "swarmscribe-leader.jobSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-leader.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Destinations no identity provider lives at, cut out of 0.0.0.0/0: this network,
Alibaba Cloud metadata, loopback, the Azure platform address, link-local (cloud metadata),
multicast and reserved. The same list as the console's and the follower's charts. */}}
{{- define "swarmscribe-leader.refusedV4" -}}
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
{{- define "swarmscribe-leader.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
