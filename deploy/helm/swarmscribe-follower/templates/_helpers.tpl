{{/* The app name label: the chart name, or nameOverride. */}}
{{- define "swarmscribe-follower.name" -}}
{{- regexReplaceAll "-+$" (default .Chart.Name .Values.nameOverride | trunc 63) "" }}
{{- end }}

{{/* The name of every object: at most 63 characters, the limit for a label value. A release
name has up to 53 characters and may hold dots, which an object name here may not. */}}
{{- define "swarmscribe-follower.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if .Values.fullnameOverride }}
{{- regexReplaceAll "-+$" (.Values.fullnameOverride | trunc 63) "" }}
{{- else if contains $name .Release.Name }}
{{- regexReplaceAll "-+$" (.Release.Name | replace "." "-" | trunc 63) "" }}
{{- else }}
{{- regexReplaceAll "-+$" (printf "%s-%s" (.Release.Name | replace "." "-") $name | trunc 63) "" }}
{{- end }}
{{- end }}

{{- define "swarmscribe-follower.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarmscribe-follower.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: follower
{{- end }}

{{- define "swarmscribe-follower.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "swarmscribe-follower.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "swarmscribe-follower.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "swarmscribe-follower.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "swarmscribe-follower.image" -}}
{{- $repository := required "image.repository is required: the follower images are not published; build one and load it into the cluster (see the README)" .Values.image.repository }}
{{- if .Values.image.digest }}
{{- printf "%s@%s" $repository .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" $repository (required "image.tag is required: the follower images are not published; build one and name its tag (see the README)" .Values.image.tag) }}
{{- end }}
{{- end }}

{{/* The TCP port of leader.url: the one it names, else 443 (https) or 80 (http). Fails the
render on a URL the follower itself would refuse. */}}
{{- define "swarmscribe-follower.leaderPort" -}}
{{- $url := required "leader.url is required: the address followers reach the leader at, e.g. https://leader.example.org" .Values.leader.url }}
{{- if not (regexMatch `^https?://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?)(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$` $url) }}
{{- fail (printf "leader.url must be http(s)://<host>[:port][/path] with no user, query, fragment or space (got %q)" $url) }}
{{- end }}
{{- if and (hasPrefix "http://" $url) (not .Values.leader.allowHttp) }}
{{- fail "leader.url must be https: the follower refuses a plain http leader unless leader.allowHttp is true (a test cluster only)" }}
{{- end }}
{{- $authority := regexFind `^https?://[^/]+` $url }}
{{- $port := regexFind `:[0-9]+$` $authority | trimPrefix ":" }}
{{- if $port }}
{{- if or (lt (atoi $port) 1) (gt (atoi $port) 65535) }}
{{- fail (printf "leader.url has the port %s: a port is 1 to 65535" $port) }}
{{- end }}
{{- atoi $port }}
{{- else }}
{{- ternary "80" "443" (hasPrefix "http://" $url) }}
{{- end }}
{{- end }}

{{/* Fails the render on values that would deploy a pool that cannot start. */}}
{{- define "swarmscribe-follower.validate" -}}
{{- $_ := include "swarmscribe-follower.image" . }}
{{- $_ := include "swarmscribe-follower.leaderPort" . }}
{{- $_ := required "poolToken.existingSecret is required: the Secret holding the pool token (swarmscribe-admin pool-tokens create)" .Values.poolToken.existingSecret }}
{{- /* Names the chart sets itself, and the four the follower also reads with the prefix
(populate_by_name): a token must not reach values, Helm's history or the pod spec that way.
The follower reads its environment case-insensitively, so every comparison is on the
upper-cased name. */}}
{{- $owned := list "STATE_DIR" "SCRATCH_DIR" "MODEL_DIR" "HEALTH_ADDR" "ON_DRAINED" "SHUTDOWN_GRACE_SECONDS" "MEMORY_LIMIT_MB" "ALLOW_HTTP" "POOL" "DEVICE" "JOIN_TOKEN" "JOIN_TOKEN_FILE" "LEADER_URL" "LEADER_CA_FILE" }}
{{- $fixed := list "SWARMSCRIBE_LEADER_URL" "SWARMSCRIBE_JOIN_TOKEN" "SWARMSCRIBE_JOIN_TOKEN_FILE" "SWARMSCRIBE_LEADER_CA_FILE" "POD_IP" "OMP_NUM_THREADS" }}
{{- range $name, $_ := .Values.settings }}
{{- if not (regexMatch "^[A-Z][A-Z0-9_]*$" $name) }}
{{- fail (printf "settings key %q must match ^[A-Z][A-Z0-9_]*$ (an upper-case environment name without the SWARMSCRIBE_FOLLOWER_ prefix)" $name) }}
{{- end }}
{{- if has $name $owned }}
{{- fail (printf "settings.%s is set by the chart: use leader, pool, gpu, resources, healthPort or terminationGracePeriodSeconds" $name) }}
{{- end }}
{{- end }}
{{- range .Values.extraEnv }}
{{- $upper := upper (toString .name) }}
{{- if or (has $upper $fixed) (and (hasPrefix "SWARMSCRIBE_FOLLOWER_" $upper) (has (trimPrefix "SWARMSCRIBE_FOLLOWER_" $upper) $owned)) }}
{{- fail (printf "extraEnv %s collides with a variable the chart sets (and a token never goes in values: use poolToken.existingSecret)" .name) }}
{{- end }}
{{- end }}
{{- if and (eq .Values.models.volume "persistentVolumeClaim") (not .Values.models.existingClaim) }}
{{- fail "models.existingClaim is required with models.volume persistentVolumeClaim" }}
{{- end }}
{{- end }}

{{/* What the follower container may use: `resources`, and on a GPU pool exactly one GPU
(one follower uses one GPU, whole). */}}
{{- define "swarmscribe-follower.resources" -}}
{{- $resources := deepCopy .Values.resources }}
{{- if .Values.gpu.enabled }}
{{- $limits := deepCopy (default dict $resources.limits) }}
{{- $_ := set $limits .Values.gpu.resource 1 }}
{{- $_ := set $resources "limits" $limits }}
{{- end }}
{{- toYaml $resources }}
{{- end }}

{{/* fsGroup: the Secret's file is then readable by the follower's group and by nobody else. */}}
{{- define "swarmscribe-follower.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "swarmscribe-follower.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Addresses no leader, storage service or model host lives at, cut out of 0.0.0.0/0: this
network, Alibaba Cloud metadata, loopback, the Azure platform address, link-local (cloud
metadata), multicast and reserved. A follower fetches the links its leader hands it; these are
the addresses such a link must never reach. The console chart cuts out the same. */}}
{{- define "swarmscribe-follower.refusedV4" -}}
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
{{- define "swarmscribe-follower.refusedV6" -}}
- ::/8
- 2001::/32
- 2002::/16
- fd00:ec2::254/128
- fe80::/10
- fec0::/10
- ff00::/8
{{- end }}
