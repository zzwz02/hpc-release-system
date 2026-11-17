export function ChipSupportFields({ x86, arm }: { x86: string[]; arm: string[] }) {
  return (
    <>
      <label>X86 芯片<input className="input" value={x86.join(", ") || "未声明支持"} disabled /></label>
      <label>ARM 芯片<input className="input" value={arm.join(", ") || "未声明支持"} disabled /></label>
    </>
  );
}
