/**
 * 资料 — optional App profile used by the cumulative manuals.
 *
 * None of these fields is a release prerequisite any more; sections start
 * collapsed.  Owner and official name are RM-only.
 */
import React from "react";
import { apiPost } from "../../api/http";
import { toast } from "../../lib/toast";
import { appTypeOptionsForTarget, normalizeDocTarget } from "../../lib/domainMetadata";
import { docTargetLabels, docTargetOptions } from "../../lib/labels";
import type { App, AppTestDoc } from "../../types";
import { APP_DESCRIPTION_LIMIT, appDescriptionCount } from "./helpers";
import { ReadField } from "./pills";

const DOC_FIELDS = [
  ["intro", "基本介绍"],
  ["image_usage", "镜像使用方法"],
  ["binary_usage", "二进制包使用方法"],
  ["env_setup", "环境搭建"],
  ["limitations", "已知限制"],
] as const;

const TEST_DOC_FIELDS = [
  ["content", "测试内容"],
  ["dataset", "测试数据集"],
  ["result_view", "结果查看"],
  ["pass_criteria", "通过标准"],
] as const;

interface ProfileForm {
  official_name: string;
  owners: string;
  doc_target: string;
  type: string;
  official_url: string;
  description: string;
  doc: Record<string, string>;
  community: Record<string, string>;
  sanity: { arm_kylin: boolean; ubuntu: boolean };
  test_docs: AppTestDoc[];
}

function formFromApp(app: App): ProfileForm {
  return {
    official_name: app.official_name,
    owners: app.owners.join(", "),
    doc_target: normalizeDocTarget(app.doc_target),
    type: app.type,
    official_url: app.official_url,
    description: app.description,
    doc: { ...(app.doc as Record<string, string>) },
    community: { ...(app.community as Record<string, string>) },
    sanity: { arm_kylin: !!app.sanity?.arm_kylin, ubuntu: !!app.sanity?.ubuntu },
    test_docs: app.test_docs ?? [],
  };
}

interface ProfilePaneProps {
  app: App;
  canEdit: boolean;
  canEditRmFields: boolean;
  onSaved: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function ProfilePane({ app, canEdit, canEditRmFields, onSaved, onDirtyChange }: ProfilePaneProps) {
  const [editMode, setEditMode] = React.useState(false);
  const [form, setForm] = React.useState<ProfileForm>(() => formFromApp(app));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setForm(formFromApp(app));
    setEditMode(false);
    setError("");
    onDirtyChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app]);

  function patch(update: Partial<ProfileForm>) {
    setForm((f) => ({ ...f, ...update }));
    onDirtyChange(true);
  }

  const tests = app.latest_app_info?.tests ?? [];
  const testDocsByPath = new Map(form.test_docs.map((doc) => [doc.path, doc]));
  const descCount = appDescriptionCount(form.description);
  const community = app.latest_app_info?.community ?? {};
  const hasCommunity = (community.image_count ?? 0) > 0 || (community.pkg_count ?? 0) > 0;
  const hasPkg = (community.pkg_count ?? 0) > 0;
  const typeOptions = appTypeOptionsForTarget(form.doc_target);

  function patchTestDoc(path: string, field: keyof AppTestDoc, value: string) {
    const current = testDocsByPath.get(path) ?? { path };
    const next = { ...current, [field]: value };
    patch({ test_docs: [...form.test_docs.filter((doc) => doc.path !== path), next] });
  }

  async function handleSave() {
    const fields: Record<string, unknown> = {
      doc_target: form.doc_target,
      type: form.type,
      official_url: form.official_url,
      description: form.description,
      doc: form.doc,
      community: form.community,
      test_docs: form.test_docs.filter((doc) => tests.some((t) => t.path === doc.path)),
    };
    if (canEditRmFields) {
      fields.official_name = form.official_name;
      fields.owners = form.owners;
      fields.sanity = form.sanity;
    }
    setSaving(true);
    setError("");
    try {
      await apiPost("/api/apps/profile", { app_id: app.id, fields });
      onDirtyChange(false);
      setEditMode(false);
      toast.success("资料已保存");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const disabled = !editMode;
  return (
    <>
      <div className="detail-body" data-testid="profile-pane">
        <div className="banner">这些资料用于使用手册和 AI4Sci 手册，不是报名或发布的前置条件，可按需填写。</div>

        <details className="section" open>
          <summary><span className="chev">▶</span> 基本信息</summary>
          <div className="section-body">
            <div className="form">
              <label>官方名称
                <input className="input" value={form.official_name} onChange={(e) => patch({ official_name: e.target.value })}
                  disabled={disabled || !canEditRmFields} data-testid="field-official-name" />
              </label>
              <label>Owner
                <input className="input" value={form.owners} onChange={(e) => patch({ owners: e.target.value })}
                  disabled={disabled || !canEditRmFields} />
              </label>
              <label>类型
                <select className="select" value={form.doc_target} disabled={disabled}
                  onChange={(e) => {
                    const target = e.target.value;
                    patch({ doc_target: target, type: appTypeOptionsForTarget(target).includes(form.type) ? form.type : "" });
                  }}
                  data-testid="field-doc-target">
                  {docTargetOptions.map((v) => <option key={v} value={v}>{docTargetLabels[v]}</option>)}
                </select>
              </label>
              <label>App 类型
                <select className="select" value={form.type} disabled={disabled}
                  onChange={(e) => patch({ type: e.target.value })} data-testid="field-app-type">
                  <option value="">请选择 App 类型</option>
                  {typeOptions.map((type) => <option key={type} value={type}>{type}</option>)}
                  {form.type && !typeOptions.includes(form.type) && <option value={form.type}>{form.type}（当前值）</option>}
                </select>
              </label>
              <label>官方 URL
                <input className="input" value={form.official_url} onChange={(e) => patch({ official_url: e.target.value })}
                  disabled={disabled} />
              </label>
            </div>
            <label className="docfield desc-field">
              描述（{APP_DESCRIPTION_LIMIT}字内）
              {editMode ? (
                <div className="textarea-wrap">
                  <textarea value={form.description} onChange={(e) => patch({ description: e.target.value })}
                    data-testid="field-description" />
                  <span className={`desc-counter ${descCount > APP_DESCRIPTION_LIMIT ? "over" : ""}`}>
                    {descCount}/{APP_DESCRIPTION_LIMIT}
                  </span>
                </div>
              ) : (
                <ReadField value={form.description} />
              )}
            </label>
          </div>
        </details>

        <details className="section">
          <summary><span className="chev">▶</span> 文档字段（Markdown 格式）</summary>
          <div className="section-body docfields-2col">
            {DOC_FIELDS.map(([key, label]) => (
              <label key={key} className="docfield docfield-split">
                <span className="df-label">{label}</span>
                {editMode ? (
                  <textarea value={form.doc[key] ?? ""}
                    onChange={(e) => patch({ doc: { ...form.doc, [key]: e.target.value } })}
                    data-testid={`field-doc-${key}`} />
                ) : (
                  <ReadField value={form.doc[key]} />
                )}
              </label>
            ))}
          </div>
        </details>

        <details className="section">
          <summary><span className="chev">▶</span> 测试说明（各 test_cmd）</summary>
          <div className="section-body">
            {!tests.length && <p className="muted small">app_info 中没有启用的测试项。</p>}
            {tests.map((test) => {
              const doc = testDocsByPath.get(test.path) ?? { path: test.path };
              return (
                <div key={test.path} className="tcard">
                  <div className="tcard-h"><span className="code">{test.path}</span></div>
                  <div className="tcard-b">
                    <label className="full">命令<ReadField value={test.command} code /></label>
                    {TEST_DOC_FIELDS.map(([field, label]) => (
                      <label key={field}>{label}
                        {editMode
                          ? <textarea value={doc[field] ?? ""} onChange={(e) => patchTestDoc(test.path, field, e.target.value)} />
                          : <ReadField value={doc[field]} />}
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </details>

        <details className="section">
          <summary><span className="chev">▶</span> 社区发布与 Sanity</summary>
          <div className="section-body">
            {hasCommunity ? (
              <div className="form community-doc-form">
                <label>开发者社区发布情况
                  <input className="input" value={community.release_status ?? ""} disabled />
                </label>
                <label>一级菜单
                  <input className="input" value={form.community.menu_level1 ?? ""} disabled={disabled}
                    onChange={(e) => patch({ community: { ...form.community, menu_level1: e.target.value } })} />
                </label>
                {hasPkg && (
                  <>
                    <label>社区包支持 Python 版本
                      <input className="input" value={form.community.python_version ?? ""} disabled={disabled}
                        onChange={(e) => patch({ community: { ...form.community, python_version: e.target.value } })} />
                    </label>
                    <label>社区包支持框架及版本
                      <input className="input" value={form.community.framework_version ?? ""} disabled={disabled}
                        onChange={(e) => patch({ community: { ...form.community, framework_version: e.target.value } })} />
                    </label>
                  </>
                )}
                <label className="grid-span-all">说明文档链接
                  <input className="input" value={form.community.doc_url ?? ""} disabled={disabled}
                    onChange={(e) => patch({ community: { ...form.community, doc_url: e.target.value } })} />
                </label>
                <label className="grid-span-all community-description-field">描述
                  <textarea value={form.community.description ?? ""} disabled={disabled}
                    onChange={(e) => patch({ community: { ...form.community, description: e.target.value } })} />
                </label>
              </div>
            ) : (
              <p className="muted small">app_info 未声明开发者社区产物。</p>
            )}
            <div className="row mt-11 gap-24 wrap">
              <label className="check">
                <input type="checkbox" checked={form.sanity.arm_kylin} disabled={disabled || !canEditRmFields}
                  onChange={(e) => patch({ sanity: { ...form.sanity, arm_kylin: e.target.checked } })} />
                ARM / Kylin Sanity <span className="small muted">（RM 填写）</span>
              </label>
              <label className="check">
                <input type="checkbox" checked={form.sanity.ubuntu} disabled={disabled || !canEditRmFields}
                  onChange={(e) => patch({ sanity: { ...form.sanity, ubuntu: e.target.checked } })} />
                Ubuntu / 兼容性 Sanity <span className="small muted">（RM 填写）</span>
              </label>
            </div>
          </div>
        </details>
      </div>
      <div className="detail-foot">
        <span className="foot-note">{editMode ? "编辑中 · 刷新前请先保存或取消" : canEdit ? "点击「修改」编辑资料" : "你没有此 app 的编辑权限"}</span>
        <div className="spacer" />
        {error && <span className="foot-blocker" role="alert">{error}</span>}
        {canEdit && !editMode && <button className="btn primary" onClick={() => setEditMode(true)}>✎ 修改</button>}
        {canEdit && editMode && (
          <>
            <button className="btn" onClick={() => { setForm(formFromApp(app)); setEditMode(false); setError(""); onDirtyChange(false); }}>取消</button>
            <button className="btn primary" onClick={() => void handleSave()} disabled={saving || descCount > APP_DESCRIPTION_LIMIT}>保存</button>
          </>
        )}
      </div>
    </>
  );
}
