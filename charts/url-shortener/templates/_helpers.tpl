{{- define "shortener.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "shortener.imageTag" -}}{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}

{{- define "shortener.image" -}}{{ .Values.image.repository }}:{{ include "shortener.imageTag" . }}{{- end -}}

{{- define "shortener.labels" -}}
app.kubernetes.io/part-of: url-shortener
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "shortener.appSelector" -}}
app.kubernetes.io/name: url-shortener
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "shortener.dbSelector" -}}
app.kubernetes.io/name: postgres
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "shortener.dbHost" -}}
{{- if .Values.postgres.enabled -}}{{ .Release.Name }}-postgres{{- else -}}{{ required "database.externalHost is required when postgres.enabled=false" .Values.database.externalHost }}{{- end -}}
{{- end -}}

{{/* Env shared by the app and the migration job. */}}
{{- define "shortener.dbEnv" -}}
- name: DB_USER
  valueFrom: { secretKeyRef: { name: {{ .Values.database.existingSecret }}, key: username } }
- name: DB_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ .Values.database.existingSecret }}, key: password } }
{{- end -}}

{{- define "shortener.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: { drop: ["ALL"] }
{{- end -}}

{{- define "shortener.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile: { type: RuntimeDefault }
{{- end -}}
