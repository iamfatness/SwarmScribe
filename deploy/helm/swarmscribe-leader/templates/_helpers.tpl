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
{{- $port := trimPrefix (printf "%s:" $host) $rest }}
{{- if or (not (or $https (hasPrefix "http://" $url))) (not (regexMatch `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$` $host)) (gt (len $host) 253) (regexMatch `(^|\.)[0-9]+$` $host) }}
{{- fail (printf "publicUrl must be exactly https://<lowercase DNS hostname>, with an optional :port: no path, query, fragment, user, uppercase letters or IP address (got %q)" $url) }}
{{- end }}
{{- if and (ne $host $rest) (or (lt (atoi $port) 1) (gt (atoi $port) 65535)) }}
{{- fail (printf "publicUrl: %s is not a port (1 to 65535) (got %q)" $port $url) }}
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
{{- /* `settings` takes the names of swarmscribe-leader.settingNames and no other: a secret
(they come from the Secret), a name another value sets (publicUrl, oidc, roles) and a
misspelt name (the leader ignores a variable it does not know, so it would be silently
without effect) are all refused. The schema says the same; this is the second line. */}}
{{- $allowed := splitList " " (include "swarmscribe-leader.settingNames" .) }}
{{- range $name, $value := .Values.settings }}
{{- if not (has $name $allowed) }}
{{- fail (printf "settings.%s is not a setting the chart passes on. settings takes %s. Secrets come from secrets.existingSecret; publicUrl, oidc and roles set the others" $name (join ", " $allowed)) }}
{{- end }}
{{- if not (or (kindIs "string" $value) (kindIs "float64" $value) (kindIs "int64" $value) (kindIs "int" $value)) }}
{{- fail (printf "settings.%s must be a number or a string (got %s)" $name (kindOf $value)) }}
{{- end }}
{{- end }}
{{- /* The leader's own two rules across settings (config.py), with its defaults for what is
not set: a leader that broke one would refuse to start, and the migration Job with it. */}}
{{- $settings := .Values.settings | default dict }}
{{- $lease := atoi (include "swarmscribe-leader.settingValue" (get $settings "LEASE_SECONDS" | default 120)) }}
{{- $heartbeat := atoi (include "swarmscribe-leader.settingValue" (get $settings "HEARTBEAT_SECONDS" | default 30)) }}
{{- $gone := atoi (include "swarmscribe-leader.settingValue" (get $settings "FOLLOWER_GONE_AFTER_SECONDS" | default 600)) }}
{{- if ge $heartbeat $lease }}
{{- fail (printf "settings: HEARTBEAT_SECONDS (%d) must be shorter than LEASE_SECONDS (%d); the leader's defaults are 30 and 120" $heartbeat $lease) }}
{{- end }}
{{- if le $gone $lease }}
{{- fail (printf "settings: FOLLOWER_GONE_AFTER_SECONDS (%d) must be longer than LEASE_SECONDS (%d); the leader's defaults are 600 and 120" $gone $lease) }}
{{- end }}
{{- /* extraEnv is for what is not a setting of the leader (a proxy, a CA bundle's path). The
leader reads its environment case-insensitively, so the prefix is compared in upper case. */}}
{{- $extra := list }}
{{- range .Values.extraEnv }}
{{- if hasPrefix "SWARMSCRIBE_" (upper (toString .name)) }}
{{- fail (printf "extraEnv %s is a setting of the leader: secrets come from secrets.existingSecret, and publicUrl, oidc, roles and settings set the others (those reach the migration Job too; extraEnv does not)" .name) }}
{{- end }}
{{- if has .name $extra }}
{{- fail (printf "extraEnv %s is listed twice" .name) }}
{{- end }}
{{- if and (hasKey . "value") (hasKey . "valueFrom") }}
{{- fail (printf "extraEnv %s has both value and valueFrom" .name) }}
{{- end }}
{{- $extra = append $extra .name }}
{{- end }}
{{- /* The chart's own labels and its checksum are not the operator's to replace: a changed
selector label fails at the API server, after the migration hook has already run. */}}
{{- range $key, $_ := .Values.podLabels }}
{{- if or (hasPrefix "app.kubernetes.io/" $key) (eq $key "helm.sh/chart") }}
{{- fail (printf "podLabels must not set %s: the chart sets it, and the Deployment's selector, the Service and the NetworkPolicy match on it" $key) }}
{{- end }}
{{- end }}
{{- if hasKey .Values.podAnnotations "checksum/settings" }}
{{- fail "podAnnotations must not set checksum/settings: the chart sets it, so that a changed setting restarts the pods" }}
{{- end }}
{{- if not .Values.storage.volumes }}
{{- fail "storage.volumes is required: the leader's only storage backend today is a folder on a filesystem, so without a volume no location can be added. Name a PersistentVolumeClaim you created (see values.yaml)" }}
{{- end }}
{{- $names := list }}
{{- $paths := list }}
{{- range $entry := .Values.storage.volumes }}
{{- if eq (empty $entry.existingClaim) (empty $entry.volume) }}
{{- fail (printf "storage.volumes %q needs exactly one of existingClaim and volume" $entry.name) }}
{{- end }}
{{- /* A volume source is one of three kinds that can hold files every replica sees. The
others are a folder per pod (emptyDir, ephemeral), a folder per node (hostPath), or not
storage at all (secret, configMap, projected, downwardAPI: a projected volume could also put
a service-account token into a pod the chart says has none). */}}
{{- range $kind, $_ := ($entry.volume | default dict) }}
{{- if eq $kind "emptyDir" }}
{{- fail (printf "storage.volumes %q is an emptyDir: every pod would get a folder of its own, and it is lost with the pod. Recordings need a volume every replica sees" $entry.name) }}
{{- else if eq $kind "ephemeral" }}
{{- fail (printf "storage.volumes %q is an ephemeral volume: every pod would get a volume of its own, and it is lost with the pod. Recordings need a volume every replica sees" $entry.name) }}
{{- else if eq $kind "hostPath" }}
{{- fail (printf "storage.volumes %q is a hostPath: a folder of one node, so pods on two nodes would see different files, and it opens the node's own filesystem to the pod. Use a claim, nfs or csi" $entry.name) }}
{{- else if not (has $kind (list "persistentVolumeClaim" "nfs" "csi")) }}
{{- fail (printf "storage.volumes %q: a volume of kind %s cannot hold the recordings. Use existingClaim, or a volume of kind persistentVolumeClaim, nfs or csi" $entry.name $kind) }}
{{- end }}
{{- end }}
{{- $path := clean $entry.mountPath }}
{{- if or (ne $path $entry.mountPath) (eq $path "/") }}
{{- fail (printf "storage.volumes %q: mountPath %q must be a clean absolute path, and not /" $entry.name $entry.mountPath) }}
{{- end }}
{{- range $root := list "/app" "/bin" "/boot" "/dev" "/etc" "/lib" "/lib64" "/proc" "/run" "/sbin" "/sys" "/tmp" "/usr" }}
{{- if or (eq $path $root) (hasPrefix (printf "%s/" $root) $path) }}
{{- fail (printf "storage.volumes %q: mountPath %q is %s or under it, which is the image's own (the leader's files are in /app, and it needs no /tmp). Mount storage somewhere of its own, for example /data" $entry.name $entry.mountPath $root) }}
{{- end }}
{{- end }}
{{- with $entry }}
{{- if or (has .name $names) (has $path $paths) }}
{{- fail (printf "storage.volumes %q: a name or mountPath is used twice" .name) }}
{{- end }}
{{- $names = append $names .name }}
{{- $paths = append $paths $path }}
{{- end }}
{{- end }}
{{- /* Every published path is /v1 or under it, matched as Exact or Prefix. Nothing else can
then reach "/" or a probe: not another prefix, and not a pattern (ImplementationSpecific is
whatever the controller makes of it: "/*" is everything on some). */}}
{{- range .Values.ingress.paths }}
{{- $path := toString .path }}
{{- if or (not (regexMatch "^/v1(/|$)" $path)) (contains ".." $path) (contains "//" $path) (not (has .pathType (list "Exact" "Prefix"))) }}
{{- fail (printf "ingress.paths must not hold %q as %s: every path is /v1 or under it, as Exact or Prefix. \"/\" and the probes are never published (/readyz tells an anonymous caller whether the database is up), and everything the leader serves to the outside is under /v1" $path (toString .pathType)) }}
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
{{- /* In a NetworkPolicy rule an empty list of peers means every address and an empty list
of ports means every port: an empty value would open the policy, not close it. */}}
{{- if not .Values.networkPolicy.egress.dns.peers }}
{{- fail "networkPolicy.egress.dns.peers is empty: a rule without peers allows port 53 to every address. Name the cluster's DNS (the default is kube-dns in kube-system)" }}
{{- end }}
{{- if not .Values.networkPolicy.egress.https.cidrs }}
{{- fail "networkPolicy.egress.https.cidrs is empty: a rule without peers allows every address, the cloud metadata addresses included. Name the ranges the identity providers are in (a leader without sign-in gets no HTTPS egress at all)" }}
{{- end }}
{{- if not .Values.networkPolicy.egress.https.ports }}
{{- fail "networkPolicy.egress.https.ports is empty: a rule without ports allows every port" }}
{{- end }}
{{- range $index, $rule := .Values.networkPolicy.egress.extra }}
{{- if or (not $rule.to) (not $rule.ports) }}
{{- fail (printf "networkPolicy.egress.extra[%d] needs both `to` and `ports`, neither empty: without `to` it allows every address, without `ports` every port" $index) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/* The names `settings` takes: every setting of the leader (config.py) that is not a
secret and that no other value of the chart sets. values.schema.json holds the same list,
and ci/check_render.py derives it from config.py and fails when the three differ. */}}
{{- define "swarmscribe-leader.settingNames" -}}
LEASE_SECONDS HEARTBEAT_SECONDS MAX_ATTEMPTS CLAIM_RETRY_AFTER REAPER_INTERVAL_SECONDS SCANNER_INTERVAL_SECONDS FOLLOWER_GONE_AFTER_SECONDS DOWNLOAD_LINK_TTL_SECONDS UPLOAD_LINK_TTL_SECONDS LINKS_REFRESH_MIN_SECONDS ROLE_CACHE_SECONDS
{{- end }}

{{/* One setting's value as the leader must read it. A string is passed as written. A number
is written out in full: Helm holds every number of a values file as a float, and printed as
one, 1000000 would arrive as "1e+06", which the leader refuses. */}}
{{- define "swarmscribe-leader.settingValue" -}}
{{- if kindIs "string" . }}{{ . }}{{ else }}{{ toJson . }}{{ end }}
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
SWARMSCRIBE_{{ $name }}: {{ include "swarmscribe-leader.settingValue" $value | quote }}
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

{{/* The leader pods. fsGroup: the storage volumes are opened with the leader's group
(storage.fsGroup: null leaves the volumes' ownership alone). */}}
{{- define "swarmscribe-leader.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
{{- if not (kindIs "invalid" .Values.storage.fsGroup) }}
fsGroup: {{ .Values.storage.fsGroup }}
fsGroupChangePolicy: OnRootMismatch
{{- end }}
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
