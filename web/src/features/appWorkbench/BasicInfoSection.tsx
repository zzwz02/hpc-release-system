import React from "react";
import { apiPost } from "../../api/http";
import { appTypeOptionsForTarget, normalizeDocTarget } from "../../lib/domainMetadata";
import { docTargetLabels, docTargetOptions } from "../../lib/labels";
import { toast } from "../../lib/toast";
import type { App } from "../../types";

function formFromApp(app: App) {
  return {
    official_name: app.official_name,
    owners: app.owners.join(", "),
    doc_target: normalizeDocTarget(app.doc_target),
    type: app.type,
  };
}

interface BasicInfoSectionProps {
  app: App;
  canEdit: boolean;
  canEditRmFields: boolean;
  onSaved: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function BasicInfoSection({ app, canEdit, canEditRmFields, onSaved, onDirtyChange }: BasicInfoSectionProps) {
  const [form, setForm] = React.useState(() => formFromApp(app));
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setForm(formFromApp(app));
    setEditing(false);
    setError("");
    onDirtyChange(false);
  }, [app, onDirtyChange]);

  function patch(update: Partial<typeof form>) {
    setForm((current) => ({ ...current, ...update }));
    onDirtyChange(true);
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const fields = {
        doc_target: form.doc_target,
        type: form.type,
        ...(canEditRmFields ? { official_name: form.official_name, owners: form.owners } : {}),
      };
      await apiPost("/api/apps/profile", { app_id: app.id, fields });
      setEditing(false);
      onDirtyChange(false);
      toast.success("基本信息已保存");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const typeOptions = appTypeOptionsForTarget(form.doc_target);
  return (
    <details className="section" open data-testid="basic-info-section">
      <summary><span className="chev">▶</span> 基本信息</summary>
      <div className="section-body">
        <div className="form basic-info-form">
          <label>官方名称
            <input className="input" value={form.official_name}
              onChange={(e) => patch({ official_name: e.target.value })}
              disabled={!editing || !canEditRmFields || saving} data-testid="field-official-name" />
          </label>
          <label>Owner
            <input className="input" value={form.owners} onChange={(e) => patch({ owners: e.target.value })}
              disabled={!editing || !canEditRmFields || saving} />
          </label>
          <label>类型
            <select className="select" value={form.doc_target} disabled={!editing || saving}
              onChange={(e) => {
                const target = e.target.value;
                patch({ doc_target: normalizeDocTarget(target), type: appTypeOptionsForTarget(target).includes(form.type) ? form.type : "" });
              }} data-testid="field-doc-target">
              {docTargetOptions.map((value) => <option key={value} value={value}>{docTargetLabels[value]}</option>)}
            </select>
          </label>
          <label>App 类型
            <select className="select" value={form.type} disabled={!editing || saving}
              onChange={(e) => patch({ type: e.target.value })} data-testid="field-app-type">
              <option value="">请选择 App 类型</option>
              {typeOptions.map((type) => <option key={type} value={type}>{type}</option>)}
              {form.type && !typeOptions.includes(form.type) && <option value={form.type}>{form.type}（当前值）</option>}
            </select>
          </label>
        </div>
        {error && <div className="banner bad mt-10" role="alert">{error}</div>}
        {canEdit && (
          <div className="row gap-8 mt-10">
            <span className="spacer" />
            {editing ? (
              <>
                <button className="btn sm" disabled={saving} onClick={() => {
                  setForm(formFromApp(app)); setEditing(false); setError(""); onDirtyChange(false);
                }}>取消</button>
                <button className="btn primary sm" disabled={saving} onClick={() => void save()}>保存</button>
              </>
            ) : <button className="btn sm" onClick={() => setEditing(true)}>✎ 修改</button>}
          </div>
        )}
      </div>
    </details>
  );
}
