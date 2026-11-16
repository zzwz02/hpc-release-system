import type { DeliveryBuild, DeliveryImage } from "../types";
import { stripDockerPullPrefix } from "../lib/containerImage";

interface Props {
  images: DeliveryImage[];
  builds?: DeliveryBuild[];
  onChange?: (images: DeliveryImage[]) => void;
  compact?: boolean;
}

export function DeliveryImages({ images, builds = [], onChange, compact = false }: Props) {
  if (compact && !onChange) return (
    <div data-testid="delivery-images">
      {images.length === 0 && <span className="muted">尚未填写交付镜像。</span>}
      {images.map((row) => <div key={row.build_key} className="delivery-image-item">
        <div className="small muted">{[row.os || "未指定构建", row.arch === "amd64" ? "x86" : row.arch === "arm64" ? "ARM" : row.arch,
          row.image_target, row.python_label, row.pytorch_label].filter(Boolean).join(" · ")}</div>
        <div className="mono small cell-wrap">{row.reference || [row.image, row.digest].filter(Boolean).join(" · ") || "—"}</div>
      </div>)}
    </div>
  );
  const rows = onChange ? [
    ...builds.map((build) => images.find((image) => image.build_key === build.build_key) ?? { ...build, reference: "", image: "", digest: "" }),
    ...images.filter((image) => !builds.some((build) => build.build_key === image.build_key)),
  ] : images;
  return (
    <div className="table delivery-images" data-testid="delivery-images">
      <table>
        <thead><tr><th>OS</th><th>CPU 架构</th><th>镜像类型</th><th>Python / PyTorch</th></tr></thead>
        {rows.length === 0 && <tbody><tr><td colSpan={4} className="muted">尚未填写交付镜像。</td></tr></tbody>}
        {rows.map((row) => {
          const editable = onChange && builds.some((build) => build.build_key === row.build_key);
          return (
            <tbody key={row.build_key}>
              <tr className="delivery-platform-row">
                <td>{row.os || "未指定构建"}</td>
                <td className="nowrap">{row.arch === "amd64" ? "x86" : row.arch === "arm64" ? "ARM" : row.arch || "—"}</td>
                <td>{row.image_target || "—"}</td>
                <td>{[row.python_label, row.pytorch_label].filter(Boolean).join(" / ") || "—"}</td>
              </tr>
              <tr className="delivery-reference-row">
                <td colSpan={4}>
                  <div className="small muted mb-6">完整镜像名（image:tag@digest）</div>
                  <div className="mono small cell-wrap">
                    {editable ? <input className="input mono" value={row.reference} data-testid="delivery-reference"
                      aria-label={`交付镜像 ${row.os} ${row.arch} ${row.image_target} ${row.python_label} ${row.pytorch_label}`}
                      placeholder="仓库/镜像:tag@sha256:…"
                      onChange={(event) => {
                        const reference = stripDockerPullPrefix(event.target.value);
                        const remaining = images.filter((image) => image.build_key !== row.build_key);
                        onChange(reference.trim() ? [...remaining, { ...row, reference }] : remaining);
                      }} /> : <>
                      <span>{row.reference || [row.image, row.digest].filter(Boolean).join(" · ") || "—"}</span>
                      {onChange && <button type="button" className="btn sm" onClick={() => onChange(images.filter((image) => image.build_key !== row.build_key))}>移除此镜像</button>}
                    </>}
                  </div>
                </td>
              </tr>
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
