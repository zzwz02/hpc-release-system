import React from "react";
import { ChipSupportFields } from "../../components/ChipSupportFields";
import { DeliveryImages } from "../../components/DeliveryImages";
import { TestScopeTable } from "../../components/TestScopeTable";
import { publicationManifestSourceLabel, type PublicationManifestSource } from "../../lib/domainMetadata";
import { qaStatusLabels } from "../../lib/labels";
import { formatServerTime } from "../../lib/time";
import type { DeliveryImage, QaStatus, TestPlatform } from "../../types";

export interface ImagePublication {
  key: string;
  official_version_id: number | null;
  entry_id: number | null;
  app_id: string;
  app_name: string;
  app_version: string;
  release: { id: string; name: string; release_date: string };
  published_at: string;
  manifest_source: PublicationManifestSource;
  note: string;
  images: DeliveryImage[];
  test_manifest: {
    app: { git_url: string; git_branch: string };
    app_info: { commit_id: string; x86_chips: string[]; arm_chips: string[] };
    qa: { status: QaStatus | ""; note: string };
    scope: { tests: Array<{ path: string }>; excluded_tests: string[] };
    commands: Array<TestPlatform & { test: string; command: string; delivery_reference: string }>;
  } | null;
}

export interface PublicationLookupResult {
  source: "publications";
  image: string;
  digest: string;
  publications: ImagePublication[];
}

export function ImagePublicationPane({ result }: { result: PublicationLookupResult }) {
  const [selectedKey, setSelectedKey] = React.useState("");
  const selected = result.publications.find((publication) => publication.key === selectedKey) ?? result.publications[0];
  if (!selected) return null;
  const manifest = selected.test_manifest;
  const info = manifest?.app_info;
  const sourceUrl = info ? `/api/lookup/app-info?${new URLSearchParams({
    image: result.image, commit_id: info.commit_id, release_id: selected.release.id,
    app_id: selected.app_id, publication_key: selected.key,
  })}` : null;
  return <div className="panel" data-testid="lookup-publication-result">
    <div className="panel-head">
      <h2>{selected.app_name} {selected.app_version}</h2>
      <span className="pill">{publicationManifestSourceLabel(selected.manifest_source)}</span>
      <span className="count">{manifest?.commands.length ?? 0} 条测试命令</span>
    </div>
    <div className="panel-body">
      <div className="row">
        <label htmlFor="image-publication">发布记录（{result.publications.length}）</label>
        <select id="image-publication" className="select select-inline" value={selected.key} aria-label="选择发布记录"
          onChange={(event) => setSelectedKey(event.target.value)}>
          {result.publications.map((publication) => <option key={publication.key} value={publication.key}>
            {publication.release.name} · {publication.app_name} · {publication.app_version}
          </option>)}
        </select>
        <span className="small muted">发布时间：{formatServerTime(selected.published_at)}</span>
      </div>
      <p className="mono small cell-wrap">查询镜像：{result.image}</p>
      <p className="hint">已匹配发布记录，以下展示所选发布的完整镜像组和测试清单。</p>
      {selected.note && <p className={manifest ? "hint" : "lerr"} role={manifest ? undefined : "alert"}>{selected.note}</p>}
      {manifest && <>
        <p className="small cell-wrap">app_info 来源：<a href={sourceUrl!} target="_blank" rel="noopener noreferrer" data-testid="publication-app-info-link">
          {manifest.app.git_url} @ {info?.commit_id || "未记录 commit"}
        </a>（分支：{manifest.app.git_branch}）</p>
        <p className="small" data-testid="publication-qa">原发布 QA（App 级）：
          {manifest.qa.status ? qaStatusLabels[manifest.qa.status] ?? manifest.qa.status : "未记录"}
          {manifest.qa.note && <> · {manifest.qa.note}</>}
        </p>
        <p className="small">纳入测试：{manifest.scope.tests.map((test) => test.path).join(", ") || "无"}</p>
        <p className="small" data-testid="publication-excluded-tests">排除测试：{manifest.scope.excluded_tests.join(", ") || "无"}</p>
        <div className="form form-compact"><ChipSupportFields x86={info?.x86_chips ?? []} arm={info?.arm_chips ?? []} /></div>
      </>}
      <DeliveryImages images={selected.images} />
      {manifest && <>
        {manifest.commands.some((command) => !command.delivery_reference) && <p className="hint">缺少对应交付镜像的命令保留为镜像模板。</p>}
        <TestScopeTable tests={manifest.commands.map((command) => ({ path: command.test, command: command.command, platforms: [command] }))} />
      </>}
    </div>
  </div>;
}
